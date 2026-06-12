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
   locale:
     language: en_US.UTF-8
     timezone: America/Toronto
   users:
     - username: admin
       password_crypted: "$6$rounds=4096$abc$..."   # openssl passwd -6
       sudo: true
   storage:
     target:
       match:
         first-non-removable: true
     layout: simple
   ```

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

| Key | Required | Description |
|---|---|---|
| `version` | yes | Schema version. Currently `1`. |
| `locale` | yes | `language` (e.g. `en_US.UTF-8`) and `timezone` (IANA, e.g. `America/Toronto`). |
| `users` | yes | At least one user; see [users](#users). |
| `storage` | yes | Disk target and layout; see [storage](#storage). |
| `keyboard` | no | `model` (default `pc105`), `layout` (default `us`), `variant`. |
| `network` | no | `hostname`. DHCP is used by default. |
| `packages` | no | `add` / `remove` lists of package names. |
| `post_install` | no | Ordered steps; see [post-install](#post-install-steps). |
| `kernel` | no | `cmdline_extra`: string appended to the installed kernel command line (serial console, driver blacklists, …). |
| `oem` | no | `enabled`: leave the machine in OEM first-boot state. |
| `on_failure` | no | Per-failure-mode policy; see [failure handling](#failure-handling). |
| `logging` | no | `destination` (log file path) and `also_serial` (e.g. `ttyS0`). |

### users

Each entry:

| Field | Required | Description |
|---|---|---|
| `username` | yes | Lowercase, starts with a letter/underscore, ≤ 32 chars. |
| `password_crypted` | yes | A crypt(5) hash (`$6$…` sha512crypt, `$y$…` yescrypt, …). **Plaintext is rejected.** Generate with `openssl passwd -6` or `mkpasswd -m sha512crypt`. |
| `full_name` | no | GECOS name. |
| `sudo` | no | Add to the `sudo` group. |
| `autologin` | no | At most one user may set this. |
| `ecryptfs_home` | no | Encrypt the home directory with eCryptfs. |
| `ssh_authorized_keys` | no | List of OpenSSH public keys installed to `~/.ssh/authorized_keys`. |

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

### post-install steps

An ordered list, each entry one of:

```yaml
post_install:
  - shell: /cdrom/scripts/join-domain.sh   # run a script in the target
  - apt_key_url: https://example.com/repo.gpg
  - apt_source: "deb https://example.com/repo trixie main"
```

`shell` scripts present on the install media are copied into the target
and executed in the chroot; package and repo steps run before the
`packages.add` install.

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
- Do **not** put secrets in `post_install` shell command text — the
  event log keeps it. Reference a script on the media instead.

## Limitations (v1)

- Custom partition layouts require the GUI installer.
- Config delivery is local file or http(s) URL; TFTP, NFS, and DNS-SRV
  discovery are not supported.
- The config is data, not a program — no conditionals, loops, or
  templating. Generate the YAML beforehand if you need that.
