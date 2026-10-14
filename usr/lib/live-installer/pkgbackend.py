#!/usr/bin/python3
# coding: utf-8
"""Package backends: apply repositories and install/remove packages in the
installed system.

cloud-init keeps `packages:` distro-agnostic (one list, dispatched per distro)
but keeps repository config per-backend (`apt:`, `yum_repos:`, `zypper:`) — they
never shared a shape. We follow that: the driver holds one PackageBackend, picks
it per distro, and calls it with the agnostic package list plus that backend's
own repo config. Today only apt exists (Mint/LMDE), but the seam keeps the
install step from being apt-hardcoded so dnf/zypper can slot in later.

Execution goes through the injected CommandRunner (chroot into the target), the
same shell-out boundary the rest of the installer uses — so this is unit-testable
with a RecordingRunner and never imports a distro library at module load.
"""

import os
import shlex


class PackageBackend:
    """Interface: apply repo config, then install/remove packages, all in the
    target chroot. Subclasses implement the distro-specific commands.

    runner   — CommandRunner (chroot/run/output) into the mounted target.
    policy   — callable(failure_mode, message); applies the answer file's
               abort/continue policy (may raise InstallationFailed).
    log      — callable(message) for progress output.
    target   — the mounted target root on the host side (for writing files
               that the chroot then sees), default "/target".
    """

    def __init__(self, runner, policy, log, target="/target"):
        self.runner = runner
        self.policy = policy
        self.log = log
        self.target = target

    def apply(self, apt_config, packages, package_remove):
        raise NotImplementedError


class AptBackend(PackageBackend):
    SOURCES_DIR = "/etc/apt/sources.list.d"
    KEYRING_DIR = "/etc/apt/trusted.gpg.d"

    def apply(self, apt_config, packages, package_remove):
        sources = apt_config.sources if apt_config is not None else {}
        for name, src in sources.items():
            self._add_source(name, src)
        if sources or packages:
            self._update()
        if packages:
            self._install(packages)
        if package_remove:
            self._remove(package_remove)

    # -- repositories ------------------------------------------------------

    def _add_source(self, name, src):
        self.log(f" --> Adding apt source: {name}")
        if src.key is not None:
            self._write_key_inline(name, src.key)
        elif src.key_url is not None:
            self._fetch_key_url(name, src.key_url)
        elif src.keyid is not None:
            self._fetch_keyid(name, src.keyid, src.keyserver)
        basename = src.filename or name
        list_path = f"{self.SOURCES_DIR}/{basename}.list"
        self.runner.chroot(
            f"echo {shlex.quote(src.source)} > {shlex.quote(list_path)}"
        )

    def _write_key_inline(self, name, armored):
        # We control the content, so write it straight into the target's
        # trusted keyring dir rather than echoing through the chroot.
        host_dir = self.target + self.KEYRING_DIR
        os.makedirs(host_dir, exist_ok=True)
        path = os.path.join(host_dir, f"{name}.asc")
        with open(path, "w") as f:
            f.write(armored if armored.endswith("\n") else armored + "\n")
        os.chmod(path, 0o644)

    def _fetch_key_url(self, name, url):
        rc = self.runner.chroot(
            f"wget -O {self.KEYRING_DIR}/{shlex.quote(name)}.asc "
            + shlex.quote(url)
        )
        if rc != 0:
            self.policy("network_unavailable", f"fetching key {url} failed")

    def _fetch_keyid(self, name, keyid, keyserver):
        # Pull the key from a keyserver into a temp keyring, then export it
        # (de-armored) into the trusted keyring dir. gnupg ships on the ISO.
        keyring = f"/tmp/li-{name}.gpg"
        out = f"{self.KEYRING_DIR}/{shlex.quote(name)}.gpg"
        cmd = (
            f"gpg --batch --no-default-keyring --keyring {keyring} "
            f"--keyserver {shlex.quote(keyserver)} --recv-keys {shlex.quote(keyid)} "
            f"&& gpg --batch --no-default-keyring --keyring {keyring} "
            f"--export {shlex.quote(keyid)} > {out} "
            f"&& rm -f {keyring}"
        )
        rc = self.runner.chroot(cmd)
        if rc != 0:
            self.policy("network_unavailable",
                        f"fetching key {keyid} from {keyserver} failed")

    # -- packages ----------------------------------------------------------

    def _update(self):
        rc = self.runner.chroot("apt-get update")
        if rc != 0:
            self.policy("network_unavailable", "apt-get update failed")
        # On EFI installs the engine dpkg-installs the bootloader stack
        # (shim-signed, grub-efi) from the ISO pool without its full
        # dependency closure, leaving dpkg in a state apt refuses to build on.
        # Complete it before installing anything else.
        rc = self.runner.chroot(
            "DEBIAN_FRONTEND=noninteractive apt-get install -f -y"
        )
        if rc != 0:
            self.log("WARNING: apt-get install -f failed; "
                     "continuing to package installation")

    def _install(self, packages):
        self.log(" --> Installing packages: " + " ".join(packages))
        rc = self.runner.chroot(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            + " ".join(shlex.quote(p) for p in packages)
        )
        if rc != 0:
            self.policy("package_install_failure", "package installation failed")

    def _remove(self, packages):
        self.log(" --> Removing packages: " + " ".join(packages))
        rc = self.runner.chroot(
            "DEBIAN_FRONTEND=noninteractive apt-get remove --purge -y "
            + " ".join(shlex.quote(p) for p in packages)
        )
        if rc != 0:
            self.policy("package_install_failure", "package removal failed")


def get_backend(runner, policy, log, *, family="debian", target="/target"):
    """Pick the package backend for the target distro family. Only apt today;
    the seam is here so dnf/zypper can be added without touching the driver."""
    if family == "debian":
        return AptBackend(runner, policy, log, target=target)
    raise ValueError(f"no package backend for distro family {family!r}")
