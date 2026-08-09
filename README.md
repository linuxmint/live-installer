# live-installer

The installer used by Linux Mint and LMDE. It runs as a GTK wizard for
interactive desktop installs, and can also run **unattended** from a YAML
answer file for labs, fleets, and OEM/imaging pipelines — covering disk
selection and custom partition layouts (LVM, btrfs subvolumes, software RAID,
LUKS with an optional first-boot passphrase prompt), static networking
(IPv4/IPv6, VLANs, wifi, 802.1X/EAP), apt repositories, a custom CA trust
store, an http(s) proxy, proprietary drivers, flatpak apps, Timeshift
snapshots, multi-layout keyboards, and PXE/netboot delivery (local file,
http(s), NFS, or TFTP).

## Layout

```
usr/bin/                       entry points (live-installer, oem-config, …)
usr/lib/live-installer/        the installer
  main.py                      GTK wizard (interactive front-end)
  installer.py                 install engine (GUI-agnostic)
  partitioning.py              disk / partition handling
  auto_installer.py            unattended front-end (answer-file driven)
  schema.py                    answer-file schema + validation
  diskmatch.py                 stable-attribute disk selection
  discovery.py                 netboot answer-file auto-discovery (mac/serial/uuid)
  netconfig.py                 network: section -> NetworkManager keyfiles
  pkgbackend.py                package backend (apt today; swappable)
  catrust.py                   ca_certs: section -> system trust store
  snapshotbackend.py           snapshots: section -> Timeshift (btrfs)
  commandrunner.py             single shell-out boundary (logging, secrets)
docs/automated-install.md      unattended-install user guide
tests/                         unit + integration tests, and TESTING.md
```

## Unattended installation

Boot the live medium with an answer file on the kernel command line:

```
live-installer.auto=/cdrom/install.yaml
```

New to it? **[docs/getting-started.md](docs/getting-started.md)** is a
hands-on walkthrough for trying it on a VM or spare machine. For the full
reference — every answer-file key, delivery options, examples, and security
notes — see **[docs/automated-install.md](docs/automated-install.md)**.
A feature comparison against kickstart, preseed, and autoinstall is in
**[docs/comparison.md](docs/comparison.md)**. The interactive GUI is
unchanged when no answer file is supplied.

For the mechanics of serial-console provisioning and encrypted (LUKS)
boot — and the live-system quirks they work around — see
**[docs/serial-console-and-luks.md](docs/serial-console-and-luks.md)**.
For running the installer from a fully network-booted (PXE/iPXE) live
system, see **[docs/pxe-netboot.md](docs/pxe-netboot.md)**.

## Development & testing

Unit tests need only Python; integration tests perform real installs in a
VM. See **[tests/TESTING.md](tests/TESTING.md)** for the architecture, how
to run and extend the suite, and the known traps.

```sh
# unit tests
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements-test.txt
.venv/bin/pytest tests/unit
```

## License

GPL-2+. See [COPYING](COPYING) and `debian/copyright`.
