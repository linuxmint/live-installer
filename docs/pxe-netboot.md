# Network (PXE / iPXE) installation

The unattended installer can run from a fully network-booted live system,
with no install media attached. This document covers how that works, the
boot command line, and the several non-obvious requirements a netboot
image has to meet that a normal desktop ISO does not. It is the companion
to [automated-install.md](automated-install.md) (the answer-file
reference) and [serial-console-and-luks.md](serial-console-and-luks.md).

Most of "PXE support" is infrastructure (DHCP, TFTP, iPXE, live-boot), not
installer code. `live-installer` runs after the live system is already up.
But netboot exposes a handful of requirements in the installer and in the
image that are easy to miss, because the normal CD/USB boot path hides
them. Those are the focus here.

- [What is delivered over the network](#what-is-delivered-over-the-network)
- [The boot command line](#the-boot-command-line)
- [Netboot image requirements](#netboot-image-requirements)
- [How the installer handles netboot](#how-the-installer-handles-netboot)
- [Per-machine configuration](#per-machine-configuration)
- [How it is tested](#how-it-is-tested)
- [Traps, in the order they bite](#traps-in-the-order-they-bite)

## What is delivered over the network

- The bootloader (iPXE) over TFTP — handed out by DHCP (next-server +
  boot filename), or chainloaded over HTTP.
- The kernel and initrd over TFTP (or HTTP, if the iPXE build has it).
- The root filesystem (squashfs) over HTTP, fetched by live-boot.
- The answer file (and any LUKS keyfiles) over HTTP(S), fetched by the
  installer from `live-installer.auto=`.

## The boot command line

The iPXE script boots the kernel/initrd and sets a command line like:

```
boot=live components ip=dhcp \
  fetch=http://server/live/filesystem.squashfs \
  live-installer.auto=http://server/install.yaml live-installer.auto-insecure
```

Three parts matter for netboot specifically:

- **`ip=dhcp` is required.** live-boot's `fetch=` runs in the initramfs,
  whose network stack is separate from iPXE's — iPXE's DHCP lease does not
  carry over. Without `ip=dhcp` the initramfs never brings up networking
  and the boot dies with `Unable to find a live file system on the
  network`.
- **`fetch=` takes a single squashfs URL.** live-boot word-splits the
  value on whitespace, and a kernel command-line value cannot contain
  spaces, so there is no way to pass two squashfs images. The dev-overlay
  trick that works on CD/USB (a second `*.squashfs` that live-boot unions
  in) does not work over PXE. See [a single squashfs](#1-a-single-squashfs-with-the-installer-baked-in).
- **`live-installer.auto=`** triggers the unattended install as usual.
  Add `live-installer.auto-insecure` for plain HTTP (the answer file
  carries a password hash; HTTPS is preferred — see automated-install.md).

## Netboot image requirements

A desktop ISO is built for CD/USB boot and does not meet these. A netboot
image has to.

### 1. A single squashfs with the installer baked in

Because `fetch=` is one URL (above), the installer's code has to be inside
the one squashfs you serve, not in a separate overlay. Build a combined
image: unpack the base `filesystem.squashfs`, lay the installer files on
top, and repack. The integration harness does exactly this in
`isotools.build_combined_squashfs()` (rootless, via
`mksquashfs -all-root`).

### 2. The rootfs must carry the kernel, initrd, and package manifests

On CD/USB the kernel, initrd, and the live-package manifests sit next to
the squashfs on the medium (`/run/live/medium/live/`). The installer reads
them there to seed `/target/boot` and to know which live-only packages to
remove from the installed system.

A netboot medium has only the fetched squashfs — none of those other
files. So the installer instead looks for them in the rootfs at:

```
/usr/lib/live-installer/netboot-live/
    vmlinuz
    initrd.img
    filesystem.packages-remove
    pool/main/          # the signed EFI bootloader .debs, for UEFI installs
```

A netboot image must place them there. If it does not, the install aborts
copying the kernel (`No such file or directory`), reading the removal
manifest, or (on UEFI) installing the signed bootloader (`Missing EFI
package`). See `installer.py` (`self.live_files`, `self.pool`) and
`isotools.build_combined_squashfs()` (which stages them).

### 3. A NetworkManager-based live system (for DNS)

The boot NIC is configured by the initramfs (`ip=dhcp`). NetworkManager
leaves an initramfs-configured interface **unmanaged**, so it never runs
its own DHCP and never writes `/etc/resolv.conf` — the live image's
placeholder (seen as `nameserver dhcp`) survives, and any package install
fails to resolve. On CD/USB this never happens because NetworkManager
manages the NIC from the start.

The installer repairs this itself (see below), so no operator action is
needed — but it does assume a NetworkManager-based live system. A server
image without NetworkManager would need its own way to get DNS into the
live session.

## How the installer handles netboot

Two pieces of the installer are netboot-aware. Both are no-ops on CD/USB.

- **`installer.py` — `self.live_files`.** The squashfs is still read from
  the medium (live-boot fetches it to `/run/live/medium/live/`), but the
  kernel, initrd, and manifests are read from the rootfs bundle in
  requirement 2 when they are absent from the medium. Detected by the
  absence of `vmlinuz` on the medium.
- **`auto_installer.py` — `_ensure_dns()`.** Before any network step, if
  `/etc/resolv.conf` has no real nameserver, it forces NetworkManager to do
  a real DHCP on the boot NIC. A plain activation makes NetworkManager
  *assume* the initramfs IP without doing DHCP, so it never learns DNS;
  the fix is to disconnect and reconnect the device, then read the
  nameservers NetworkManager learned (`nmcli IP4.DNS` / the DHCP4 options)
  and write `/etc/resolv.conf` directly, since NetworkManager's rc-manager
  may not own that file.

## Per-machine configuration

Templating one PXE entry per machine does not scale. Point every machine
at the same base and let the installer find its own answer file from its
identity:

```
live-installer.auto=auto:https://cfg.example.com/configs/
```

See [auto-discovery](automated-install.md#auto-discovery-for-netboot) for
the lookup order (by MAC, then SMBIOS serial/UUID, then a fleet-wide
default).

## How it is tested

The `pxe-simple` (BIOS) and `pxe-uefi-simple` (UEFI) integration scenarios
perform real PXE installs end to end with no media, using QEMU's user-mode
network: its built-in TFTP/BOOTP server boots the kernel/initrd, live-boot
fetches the combined squashfs over the harness HTTP server, and the
install runs and is verified over SSH on the booted system. No privileged
host networking and no real DHCP/TFTP daemons are involved, so they run on
a standard GitHub hosted runner (with KVM). See
[../tests/TESTING.md](../tests/TESTING.md) and the `netboot:` scenario
knob.

### BIOS vs UEFI

The two differ only at the front of the boot chain:

- **BIOS** — the NIC's iPXE option ROM (shipped with QEMU) runs the
  `boot.ipxe` script directly.
- **UEFI** — OVMF cannot run a raw script, so it PXE-loads an EFI binary.
  The harness builds `ipxe.efi` once (in `vm-setup`, cached) with an
  embedded script that `dhcp`s and chainloads `boot.ipxe` over TFTP, so the
  EFI binary stays static while the boot script stays per-run. OVMF honours
  `bootindex` rather than the legacy boot order, so the NIC carries one.
  The UEFI install also pulls the signed bootloader packages
  (`grub-efi-amd64`, `shim-signed`, …) from the pool, which the netboot
  bundle carries alongside the kernel (requirement 2).

From the chainload onward the two paths are identical.

## Traps, in the order they bite

Getting a PXE install green meant clearing these one by one. They are
listed so the next person does not rediscover them:

1. **No `ip=dhcp`** → the initramfs has no network → `Unable to find a
   live file system on the network`.
2. **Multiple squashfs in `fetch=`** → live-boot treats the
   comma-joined URLs as one bad URL → same failure. Use one combined
   squashfs.
3. **Rootless `unsquashfs`** → cannot write `security.*` xattrs
   (`-no-xattrs`) and cannot `mknod` device nodes (returns rc=2, which is
   safe to tolerate — devtmpfs recreates `/dev` at boot).
4. **Kernel/initrd/manifest not on the netboot medium** → the engine's
   medium reads fail; carry them in the rootfs (requirement 2).
5. **No DNS in the live system** → NetworkManager leaves the
   initramfs-configured NIC unmanaged; force a real DHCP (requirement 3 /
   `_ensure_dns`).
6. **UEFI: signed bootloader packages not on the medium** → the
   `grub-efi-amd64` / `shim-signed` `.deb`s come from `/pool`; carry them in
   the bundle (requirement 2). UEFI also needs an EFI boot binary
   (`ipxe.efi`) and a NIC `bootindex`, since OVMF cannot run a raw script.

### Not yet covered: IPv6

Both BIOS and UEFI PXE are tested over IPv4. IPv6 netboot is not yet
covered: the initramfs networking (`ip=dhcp`) is IPv4-only, so an IPv6
`fetch=` can fail in the initramfs even though the running installer
resolves IPv6 fine. Treat IPv6 PXE boot as unproven until the initramfs
IPv6 bring-up has been watched working. Answer-file and keyfile fetching
over IPv6 (http/https/nfs) in the running installer is supported.
