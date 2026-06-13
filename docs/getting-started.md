# Trying out unattended installation

This is a hands-on walkthrough for developers and beta testers who want to
try the automated installer. For the full key-by-key reference, see
[automated-install.md](automated-install.md).

## Status, read this first

This feature is new and still being stabilized. Be aware:

- It has been developed and tested end to end on **LMDE** (Debian-based).
  It has **not** been tested on Linux Mint itself yet, because the Mint 23
  base it targets has not had a beta to test against. Treat Mint as
  unverified until that lands.
- The answer-file schema may still change before it is final. Pin to a
  known build and re-validate your files with `--check` after updating.
- **It erases a disk without asking.** Only run a real install against a
  VM or a machine whose disk you are willing to lose.

The safest way to try it is in a virtual machine. The walkthrough below
works the same on a VM or on spare physical hardware.

## What you need

- A live ISO built from this branch (the unattended driver and its
  dependencies are included in the image).
- A target to install onto: a VM with a blank virtual disk, or a spare
  physical machine.

## Step 1: boot the live medium

Boot the live ISO normally. You do **not** need to start the graphical
installer. Open a terminal in the live session.

The unattended driver is invoked as `live-installer --automated`. Confirm
it runs:

```
live-installer --automated --help
```

## Step 2: find your disk's stable name

The answer file never refers to a disk as `/dev/sda` — that name is not
stable across reboots or hardware. Instead you pick the disk by a stable
attribute. List what this machine offers:

```
live-installer --automated --list-disks
```

You get each disk with everything you can match on:

```
/dev/sda  274GB  model='Samsung SSD 870 EVO 250GB'  removable=no
  by-path (stable per chassis slot, fleet-reusable):
    pci-0000:00:17.0-ata-1
  by-id (unique to this physical drive):
    ata-Samsung_SSD_870_EVO_250GB_S6PMNX0T123456
    wwn-0x5002538f12345678
```

Which one to use:

| You are... | Use |
|---|---|
| Installing a machine with one internal disk | `first-non-removable: true` (needs nothing from this list; aborts if the machine has several internal disks) |
| Picking one disk out of several, this exact machine | a `by-id` name (unique to the drive) |
| Imaging a fleet of identical hardware | a `by-path` name (same slot on every unit) |
| Going off a spec sheet, no machine in hand | `model` (with a trailing `*`) or `size-min` |

When you copy a `by-id` or `model` value, replace the serial-number tail
with `*` so it is a glob, e.g.
`ata-Samsung_SSD_870_EVO_250GB_*`.

## Step 3: write a minimal answer file

Create `install.yaml`. The smallest useful file:

```yaml
version: 1
locale: en_US.UTF-8
timezone: America/Toronto
hostname: testbox
users:
  - name: admin
    # generate with: openssl passwd -6
    passwd: "$6$rounds=4096$REPLACE$ME..."
    groups: [sudo]
storage:
  target:
    match:
      first-non-removable: true   # or by-id / by-path / model from step 2
  layout: simple                  # simple | lvm | lvm-on-luks
```

Generate the password hash with `openssl passwd -6` (plaintext passwords
are rejected). Paste the result into `passwd`.

## Step 4: validate before you touch a disk

Check the file is well-formed. This touches no disks and can run anywhere,
including on your laptop before you ever boot the target:

```
live-installer --automated --check --config install.yaml
```

It prints `Answer file OK`, or the exact problems and a non-zero exit.

Then confirm it resolves to the disk you expect on the target machine
(this one does read the disks, so run it on the target):

```
live-installer --automated --dry-run --config install.yaml
```

It prints which disk it would install to, and stops. If your match is
ambiguous or matches nothing, it tells you here instead of mid-install.

## Step 5: run the install

Two ways to hand the installer the file.

**Interactively, from the live session** (good for a first try):

```
sudo live-installer --automated --config /path/to/install.yaml
```

**Unattended, from the boot menu** (how a real deployment works): put the
file on the install media or an HTTPS URL and add to the kernel command
line:

```
live-installer.auto=/cdrom/install.yaml
```

or

```
live-installer.auto=https://you.example.com/install.yaml
```

Plain HTTP is refused by default because the file carries a password hash;
use HTTPS, or add `live-installer.auto-insecure` on a trusted network.

The console prints `Automated installation complete` when the machine is
ready to reboot, or `Automated installation FAILED` with a reason if a
step hit an `abort` policy. Nothing is left half-installed on an abort.

## Watching a headless box

If the target has no monitor, add a serial console so the whole install
(and a LUKS unlock prompt, if you use encryption) appears on serial:

```yaml
kernel:
  serial_console: "ttyS0,115200"
logging:
  destination: /var/log/live-installer-auto.log
  also_serial: ttyS0
```

See [serial-console-and-luks.md](serial-console-and-luks.md) for the
mechanics and how to drive it over IPMI Serial-over-LAN.

## Ready-made examples

Four complete, valid answer files live in
[`tests/integration/scenarios/answers/`](../tests/integration/scenarios/answers/),
one per layout. They double as the integration-test fixtures, so they are
guaranteed to match the current schema. Start from the one closest to what
you want and run it through `--check`.

## Reporting back

Useful things to include in feedback:

- The exact build/commit of the ISO.
- Your full `install.yaml` (redact the password hash).
- The console output, and `/var/log/live-installer-auto.log` from the
  target if it got that far.
- `--list-disks` output if a disk match did not resolve the way you
  expected.
- Whether you were on LMDE, Mint, or a VM, and BIOS vs UEFI.
