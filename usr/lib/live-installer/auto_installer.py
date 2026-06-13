#!/usr/bin/python3
# coding: utf-8
"""Headless driver for unattended installation.

Sits beside the GTK InstallerWindow as a second consumer of
InstallerEngine: it builds a Setup from a validated answer file
(schema.py), resolves the target disk by stable attributes
(diskmatch.py), registers console/log implementations of the engine's
progress and error hooks, and runs the same
start_installation()/finish_installation() sequence the GUI does.
After the engine finishes, it re-enters the target to create any
additional users, apply package changes and run post-install steps,
honouring the answer file's per-failure-mode abort/continue policy.

Answer-file sources: a local path, or an http(s) URL.  Plain HTTP is
refused (answer files carry password hashes) unless --insecure is
given.  On the kernel command line: live-installer.auto=<source>.
"""

import argparse
import os
import shlex
import sys
import urllib.error
import urllib.request

import diskmatch
import schema
from commandrunner import CommandRunner

FINAL_MARKER = "Automated installation complete"
FAILURE_MARKER = "Automated installation FAILED"
CMDLINE_KEY = "live-installer.auto="
# kernel-cmdline equivalent of --insecure (cmdline boots have no argv)
CMDLINE_INSECURE = "live-installer.auto-insecure"

# Mirrors main.py's IS_MINT detection without importing the GTK module
def _is_mint():
    try:
        import distro
        like = distro.like()
    except ImportError:
        like = ""
    return (
        os.path.exists("/usr/share/doc/ubuntu-system-adjustments/copyright")
        or "ubuntu" in like
    )


class InstallationFailed(Exception):
    pass


