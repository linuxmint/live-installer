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

    # -- setup ------------------------------------------------------------

    def create_disk(self, size_gb):
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(self.disk), f"{size_gb}G"],
            check=True,
            capture_output=True,
        )

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
    ):
        if self.process is not None:
            raise VMError("VM already running")
        self.serial_log.unlink(missing_ok=True)

        cmd = [
            find_qemu(),
            "-name", self.name,
            "-machine", "q35" + (",smm=on" if firmware == "uefi-secureboot" else ""),
            "-m", str(self.memory_mb),
            "-smp", str(self.cpus),
            "-display", "none",
            "-serial", f"file:{self.serial_log}",
            "-monitor", "none",
        ]
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

        if self.disk.exists():
            cmd += ["-drive", f"file={self.disk},if=virtio,format=qcow2"]
        if iso:
            cmd += ["-cdrom", str(iso)]
        cmd += ["-boot", {"cdrom": "d", "disk": "c"}[boot]]

        netdev = "user,id=net0"
        if ssh_port:
            netdev += f",hostfwd=tcp:127.0.0.1:{ssh_port}-:22"
        cmd += ["-netdev", netdev, "-device", "virtio-net-pci,netdev=net0"]

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

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return self

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
                stderr = self.process.stderr.read().decode(errors="replace")
                raise VMError(
                    f"VM exited (rc={self.process.returncode}) while waiting "
                    f"for serial output.\nstderr: {stderr[-2000:]}\n"
                    f"serial tail: {text[-2000:]}"
                )
            time.sleep(poll_interval)
        tail = ""
        if self.serial_log.exists():
            tail = self.serial_log.read_text(errors="replace")[-2000:]
        raise TimeoutError(
            f"Timed out after {timeout_s}s waiting for {patterns} on serial "
            f"console.\nserial tail: {tail}"
        )

    def stop(self, grace_s=10):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(grace_s)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self._swtpm is not None:
            self._swtpm.terminate()
            self._swtpm = None
