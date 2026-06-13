# Automated (unattended) installation

`live-installer` can run without any GUI interaction, driven by a YAML
**answer file**. This is intended for labs, fleets, and OEM/imaging
pipelines where the same install is performed on many machines.

If no answer file is supplied, the installer behaves exactly as before —
the GUI wizard. The automated path is additive; it changes nothing for
interactive desktop users.

- [Quick start](#quick-start)
- [Triggering an unattended install](#triggering-an-unattended-install)
- [Answer file reference](#answer-file-reference)
- [Worked examples](#worked-examples)
- [Failure handling](#failure-handling)
- [Security notes](#security-notes)
- [Limitations](#limitations)

## Quick start

1. Write an answer file (`install.yaml`):

   ```yaml
   version: 1
   locale: en_US.UTF-8
   timezone: America/Toronto
   users:
     - name: admin
       passwd: "$6$rounds=4096$abc$..."   # openssl passwd -6
       groups: [sudo]
   storage:
     target:
       match:
         first-non-removable: true
     layout: simple
   ```

   Where this overlaps with cloud-init (identity, users, packages, runcmd),
   it uses the same keys and structure, so a cloud-init or Ubuntu
   autoinstall user should find it familiar. It is not a drop-in for either
   format.

2. Put it on the install media (e.g. at `/cdrom/install.yaml`) **or** serve
   it over HTTPS.

3. Boot the live medium with the answer-file source on the kernel command
   line:

   ```
   live-installer.auto=/cdrom/install.yaml
   ```

   The install runs unattended and the machine is ready to reboot when the
   console prints `Automated installation complete`.

## Triggering an unattended install

The installer runs in automated mode when **either** is present:

- the kernel command-line argument `live-installer.auto=<source>`, or
- the program is started with `--automated=<source>`.

`<source>` is one of:

| Source | Example | Notes |
|---|---|---|
| Local path | `/cdrom/install.yaml` | On the install media or any mounted filesystem. Zero infrastructure. |
| HTTPS URL | `https://cfg.example.com/host.yaml` | For PXE / netboot. TLS required (see below). |
| HTTP URL | `http://10.0.0.1/host.yaml` | Refused unless `live-installer.auto-insecure` is also on the cmdline (or `--insecure`). |

Answer files carry password hashes (and may reference key material), so
plain HTTP is refused by default. Use HTTPS, or opt in explicitly with
`live-installer.auto-insecure` on a trusted network.

### Per-machine answer files

The source is a literal string, so a provisioning server can hand each
machine its own file by templating the URL — for example keying on the
SMBIOS serial in your PXE config:

```
live-installer.auto=https://cfg.example.com/by-serial/${serial}.yaml
```

## Answer file reference

The answer file is validated strictly: unknown keys, wrong types, and
malformed YAML are hard errors. The installer never guesses — a bad file
aborts before any disk is touched.

### Top-level keys

Keys that overlap with cloud-init use cloud-init's name and structure.

| Key | Required | Description |
|---|---|---|
| `version` | yes | Schema version. Currently `1`. |
| `locale` | yes | A locale string, e.g. `en_US.UTF-8` (cloud-init style, top level). |
| `timezone` | yes | IANA timezone, e.g. `America/Toronto` (top level). |
| `users` | yes | At least one user; see [users](#users). |
| `storage` | yes | Disk target and layout; see [storage](#storage). |
| `hostname` | no | System hostname. DHCP/default if omitted. |
| `keyboard` | no | `model` (default `pc105`), `layout` (default `us`), `variant`. |
| `packages` | no | Flat list of packages to install (cloud-init style). |
| `package_remove` | no | Flat list of packages to remove (extension; cloud-init has no declarative remove). |
| `repositories` | no | Package repositories to add; see [repositories](#repositories). |
| `runcmd` | no | List of shell commands; see [runcmd](#runcmd). |
| `kernel` | no | `cmdline_extra` and `serial_console`; see [kernel](#kernel). |
| `oem` | no | `enabled`: leave the machine in OEM first-boot state. |
| `on_failure` | no | Per-failure-mode policy; see [failure handling](#failure-handling). |
| `logging` | no | `destination` (log file path) and `also_serial` (e.g. `ttyS0`). |

### users

Uses cloud-init key names. Each entry:

| Field | Required | Description |
|---|---|---|
| `name` | yes | Username. Lowercase, starts with a letter/underscore, ≤ 32 chars. |
| `passwd` | yes | A crypt(5) hash (`$6$…` sha512crypt, `$y$…` yescrypt, …). **Plaintext is rejected.** Generate with `openssl passwd -6` or `mkpasswd -m sha512crypt`. |
| `gecos` | no | Full name (GECOS). |
| `groups` | no | Supplementary groups. Put `sudo` here to grant admin (cloud-init idiom). |
| `ssh_authorized_keys` | no | List of OpenSSH public keys installed to `~/.ssh/authorized_keys`. |
| `autologin` | no | At most one user may set this. (Extension.) |
| `ecryptfs_home` | no | Encrypt the home directory with eCryptfs. (Extension.) |

The first user is the primary account created by the installer; any
others are created during post-install.

### storage

```yaml
storage:
  target:
    match:               # select the disk by a STABLE attribute
      by-id: "nvme-Samsung_SSD_980_PRO_*"
    on_no_match: abort   # only 'abort' is supported in v1
  layout: lvm-on-luks    # simple | lvm | lvm-on-luks
  luks:                  # only with layout: lvm-on-luks
    passphrase_source: keyfile   # prompt-on-first-boot | tpm2 | keyfile
    keyfile: "https://cfg.example.com/by-serial/${serial}.key"
```

**Disk targeting never uses `/dev/sdX`.** Kernel device naming is not
stable across NVMe/SATA/USB combinations, so the target is chosen by a
match expression — at least one of:

| Matcher | Matches |
|---|---|
| `by-id` | a glob against `/dev/disk/by-id/*` names |
| `by-path` | a glob against `/dev/disk/by-path/*` names |
| `model` | a glob against the disk model string |
| `size-min` | disks at least this large (e.g. `500GB`, `1TB`) |
| `first-non-removable` | the first fixed (non-USB) disk |

All present matchers must agree. If **no** disk matches, or if **more
than one** matches, the install aborts with the list of available disks
— it never guesses which disk to erase.

Layout presets: `simple` (single root + swap), `lvm` (LVM with root and
swap logical volumes), `lvm-on-luks` (the same, on a LUKS2 container).
Custom partition layouts are not available in unattended mode — use the
GUI for those.

For `lvm-on-luks`, `passphrase_source` selects how the volume is
unlocked at boot: `prompt-on-first-boot` (the admin types it), `keyfile`
(read from a local path or an http(s) URL — same TLS rule as the answer
file), or `tpm2`.

### kernel

```yaml
kernel:
  cmdline_extra: "mitigations=off ipv6.disable=1"   # appended verbatim
  serial_console: "ttyS0,115200"                    # provision a serial console
```

- `cmdline_extra` — appended to the installed system's kernel command
  line (`GRUB_CMDLINE_LINUX_DEFAULT`).
- `serial_console` — provision a full serial console on the installed
  system (`ttyS0` or `ttyS0,<baud>`). This drops `quiet`/`splash`, adds
  `console=tty0 console=<dev>,<baud>n8 plymouth.enable=0`, and points
  GRUB's terminal at the serial line. Boot output — **including a LUKS
  unlock passphrase prompt** — then appears on serial, so a headless box
  can be unlocked and watched over IPMI Serial-over-LAN. Without this, an
  encrypted machine prompts for its passphrase only on the local screen.

Both are applied via a `grub.d` snippet and `update-grub`. For the
mechanics and the live-system quirks involved, see
[serial-console-and-luks.md](serial-console-and-luks.md).

### repositories

Package repositories to add before installing packages:

```yaml
repositories:
  - source: "deb https://example.com/repo trixie main"
    key_url: https://example.com/repo.gpg   # optional, https only
```

The section name is package-system-neutral (cloud-init calls the
equivalent `apt:`); the `source` value is apt syntax on Debian/Mint.
`key_url` must use HTTPS.

### runcmd

A list of shell commands, run in the target during install:

```yaml
runcmd:
  - /cdrom/scripts/join-domain.sh
  - systemctl enable ssh
```

This uses cloud-init's `runcmd` key and structure. One difference from
cloud-init: these run in the target (in a chroot) at install time, not on
first boot. A command whose first word is a file present on the install
media is copied into the target and run there, so on-media scripts work.

## Validating an answer file

You do not need a target machine, or any disks, to check that an answer
file is well-formed. `--check` validates syntax and schema and exits:

```
live-installer --automated --check --config install.yaml
```

It prints `Answer file OK` and exits `0` on success, or the list of
problems and exits `1` on failure. Because it stops before any disk is
resolved, it runs anywhere — CI, a build host, a laptop — which makes it
the natural lint step in a pipeline that generates these files. (`--dry-run`
goes one step further and also resolves the target disk, so it must run on
a machine that actually has the disk.)

## Worked examples

A complete, runnable answer file for each layout lives under
[`tests/integration/scenarios/answers/`](../tests/integration/scenarios/answers/):

| File | Demonstrates |
|---|---|
| `bios-simple.yaml` | Simplest case: simple layout, one user, one package |
| `uefi-lvm.yaml` | LVM layout on a UEFI machine |
| `uefi-lvm-luks.yaml` | LUKS-encrypted LVM with a keyfile and serial console |
| `bios-multi-disk.yaml` | Selecting one disk out of several by `by-id` |

These double as the integration-test fixtures, so they are guaranteed to
stay valid against the current schema.

## Failure handling

Every failure mode has an explicit policy, and the defaults fail closed
(`abort`):

```yaml
on_failure:
  partition_mismatch: abort           # disk match found nothing
  network_unavailable: continue       # an apt/network step failed
  package_install_failure: abort
  post_install_script_failure: abort
```

`abort` stops the install and prints `Automated installation FAILED`
(plus the reason) to the console, log, and serial. `continue` logs a
warning and proceeds. On any abort the machine is left unbooted rather
than half-installed.

## Security notes

- **Passwords** are crypt(5) hashes only; plaintext is rejected outright.
- **Answer files and keyfiles** are refused over plain HTTP by default
  (they carry secrets) — use HTTPS or opt in with
  `live-installer.auto-insecure`.
- **LUKS passphrases** are passed to `cryptsetup` on stdin, never as a
  command argument, so they are not visible in the process table; they
  are also redacted from all logs.
- Do not put secrets in `runcmd` command text. It is logged. Reference a
  script on the media instead.

## Limitations (v1)

- Custom partition layouts require the GUI installer.
- Config delivery is local file or http(s) URL; TFTP, NFS, and DNS-SRV
  discovery are not supported.
- The config is data, not a program — no conditionals, loops, or
  templating. Generate the YAML beforehand if you need that.
