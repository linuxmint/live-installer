# Integration test harness

Boots the pinned LMDE ISO in QEMU/KVM, drives an unattended install via
an answer file served over HTTP, reboots into the installed disk, and
asserts on the result over SSH. Results are emitted as JUnit XML.

## Requirements

- KVM available (`/dev/kvm`) — falls back to slow TCG emulation without it
- QEMU: `dnf install qemu-kvm` (EL) or `apt install qemu-system-x86` (Debian/Ubuntu)
- UEFI firmware: `dnf install edk2-ovmf` / `apt install ovmf`
- TPM scenarios: `dnf install swtpm swtpm-tools` / `apt install swtpm`
- Python: `pip install pyyaml`

## Fixtures

The pinned ISO lives in `fixtures/` (not committed — ~3 GB):

```sh
cd fixtures
curl -sLO https://mirrors.kernel.org/linuxmint/debian/sha256sum.txt
curl -sLO https://mirrors.kernel.org/linuxmint/debian/lmde-7-cinnamon-64bit.iso
sha256sum -c <(grep lmde-7-cinnamon-64bit.iso sha256sum.txt)
```

LMDE is the edition under test because it ships `live-installer` today;
Mint 22.x still ships Ubiquity. When testing changes to the installer the
working tree must be injected into the live session (squashfs remaster —
tooling for that lands together with the headless driver).

## Running

```sh
# Smoke mode: boot the ISO, confirm the VM stays up, tear down.
# This is the only mode that passes until the headless driver exists.
harness/run_scenario.py scenarios/bios-simple.yaml --smoke

# Full mode (install + verify) — requires the headless driver:
harness/run_scenario.py scenarios/bios-simple.yaml --junit results.xml
```

## Scenario format

See `scenarios/bios-simple.yaml`. Key fields: `firmware`
(bios | uefi | uefi-secureboot), `tpm`, `disk_gb`, `answer_file`
(served over HTTP; the guest reaches the host at 10.0.2.2),
`expect.serial_markers`, and `verify` (SSH assertions:
`command` / `file_exists` / `mount_source`).

## Design rules

- Always verify by rebooting into the installed disk — never by
  inspecting /target from the live session.
- Fresh disk image per scenario; no snapshot reuse.
- Assertions check outcomes (file exists, service active), never just
  exit codes.
- Every wait has a timeout; every assertion that can race has a retry.
