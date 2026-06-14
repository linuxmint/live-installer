# Automated (unattended) installation

`live-installer` can run without any GUI interaction, driven by a YAML
**answer file**. This is intended for labs, fleets, and OEM/imaging
pipelines where the same install is performed on many machines.

If no answer file is supplied, the installer behaves exactly as before —
the GUI wizard. The automated path is additive; it changes nothing for
interactive desktop users.

New to this? [getting-started.md](getting-started.md) is a step-by-step
walkthrough for trying it on a VM or spare machine. This page is the
reference.

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

   Where this overlaps with cloud-init (identity, users, packages), it uses
   the same keys and structure, so a cloud-init or Ubuntu autoinstall user
   should find it familiar. It is not a drop-in for either
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
| NFS URL | `nfs://10.0.0.1/srv/cfg/host.yaml` | The directory is mounted read-only and the file read from it. Cleartext, so refused unless `live-installer.auto-insecure` (or `--insecure`). |
| TFTP URL | `tftp://10.0.0.1/host.yaml` | Fetched with a built-in TFTP read client (no extra tooling), for PXE setups that already run a TFTP server. Cleartext, so refused unless `live-installer.auto-insecure` (or `--insecure`). |
| Auto-discovery | `auto:https://cfg.example.com/` | One entry for a whole fleet: the installer finds its own file from this machine's identity. See [auto-discovery](#auto-discovery-for-netboot). |

Answer files carry password hashes (and may reference key material), so
cleartext transports (plain HTTP, NFS, TFTP) are refused by default. Use HTTPS,
or opt in explicitly with `live-installer.auto-insecure` on a trusted
network.

IPv6 works everywhere a host appears: use a bracketed literal
(`https://[2001:db8::1]/host.yaml`, `nfs://[2001:db8::1]/srv/host.yaml`) or
a hostname that resolves to an AAAA record.

### Per-machine answer files

The source is a literal string, so a provisioning server can hand each
machine its own file by templating the URL — for example keying on the
SMBIOS serial in your PXE config:

```
live-installer.auto=https://cfg.example.com/by-serial/${serial}.yaml
```

### Auto-discovery for netboot

Templating one PXE entry per machine does not scale. Instead, point every
machine at the same base with `auto:<base-url>` and let the installer find
its own file from its identity:

```
live-installer.auto=auto:https://cfg.example.com/configs/
```

It then tries, in order, the first that fetches **and** validates:

```
<base>/by-mac/<mac>.yaml      # one per ethernet NIC, in interface order
<base>/by-serial/<serial>.yaml
<base>/by-uuid/<uuid>.yaml
<base>/default.yaml           # fleet-wide fallback
```

`<mac>` is lowercase colon-separated (e.g. `aa:bb:cc:00:11:22`); `<serial>`
and `<uuid>` come from SMBIOS (`/sys/class/dmi/id`). So one boot entry
covers the whole fleet: give a few machines their own `by-serial` file and
everything else falls through to `default.yaml`. The chosen source is
logged. If none validate, the install aborts with the list of what was
tried.

Bare `auto` (no base) skips the network step and only checks the
well-known local paths `/cdrom/auto-install.yaml` and
`/run/live/medium/auto-install.yaml`, so a USB or remastered ISO still
works offline.

### Network boot (PXE / iPXE)

