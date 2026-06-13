# How this compares to kickstart, preseed, and autoinstall

This is an honest feature comparison between live-installer's unattended
mode and the three established Linux unattended-install systems. It exists
so anyone deciding whether to use this, or thinking about contributing,
can see exactly what is and is not implemented.

The short version: live-installer's automated mode is a capable base for
the common desktop and workstation case. It is not, and does not try to
be, a kickstart-class enterprise provisioner. The gaps below are real, and
most are well-scoped enough to be good contributions.

The systems compared:

- **kickstart** — Red Hat / Fedora, driven by Anaconda. The most mature,
  ~20 years old. Its config is a custom directive syntax.
- **preseed** — Debian Installer (d-i). debconf key/value answers.
- **autoinstall** — Ubuntu Server, driven by Subiquity. YAML, and the
  closest relative to this design (it embeds cloud-init).
- **live-installer auto** — this project.

Legend: ✅ full, ◐ partial or preset-only, ✗ not supported.

## Format and validation

| Feature | kickstart | preseed | autoinstall | live-installer |
|---|---|---|---|---|
| Config format | custom syntax | debconf k/v | YAML | YAML (cloud-init-flavored) |
| Strict validation | ✅ ksvalidator | ◐ syntax check | ✅ JSON schema | ✅ pydantic |
| Standalone validator CLI | ✅ | ◐ | ◐ built-in | ✅ `--check` (no disk needed) |
| Versioned schema | ✗ | ✗ | ✅ | ✅ |
| Dump config from a manual install | ✅ anaconda-ks.cfg | ✗ | ✅ | ✗ |

## Storage

| Feature | kickstart | preseed | autoinstall | live-installer |
|---|---|---|---|---|
| Disk select by stable id | ✅ | ◐ awkward | ✅ | ✅ |
| Refuses ambiguous disk match | ◐ | ✗ | ◐ | ✅ |
| Layout presets | ✅ autopart | ◐ recipes | ✅ | ✅ (3) |
| Fully custom partitioning | ✅ | ✅ | ✅ | ✗ |
| LVM | ✅ | ✅ | ✅ | ✅ |
| Software RAID | ✅ | ✅ | ✅ | ✗ |
| btrfs / zfs | ✅ / ◐ | ◐ / ✗ | ✅ / ✅ | ✗ |
| LUKS encryption | ✅ | ✅ | ✅ | ✅ |
| Passwordless unlock (TPM2 / NBDE) | ✅ | ✗ | ◐ | ✗ (schema reserves tpm2) |
| LUKS unlock prompt on serial | ◐ | ◐ | ◐ | ✅ |
| Enterprise storage (iSCSI/FCoE/multipath) | ✅ | ◐ | ◐ | ✗ |

## Network

| Feature | kickstart | preseed | autoinstall | live-installer |
|---|---|---|---|---|
| DHCP | ✅ | ✅ | ✅ | ✅ |
| Static IP / DNS / gateway | ✅ | ✅ | ✅ | ✗ |
| IPv6 | ✅ | ✅ | ✅ | ✗ |
| VLAN / bonding / bridges | ✅ | ◐ | ✅ (netplan) | ✗ |
| Hostname | ✅ | ✅ | ✅ | ✅ |

## Users, packages, scripting

| Feature | kickstart | preseed | autoinstall | live-installer |
|---|---|---|---|---|
| Users with hashed passwords | ✅ | ✅ | ✅ | ✅ |
| Reject plaintext passwords | ◐ | ✗ | ◐ | ✅ |
| SSH authorized keys | ✅ | ◐ | ✅ | ✅ |
| Multiple users | ✅ | ◐ | ◐ +cloud-init | ✅ |
| Package install list | ✅ | ✅ | ✅ | ✅ |
| Package groups / environments | ✅ | ◐ tasks | ◐ | ✗ |
| Declarative package removal | ✅ | ◐ | ◐ | ✅ |
| Add third-party repositories | ✅ | ◐ | ✅ | ✅ |
| Pre-install scripts (%pre) | ✅ | ✅ | ✅ | ✗ |
| Post-install scripts | ✅ | ✅ | ✅ | ✅ (late_commands, in target) |
| First-boot scripts | ◐ | ✗ | ✅ cloud-init | ✗ |

## Services, security, and system config

| Feature | kickstart | preseed | autoinstall | live-installer |
|---|---|---|---|---|
| Enable / disable services | ✅ | ◐ | ◐ | ✗ |
| Firewall config | ✅ | ✗ | ◐ | ✗ |
| SELinux / AppArmor config | ✅ | ✗ | ◐ | ✗ |
| Locale / timezone / keyboard | ✅ | ✅ | ✅ | ✅ |
| Kernel cmdline / serial console | ✅ | ◐ | ◐ | ✅ |
| OEM / first-boot user setup | ◐ | ✗ | ✗ | ✅ |
| Security hardening add-on (OSCAP) | ✅ | ✗ | ✗ | ✗ |

## Delivery

| Feature | kickstart | preseed | autoinstall | live-installer |
|---|---|---|---|---|
| Local file on media | ✅ | ✅ | ✅ | ✅ |
| HTTP(S) URL | ✅ | ✅ | ✅ | ✅ (HTTPS default, HTTP opt-in) |
| NFS | ✅ | ✅ | ◐ | ✅ (cleartext, opt-in) |
| Kernel cmdline trigger | ✅ | ✅ | ✅ | ✅ |
| PXE / netboot install (no media) | ✅ | ✅ | ✅ | ◐ (BIOS, tested in CI; UEFI/IPv6 not yet) |
| Per-machine by serial | ✅ | ◐ | ◐ | ✅ (auto-discovery by mac/serial/uuid) |
| Includes / file composition | ✅ %include | ✅ | ✗ | ✗ |
| Provisioning-server integration (Cobbler/MAAS/Foreman) | ✅ | ✅ | ✅ | ✗ |

## Gaps, as possible contributions

Roughly in order of value for a desktop/workstation fleet, which is what
this targets:

1. **Static networking.** Only hostname plus DHCP today. The natural design
   is to embed netplan under `network:`, the way autoinstall does, since
   NetworkManager can render netplan. Biggest single gap.
2. **Services enable/disable.** A small `services:` section. Easy, high
   value (e.g. enable ssh, disable a default daemon).
3. **Package groups / metapackages.** Today it is a flat list; Mint has
   meta-packages that would be convenient to name.
4. **Custom partition layouts.** The hardest one. Would mean exposing more
   of the engine's partitioning, and touches the GTK-coupled partition
   code. Big.
5. **TPM2 / NBDE (Clevis-Tang) LUKS unlock.** For passwordless encrypted
   boot at scale. The schema already reserves `tpm2`.
6. **A `%pre`-style hook.** Run logic before partitioning (e.g. choose a
   layout from disk size).
7. **Firewall / SELinux-AppArmor config**, if the fleet needs it.
8. **Mint (casper) integration testing.** The harness assumes Debian-live;
   a casper variant is needed before Mint itself (not just LMDE) is
   covered end to end. See [serial-console-and-luks.md](serial-console-and-luks.md)
   and the testing guide.

Where this design already does well: stable-attribute disk selection that
refuses to guess, plaintext-password rejection, explicit per-failure
policy, a serial-console LUKS unlock, strict schema validation, and a
turnkey integration test suite. Those are not universal among the systems
above.
