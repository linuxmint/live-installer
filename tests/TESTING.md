# Testing & maintaining live-installer

This document is for maintainers. It explains how the automated-install
code is structured, how the test suite works, how to run and extend it,
and the non-obvious traps a change is likely to hit. You do **not** need
PXE, automation, or VM expertise to use it — the harness hides all of
that behind one command.

- [Architecture](#architecture-of-the-automated-install-path)
- [Test layers](#test-layers)
- [Running the tests](#running-the-tests)
- [Adding an integration scenario](#adding-an-integration-scenario)
- [Debugging a failing scenario](#debugging-a-failing-scenario)
- [Known traps](#known-traps)
- [CI](#ci)

## Architecture of the automated-install path

The GUI and the unattended driver are two front-ends over the **same**
install engine. The engine was already GUI-agnostic — it talks to its
caller through two callbacks (`set_progress_hook`, `set_error_hook`) and
never imports GTK — so the unattended path is mostly new code beside the
existing engine, not a rewrite of it.

```
                         InstallerEngine  (installer.py)
                         ── the actual install, GUI-agnostic ──
                          ▲                          ▲
          progress/error  │                          │  progress/error
          hooks (GTK)     │                          │  hooks (console/log/serial)
                          │                          │
   InstallerWindow ───────┘                          └─────── HeadlessDriver
   (main.py, the GUI)                                          (auto_installer.py)
```

New modules (all under `usr/lib/live-installer/`):

| Module | Responsibility |
|---|---|
| `schema.py` | Parse + strictly validate the YAML answer file (pydantic). All input rules live here. |
| `diskmatch.py` | Resolve a `storage.target.match` expression to exactly one `/dev/...` path, or fail. No `/dev/sdX`. |
| `auto_installer.py` | The headless driver: fetch the answer file, build the engine's `Setup`, run the install, do post-install steps (extra users, SSH keys, packages, kernel cmdline, scripts). |
| `commandrunner.py` | The single point every shell-out goes through. Logs commands, checks exit codes, redacts secrets, feeds stdin. |

The engine change that made the driver possible: every `os.system()` /
`subprocess.getoutput()` in `installer.py` now goes through a
`CommandRunner` held on the engine (`self.runner`), injectable via the
constructor. The GUI passes nothing and gets the real runner; tests pass
a recording fake.

Entry point (`main.py`): if `live-installer.auto=` is on the kernel
cmdline (or `--automated=`), `main.py` hands off to
`auto_installer.main()` before any GTK setup. Otherwise the GUI starts
as before.

The system unit `live-installer-auto.service` launches the installer in
the live session when the cmdline trigger is present (and prints a
failure marker if it dies before doing so).

## Test layers

| Layer | Where | Speed | Needs |
|---|---|---|---|
| **Unit** | `tests/unit/` | ~10 s | Python + pytest only |
| **Integration** | `tests/integration/` | ~10–15 min/scenario | KVM, QEMU, OVMF, swtpm, an ISO |

The unit tests import the real installer modules with `gi`, `parted` and
`dialogs` replaced by stubs (`tests/unit/conftest.py`), so they run
anywhere with no GTK and no root. They cover the schema, disk matching,
the command runner (including secret redaction), and the driver's
config-to-`Setup` mapping and failure-policy logic.

The integration tests perform **real installs** in a VM: boot the live
ISO, drive it with an answer file served over HTTP, wait for the
completion marker on the serial console, reboot into the installed disk,
and assert on the result over SSH.

## Running the tests

### Unit

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements-test.txt
.venv/bin/pytest tests/unit -v
```

### Integration (one-time setup)

Install the tooling (Debian/Ubuntu shown; EL uses `qemu-kvm`,
`edk2-ovmf`):

```sh
sudo apt-get install -y qemu-system-x86 qemu-utils ovmf swtpm \
                        swtpm-tools squashfs-tools xorriso
pip install pyyaml
```

Fetch the pinned ISO once:

```sh
cd tests/integration/fixtures
curl -sLO https://mirrors.kernel.org/linuxmint/debian/sha256sum.txt
curl -sLO https://mirrors.kernel.org/linuxmint/debian/lmde-7-cinnamon-64bit.iso
sha256sum -c <(grep lmde-7-cinnamon-64bit.iso sha256sum.txt)
```

### Integration (per run)

The harness can't modify the read-only main squashfs, so it builds a
**dev ISO**: an overlay squashfs containing the working-tree installer is
added to the ISO (live-boot union-mounts it over the stock files).
**Rebuild it whenever you change installer code:**

```sh
python tests/integration/harness/make_test_iso.py
```

Then run a scenario:

```sh
python tests/integration/harness/run_scenario.py \
    tests/integration/scenarios/bios-simple.yaml
```

`--smoke` boots the stock ISO and just checks the VM stays up (no
install) — a fast sanity check that QEMU/KVM/firmware work. `--keep`
preserves the work directory (disk image, serial log) for inspection.
`--junit out.xml` writes JUnit results.

## Adding an integration scenario

A scenario is two files under `tests/integration/scenarios/`:

1. `answers/<name>.yaml` — the answer file under test (a real example, per
   the schema). It's served to the VM; the harness injects an SSH key
   into the first user automatically so it can log in to verify.
2. `<name>.yaml` — the scenario: firmware, disk topology, timeouts, and
   the `verify` assertions.

Minimal scenario:

```yaml
name: my-case
firmware: bios            # bios | uefi | uefi-secureboot
disk_gb: 25
answer_file: answers/my-case.yaml
install_timeout_s: 1800
boot_timeout_s: 300
expect:
  serial_markers: ["Automated installation complete"]
  failure_markers: ["Automated installation FAILED"]
ssh:
  user: testuser
verify:
  - name: hostname-applied
    type: command
    command: hostname
    expect_substring: "my-host"
```

Verify assertion types: `command` (run over SSH; check `expect_rc` and/or
`expect_substring`), `file_exists`, `mount_source` (the device backing a
mountpoint contains a substring — e.g. `lvmmint-root`).

Useful scenario knobs:

- `disks: [{size_gb, serial}, ...]` — multiple disks, each with a serial
  that surfaces as `/dev/disk/by-id/virtio-<serial>` (for `by-id` tests).
- `boot_unlock: {prompt, passphrase_file, timeout_s}` — type a LUKS
  passphrase over serial at the initramfs prompt during boot-verify.
- `expect.outcome: failure` — a negative test: the failure marker is the
  expected result and there is no boot/verify phase.

Add the scenario name to the matrix in
`.github/workflows/integration-tests.yml` so CI runs it.

## Debugging a failing scenario

Run with `--keep` and look in `tests/integration/.work/<name>/`:

- `<name>-serial.log` — the full guest serial console. **Start here.**
  Every command the installer runs is logged (`EXEC: ...`), so the last
  `EXEC` line before a failure is usually the culprit. The log contains
  terminal escape sequences; strip them with
  `sed 's/\x1b\[[0-9;]*m//g'`.
- `<name>-serial.install.log` — for full-boot scenarios, the serial log
  of the **install** phase (phase 2 truncates the live `-serial.log`).
  Look here for what the installer did; look in `-serial.log` for the
  installed system's boot.
- `<name>.qcow2` — the installed disk; boot it by hand with `qemu-system-x86_64`
  if you need to poke around (`--keep` preserves it).

The driver also logs the resulting `grub.cfg` kernel line after it
regenerates grub, so the booted cmdline is visible in the install log
without re-running — invaluable for serial-console / boot debugging.

The installer tolerates a failed command unless `check=True`, so a broken
install can still "finish" — the `EXEC failed (rc=...)` lines in the log
are the signal, not just the final marker.

## Known traps

These have each caused a real failure; keep them in mind when changing
the relevant code.

- **udev races after partitioning.** Each `parted` call triggers a
  partition-table rescan; udev briefly removes and recreates the device
  nodes. `mkfs`/`mount` must wait (`udevadm settle`) or they race a
  missing node. A tolerated `mkfs` failure is worse than a loud one — it
  cascades into the file copy landing in tmpfs until it fills.
- **`finish_installation()` unmounts the target.** Anything that needs
  the chroot mounted with network (extra users, packages) must run via
  the `before_unmount_hook`, not after `finish_installation()` returns.
- **Offline EFI bootloader install leaves dpkg unsatisfied.** The engine
  dpkg-installs shim/grub-efi from the ISO pool without their dependency
  closure; the driver runs `apt-get install -f` before any other package
  op to repair it.
- **Secrets in commands.** Never build a shell command with a secret in
  it. Feed it on stdin via `runner.run(..., stdin=secret)`; if it truly
  must be in the command, pass `secrets=[...]` so it is redacted from
  logs. Serial logs are routinely captured off real hardware.
- **The dev ISO is stale until rebuilt.** Changing installer code without
  re-running `make_test_iso.py` tests the *old* code. The integration CI
  always rebuilds, but local runs don't.
- **The answer-file fixtures are also the docs' examples.** A unit test
  parses them against the schema so the two can't drift; if you change
  the schema, update the fixtures.
- **Serial console & LUKS boot are a minefield of live-system quirks.**
  Editing `/etc/default/grub` is silently overridden by distro `grub.d`
  snippets; `update-initramfs` is a diverted no-op in the live session;
  a live-boot initramfs hook fails on the installed system. Each is
  invisible until you watch the installed system boot on serial. The full
  map — what each piece does and which approaches were tried and rejected
  — is in **[docs/serial-console-and-luks.md](../docs/serial-console-and-luks.md)**.
  Read it before touching `_build_grub_snippet` or
  `_regenerate_initramfs_if_luks` in `auto_installer.py`.

## Mint vs LMDE coverage

The engine reads the live filesystem and the grub-title script from
different places depending on edition (`Setup.is_mint`):

| | Mint (Ubuntu/casper) | LMDE (Debian/live-boot) |
|---|---|---|
| squashfs / kernel | `/cdrom/casper` | `/run/live/medium/live` |
| grub-title script | `ubuntu-system-adjustments` | `debian-system-adjustments` |

**Integration tests currently cover the LMDE branch only**, because LMDE
is the edition that ships `live-installer` today. Mint does not adopt it
until Mint 23. The `is_mint=True` path-selection is covered by unit tests
(`TestEditionPaths`) so a wrong path can't regress silently, but it is
not yet exercised end-to-end.

To add Mint-side integration coverage:

- A Mint ISO's live session is **casper**-based, not Debian live-boot, so
  `isotools.build_dev_iso` (which adds an overlay squashfs under `/live`
  for live-boot to union-mount) needs a casper variant — casper layers
  squashfs differently and the installer autostart differs.
- Until a Mint 23 ISO exists, the Mint 22.x beta can stand in as a
  *casper environment* proxy (it exercises the same `is_mint=True` paths)
  even though Ubiquity, not live-installer, is its native installer.
- This is best validated against the **Mint 23 beta** when it lands; its
  live environment is the real target and may differ from 22.x.

## CI

Two GitHub Actions workflows (`.github/workflows/`):

- **unit-tests.yml** — `pytest tests/unit` on Python 3.11–3.13, on every
  push and pull request. Fast, no special hardware.
- **integration-tests.yml** — the real VM installs. KVM-dependent and
  slow, so it runs nightly and on manual dispatch (Actions → integration
  tests → Run workflow), not per-push. A fast failure-mode gate runs
  first; the install scenarios then run as a parallel matrix. Serial logs
  and JUnit results are uploaded as artifacts (always for the failure
  gate, on failure for installs) so a red run is debuggable without
  reproducing locally. `workflow_dispatch` accepts a space-separated
  `scenarios` input to run a subset.

The shared setup (enable KVM, install QEMU/OVMF/swtpm, fetch + cache the
ISO, build the dev ISO) lives in the composite action
`.github/actions/vm-setup`.
