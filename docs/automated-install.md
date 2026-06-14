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
| NFS URL | `nfs://10.0.0.1/srv/cfg/host.yaml` | The directory is mounted read-only and the file read from it. The protocol version is negotiated (typically v4.2 down to v3); pin it with a `?vers=` query (`nfs://host/srv/host.yaml?vers=3` or `?vers=4.2`) for a version-restricted filer. IPv4, IPv6 (bracketed literal), and hostnames all work. Cleartext, so refused unless `live-installer.auto-insecure` (or `--insecure`). |
| TFTP URL | `tftp://10.0.0.1/host.yaml` | Fetched with a built-in TFTP read client (no extra tooling), for PXE setups that already run a TFTP server. Negotiates RFC 2347 options (`blksize`/`tsize`) and falls back to plain RFC 1350 against option-unaware servers. Intended for small files (answer file, keyfile) — use HTTP for large payloads. Cleartext, so refused unless `live-installer.auto-insecure` (or `--insecure`). |
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
| `locale` | yes | The primary locale, e.g. `en_US.UTF-8` — sets `LANG` (cloud-init style, top level). |
| `additional_locales` | no | Extra locales to also generate (e.g. `[fr_CA.UTF-8]`), available even though `LANG` stays `locale`. |
| `proxy` | no | System-wide http(s) proxy URL (e.g. `http://proxy.corp:3128`); used by apt during the install and by the installed system. |
| `ca_certs` | no | CA certificates to add to the system trust store (cloud-init `ca_certs:` shape); see [ca_certs](#ca_certs). |
| `timezone` | yes | IANA timezone, e.g. `America/Toronto` (top level). |
| `users` | yes | At least one user; see [users](#users). |
| `storage` | yes | Disk target and layout; see [storage](#storage). |
| `hostname` | no | System hostname. Defaults to `mint` if omitted. |
| `keyboard` | no | `model` (default `pc105`), `layout` (default `us`), `variant`, plus `additional_layouts` (list of `{layout, variant}`) and a `toggle` to switch them; see [keyboard](#keyboard). |
| `network` | no | Static IP / DNS / VLAN / wifi config (netplan v2 subset); see [network](#network). DHCP on all NICs if omitted. |
| `packages` | no | Flat list of packages to install (cloud-init style). |
| `package_remove` | no | Flat list of packages to remove (extension; cloud-init has no declarative remove). |
| `apt` | no | Apt repositories to add (cloud-init `apt:` shape); see [apt](#apt). |
| `late_commands` | no | Shell commands run in the target at end of install; see [late_commands](#late_commands). |
| `kernel` | no | `cmdline_extra` and `serial_console`; see [kernel](#kernel). |
| `oem` | no | `enabled`: leave the machine in OEM first-boot state. |
| `on_failure` | no | Per-failure-mode policy; see [failure handling](#failure-handling). |
| `logging` | no | `destination` (log file path) and `also_serial` (e.g. `ttyS0`). |

### keyboard and locales

`keyboard.layout`/`keyboard.variant` is the primary layout. Add more layouts
to switch between with `additional_layouts`, and a `toggle` (an XKB switch
option) to flip among them — the multi-layout case kickstart and autoinstall
have long supported (e.g. an `en_CA` + `fr_CA` desktop):

```yaml
keyboard:
  model: pc105
  layout: us
  additional_layouts:
    - {layout: ca, variant: fr}
  toggle: grp:alt_shift_toggle      # e.g. Alt+Shift switches layout

additional_locales:                  # generated alongside the primary `locale`
  - fr_CA.UTF-8
```

The layouts are written to `/etc/default/keyboard` as the comma-joined
`XKBLAYOUT`/`XKBVARIANT` lists (primary first) with `XKBOPTIONS` set to the
toggle. `toggle` is only valid when `additional_layouts` is non-empty.
`additional_locales` are `locale-gen`'d so they are available system-wide,
but `LANG` stays the primary `locale`.

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
    passphrase_source: keyfile   # keyfile | prompt-on-first-boot (default); tpm2 reserved
    keyfile: "https://cfg.example.com/keys/ws-01.key"   # only for passphrase_source: keyfile
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

`on_no_match` currently accepts only `abort` (the fail-closed default). It
is a field rather than implied behaviour so future values — e.g. `prompt`
(ask on the console) or `first-internal-disk` (a documented relaxation) —
can be added without a schema version bump.

Layout presets: `simple` (single root + swap), `lvm` (LVM with root and
swap logical volumes), `lvm-on-luks` (the same, on a LUKS2 container).

For `lvm-on-luks`, `passphrase_source` selects how the encryption passphrase
is established:

- **`keyfile`** — read from a local path or an http(s) URL (same TLS rule as
  the answer file). The URL is fetched verbatim: there is no `${...}`
  templating (the config is data, not a program). For per-machine keyfiles,
  let each machine fetch its own answer file via `auto:` discovery and put the
  concrete keyfile URL in it. This is a fully unattended install.
- **`prompt-on-first-boot`** (the default) — nothing secret in the answer
  file. The installer formats LUKS with a random throwaway key and embeds it
  in the initramfs so the **first** boot unlocks unattended; a one-shot
  service then prompts the operator for the real passphrase (on the console,
  including serial), adds it, removes the throwaway key, rebuilds the
  initramfs, and reboots. Every subsequent boot prompts normally. This suits
  image-now / set-the-passphrase-on-deployment workflows. The tradeoff: the
  random throwaway key sits in the unencrypted `/boot` initramfs until that
  first boot completes the rekey.
- **`tpm2`** — reserved in the schema, not yet implemented (the driver aborts
  with a clear error).

Either way, the *installed* system prompts for the passphrase at every boot —
on the serial console when a `kernel.serial_console` is set (see
[serial-console-and-luks.md](serial-console-and-luks.md)).

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
have logical volumes and vice versa; logical-volume names are unique within
each VG; an `esp` partition must be `vfat` at `/boot/efi`, and a `/boot/efi`
partition must carry the `esp` flag. Filesystems: `ext4`/`ext3`/`ext2`,
`xfs`, `btrfs`, `vfat`, `f2fs`, `swap`. Software RAID is not in custom
layouts yet. (One firmware rule can only be checked at install time, not by
the schema: an `esp` partition on a BIOS machine, or a `bios_grub` partition
on a UEFI machine, is rejected by the engine before any partition is created.)

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
an `id` (the 802.1Q tag) and a `link` (the parent ethernet/wifi/vlan id).

Each interface takes: `dhcp4`/`dhcp6` (default false), `addresses` (a list of
`IP/prefix`, IPv4 and/or IPv6), a default gateway, `nameservers`
(`addresses` + `search`), and `routes` (`to` is `default` or a CIDR, `via` is
the next hop, optional `metric`). Per family: DHCP → NM `auto`; a static
address → `manual`; neither → IPv4 `disabled` / IPv6 `link-local`. So a NIC
with only an IPv4 address gets no global IPv6, and `dhcp6: true` selects
SLAAC/DHCPv6 (`auto`). Validation rejects addresses without a prefix length,
gateways of the wrong family or with no matching address, routes whose `via`
family disagrees with `to`, VLAN ids outside 0..4094, VLAN links that do not
name a defined interface (or that point at themselves), and an interface id
reused across `ethernets`/`wifis`/`vlans`. Bonds and bridges are not
modelled yet.

**Default gateway — prefer `routes`.** netplan deprecated `gateway4`/`gateway6`
in 2022 in favour of an explicit default route, so use that form as the
canonical one:

```yaml
      routes:
        - {to: default, via: 192.168.50.1}      # IPv4 default
        - {to: default, via: "2001:db8:50::1"}  # IPv6 default
```

`gateway4`/`gateway6` are still accepted as a convenience for configs carried
over from older netplan, and render identically, but new answer files should
use `routes`.

**Binding is your responsibility.** Each connection should select exactly one
device — bind by `match.macaddress` (best for a mixed fleet) or a unique
interface name. If a `match` is loose enough to apply to several NICs, or two
connections claim the same interface name, which one NetworkManager activates
is undefined. The installer does not set `autoconnect-priority`, so don't rely
on ordering to disambiguate — make each match specific.

#### wifi

**wifis** is a map of interface id → config, taking all the same IP keys as an
ethernet (static/DHCP, dual-stack, gateways, DNS, routes, `match`) plus an
`access-points` map of SSID → access point. Each access point has an optional
`password` (omit for an open network) and an optional `hidden: true` for a
non-broadcast SSID:

```yaml
  wifis:
    wlan0:
      dhcp4: true
      access-points:
        "Corp-WPA":
          password: "a-wpa2-passphrase"     # 8..63 chars, or a 64-hex PSK
        "Guest":
          hidden: true                       # open, non-broadcast
```

Each access point becomes its own NetworkManager wifi connection (so a device
with several APs roams between them); the connection keyfile carries the PSK in
cleartext, which is why every keyfile is written `0600`. A password becomes
WPA-PSK; no password renders an open network. **Note:** wifi cannot be
exercised end-to-end in CI — QEMU does not emulate 802.11 — so it is validated
by schema and keyfile-renderer unit tests, not an integration scenario.
WPA-Enterprise (EAP) is not modelled yet.

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
`/etc/apt/sources.list.d/<name>.list` (override that basename with
`filename:`), and the key to `/etc/apt/trusted.gpg.d/<name>.asc` (inline
`key` or `key_url`) or `<name>.gpg` (`keyid`). The key filename always uses
the source `<name>`; `filename:` only renames the `.list`. `key_url` must
use HTTPS. **Quote `keyid` values** — an unquoted `0x…` is parsed as a YAML
integer.

Repo config is deliberately apt-specific: cloud-init never unified
apt/yum/zypper, and here it is executed by a swappable package backend
(`pkgbackend.py`) rather than hardcoded into the driver, leaving room for
dnf/zypper later. The agnostic `packages:`/`package_remove:` lists stay
top-level and are dispatched through whichever backend is selected.

### proxy

A system-wide http(s) proxy for corporate networks:

```yaml
proxy: http://proxy.corp.example:3128
```

It is written to `/etc/apt/apt.conf.d/00proxy` **before** packages are
installed, so the install's own apt fetches go through it, and to
`/etc/environment` (`http_proxy`/`https_proxy` + upper-case) for every other
TLS client on the installed system. The URL must be an `http://`/`https://`
URL with no quotes or whitespace. (Note: this does not affect the installer's
own answer-file/keyfile fetch, which happens before the proxy is known.)

### ca_certs

Add CA certificates to the **system** trust store (`/etc/ssl/certs`, consulted
by apt, curl, and TLS clients) — for a corporate MITM-proxy CA or an internal
PKI root. Mirrors cloud-init's `ca_certs:`:

```yaml
ca_certs:
  remove_defaults: false        # almost always false; see the warning below
  trusted:
    - |
      -----BEGIN CERTIFICATE-----
      MIID...corporate root CA...
      -----END CERTIFICATE-----
```

Each `trusted` entry is an inline PEM certificate, written to
`/usr/local/share/ca-certificates/li-ca-N.crt` and merged into the trust store
with `update-ca-certificates` (run **before** packages install, so a private
mirror's CA is trusted in time). Validation **rejects a private key** in
`trusted` (a trust store holds public certs only) and non-PEM junk.

`remove_defaults: true` wipes the bundled (Mozilla) trust and keeps only your
`trusted` certs — dangerous (it breaks apt-over-HTTPS and most TLS unless you
provide replacements), so it is rejected with an empty `trusted`.

This is the **system** trust store only. It is deliberately separate from any
future per-connection 802.1X/EAP trust (which lives in a NetworkManager
profile and does not consult the system store).

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

## Schema versioning policy

The answer file declares `version: 1`. The policy for evolving it (chosen to
match the closest analogues — Ubuntu autoinstall, whose `version: 1` this
mirrors, and cloud-init, which parses network-config `version: 1` and `2`
side by side):

- **Additive changes never bump the version.** New optional keys and sections
  are added under `version: 1`. An older answer file keeps validating, since
  the new keys default to off. This is the common case.
- **A breaking change mints `version: 2`, and `version: 1` stays supported.**
  If a field is ever renamed, restructured, or removed, that goes in a new
  `version: 2` shape; the parser keeps a `version: 1` model and routes old
  files to it. Both are accepted for an extended overlap, so no existing
  answer file breaks on upgrade.
- A removed-in-`v1` field would first be accepted with a deprecation warning
  for a release before it moves to `v2`-only.

In short: grow `v1` additively, and only fork to `v2` for genuinely breaking
changes while continuing to parse `v1`. (If breaking changes ever become
frequent, the next step would be an Ignition-style forward-translation tool
that migrates an old file up to the current version automatically.)

## Limitations (v1)

- Custom partition layouts cover explicit partitions, custom LVM, and
  btrfs subvolumes; software RAID is not supported yet.
- `network:` covers static IPv4/IPv6, gateways, DNS, routes, 802.1Q VLANs,
  and wifi (WPA-PSK / open) (netplan v2 subset); WPA-Enterprise, bonds, and
  bridges are not modelled yet. Wifi is unit-tested only (no 802.11 in CI).
- Config delivery is local file, http(s) URL, NFS, or TFTP, plus `auto`
  identity-based discovery; DNS-SRV discovery is not supported.
- The config is data, not a program — no conditionals, loops, or
  templating. Generate the YAML beforehand if you need that.
