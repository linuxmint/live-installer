"""QEMU/KVM virtual machine management for live-installer integration tests.

Stdlib-only.  Supports BIOS, UEFI (OVMF) and UEFI+SecureBoot firmware,
an emulated TPM2 via swtpm, serial-console capture to a log file, and
user-mode networking with an SSH host-forward.  Firmware and binary
paths cover both EL (AlmaLinux/Fedora) and Debian/Ubuntu layouts so the
harness runs on developer machines and CI runners alike.
"""

import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

_EL_OVMF = Path("/usr/share/edk2/ovmf")
_DEB_OVMF = Path("/usr/share/OVMF")

# (code, vars) candidate pairs, first existing pair wins
_FIRMWARE_PATHS = {
    "uefi": [
        (_EL_OVMF / "OVMF_CODE.fd", _EL_OVMF / "OVMF_VARS.fd"),
        (_DEB_OVMF / "OVMF_CODE_4M.fd", _DEB_OVMF / "OVMF_VARS_4M.fd"),
        (_DEB_OVMF / "OVMF_CODE.fd", _DEB_OVMF / "OVMF_VARS.fd"),
    ],
    "uefi-secureboot": [
        (_EL_OVMF / "OVMF_CODE.secboot.fd", _EL_OVMF / "OVMF_VARS.secboot.fd"),
        (_DEB_OVMF / "OVMF_CODE_4M.ms.fd", _DEB_OVMF / "OVMF_VARS_4M.ms.fd"),
    ],
}


class VMError(Exception):
    pass


def find_qemu():
    """Locate the system emulator binary."""
    candidates = [os.environ.get("QEMU_BIN")]
    candidates.append(shutil.which("qemu-system-x86_64"))
    candidates.append("/usr/libexec/qemu-kvm")  # EL packaging
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise VMError(
        "No QEMU binary found. Install qemu-system-x86 (Debian/Ubuntu) or "
        "qemu-kvm (EL), or set QEMU_BIN."
    )


def find_firmware(firmware):
    """Return (code, vars) firmware image paths for 'uefi'/'uefi-secureboot'."""
    for code, vars_template in _FIRMWARE_PATHS[firmware]:
        if code.exists() and vars_template.exists():
            return code, vars_template
    raise VMError(
        f"No OVMF firmware found for {firmware!r}. Install edk2-ovmf (EL) "
        "or ovmf (Debian/Ubuntu)."
    )


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def kvm_available():
    return os.access("/dev/kvm", os.R_OK | os.W_OK)