The installer can run from a fully network-booted live system, with no
install media: DHCP/TFTP/iPXE boot the kernel and initrd, live-boot
fetches the root filesystem over HTTP, and the installer fetches its
answer file over HTTP as above. This needs a netboot-capable image (a
single squashfs with the installer, the kernel/initrd/manifests carried
in the rootfs, and a NetworkManager-based live system), which a normal
desktop ISO is not. The mechanics, the boot command line, the image
requirements, and the traps are documented separately in
[pxe-netboot.md](pxe-netboot.md).

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
| `network` | no | Static IP / DNS / VLAN config (netplan v2 subset); see [network](#network). DHCP on all NICs if omitted. |
| `packages` | no | Flat list of packages to install (cloud-init style). |
| `package_remove` | no | Flat list of packages to remove (extension; cloud-init has no declarative remove). |
| `apt` | no | Apt repositories to add (cloud-init `apt:` shape); see [apt](#apt). |
| `late_commands` | no | Shell commands run in the target at end of install; see [late_commands](#late_commands). |
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
| `first-non-removable` | the sole fixed (non-USB) disk. If a machine has more than one internal disk this is ambiguous and aborts — add `size-min`, `by-id`, or `by-path` to disambiguate. |

All present matchers must agree. If **no** disk matches, or if **more
than one** matches, the install aborts with the list of available disks
— it never guesses which disk to erase.

Layout presets: `simple` (single root + swap), `lvm` (LVM with root and
swap logical volumes), `lvm-on-luks` (the same, on a LUKS2 container).

For `lvm-on-luks`, `passphrase_source` selects how the volume is
unlocked at boot: `prompt-on-first-boot` (the admin types it), `keyfile`
(read from a local path or an http(s) URL — same TLS rule as the answer
file), or `tpm2`.

### Custom partition layouts

For full control, `layout: custom` takes an explicit `partitions` list
(and optionally an `lvm` list). The disk is wiped and the partitions are
created in order.

```yaml
storage:
  target:
    match: {first-non-removable: true}
  layout: custom
  partitions:
    - {size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}
    - {size: 2GB,   mount: swap,      filesystem: swap}
    - {size: rest,  lvm_pv: vg0}          # this partition becomes an LVM PV
  lvm:
    - {vg: vg0, lv: root, size: 40GB, mount: /,     filesystem: ext4}
    - {vg: vg0, lv: home, size: rest, mount: /home, filesystem: ext4}
```

Each **partition** has a `size` (`512MB`/`40GB`/`1TB`, or `rest` for the
remainder), and is one of: a mounted partition (`mount` + `filesystem`),
an LVM physical volume (`lvm_pv: <vg>`), or a flag-only partition. `flags`
may include `esp` (an EFI System Partition — must be `vfat` at `/boot/efi`),
`bios_grub` (the BIOS-boot partition for GPT), or `swap`. Each **lvm**
entry is a logical volume (`vg`, `lv`, `size`, `mount`, `filesystem`) on a
VG backed by an `lvm_pv` partition.

#### btrfs subvolumes

A `btrfs` partition (or LVM logical volume) may carry a `subvolumes` list
instead of a single `mount`. Each subvolume has a `name` (the on-disk
subvolume name, e.g. `@`) and a `mount`. The filesystem is created once,
the subvolumes are created in it, and each is mounted at its `mount` with
`subvol=<name>` — the snapshot-friendly layout Mint/Timeshift expect.

```yaml
  partitions:
    - {size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}
    - {size: 2GB,   mount: swap,      filesystem: swap}
    - size: rest
      filesystem: btrfs
      subvolumes:
        - {name: "@",     mount: /}
        - {name: "@home", mount: /home}
```

A partition/LV with `subvolumes` must be `btrfs` and takes no top-level
`mount`; subvolume mounts participate in the same uniqueness rules below.

Rules, all enforced at validation: exactly one `/`; no duplicate mount
points; at most one `rest` per disk and per VG; every `lvm_pv` VG must
have logical volumes and vice versa. Filesystems: `ext4`/`ext3`/`ext2`,
`xfs`, `btrfs`, `vfat`, `f2fs`, `swap`. Software RAID is not in custom
layouts yet.

### network

Configures the **installed** system's networking. With no `network:` section
every NIC is left to NetworkManager's default DHCP (the live session's own
networking is never touched). The schema is a subset of netplan's v2 format —
the same key names and structure — but it is rendered directly to
NetworkManager keyfiles in `/etc/NetworkManager/system-connections/` (mode
0600). It does **not** depend on the `netplan` binary, which LMDE/Debian does
not ship; this mirrors how cloud-init renders its own Network Config v2 to the
NetworkManager backend on Debian-family systems.

```yaml
network:
  version: 2
  ethernets:
    lan0:
      # Select the device by MAC (survives kernel renaming) or by name.
      match: {macaddress: "52:54:00:aa:bb:01"}
      addresses: [192.168.50.10/24, 2001:db8:50::10/64]   # dual-stack
      gateway4: 192.168.50.1
      gateway6: 2001:db8:50::1
      nameservers:
        addresses: [192.168.50.53, 2001:db8:50::53]
        search: [lab.example]
      routes:
        - {to: 10.0.0.0/8, via: 192.168.50.254}           # optional extra route
  vlans:
    vlan50:
      id: 50            # 802.1Q tag, 0..4094
      link: lan0        # parent interface id
      addresses: [10.50.0.5/24]
```

**ethernets** is a map of interface id → config. The id is the interface name
unless a `match` is given: `match.macaddress` binds by hardware address (the
robust choice for a mixed fleet, since the kernel name is unpredictable),
`match.name` binds by interface name. **vlans** is a map of id → config with
an `id` (the 802.1Q tag) and a `link` (the parent ethernet/vlan id).

Each interface takes: `dhcp4`/`dhcp6` (default false), `addresses` (a list of
`IP/prefix`, IPv4 and/or IPv6), `gateway4`/`gateway6`, `nameservers`
(`addresses` + `search`), and `routes` (`to` is `default` or a CIDR, `via` is
the next hop, optional `metric`). Per family: DHCP → NM `auto`; a static
address → `manual`; neither → IPv4 `disabled` / IPv6 `link-local`. So a NIC
with only an IPv4 address gets no global IPv6, and `dhcp6: true` selects
SLAAC/DHCPv6 (`auto`). Validation rejects addresses without a prefix length,
gateways of the wrong family or with no matching address, routes whose `via`
family disagrees with `to`, VLAN ids outside 0..4094, and VLAN links that do
not name a defined interface. Bonds and bridges are not modelled yet.

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

### apt

Apt repositories to add before installing packages. This mirrors
cloud-init's `apt:` section — a `sources` map keyed by an arbitrary name,
each entry an apt sources.list line plus an optional signing key:

```yaml
apt:
  sources:
    vendor:
      source: "deb https://example.com/repo trixie main"
      key_url: https://example.com/repo.gpg     # extension: fetch over https
    upstream:
      source: "deb https://other.example/deb stable main"
      keyid: "0xABCDEF0123456789"                # fetched from a keyserver
      keyserver: keyserver.ubuntu.com            # default shown
    local:
      source: "deb https://local.example/apt trixie main"
      key: |                                     # inline ASCII-armored key
        -----BEGIN PGP PUBLIC KEY BLOCK-----
        ...
        -----END PGP PUBLIC KEY BLOCK-----
```

Each source must carry **at most one** signing key — `key` (inline armored),
`keyid` (fetched from `keyserver`), or `key_url` (fetched over https; an
extension beyond cloud-init). The source line is written to
`/etc/apt/sources.list.d/<name>.list` (override the basename with
`filename:`), and the key to `/etc/apt/trusted.gpg.d/<name>`. `key_url` must
use HTTPS. **Quote `keyid` values** — an unquoted `0x…` is parsed as a YAML
integer.

Repo config is deliberately apt-specific: cloud-init never unified
apt/yum/zypper, and here it is executed by a swappable package backend
(`pkgbackend.py`) rather than hardcoded into the driver, leaving room for
dnf/zypper later. The agnostic `packages:`/`package_remove:` lists stay
top-level and are dispatched through whichever backend is selected.

### late_commands

A list of shell commands, run in the target (in a chroot) at the end of
the install:

```yaml
late_commands:
  - /cdrom/scripts/join-domain.sh
  - systemctl enable ssh
```

This matches Ubuntu autoinstall's `late-commands` and kickstart `%post`:
the commands run in the installed system at install time. It is
deliberately **not** named `runcmd`, because cloud-init's `runcmd` runs on
first boot — a different lifecycle (network up, systemd running) that this
key does not provide. A command whose first word is a file present on the
install media is copied into the target and run there, so on-media scripts
work.

## Discovering disk names

To target a disk you need one of its stable attributes, not `/dev/sdX`.
Boot the live medium and run:

```
live-installer --automated --list-disks
```

It prints every installable disk with its `by-id`, `by-path`, `model`,
and size — the values you can paste into a `storage.target.match`
expression. `by-path` is stable per chassis slot (reusable across
identical machines); `by-id` is unique to one physical drive. It touches
nothing and exits.

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
| `custom.yaml` | Custom layout: explicit ESP + swap partitions and a custom LVM vg with root/home |
| `btrfs.yaml` | Custom layout: btrfs root split into `@` (/) and `@home` (/home) subvolumes |
| `static-network.yaml` | Static dual-stack (IPv4+IPv6) IP + 802.1Q VLAN on a second NIC |

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

### If the answer file itself cannot be loaded

A source that is unreachable, missing, malformed, or fails schema
validation is a hard failure: the driver prints the reason and
`Automated installation FAILED`, then exits non-zero. It does **not**
fall through to the interactive GUI, and it does not retry — falling
through would risk an operator walking up to a half-expected manual
install, and a silent retry loop hides a broken config. The fix is to
correct the source and reboot. Validate the file with `--check` before
deploying it to avoid this class of failure entirely.

## Security notes

- **Passwords** are crypt(5) hashes only; plaintext is rejected outright.
- **Answer files and keyfiles** are refused over cleartext transports
  (plain HTTP, NFS) by default (they carry secrets) — use HTTPS or opt in
  with `live-installer.auto-insecure`.
- **LUKS passphrases** are passed to `cryptsetup` on stdin, never as a
  command argument, so they are not visible in the process table; they
  are also redacted from all logs.
- Do not put secrets in `late_commands` command text. It is logged.
  Reference a script on the media instead.

### When `--insecure` (plain HTTP / NFS / TFTP) is appropriate

The default refuses to fetch an answer file or keyfile over a cleartext
transport (plain HTTP, NFS, or TFTP) because they carry secrets. `--insecure` (or
`live-installer.auto-insecure`) lifts that, and there are legitimate uses:
an air-gapped lab, an isolated provisioning VLAN, or a manufacturing floor
where standing up trusted TLS is real work for little gain, and the wire is
already trusted.

It is *not* appropriate on any network an untrusted party can reach. The
residual risk on plain HTTP is that anyone who can capture packets can
observe the password hashes (salted hashes resist offline cracking but are
still worth protecting) and could tamper with the install in flight. Make
the choice deliberately, per network — not as a reflex to silence the
error. HTTP fetches still time out after 60 seconds, so a hung or hostile
server cannot wedge the install indefinitely.

## Limitations (v1)

- Custom partition layouts cover explicit partitions, custom LVM, and
  btrfs subvolumes; software RAID is not supported yet.
- `network:` covers static IPv4/IPv6, gateways, DNS, routes, and 802.1Q
  VLANs (netplan v2 subset); bonds and bridges are not modelled yet.
- Config delivery is local file, http(s) URL, NFS, or TFTP, plus `auto`
  identity-based discovery; DNS-SRV discovery is not supported.
- The config is data, not a program — no conditionals, loops, or
  templating. Generate the YAML beforehand if you need that.
