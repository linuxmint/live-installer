# Serial console & encrypted (LUKS) installs — how it works and why

This document explains two coupled, non-obvious features of the
unattended installer, and the live-system quirks they have to work
around. If you touch `auto_installer.py`'s `_build_grub_snippet` or
`_regenerate_initramfs_if_luks`, read this first — each piece below
exists because of a specific failure that is invisible until you watch
the installed system boot on a serial console.

- [What the user asks for](#what-the-user-asks-for)
- [Serial console: the four moving parts](#serial-console-the-four-moving-parts)
- [Why a grub.d snippet, not /etc/default/grub](#why-a-grubd-snippet-not-etcdefaultgrub)
- [Why LUKS needs the initramfs rebuilt](#why-luks-needs-the-initramfs-rebuilt)
- [The live-system traps](#the-live-system-traps)
- [End-to-end sequence](#end-to-end-sequence)
- [How the test exercises it](#how-the-test-exercises-it)

## What the user asks for

```yaml
storage:
  layout: lvm-on-luks
  luks:
    passphrase_source: keyfile        # or prompt-on-first-boot
    keyfile: "https://cfg/host.key"
kernel:
  serial_console: "ttyS0,115200"      # headless: console on the serial line
```

The intent: a LUKS-encrypted machine whose disk-unlock passphrase prompt
appears on the **serial console** at boot, so an operator can unlock it
over IPMI Serial-over-LAN (or the test harness can type it) — no monitor
or keyboard required. Both halves (serial console, encrypted root) are
ordinary fleet needs; together they hit several live-system quirks.

## Serial console: the four moving parts

For the cryptsetup unlock prompt — or any boot output — to reach
`ttyS0`, **all** of these must be true on the *installed* system. Getting
three of four produces a silent boot that looks hung on serial.

1. **Kernel console on serial.** `console=ttyS0,115200n8` on the kernel
   command line. The last `console=` becomes `/dev/console`, which is
   where cryptsetup's askpass reads/writes. We also add `console=tty0`
   first so the local screen still works.
2. **No plymouth.** With `splash` (and even without it, if plymouth is in
   the initramfs), plymouth grabs the password prompt and renders it
   graphically — nothing reaches serial. We drop `quiet splash` and add
   `plymouth.enable=0`, so cryptsetup falls back to a plain-text askpass
   on `/dev/console`.
3. **GRUB on serial.** `GRUB_TERMINAL="console serial"` +
   `GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"` so the GRUB
   menu is also on serial. (Under OVMF/EFI this is partly automatic — the
   EFI console already mirrors to serial — but on BIOS/real hardware it
   is required, so we always set it.)
4. **The initramfs can actually unlock the disk** — see the LUKS section
   below. Without this the prompt never even appears; the boot drops to
   an `(initramfs)` rescue shell with `ALERT! /dev/mapper/... does not
   exist`.

`_build_grub_snippet()` produces parts 1–3; `_regenerate_initramfs_if_luks()`
handles part 4.

## Why a grub.d snippet, not /etc/default/grub

The obvious approach — edit `GRUB_CMDLINE_LINUX_DEFAULT` in
`/etc/default/grub` — **silently does not work**. `grub-mkconfig` sources
`/etc/default/grub` *and then* every `/etc/default/grub.d/*.cfg`, in
order. LMDE ships a snippet there that re-sets
`GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"`, clobbering our edit. The
generated `grub.cfg` ends up with `quiet splash` and no `console=`, and
the diagnostic (see below) showed exactly that: `/etc/default/grub` held
our value while `grub.cfg` did not.

So we instead drop our own snippet at
`/etc/default/grub.d/zz-live-installer.cfg`. The `zz-` prefix sorts it
**last**, so it sees whatever earlier snippets set and rewrites it:

```sh
GRUB_CMDLINE_LINUX_DEFAULT="$(echo " ${GRUB_CMDLINE_LINUX_DEFAULT} " \
  | sed -e 's/ quiet / /g' -e 's/ splash / /g' -e 's/^ *//' -e 's/ *$//') \
  console=tty0 console=ttyS0,115200n8 plymouth.enable=0"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"
```

It strips `quiet`/`splash` from the *runtime* value (whatever the distro
snippet set) and appends our console args. Last writer wins.

> **Rule:** anything that influences the installed kernel cmdline or GRUB
> behaviour must go through a `grub.d` snippet that sorts after the
> distro's, never a direct edit of `/etc/default/grub`.

## Why LUKS needs the initramfs rebuilt

The cryptsetup *initramfs* — not the running system — is what unlocks the
root device at boot. It learns which devices to unlock from `/etc/crypttab`
**at `update-initramfs` time**: the cryptroot hook reads crypttab and
bakes the device list into the initramfs. The engine writes
`/etc/crypttab` and then runs `update-initramfs`.

The problem: in the live session that `update-initramfs` is a **no-op**,
so the crypttab is never baked in, and at boot the initramfs has no idea
the root is encrypted. It waits, gives up, and drops to a rescue shell —
with **no unlock prompt at all**. Plain and LVM (non-encrypted) installs
are unaffected because they boot fine from the stock initramfs; only LUKS
needs the rebuild, which is why this stayed hidden until serial console
made the boot visible.

`_regenerate_initramfs_if_luks()` (run from the post-install hook, only
for `lvm-on-luks`) does the rebuild correctly — but it has to get past
three live-system traps to do it.

## The live-system traps

These are properties of the Debian/LMDE **live** environment that the
installer runs inside. Each one silently breaks `update-initramfs`.

1. **`update-initramfs` is diverted.** `live-tools` replaces
   `/usr/sbin/update-initramfs` (via `dpkg-divert`) with a wrapper that
   refuses to run while booted from live media — it prints
   `update-initramfs is disabled (live system is running on read-only
   media)` and exits 0. The engine's `update-initramfs` therefore does
   nothing. We resolve the real binary with
   `dpkg-divert --truename /usr/sbin/update-initramfs` (which returns
   e.g. `/usr/sbin/update-initramfs.orig.initramfs-tools`) and run that
   directly, bypassing the wrapper.

   - *Tried first and rejected:* bind-mounting the live medium into the
     chroot so the wrapper's `/run/live/medium` check passes. The wrapper
     then failed a **different** check (`read-only media`). Don't bother;
     go straight to `--truename`.

2. **A live-boot initramfs hook fails.** Once the real `update-initramfs`
   runs, `live-boot`'s hook (`/usr/share/initramfs-tools/hooks/live`)
   errors with `cp: cannot stat '/usr/lib/live/boot'` — a live-only path
   that doesn't exist in the installed system — and makes
   `update-initramfs` exit non-zero. We `rm -f` that hook before
   rebuilding; the installed system is not a live system and has no
   business carrying it.

   - *Tried first and rejected:* `apt-get purge` of the live packages.
     `apt-get purge` fails wholesale if *any* named package is absent (it
     removed nothing and returned 100), and resolving the exact package
     set across versions is fragile. Removing the one offending hook is
     surgical and reliable. (Fully purging the live stack is a reasonable
     future nicety, but do it package-by-package and tolerant of
     absence.)

3. **The `chroot` quoting trap.** `CommandRunner.chroot()` wraps the
   command in `sh -c "..."` *and replaces `"` with `'`*. Shell variables
   and command substitutions inside a chroot command are therefore
   evaluated by the **host** shell, not the target's, unless you are very
   careful. Prefer host-side file operations on `/target/...` paths
   (e.g. `rm -f /target/usr/share/initramfs-tools/hooks/live`), or
   resolve values on the host (via `runner.output(...)`) and substitute
   them in Python, rather than writing `$(...)`/`$var` inside a
   `runner.chroot()` string.

## End-to-end sequence

For a `lvm-on-luks` + `serial_console` install, the post-install hook
(`before_unmount_hook`, while the chroot is still mounted) does, in order:

1. `_regenerate_initramfs_if_luks()`
   - `rm -f /target/usr/share/initramfs-tools/hooks/live`  (trap 2)
   - `real = dpkg-divert --truename /usr/sbin/update-initramfs`  (trap 1)
   - `chroot $real -u -k all`  → crypttab is baked into the initramfs
2. `_apply_kernel_config()`
   - write `/etc/default/grub.d/zz-live-installer.cfg`  (the grub.d snippet)
   - `chroot update-grub`  → grub.cfg gets `console=...` + serial terminal
   - log the resulting `grub.cfg` kernel line (diagnostic)

At boot: GRUB (on serial) → kernel (on serial, no plymouth) → initramfs
cryptroot prompts `Please unlock disk lvmmint:` on `ttyS0` → operator (or
harness) types the passphrase → LVM activates → root mounts → multi-user.

## Prompt-on-first-boot rekey (serial)

When `luks.passphrase_source: prompt-on-first-boot`, no passphrase is in the
answer file at all: the installer formats LUKS with a random throwaway key and
embeds it in the initramfs so the **first** boot unlocks unattended, then a
one-shot service prompts for the real passphrase on the console — including the
serial console when `serial_console` is set — and rekeys.

The post-install step `_setup_luks_first_boot_rekey()` (in `auto_installer.py`)
writes, into the target:

- the keyfile at `/etc/cryptsetup-keys.d/cryptroot.key` (byte-exact, no trailing
  newline) and the matching `crypttab` keyfile entry, plus the cryptsetup
  initramfs hook so the key is carried into the initramfs;
- a `li-luks-rekey` systemd one-shot, ordered `After=systemd-user-sessions`
  and `Before=getty.target serial-getty@ttyS0.service`, with
  `StandardInput=tty-force` / `TTYPath=/dev/console`, so its prompt lands on
  the same console (tty0 *and* serial) as the boot.

On that first boot the service uses a plain `read` on `/dev/console` to collect
the passphrase, `luksAddKey`s it, `luksRemoveKey`s the throwaway key, rebuilds
the initramfs (so the key is gone from `/boot`), removes itself, and reboots.
Every subsequent boot is an ordinary cryptroot prompt as above. The
`uefi-luks-prompt` integration scenario drives this over serial end to end.

## How the test exercises it

`tests/integration/scenarios/uefi-lvm-luks.yaml` runs the whole path:
install, reboot, **type the passphrase over the serial console**
(`boot_unlock:` in the scenario; `VM.send_serial()` in the harness),
then verify the unlocked system over SSH. Two diagnostics made this
debuggable and are kept in place:

- the driver logs the actual `grub.cfg` kernel line after `update-grub`,
  so a wrong cmdline is visible in the install log without re-running;
- `run_scenario.py` copies the install-phase serial log to
  `*-serial.install.log` before phase 2 truncates it, so the install and
  the boot can be inspected separately.

If you change any of the four serial-console parts or the initramfs
rebuild, run this scenario and watch the serial log — a regression here
is silent everywhere else.
