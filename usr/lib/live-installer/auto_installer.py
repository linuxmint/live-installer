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
import glob
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import discovery
import diskmatch
import mint_detect
import netconfig
import pkgbackend
import schema
from commandrunner import CommandRunner

FINAL_MARKER = "Automated installation complete"
FAILURE_MARKER = "Automated installation FAILED"
CMDLINE_KEY = "live-installer.auto="
# kernel-cmdline equivalent of --insecure (cmdline boots have no argv)
CMDLINE_INSECURE = "live-installer.auto-insecure"

# First-boot LUKS rekey (passphrase_source: prompt-on-first-boot). The install
# formats LUKS with a random throwaway key and embeds it in the initramfs so the
# first boot auto-unlocks; this oneshot then prompts the operator for the real
# passphrase, swaps it in, removes the throwaway key + keyfile, and restores a
# normal prompting boot. printf %s feeds the new key with no trailing newline,
# matching what cryptsetup reads from the boot-time passphrase prompt.
_LUKS_REKEY_SCRIPT = r"""#!/bin/sh
# Installed by live-installer for storage.luks.passphrase_source:
# prompt-on-first-boot. Runs once on first boot, prompting on the console
# (serial included) for the real passphrase via a plain read on the tty.
# Deliberately NOT `set -e` (a transient blkid must not kill the rekey) and
# NOT systemd-ask-password (its agent path is fragile this early / headless).
KEYFILE=/etc/cryptsetup-keys.d/cryptroot.key
CRYPTTAB=/etc/crypttab
HOOK=/etc/cryptsetup-initramfs/conf-hook

[ -f "$KEYFILE" ] || exit 0   # already rekeyed

DEV=$(awk '$1=="lvmmint"{print $2}' "$CRYPTTAB")
case "$DEV" in
    UUID=*) RES=$(blkid -U "${DEV#UUID=}" 2>/dev/null) && DEV="$RES" ;;
esac

stty -echo 2>/dev/null
while :; do
    printf '\n>>> Set the disk-encryption passphrase for this system: ' > /dev/console
    IFS= read -r PASS || PASS=""
    printf '\n>>> Confirm the disk-encryption passphrase: ' > /dev/console
    IFS= read -r PASS2 || PASS2=""
    if [ -n "$PASS" ] && [ "$PASS" = "$PASS2" ]; then
        break
    fi
    printf '\nPassphrases were empty or did not match; try again.\n' > /dev/console
done
stty echo 2>/dev/null
printf '\n' > /dev/console

printf '%s' "$PASS" | cryptsetup luksAddKey --key-file "$KEYFILE" "$DEV" - || exit 1
cryptsetup luksRemoveKey --key-file "$KEYFILE" "$DEV" || exit 1

shred -u "$KEYFILE" 2>/dev/null || rm -f "$KEYFILE"
sed -i "s#$KEYFILE#none#" "$CRYPTTAB"
rm -f "$HOOK"
update-initramfs -u

systemctl disable li-luks-rekey.service
rm -f /etc/systemd/system/li-luks-rekey.service /usr/local/sbin/li-luks-rekey
systemctl reboot
"""

_LUKS_REKEY_SERVICE = """\
[Unit]
Description=First-boot LUKS passphrase setup (live-installer)
ConditionPathExists=/etc/cryptsetup-keys.d/cryptroot.key
# Run late (the system is up and the tty layer is ready) but before any getty
# claims the console, so this owns the serial line for the prompt.
After=systemd-user-sessions.service
Before=getty.target serial-getty@ttyS0.service getty@tty1.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/li-luks-rekey
StandardInput=tty-force
StandardOutput=tty
StandardError=journal+console
TTYPath=/dev/console
TTYReset=yes
TTYVHangup=yes
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
"""


class InstallationFailed(Exception):
    pass