def fetch_answer_file(source, insecure=False):
    """Return the text of an unattended-install file from a path or URL.

    Used for the answer file and for LUKS keyfiles — both carry secrets,
    so plain HTTP is refused unless explicitly opted into.
    """
    if source.startswith("http://") and not insecure:
        raise schema.ConfigError(
            f"Refusing to fetch {source} over plain HTTP: unattended-install "
            "files carry secrets (password hashes, key material). Use "
            "https://, or pass --insecure / boot with "
            f"{CMDLINE_INSECURE} if you accept the risk."
        )
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(source, timeout=60) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, OSError) as exc:
            raise schema.ConfigError(
                f"Could not fetch {source}: {exc}"
            )
    try:
        with open(source, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise schema.ConfigError(f"Cannot read {source}: {exc}")


def cmdline_source(cmdline_path="/proc/cmdline"):
    """Extract the answer-file source from the kernel command line."""
    try:
        with open(cmdline_path) as f:
            cmdline = f.read()
    except OSError:
        return None
    for token in cmdline.split():
        if token.startswith(CMDLINE_KEY):
            return token[len(CMDLINE_KEY):]
    return None


def cmdline_insecure(cmdline_path="/proc/cmdline"):
    """True if the kernel command line opts in to plain-HTTP answer files."""
    try:
        with open(cmdline_path) as f:
            return CMDLINE_INSECURE in f.read().split()
    except OSError:
        return False


def build_setup(config, *, disk=None, efi=None, is_mint=None, insecure=False):
    """Map a validated AutoInstallConfig onto the engine's Setup object.

    disk/efi/is_mint are injectable for tests; by default the disk is
    resolved from the config's match expression and EFI/edition are
    detected from the running system.
    """
    import installer
    import partitioning

    setup = installer.Setup()
    setup.automated = True
    setup.skip_mount = False
    setup.is_mint = _is_mint() if is_mint is None else is_mint

    setup.language = config.locale.language.split(".")[0]
    setup.timezone = config.locale.timezone
    setup.keyboard_model = config.keyboard.model
    setup.keyboard_layout = config.keyboard.layout
    setup.keyboard_variant = config.keyboard.variant
    setup.hostname = config.network.hostname or "mint"

    primary = config.users[0]
    setup.username = primary.username
    setup.real_name = primary.full_name or primary.username
    setup.password1 = primary.password_crypted
    setup.password2 = primary.password_crypted
    setup.password_is_crypted = True
    setup.autologin = primary.autologin
    setup.ecryptfs = primary.ecryptfs_home

    setup.lvm = config.storage.layout in ("lvm", "lvm-on-luks")
    setup.luks = config.storage.layout == "lvm-on-luks"
    if setup.luks:
        luks = config.storage.luks
        if luks.passphrase_source == "keyfile":
            # The keyfile may be a local path (install media) or an http(s)
            # URL (per-machine keyfiles from a provisioning server); URLs
            # follow the same HTTPS-only rule as the answer file itself.
            passphrase = fetch_answer_file(luks.keyfile, insecure).strip()
            if not passphrase:
                raise schema.ConfigError(
                    f"LUKS keyfile {luks.keyfile} is empty"
                )
            setup.passphrase1 = setup.passphrase2 = passphrase
        else:
            raise schema.ConfigError(
                f"passphrase_source: {luks.passphrase_source} is not yet "
                "implemented in the headless driver (use 'keyfile')"
            )

    setup.disk = disk or diskmatch.resolve_disk(config.storage.target.match)
    setup.diskname = os.path.basename(setup.disk)
    setup.grub_device = setup.disk
    setup.gptonefi = partitioning.is_efi_supported() if efi is None else efi
    setup.oem_mode = config.oem.enabled
    return setup


class HeadlessDriver:
    """Drives InstallerEngine without a GUI."""

    def __init__(self, config, runner=None, engine_factory=None,
                 insecure=False):
        self.config = config
        self.insecure = insecure
        self._log_file = None
        self._serial = None
        self._failed = False
        self._last_progress = None
        self._open_logs()
        self.runner = runner or CommandRunner(log=self.log)
        self._engine_factory = engine_factory

    # -- logging / hooks ---------------------------------------------------

    def _open_logs(self):
        logging_cfg = self.config.logging
        try:
            os.makedirs(os.path.dirname(logging_cfg.destination), exist_ok=True)
            self._log_file = open(logging_cfg.destination, "a", buffering=1)
        except OSError:
            self._log_file = None
        if logging_cfg.also_serial:
            try:
                device = logging_cfg.also_serial
                if not device.startswith("/dev/"):
                    device = "/dev/" + device
                self._serial = open(device, "w", buffering=1)
            except OSError:
                self._serial = None

    def log(self, message):
        line = str(message)
        print(line, flush=True)
        for sink in (self._log_file, self._serial):
            if sink is not None:
                try:
                    sink.write(line + "\n")
                except OSError:
                    pass

    def on_progress(self, percentage, pulse, done, message):
        # The engine fires this once per copied file during the rsync phase
        # (hundreds of thousands of calls); only log actual changes or the
        # serial console becomes the bottleneck of the entire installation.
        line = f"[{percentage:3d}%] {message}"
        if line == self._last_progress:
            return
        self._last_progress = line
        self.log(line)

    def on_error(self, message=""):
        self._failed = True
        self.log(f"ERROR: {message}")

    # -- failure policy ----------------------------------------------------

    def _policy(self, failure_mode, what):
        """Apply the answer file's abort/continue policy for a failure."""
        policy = getattr(self.config.on_failure, failure_mode)
        if policy == "abort":
            raise InstallationFailed(f"{what} (on_failure.{failure_mode}: abort)")
        self.log(f"WARNING: {what} — continuing (on_failure.{failure_mode})")

    # -- post-engine steps -------------------------------------------------
    # These run via the engine's before_unmount_hook: after the system is
    # fully configured but while the chroot is still mounted with network.

    def _create_extra_users(self):
        for user in self.config.users[1:]:
            self.log(f" --> Creating additional user {user.username}")
            gecos = (user.full_name or user.username).replace('"', "'")
            rc = self.runner.chroot(
                f'adduser --disabled-password --gecos "{gecos}" {user.username}'
            )
            if rc != 0:
                self._policy("post_install_script_failure",
                             f"adduser {user.username} failed")
                continue
            # Write the hash via a file, exactly like the engine does for the
            # primary user: crypt hashes contain '$' and must never pass
            # through a shell.
            with open("/target/dev/shm/.passwd", "w") as fp:
                fp.write(user.username + ":" + user.password_crypted + "\n")
            self.runner.chroot("cat /dev/shm/.passwd | chpasswd -e")
            self.runner.run("rm -f /target/dev/shm/.passwd")
            if user.sudo:
                self.runner.chroot(f"adduser {user.username} sudo")

    def _apply_ssh_keys(self):
        for user in self.config.users:
            if not user.ssh_authorized_keys:
                continue
            self.log(f" --> Installing SSH keys for {user.username}")
            ssh_dir = f"/home/{user.username}/.ssh"
            self.runner.chroot(f"mkdir -p {ssh_dir}")
            # written via /target to keep key material out of shell commands
            with open(f"/target{ssh_dir}/authorized_keys", "a") as fp:
                for key in user.ssh_authorized_keys:
                    fp.write(key.rstrip("\n") + "\n")
            self.runner.chroot(f"chmod 700 {ssh_dir}")
            self.runner.chroot(f"chmod 600 {ssh_dir}/authorized_keys")
            self.runner.chroot(
                f"chown -R {user.username}:{user.username} {ssh_dir}"
            )

    def _apply_apt_steps(self):
        packages = self.config.packages
        sources_changed = False
        for step in self.config.post_install:
            if hasattr(step, "apt_key_url"):
                name = os.path.basename(step.apt_key_url) or "extra-key"
                rc = self.runner.chroot(
                    f"wget -O /etc/apt/trusted.gpg.d/{name} "
                    + shlex.quote(step.apt_key_url)
                )
                if rc != 0:
                    self._policy("network_unavailable",
                                 f"fetching {step.apt_key_url} failed")
                sources_changed = True
            elif hasattr(step, "apt_source"):
                self.runner.chroot(
                    f"echo {shlex.quote(step.apt_source)} "
                    ">> /etc/apt/sources.list.d/live-installer-auto.list"
                )
                sources_changed = True

        if packages.add or sources_changed:
            rc = self.runner.chroot("apt-get update")
            if rc != 0:
                self._policy("network_unavailable", "apt-get update failed")
            # On EFI installs the engine dpkg-installs the bootloader stack
            # (shim-signed, grub-efi) from the ISO pool without its full
            # dependency closure, leaving dpkg in a state apt refuses to
            # build on. Complete it before installing anything else.
            rc = self.runner.chroot(
                "DEBIAN_FRONTEND=noninteractive apt-get install -f -y"
            )
            if rc != 0:
                self.log("WARNING: apt-get install -f failed; "
                         "continuing to package installation")
        if packages.add:
            self.log(" --> Installing packages: " + " ".join(packages.add))
            rc = self.runner.chroot(
                "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                + " ".join(shlex.quote(p) for p in packages.add)
            )
            if rc != 0:
                self._policy("package_install_failure",
                             "package installation failed")
        if packages.remove:
            self.log(" --> Removing packages: " + " ".join(packages.remove))
            rc = self.runner.chroot(
                "DEBIAN_FRONTEND=noninteractive apt-get remove --purge -y "
                + " ".join(shlex.quote(p) for p in packages.remove)
            )
            if rc != 0:
                self._policy("package_install_failure",
                             "package removal failed")

    def _build_grub_snippet(self):
        """Build an /etc/default/grub.d snippet for kernel.* settings.

        Written as a grub.d snippet rather than edits to /etc/default/grub
        because distro snippets in that directory are sourced AFTER the
        main file and would otherwise clobber our cmdline. Sourced last
        (zz- name), this snippet sees the final GRUB_CMDLINE_LINUX_DEFAULT
        and rewrites it, so our settings always win.
        """
        kernel = self.config.kernel
        serial = kernel.serial_console.strip()
        extra = kernel.cmdline_extra.strip()
        lines = ["# Added by live-installer (automated installation)"]
        if serial:
            device, _, speed = serial.partition(",")
            speed = speed or "115200"
            unit = device[len("ttyS"):]
            # strip quiet/splash from whatever earlier config set, then add
            # the serial console + disable plymouth so the boot (and the
            # LUKS unlock prompt) is a plain-text askpass on the serial line
            lines.append(
                'GRUB_CMDLINE_LINUX_DEFAULT="$(echo " ${GRUB_CMDLINE_LINUX_DEFAULT} "'
                " | sed -e 's/ quiet / /g' -e 's/ splash / /g'"
                " -e 's/^ *//' -e 's/ *$//')"
                f' console=tty0 console={device},{speed}n8 plymouth.enable=0"'
            )
            lines.append('GRUB_TERMINAL="console serial"')
            lines.append(
                f'GRUB_SERIAL_COMMAND="serial --unit={unit} --speed={speed}"'
            )
        if extra:
            lines.append(
                f'GRUB_CMDLINE_LINUX_DEFAULT="${{GRUB_CMDLINE_LINUX_DEFAULT}} {extra}"'
            )
        return "\n".join(lines) + "\n"

    def _apply_kernel_config(self):
        kernel = self.config.kernel
        serial = kernel.serial_console.strip()
        extra = kernel.cmdline_extra.strip()
        if not serial and not extra:
            return
        if serial:
            self.log(f" --> Provisioning serial console on {serial}")
        if extra:
            self.log(f" --> Appending kernel cmdline: {extra}")

        self.runner.run("mkdir -p /target/etc/default/grub.d")
        with open("/target/etc/default/grub.d/zz-live-installer.cfg", "w") as f:
            f.write(self._build_grub_snippet())
        rc = self.runner.chroot("update-grub")
        if rc != 0:
            self._policy("post_install_script_failure", "update-grub failed")
        # Diagnostic: record the cmdline that actually landed in grub.cfg,
        # so a serial-console/boot issue is traceable from the install log.
        self.log(" --> grub.cfg kernel line: " + self.runner.output(
            "grep -m1 'vmlinuz' /target/boot/grub/grub.cfg | sed 's/^[[:space:]]*//'"))

    def _run_shell_steps(self):
        for step in self.config.post_install:
            if not hasattr(step, "shell"):
                continue
            script = step.shell
            self.log(f" --> Running post-install script {script}")
            if os.path.exists(script):
                # script lives in the live environment (e.g. on the install
                # media) — copy it into the target so the chroot can see it
                self.runner.run(f"cp {shlex.quote(script)} /target/tmp/")
                target_path = "/tmp/" + os.path.basename(script)
                rc = self.runner.chroot(f"sh {target_path}")
                self.runner.run(f"rm -f /target{target_path}")
            else:
                rc = self.runner.chroot(f"sh {shlex.quote(script)}")
            if rc != 0:
                self._policy("post_install_script_failure",
                             f"{script} exited {rc}")

    # -- main flow ----------------------------------------------------------

    def run(self, setup=None):
        """Run the full unattended installation.  Returns an exit code."""
        import installer

        try:
            if setup is None:
                setup = build_setup(self.config, insecure=self.insecure)
            self.log(f" --> Target disk: {setup.disk}")
            setup.print_setup()

            if self._engine_factory is not None:
                engine = self._engine_factory(setup, self.runner)
            else:
                engine = installer.InstallerEngine(setup, runner=self.runner)
            engine.set_progress_hook(self.on_progress)
            engine.set_error_hook(self.on_error)

            engine.start_installation()
            if self._failed:
                raise InstallationFailed("engine error during installation")

            needs_post = (
                len(self.config.users) > 1
                or any(user.ssh_authorized_keys for user in self.config.users)
                or self.config.packages.add or self.config.packages.remove
                or self.config.post_install
                or self.config.kernel.cmdline_extra.strip()
                or self.config.kernel.serial_console.strip()
            )

            def post_install_hook():
                self.log(" --> Applying post-install configuration")
                self._create_extra_users()
                self._apply_ssh_keys()
                self._apply_apt_steps()
                self._apply_kernel_config()
                self._run_shell_steps()

            engine.finish_installation(
                before_unmount_hook=post_install_hook if needs_post else None
            )
            if self._failed:
                raise InstallationFailed("engine error during finalization")
        except (schema.ConfigError, diskmatch.DiskMatchError,
                InstallationFailed) as exc:
            self.log(f"ERROR: {exc}")
            self.log(FAILURE_MARKER)
            return 1
        except Exception as exc:  # never die silently on a target machine
            self.log(f"ERROR: unexpected failure: {exc!r}")
            self.log(FAILURE_MARKER)
            return 1

        self.log(FINAL_MARKER)
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="live-installer --automated",
        description="Unattended installation from a YAML answer file",
    )
    parser.add_argument(
        "--config",
        help="answer file path or URL (default: live-installer.auto= "
             "from the kernel command line)",
    )
    parser.add_argument(
        "--insecure", action="store_true",
        help="allow fetching the answer file over plain HTTP",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate the answer file and resolve the target disk, "
             "then exit without installing",
    )
    args = parser.parse_args(argv)

    source = args.config or cmdline_source()
    if not source:
        parser.error(
            f"no answer file: pass --config or boot with {CMDLINE_KEY}<source>"
        )
    insecure = args.insecure or cmdline_insecure()

    try:
        config = schema.parse_config(fetch_answer_file(source, insecure))
    except schema.ConfigError as exc:
        print(f"ERROR: {exc}", flush=True)
        print(FAILURE_MARKER, flush=True)
        return 1

    if args.dry_run:
        try:
            setup = build_setup(config, insecure=insecure)
        except (schema.ConfigError, diskmatch.DiskMatchError) as exc:
            print(f"ERROR: {exc}", flush=True)
            return 1
        print(f"Answer file OK; would install to {setup.disk}", flush=True)
        return 0

    return HeadlessDriver(config, insecure=insecure).run()


if __name__ == "__main__":
    sys.exit(main())