class VM:
    """A single QEMU machine bound to a working directory."""

    def __init__(self, workdir, name="vm", memory_mb=4096, cpus=2):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.disk = self.workdir / f"{name}.qcow2"
        self.serial_log = self.workdir / f"{name}-serial.log"
        self.process = None
        self._swtpm = None
        self._serial_sock = None
        self._serial_reader = None
        self._serial_stop = None
        self._serial_dir = None
        self._disks = []  # list of (path, serial)
        # Extra NICs (besides the SSH/boot NIC), each pinned to a fixed MAC so
        # the answer file can bind a static/VLAN config to it by macaddress.
        # Each gets its own isolated user-net (no hostfwd), so it never
        # competes with the primary NIC's port-forwarded SSH.
        self.extra_nics = []  # list of MAC strings

    # -- setup ------------------------------------------------------------

    def create_disk(self, size_gb, serial=None):
        """Create the primary target disk. An optional serial surfaces in
        the guest as /dev/disk/by-id/virtio-<serial>, for by-id matching."""
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(self.disk), f"{size_gb}G"],
            check=True,
            capture_output=True,
        )
        self._disks = [(self.disk, serial)]

    def add_disk(self, size_gb, serial):
        """Attach an additional disk (multi-disk by-id scenarios)."""
        path = self.workdir / f"{self.name}-disk{len(self._disks)}.qcow2"
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(path), f"{size_gb}G"],
            check=True,
            capture_output=True,
        )
        self._disks.append((path, serial))
        return path

    def _start_swtpm(self):
        tpm_dir = self.workdir / "tpm"
        tpm_dir.mkdir(exist_ok=True)
        self._tpm_sock = tpm_dir / "swtpm.sock"
        self._swtpm = subprocess.Popen(
            [
                "swtpm", "socket", "--tpm2",
                "--tpmstate", f"dir={tpm_dir}",
                "--ctrl", f"type=unixio,path={self._tpm_sock}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            if self._tpm_sock.exists():
                return
            time.sleep(0.1)
        raise VMError("swtpm socket did not appear")

    # -- lifecycle --------------------------------------------------------

    def start(
        self,
        *,
        iso=None,
        boot="cdrom",  # cdrom | disk
        firmware="bios",  # bios | uefi | uefi-secureboot
        tpm=False,
        ssh_port=None,
        kernel=None,
        initrd=None,
        append=None,
        boot_serial=None,
        tftp_dir=None,
        bootfile=None,
        ipv6=False,
    ):
        if self.process is not None:
            raise VMError("VM already running")
        self.serial_log.unlink(missing_ok=True)

        # Serial goes to a unix socket QEMU listens on; a reader thread tees
        # everything to serial_log (so wait_serial still works on the file)
        # and the same socket carries keystrokes back via send_serial — the
        # channel a real admin drives over IPMI Serial-over-LAN.
        #
        # The socket lives in a short tempdir, NOT the workdir: AF_UNIX paths
        # are capped at ~108 bytes and a deep workdir (e.g. on a CI runner)
        # blows past it, so QEMU silently fails to create the socket.
        self._serial_dir = tempfile.mkdtemp(prefix="li-ser-")
        serial_path = Path(self._serial_dir) / "s"
        cmd = [
            find_qemu(),
            "-name", self.name,
            "-machine", "q35" + (",smm=on" if firmware == "uefi-secureboot" else ""),
            "-m", str(self.memory_mb),
            "-smp", str(self.cpus),
            "-display", "none",
            "-chardev", f"socket,id=ser0,path={serial_path},server=on,wait=off",
            "-serial", "chardev:ser0",
            "-monitor", "none",
        ]
        self._serial_path = serial_path
        if kvm_available():
            cmd += ["-accel", "kvm", "-cpu", "host"]
        else:  # slow, but lets the harness run where nesting is unavailable
            cmd += ["-accel", "tcg", "-cpu", "max"]

        if firmware in ("uefi", "uefi-secureboot"):
            code, vars_template = find_firmware(firmware)
            vars_copy = self.workdir / f"{self.name}-VARS.fd"
            if not vars_copy.exists():
                shutil.copyfile(vars_template, vars_copy)
            cmd += [
                "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
                "-drive", f"if=pflash,format=raw,file={vars_copy}",
            ]
            if firmware == "uefi-secureboot":
                cmd += ["-global", "driver=cfi.pflash01,property=secure,value=on"]

        # Attach disks via explicit blockdev+device so each can carry a
        # serial (-> /dev/disk/by-id/virtio-<serial> in the guest). Fall
        # back to the legacy single-drive form when create_disk was never
        # called but the qcow2 exists (boot-from-disk phase 2).
        disks = self._disks
        if not disks and self.disk.exists():
            disks = [(self.disk, None)]
        for index, (path, serial) in enumerate(disks):
            if not Path(path).exists():
                continue
            node = f"disk{index}"
            cmd += ["-blockdev",
                    f"driver=qcow2,node-name={node},"
                    f"file.driver=file,file.filename={path}"]
            dev = f"virtio-blk-pci,drive={node}"
            if serial:
                dev += f",serial={serial}"
            # When booting from disk in a multi-disk VM, the firmware must
            # boot the disk the OS was installed to — not whichever disk
            # enumerates first. Pin it with bootindex.
            if boot == "disk" and boot_serial is not None and serial == boot_serial:
                dev += ",bootindex=0"
            cmd += ["-device", dev]
        if iso:
            cmd += ["-cdrom", str(iso)]
        cmd += ["-boot", {"cdrom": "d", "disk": "c", "net": "n"}[boot]]

        # QEMU's user-mode network has a built-in TFTP/BOOTP server and the
        # NIC carries an iPXE option ROM, so a full PXE boot needs no
        # privileged host networking: the ROM DHCPs, TFTPs `bootfile`, and
        # (for a #!ipxe script) runs it. The squashfs and answer file are
        # then pulled over HTTP from the harness server at 10.0.2.2.
        netdev = "user,id=net0"
        if ssh_port:
            netdev += f",hostfwd=tcp:127.0.0.1:{ssh_port}-:22"
        if ipv6:
            # Give the guest an IPv6 ULA (host at fd00::2) alongside IPv4, so a
            # scenario can fetch the rootfs and answer file over IPv6. (PXE
            # firmware boot stays IPv4: slirp has no DHCPv6 boot-URL option.)
            netdev += ",ipv6=on,ipv6-net=fd00::/64"
        if tftp_dir:
            netdev += f",tftp={tftp_dir},bootfile={bootfile}"
        netcard = "virtio-net-pci,netdev=net0"
        # On UEFI, OVMF honours bootindex rather than the legacy -boot order,
        # so the NIC must carry one to be tried for PXE. (BIOS PXE already works
        # via -boot n and the NIC option ROM, so leave it alone.)
        if boot == "net" and firmware in ("uefi", "uefi-secureboot"):
            netcard += ",bootindex=0"
        cmd += ["-netdev", netdev, "-device", netcard]

        # Additional NICs with fixed MACs on isolated user-nets. They carry no
        # boot/SSH role — they exist so the installed system has a stable
        # hardware address to bind a static/VLAN connection to.
        for index, mac in enumerate(self.extra_nics, start=1):
            nid = f"net{index}"
            cmd += ["-netdev", f"user,id={nid}",
                    "-device", f"virtio-net-pci,netdev={nid},mac={mac}"]

        if tpm:
            self._start_swtpm()
            cmd += [
                "-chardev", f"socket,id=chrtpm,path={self._tpm_sock}",
                "-tpmdev", "emulator,id=tpm0,chardev=chrtpm",
                "-device", "tpm-tis,tpmdev=tpm0",
            ]

        if kernel:
            cmd += ["-kernel", str(kernel)]
            if initrd:
                cmd += ["-initrd", str(initrd)]
            if append:
                cmd += ["-append", append]

        # QEMU stderr goes to a file, not an unread PIPE: a chatty backend
        # (e.g. slirp warnings) can fill a 64K pipe and block QEMU's write,
        # freezing the guest — alive but silent — until the harness times out.
        self._stderr_path = self.workdir / f"{self.name}-qemu-stderr.log"
        self._stderr_file = open(self._stderr_path, "wb")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_file,
        )
        self._start_serial_reader()
        return self

    def qemu_stderr(self):
        try:
            self._stderr_file.flush()
            return self._stderr_path.read_text(errors="replace")
        except (OSError, AttributeError):
            return ""

    def _start_serial_reader(self):
        # Connect to QEMU's serial socket (created at launch) and tee
        # everything received to serial_log in a background thread.
        sock = None
        for _ in range(100):
            # If QEMU died at launch the socket will never appear; surface
            # its stderr instead of a misleading "socket" error.
            if self.process.poll() is not None:
                raise VMError(
                    f"QEMU exited at launch (rc={self.process.returncode}).\n"
                    f"stderr: {self.qemu_stderr()[-2000:]}"
                )
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(str(self._serial_path))
                break
            except OSError:
                sock.close()
                sock = None
                time.sleep(0.1)
        if sock is None:
            raise VMError(
                f"could not connect to QEMU serial socket {self._serial_path} "
                "(QEMU is running but never created it)"
            )
        self._serial_sock = sock
        self._serial_stop = threading.Event()

        def reader():
            sock.settimeout(0.5)
            with open(self.serial_log, "ab", buffering=0) as log:
                while not self._serial_stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    log.write(chunk)

        self._serial_reader = threading.Thread(target=reader, daemon=True)
        self._serial_reader.start()

    def send_serial(self, text):
        """Type `text` into the guest's serial console (e.g. a LUKS
        passphrase at the initramfs unlock prompt). Include the trailing
        newline yourself."""
        if self._serial_sock is None:
            raise VMError("serial socket not connected")
        self._serial_sock.sendall(text.encode("utf-8"))

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def wait_serial(self, patterns, timeout_s, poll_interval=0.5):
        """Block until any regex in `patterns` appears on the serial console.

        Returns the matched pattern.  Raises TimeoutError (with the tail of
        the serial log) on timeout, VMError if the VM exits prematurely.
        """
        compiled = [re.compile(p) for p in patterns]
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            text = ""
            if self.serial_log.exists():
                text = self.serial_log.read_text(errors="replace")
            for pattern in compiled:
                if pattern.search(text):
                    return pattern.pattern
            if not self.alive():
                raise VMError(
                    f"VM exited (rc={self.process.returncode}) while waiting "
                    f"for serial output.\nstderr: {self.qemu_stderr()[-2000:]}\n"
                    f"serial tail: {text[-2000:]}"
                )
            time.sleep(poll_interval)
        tail = ""
        if self.serial_log.exists():
            tail = self.serial_log.read_text(errors="replace")[-2000:]
        raise TimeoutError(
            f"Timed out after {timeout_s}s waiting for {patterns} on serial "
            f"console.\nserial tail: {tail}\n"
            f"qemu stderr: {self.qemu_stderr()[-2000:]}"
        )

    def stop(self, grace_s=10):
        if self._serial_stop is not None:
            self._serial_stop.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(grace_s)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self._serial_reader is not None:
            self._serial_reader.join(timeout=2)
            self._serial_reader = None
        if self._serial_sock is not None:
            self._serial_sock.close()
            self._serial_sock = None
        if self._serial_dir is not None:
            shutil.rmtree(self._serial_dir, ignore_errors=True)
            self._serial_dir = None
        if self._swtpm is not None:
            self._swtpm.terminate()
            self._swtpm = None
