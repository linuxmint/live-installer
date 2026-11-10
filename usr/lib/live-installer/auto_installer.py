#!/usr/bin/python3
"""Unattended installation from a YAML answer file.

This drives InstallerEngine the same way the GUI does, with a Setup built
from the answer file (schema.py) instead of from the GTK pages. The target
disk is resolved by stable attributes (diskmatch.py).

The answer file is given on the kernel command line:

    live-installer.auto=/path/to/answers.yaml
    live-installer.auto=auto     (auto-install.yaml on the install media)

where live-installer-auto.service runs it, or by hand with --config.
See docs/automated-install.md.
"""

import argparse
import os
import subprocess
import sys

import diskmatch
import mint_detect
import schema

CMDLINE_KEY = "live-installer.auto="
MEDIA_DIRS = ("/cdrom", "/run/live/medium")
WELL_KNOWN_NAME = "auto-install.yaml"
SUCCESS_MARKER = "Automated installation complete"
FAILURE_MARKER = "Automated installation FAILED"


class InstallationFailed(Exception):
    pass


def cmdline_source(cmdline_path="/proc/cmdline"):
    """The live-installer.auto= value from the kernel command line, or None."""
    try:
        with open(cmdline_path) as f:
            tokens = f.read().split()
    except OSError:
        return None
    for token in tokens:
        if token.startswith(CMDLINE_KEY):
            return token[len(CMDLINE_KEY):]
    return None


def find_answer_file(source, media_dirs=MEDIA_DIRS):
    """Turn a source (a path, or 'auto') into the path of the answer file."""
    if "://" in source:
        raise schema.ConfigError(
            f"{source}: only local paths are supported (or 'auto' for "
            f"{WELL_KNOWN_NAME} on the install media)")
    if source != "auto":
        return source
    for directory in media_dirs:
        path = os.path.join(directory, WELL_KNOWN_NAME)
        if os.path.isfile(path):
            return path
    raise schema.ConfigError(
        f"no {WELL_KNOWN_NAME} found in {', '.join(media_dirs)}")


def read_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise schema.ConfigError(f"cannot read {path}: {exc}")


def read_passphrase(keyfile, answer_file):
    """The LUKS passphrase from its keyfile. A relative path is taken
    relative to the answer file. Only the trailing newline is removed."""
    path = os.path.join(os.path.dirname(os.path.abspath(answer_file)), keyfile)
    passphrase = read_file(path).rstrip("\r\n")
    if not passphrase:
        raise schema.ConfigError(f"the LUKS keyfile {path} is empty")
    return passphrase


def build_setup(config, answer_file, *, disk=None, efi=None, is_mint=None):
    """Fill the engine's Setup from a validated answer file. disk, efi and
    is_mint are detected unless given (the tests give them)."""
    import installer
    import partitioning

    setup = installer.Setup()
    setup.automated = True
    setup.is_mint = mint_detect.is_mint() if is_mint is None else is_mint
    setup.gptonefi = partitioning.is_efi_supported() if efi is None else efi

    # The engine appends .UTF-8 itself
    setup.language = config.locale.split(".")[0]
    setup.timezone = config.timezone
    setup.keyboard_model = config.keyboard.model
    setup.keyboard_layout = config.keyboard.layout
    setup.keyboard_variant = config.keyboard.variant
    setup.hostname = config.hostname or "mint"

    user = config.users[0]
    setup.username = user.name
    setup.real_name = user.gecos or user.name
    setup.password1 = setup.password2 = user.passwd
    setup.password_is_crypted = True
    setup.autologin = user.autologin
    setup.ecryptfs = user.ecryptfs_home

    storage = config.storage
    setup.disk = disk or diskmatch.resolve_disk(storage.target.match)
    setup.diskname = os.path.basename(setup.disk)
    setup.grub_device = setup.disk
    setup.luks = storage.layout == "luks"
    if setup.luks:
        setup.passphrase1 = setup.passphrase2 = read_passphrase(
            storage.luks.keyfile, answer_file)
    setup.swapfile = storage.swapfile
    if storage.swapfile_size:
        setup.swapfile_size = schema.parse_size(storage.swapfile_size) // 2**20
    return setup


class HeadlessDriver:
    """Runs the engine with console output instead of the GUI."""

    def __init__(self, config, setup, engine=None):
        import installer
        self.config = config
        self.setup = setup
        self.engine = engine or installer.InstallerEngine(setup)
        self.error = None
        self._last_progress = None

    def on_progress(self, percentage, pulse, done, message):
        # The engine reports every copied file; only print changes.
        line = f"[{percentage:3d}%] {message}"
        if line != self._last_progress:
            self._last_progress = line
            print(line, flush=True)

    def on_error(self, message=""):
        if self.error is None:
            self.error = message
        print(f"ERROR: {message}", flush=True)

    def release_target_disk(self):
        # The live session may have mounted partitions of the target disk.
        # The GUI asks the user to unmount them; here nobody can answer.
        import partitioning
        mounted = partitioning.get_mounted_partitions_on_target_disk(self.setup.disk)
        if mounted:
            print(" --> Unmounting " + ", ".join(mounted), flush=True)
            failed = partitioning.unmount_partitions(mounted)
            if failed:
                raise InstallationFailed("cannot unmount the target disk: " + "; ".join(failed))

    def run_late_commands(self):
        for command in self.config.late_commands:
            print(f" --> late_command: {command}", flush=True)
            result = subprocess.run(["chroot", "/target", "/bin/sh", "-c", command])
            if result.returncode != 0:
                raise InstallationFailed(
                    f"late_command exited with {result.returncode}: {command}")

    def run(self):
        """Install. Returns the process exit code."""
        try:
            self.release_target_disk()
            self.engine.set_progress_hook(self.on_progress)
            self.engine.set_error_hook(self.on_error)
            self.engine.start_installation()
            if self.error is None:
                hook = self.run_late_commands if self.config.late_commands else None
                self.engine.finish_installation(before_unmount_hook=hook)
        except Exception as exc:
            self.on_error(str(exc))
        if self.error is not None:
            print(FAILURE_MARKER, flush=True)
            return 1
        print(SUCCESS_MARKER, flush=True)
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="live-installer --automated",
        description="Unattended installation from a YAML answer file")
    parser.add_argument(
        "--config", help="the answer file, or 'auto' for auto-install.yaml on "
        "the install media (default: live-installer.auto= on the kernel "
        "command line)")
    parser.add_argument(
        "--check", action="store_true",
        help="validate the answer file and exit (touches no disks)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate the answer file, find the target disk, and exit")
    args = parser.parse_args(argv)

    source = args.config or cmdline_source()
    if not source:
        parser.error(f"no answer file: use --config or boot with {CMDLINE_KEY}<path>")

    try:
        answer_file = find_answer_file(source)
        config = schema.parse_config(read_file(answer_file))
        if args.check:
            print(f"{answer_file}: OK", flush=True)
            return 0
        setup = build_setup(config, answer_file)
    except (schema.ConfigError, diskmatch.DiskMatchError) as exc:
        print(f"ERROR: {exc}", flush=True)
        print(FAILURE_MARKER, flush=True)
        return 1

    print(f" --> Answer file: {answer_file}", flush=True)
    print(f" --> Target disk: {setup.disk}", flush=True)
    if args.dry_run:
        return 0
    return HeadlessDriver(config, setup).run()


if __name__ == "__main__":
    sys.exit(main())