def fetch_answer_file(source, insecure=False):
    """Return the text of an unattended-install file from a path or URL.

    Used for the answer file and for LUKS keyfiles — both carry secrets,
    so cleartext transports (plain HTTP, NFS, TFTP) are refused unless
    explicitly opted into.
    """
    scheme = source.split("://", 1)[0] if "://" in source else ""
    if scheme in ("http", "nfs", "tftp") and not insecure:
        label = {"http": "plain HTTP", "nfs": "NFS", "tftp": "TFTP"}[scheme]
        raise schema.ConfigError(
            f"Refusing to fetch {source} over {label}: unattended-install "
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
    if source.startswith("nfs://"):
        return _fetch_nfs(source)
    if source.startswith("tftp://"):
        return _fetch_tftp(source)
    try:
        with open(source, encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise schema.ConfigError(f"Cannot read {source}: {exc}")


_NFS_VERSIONS = ("3", "4", "4.0", "4.1", "4.2")


def _parse_nfs_url(source):
    """nfs://host[:port]/export/dir/file.yaml[?vers=N] ->
    (host, '/export/dir', 'file.yaml', vers_or_None).

    The whole directory is mounted and the file read from it; NFSv4 and most
    NFSv3 exports allow mounting a subdirectory of an export this way. An
    optional ?vers= query pins the protocol version (e.g. for a v3-only filer
    or a policy that requires v4.2); omitted, mount.nfs negotiates.
    """
    # urlsplit is IPv6-aware: it strips the brackets from a [2001:db8::1]
    # literal and separates any :port, which a naive split(":") would mangle.
    parts = urllib.parse.urlsplit(source)
    host, path = parts.hostname, parts.path
    if not host or not path or path == "/":
        raise schema.ConfigError(
            f"Malformed NFS URL {source!r}; expected nfs://host/export/file.yaml"
        )
    vers = None
    if parts.query:
        query = urllib.parse.parse_qs(parts.query)
        if "vers" in query:
            vers = query["vers"][-1]
            if vers not in _NFS_VERSIONS:
                raise schema.ConfigError(
                    f"Unsupported NFS vers={vers!r} in {source!r}; expected one "
                    f"of {', '.join(_NFS_VERSIONS)}")
    return host, os.path.dirname(path), os.path.basename(path), vers


def _nfs_mount_source(host, export_dir):
    """The host:export string mount.nfs expects, bracketing IPv6 literals
    (a bare 2001:db8::1 would be misread as host:port)."""
    spec_host = f"[{host}]" if ":" in host else host
    return f"{spec_host}:{export_dir}"


def _nfs_mount(host, export_dir, vers=None):
    """Mount host:export_dir read-only on a fresh temp dir; return its path.
    Factored out so tests can stub the actual mount. `vers` pins the NFS
    protocol version when set; otherwise mount.nfs negotiates."""
    mountpoint = tempfile.mkdtemp(prefix="li-nfs-")
    spec = _nfs_mount_source(host, export_dir)
    options = "ro,nolock,soft,timeo=100,retrans=2"
    if vers:
        options += f",vers={vers}"
    result = subprocess.run(
        ["mount", "-t", "nfs", "-o", options, spec, mountpoint],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        os.rmdir(mountpoint)
        raise schema.ConfigError(
            f"Could not NFS-mount {spec}: "
            f"{result.stderr.strip() or result.returncode}"
        )
    return mountpoint


def _nfs_umount(mountpoint):
    subprocess.run(["umount", mountpoint], capture_output=True, text=True)
    try:
        os.rmdir(mountpoint)
    except OSError:
        pass


def _parse_tftp_url(source):
    """tftp://host[:port]/path -> (host, port, path). IPv6-aware via urlsplit."""
    parts = urllib.parse.urlsplit(source)
    host, path = parts.hostname, parts.path.lstrip("/")
    if not host or not path:
        raise schema.ConfigError(
            f"Malformed TFTP URL {source!r}; expected tftp://host/path"
        )
    return host, parts.port or 69, path


# Large TFTP transfers are discouraged regardless of block size; warn past this.
_TFTP_WARN_BYTES = 256 * 1024
# RFC 2348 block size we request. 1428 keeps a DATA packet inside a 1500-byte
# Ethernet MTU (1428 + 4 TFTP + 8 UDP + 20 IPv4 ≈ 1460), avoiding fragmentation.
_TFTP_BLKSIZE = 1428
_TFTP_DEFAULT_BLKSIZE = 512   # RFC 1350 default, used until/unless negotiated


class _TftpOptionRejected(Exception):
    """Server returned ERROR code 8 (option negotiation); retry without options."""


def _parse_oack(payload):
    """Parse an OACK option payload (name\\0value\\0... pairs) into a dict with
    lower-cased option names."""
    fields = [f for f in payload.split(b"\x00") if f != b""]
    opts = {}
    for i in range(0, len(fields) - 1, 2):
        opts[fields[i].decode(errors="replace").lower()] = \
            fields[i + 1].decode(errors="replace")
    return opts


def _fetch_tftp(source, timeout=10):
    """TFTP read client (octet mode), for a small answer file or keyfile.

    Negotiates RFC 2347 options (RFC 2348 ``blksize``, RFC 2349 ``tsize``) for
    fewer round-trips, and falls back cleanly to RFC 1350 (512-byte blocks)
    when the server ignores the options or rejects them with ERROR code 8 — so
    it works against option-aware and option-unaware servers alike. TFTP is
    cleartext, so it is gated like HTTP."""
    host, port, path = _parse_tftp_url(source)
    try:
        family = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0][0]
        addr = (host, port)
    except OSError as exc:
        raise schema.ConfigError(f"Cannot resolve {host} for {source}: {exc}")
    try:
        return _tftp_read(family, addr, path, source, timeout,
                          request_options=True)
    except _TftpOptionRejected:
        # A strict server actively refused our options; retry as plain RFC 1350.
        return _tftp_read(family, addr, path, source, timeout,
                          request_options=False)


def _tftp_read(family, addr, path, source, timeout, request_options):
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        rrq = bytearray(b"\x00\x01" + path.encode() + b"\x00octet\x00")
        if request_options:
            rrq += b"blksize\x00%d\x00" % _TFTP_BLKSIZE
            rrq += b"tsize\x000\x00"
        sock.sendto(bytes(rrq), addr)

        data = bytearray()
        blksize = _TFTP_DEFAULT_BLKSIZE
        expected = 1
        server = None
        warned = False
        negotiated = False     # have we seen the first DATA/OACK yet?
        while True:
            try:
                pkt, src = sock.recvfrom(65536)
            except socket.timeout:
                raise schema.ConfigError(f"TFTP timed out fetching {source}")
            if server is None:
                server = src  # the server answers from a fresh transfer port
            opcode = int.from_bytes(pkt[:2], "big")
            if opcode == 6 and not negotiated:  # OACK
                opts = _parse_oack(pkt[2:])
                if "blksize" in opts:
                    try:
                        blksize = int(opts["blksize"])
                    except ValueError:
                        raise schema.ConfigError(
                            f"TFTP server sent a bad blksize for {source}")
                negotiated = True
                sock.sendto(b"\x00\x04\x00\x00", server)  # ACK block 0
                continue
            if opcode == 5:  # ERROR
                code = int.from_bytes(pkt[2:4], "big")
                msg = pkt[4:].split(b"\x00", 1)[0].decode(errors="replace")
                if code == 8 and request_options and not negotiated:
                    raise _TftpOptionRejected()
                raise schema.ConfigError(f"TFTP error for {source}: {msg}")
            if opcode != 3:  # not DATA
                raise schema.ConfigError(
                    f"TFTP unexpected opcode {opcode} for {source}")
            # A direct DATA reply means the server ignored our options: fall
            # back to the 512-byte default already in `blksize`.
            negotiated = True
            block = pkt[2:4]
            if int.from_bytes(block, "big") == expected:
                chunk = pkt[4:]
                data.extend(chunk)
                sock.sendto(b"\x00\x04" + block, server)
                expected += 1
                if not warned and len(data) > _TFTP_WARN_BYTES:
                    warned = True
                    print(f"WARNING: TFTP transfer of {source} exceeds "
                          f"{_TFTP_WARN_BYTES // 1024} KiB; TFTP is slow for "
                          "large files — prefer HTTP.")
                if len(chunk) < blksize:
                    break  # short block ends the transfer
            else:  # duplicate; re-ack what we got and wait for the right one
                sock.sendto(b"\x00\x04" + block, server)
        return data.decode("utf-8")
    finally:
        sock.close()


def _fetch_nfs(source):
    host, export_dir, filename, vers = _parse_nfs_url(source)
    mountpoint = _nfs_mount(host, export_dir, vers=vers)
    try:
        with open(os.path.join(mountpoint, filename), encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise schema.ConfigError(f"Cannot read {source}: {exc}")
    finally:
        _nfs_umount(mountpoint)


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
    setup.is_mint = mint_detect.is_mint() if is_mint is None else is_mint

    setup.language = config.locale.split(".")[0]
    setup.timezone = config.timezone
    setup.keyboard_model = config.keyboard.model
    setup.keyboard_layout = config.keyboard.layout
    setup.keyboard_variant = config.keyboard.variant
    setup.hostname = config.hostname or "mint"

    primary = config.users[0]
    setup.username = primary.name
    setup.real_name = primary.gecos or primary.name
    setup.password1 = primary.passwd
    setup.password2 = primary.passwd
    setup.password_is_crypted = True
    setup.autologin = primary.autologin
    setup.ecryptfs = primary.ecryptfs_home

    setup.layout = config.storage.layout
    setup.lvm = config.storage.layout in ("lvm", "lvm-on-luks")
    setup.luks = config.storage.layout == "lvm-on-luks"
    if config.storage.layout == "custom":
        # Hand the engine plain dicts; it does not import the schema.
        setup.custom_partitions = [
            {"size": p.size, "mount": p.mount, "filesystem": p.filesystem,
             "flags": list(p.flags), "lvm_pv": p.lvm_pv,
             "subvolumes": [{"name": s.name, "mount": s.mount}
                            for s in p.subvolumes]}
            for p in config.storage.partitions
        ]
        setup.custom_lvm = [
            {"vg": v.vg, "lv": v.lv, "size": v.size, "mount": v.mount,
             "filesystem": v.filesystem,
             "subvolumes": [{"name": s.name, "mount": s.mount}
                            for s in v.subvolumes]}
            for v in config.storage.lvm
        ]
        # so write_mtab() runs when the custom layout uses LVM
        setup.lvm = bool(setup.custom_lvm)
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
        elif luks.passphrase_source == "prompt-on-first-boot":
            # Format LUKS with a random throwaway key so the install stays
            # unattended; a keyfile holding it is embedded in the initramfs to
            # auto-unlock the first boot, where a oneshot service prompts the
            # operator for the real passphrase and removes the throwaway key.
            # token_urlsafe gives a newline-free ASCII key (matters: the same
            # bytes are written to the keyfile, see _setup_luks_first_boot_rekey).
            setup.passphrase1 = setup.passphrase2 = secrets.token_urlsafe(32)
            setup.luks_rekey_on_first_boot = True
        else:
            raise schema.ConfigError(
                f"passphrase_source: {luks.passphrase_source} is not yet "
                "implemented in the headless driver (use 'keyfile' or "
                "'prompt-on-first-boot')"
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
            self.log(f" --> Creating additional user {user.name}")
            gecos = (user.gecos or user.name).replace('"', "'")
            rc = self.runner.chroot(
                f'adduser --disabled-password --gecos "{gecos}" {user.name}'
            )
            if rc != 0:
                self._policy("post_install_script_failure",
                             f"adduser {user.name} failed")
                continue
            # Write the hash via a file, exactly like the engine does for the
            # primary user: crypt hashes contain '$' and must never pass
            # through a shell.
            with open("/target/dev/shm/.passwd", "w") as fp:
                fp.write(user.name + ":" + user.passwd + "\n")
            self.runner.chroot("cat /dev/shm/.passwd | chpasswd -e")
            self.runner.run("rm -f /target/dev/shm/.passwd")
            for group in user.groups:
                self.runner.chroot(f"adduser {user.name} {group}")

    def _apply_ssh_keys(self):
        for user in self.config.users:
            if not user.ssh_authorized_keys:
                continue
            self.log(f" --> Installing SSH keys for {user.name}")
            ssh_dir = f"/home/{user.name}/.ssh"
            self.runner.chroot(f"mkdir -p {ssh_dir}")
            # written via /target to keep key material out of shell commands
            with open(f"/target{ssh_dir}/authorized_keys", "a") as fp:
                for key in user.ssh_authorized_keys:
                    fp.write(key.rstrip("\n") + "\n")
            self.runner.chroot(f"chmod 700 {ssh_dir}")
            self.runner.chroot(f"chmod 600 {ssh_dir}/authorized_keys")
            self.runner.chroot(
                f"chown -R {user.name}:{user.name} {ssh_dir}"
            )

    @staticmethod
    def _resolv_has_nameserver(path):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            return False
        return bool(re.search(r"(?m)^\s*nameserver\s+\d+\.\d+\.\d+\.\d+", text))

    def _ensure_dns(self, resolv_path="/etc/resolv.conf",
                    lease_glob="/run/net-*.conf"):
        """Give the live session working DNS before any network step.

        On a netboot the NIC is configured by the initramfs, which
        NetworkManager leaves unmanaged — so it never DHCPs and never writes
        DNS, and /etc/resolv.conf keeps the live image's placeholder (observed
        as 'nameserver dhcp'). apt then can't resolve. A no-op on CD/USB boot
        where NetworkManager already populated resolv.conf.
        """
        if self._resolv_has_nameserver(resolv_path):
            return

        # Fast path: the DHCP nameservers the initramfs saved, if its
        # /run/net-*.conf survived the pivot to the live system.
        servers = []
        for conf in sorted(glob.glob(lease_glob)):
            try:
                text = open(conf, encoding="utf-8").read()
            except OSError:
                continue
            for match in re.finditer(r"(?m)^IPV4DNS\d+=(\d+\.\d+\.\d+\.\d+)", text):
                ip = match.group(1)
                if ip != "0.0.0.0" and ip not in servers:
                    servers.append(ip)
        if servers:
            try:
                with open(resolv_path, "w", encoding="utf-8") as fd:
                    fd.writelines(f"nameserver {ip}\n" for ip in servers)
                self.log(" --> Set DNS from netboot lease: " + ", ".join(servers))
                return
            except OSError as exc:
                self.log(f"WARNING: could not write {resolv_path}: {exc}")

        # The lease is usually gone (the initramfs /run does not survive the
        # pivot), so drive NetworkManager to take over the boot NIC and DHCP
        # it, which writes real nameservers into resolv.conf.
        if self._nm_dhcp_boot_nic(resolv_path):
            return
        self.log("WARNING: could not establish DNS in the live session; "
                 "network-dependent install steps may fail")

    def _nm_dhcp_boot_nic(self, resolv_path):
        """Force NetworkManager to manage and DHCP the boot interface, then
        write the DNS it learned into resolv.conf ourselves — NM's rc-manager
        may be configured not to own /etc/resolv.conf, so we cannot rely on it
        updating the file even once it has the nameservers."""
        if shutil.which("nmcli") is None:
            return False
        dev = self.runner.output(
            "ip -o route show default 2>/dev/null | awk '{print $5; exit}'")
        if not dev:
            dev = self.runner.output(
                "for d in /sys/class/net/*; do n=${d##*/}; "
                'case $n in lo) ;; *) echo "$n"; break ;; esac; done')
        if not dev:
            return False
        d = shlex.quote(dev)
        self.log(f" --> Asking NetworkManager to configure {dev} for DNS")
        self.runner.run(f"nmcli device set {d} managed yes")
        # A plain 'connect' makes NM *assume* the initramfs IP config without
        # doing DHCP, so it never learns DNS. Disconnect first to force a fresh
        # DHCP lease on reconnect.
        self.runner.run(f"nmcli device disconnect {d}")
        self.runner.output(f"nmcli -w 30 device connect {d} 2>&1")

        info = ""
        for _ in range(20):
            info = self.runner.output(
                f"nmcli -t -f IP4.DNS,DHCP4.OPTION device show {d} 2>/dev/null")
            servers = []
            for match in re.finditer(
                    r"(?:IP4\.DNS\[\d+\]:|domain_name_servers\s*=\s*)"
                    r"(\d+\.\d+\.\d+\.\d+)", info):
                ip = match.group(1)
                if ip not in servers:
                    servers.append(ip)
            if servers:
                try:
                    with open(resolv_path, "w", encoding="utf-8") as fd:
                        fd.writelines(f"nameserver {ip}\n" for ip in servers)
                    self.log(" --> Set DNS from NetworkManager: "
                             + ", ".join(servers))
                    return True
                except OSError as exc:
                    self.log(f"WARNING: could not write {resolv_path}: {exc}")
                    return False
            if self._resolv_has_nameserver(resolv_path):
                return True  # NM owns resolv.conf after all
            time.sleep(1)
        self.log(f"[nm] no DNS learned; last device show:\n{info}")
        return False

    def _apply_packages(self):
        # Repo config (cloud-init's apt: shape) and the agnostic packages/
        # package_remove lists are applied by a swappable package backend, so
        # the install step is not apt-hardcoded. Only apt exists today.
        backend = pkgbackend.get_backend(self.runner, self._policy, self.log)
        backend.apply(self.config.apt, self.config.packages,
                      self.config.package_remove)

    def _setup_luks_first_boot_rekey(self, setup, target="/target"):
        """Prepare the first-boot LUKS rekey (passphrase_source:
        prompt-on-first-boot): embed the throwaway key in the initramfs so the
        first boot auto-unlocks, and install the oneshot that prompts the
        operator for the real passphrase. Runs BEFORE the initramfs is rebuilt
        (_regenerate_initramfs_if_luks) so the keyfile is baked in."""
        if not getattr(setup, "luks_rekey_on_first_boot", False):
            return
        self.log(" --> Configuring first-boot LUKS passphrase prompt")

        # 1. Write the throwaway key to a root-only keyfile, byte-for-byte the
        #    key LUKS was formatted with (no trailing newline).
        keydir = target + "/etc/cryptsetup-keys.d"
        os.makedirs(keydir, exist_ok=True)
        os.chmod(keydir, 0o700)
        keypath = os.path.join(keydir, "cryptroot.key")
        with open(keypath, "w") as f:
            f.write(setup.passphrase1)   # NO newline — must equal the LUKS key
        os.chmod(keypath, 0o600)

        # 2. Have the cryptsetup initramfs hook copy the keyfile in, and lock
        #    down the initramfs (it holds the throwaway key on /boot until the
        #    first boot completes the rekey).
        hookdir = target + "/etc/cryptsetup-initramfs"
        os.makedirs(hookdir, exist_ok=True)
        with open(hookdir + "/conf-hook", "w") as f:
            f.write('KEYFILE_PATTERN="/etc/cryptsetup-keys.d/*.key"\n')
        os.makedirs(target + "/etc/initramfs-tools", exist_ok=True)
        with open(target + "/etc/initramfs-tools/initramfs.conf", "a") as f:
            f.write("\n# live-installer: protect the embedded first-boot LUKS "
                    "key\nUMASK=0077\n")

        # 3. Install and enable the first-boot rekey oneshot.
        sbindir = target + "/usr/local/sbin"
        os.makedirs(sbindir, exist_ok=True)
        script = sbindir + "/li-luks-rekey"
        with open(script, "w") as f:
            f.write(_LUKS_REKEY_SCRIPT)
        os.chmod(script, 0o755)
        os.makedirs(target + "/etc/systemd/system", exist_ok=True)
        with open(target + "/etc/systemd/system/li-luks-rekey.service", "w") as f:
            f.write(_LUKS_REKEY_SERVICE)
        rc = self.runner.chroot("systemctl enable li-luks-rekey.service")
        if rc != 0:
            self._policy("post_install_script_failure",
                         "enabling the first-boot LUKS rekey service failed")

    def _regenerate_initramfs_if_luks(self):
        """Rebuild the target initramfs so it can unlock the encrypted root.

        The target is copied from the live squashfs, which carries
        live-boot's diverted update-initramfs — a wrapper that no-ops
        unless the live medium is mounted at /run/live/medium *inside the
        chroot*. The engine bind-mounts /run but not that submount, so its
        update-initramfs is skipped and the crypttab never reaches the
        initramfs; the encrypted root then can't be unlocked at boot
        (the system drops to an initramfs shell). Plain and LVM installs
        boot from the stock initramfs and are unaffected, so this only
        matters for lvm-on-luks. Make the medium visible and regenerate.
        """
        if self.config.storage.layout != "lvm-on-luks":
            return
        self.log(" --> Regenerating initramfs for the encrypted root")
        # Remove live-boot's initramfs hook: it references a live-only path
        # (/usr/lib/live/boot) and fails on the installed system, which made
        # update-initramfs exit non-zero. The installed system is not a live
        # system, so the hook has no business in its initramfs.
        self.runner.run(
            "rm -f /target/usr/share/initramfs-tools/hooks/live")
        # Resolve the real update-initramfs (live-tools diverts it to a
        # wrapper that no-ops on live media) and run it directly so the
        # crypttab is baked into the initramfs.
        real = self.runner.output(
            "chroot /target/ /bin/sh -c "
            "'dpkg-divert --truename /usr/sbin/update-initramfs'"
        ).strip() or "/usr/sbin/update-initramfs"
        rc = self.runner.chroot("%s -u -k all" % real)
        if rc != 0:
            self._policy("post_install_script_failure",
                         "regenerating the initramfs for LUKS failed")

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

    def _apply_network(self):
        # Render the netplan-v2-shaped network: section to NetworkManager
        # keyfiles in the target. NM is what the installed Mint/LMDE system
        # uses; we write the keyfiles directly rather than depending on the
        # netplan binary (which LMDE/Debian does not ship). With no network:
        # section the system keeps its default (NM-managed DHCP).
        network = self.config.network
        if network is None:
            return
        files = netconfig.render(network)
        if not files:
            return
        conn_dir = "/target/etc/NetworkManager/system-connections"
        self.runner.run("mkdir -p %s" % conn_dir)
        for name, content in files.items():
            path = os.path.join(conn_dir, name)
            self.log(" --> Writing NetworkManager connection: %s" % name)
            with open(path, "w") as f:
                f.write(content)
            # Keyfiles may hold secrets and NM refuses world-readable ones.
            os.chmod(path, 0o600)

    def _run_commands(self):
        # late_commands: a list of shell commands run in the target chroot at
        # the end of the install, like Ubuntu autoinstall's late-commands and
        # kickstart %post. A command that is a path to a file present on the
        # install media is copied into the target and run, so on-media scripts
        # work.
        for command in self.config.late_commands:
            self.log(f" --> late_command: {command}")
            first = command.split()[0] if command.split() else ""
            if first and os.path.isfile(first):
                self.runner.run(f"cp {shlex.quote(first)} /target/tmp/")
                target_path = "/tmp/" + os.path.basename(first)
                rest = command[len(first):]
                rc = self.runner.chroot(f"sh {target_path}{rest}")
                self.runner.run(f"rm -f /target{target_path}")
            else:
                rc = self.runner.chroot(command)
            if rc != 0:
                self._policy("post_install_script_failure",
                             f"late_command failed (rc={rc}): {command}")

    # -- main flow ----------------------------------------------------------

    def run(self, setup=None):
        """Run the full unattended installation.  Returns an exit code."""
        import installer

        try:
            if setup is None:
                setup = build_setup(self.config, insecure=self.insecure)
            self.log(f" --> Target disk: {setup.disk}")
            setup.print_setup()

            # Before the engine copies resolv.conf into the target, make sure
            # the live session can actually resolve names (netboot leaves a
            # placeholder resolv.conf; see _ensure_dns).
            self._ensure_dns()

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
                or self.config.packages or self.config.package_remove
                or self.config.apt is not None or self.config.late_commands
                or self.config.kernel.cmdline_extra.strip()
                or self.config.kernel.serial_console.strip()
                or self.config.storage.layout == "lvm-on-luks"
                or self.config.network is not None
            )

            def post_install_hook():
                self.log(" --> Applying post-install configuration")
                self._create_extra_users()
                self._apply_ssh_keys()
                self._apply_packages()
                self._apply_network()
                self._setup_luks_first_boot_rekey(setup)
                self._regenerate_initramfs_if_luks()
                self._apply_kernel_config()
                self._run_commands()

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


def format_disk_list(disks):
    """Render diskmatch.describe_disks() output as copy-pasteable text, so a
    user can read the stable attributes off a live machine and build a match
    expression for the answer file."""
    if not disks:
        return "No installable disks found."
    def human_size(n):
        if n >= 10**12:
            return f"{n / 10**12:.1f}TB"
        if n >= 10**9:
            return f"{n / 10**9:.0f}GB"
        return f"{n / 10**6:.0f}MB"

    lines = []
    for d in disks:
        removable = "yes" if d["removable"] else "no"
        lines.append(
            f"{d['path']}  {human_size(d['size_bytes'])}  "
            f"model={d['model']!r}  removable={removable}"
        )
        # by-path is stable per hardware slot (reusable across identical
        # machines); by-id is unique to this physical drive.
        if d["by_path"]:
            lines.append("  by-path (stable per chassis slot, fleet-reusable):")
            lines.extend(f"    {name}" for name in d["by_path"])
        if d["by_id"]:
            lines.append("  by-id (unique to this physical drive):")
            lines.extend(f"    {name}" for name in d["by_id"])
        if not d["by_path"] and not d["by_id"]:
            lines.append("  (no by-id/by-path links; use model or size-min)")
        lines.append("")
    lines.append(
        "Put one of these under storage.target.match in the answer file, "
        "e.g.:\n"
        "  match:\n"
        '    by-id: "<paste a by-id name, globbing the serial with *>"\n'
        "Run with --check to validate the file once written."
    )
    return "\n".join(lines)


def acquire_answer_text(source, insecure, log=lambda _m: None):
    """Resolve an answer-file source to its text.

    Handles the netboot discovery trigger ('auto' or 'auto:<base>'): the
    machine's identity (MAC/serial/UUID) is read and a list of candidate
    sources is tried in order until one fetches and validates. A plain
    path/URL is fetched directly.
    """
    is_auto, base = discovery.parse_auto_trigger(source)
    if not is_auto:
        return fetch_answer_file(source, insecure)
    identity = discovery.read_machine_identity(log=log)
    candidates = discovery.candidate_sources(
        base, macs=identity["macs"], serial=identity["serial"],
        uuid=identity["uuid"])
    try:
        _resolved, text = discovery.discover(
            candidates,
            fetch=lambda s: fetch_answer_file(s, insecure),
            validate=schema.parse_config,
            log=log,
        )
    except discovery.DiscoveryError as exc:
        raise schema.ConfigError(str(exc))
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="live-installer --automated",
        description="Unattended installation from a YAML answer file",
    )
    parser.add_argument(
        "--config",
        help="answer file path or URL (file, http(s)://, nfs://, tftp://), or "
             "'auto' / 'auto:<base-url>' to discover it from this machine's "
             "MAC/serial/UUID. Default: live-installer.auto= from the kernel "
             "command line.",
    )
    parser.add_argument(
        "--insecure", action="store_true",
        help="allow fetching the answer file over cleartext transports "
             "(plain HTTP, NFS, TFTP)",
    )
    parser.add_argument(
        "--list-disks", action="store_true",
        help="print this machine's disks with the stable attributes "
             "(by-id, by-path, model, size) usable in a match expression, "
             "then exit. Boot the live medium and run this to learn the "
             "names to put in an answer file.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="validate the answer file's syntax and schema, then exit. "
             "Touches no disks, so it runs anywhere (CI, a dev laptop).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate the answer file and resolve the target disk, "
             "then exit without installing",
    )
    args = parser.parse_args(argv)

    if args.list_disks:
        print(format_disk_list(diskmatch.describe_disks()), flush=True)
        return 0

    source = args.config or cmdline_source()
    if not source:
        parser.error(
            f"no answer file: pass --config or boot with {CMDLINE_KEY}<source>"
        )
    insecure = args.insecure or cmdline_insecure()

    try:
        text = acquire_answer_text(
            source, insecure, log=lambda m: print(m, flush=True))
        config = schema.parse_config(text)
    except schema.ConfigError as exc:
        print(f"ERROR: {exc}", flush=True)
        print(FAILURE_MARKER, flush=True)
        return 1

    if args.check:
        print("Answer file OK", flush=True)
        return 0

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
