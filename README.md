# live-installer

The installer used by Linux Mint and LMDE. It runs as a GTK wizard for
interactive desktop installs, and can also run unattended from a YAML answer
file, for labs, fleets and OEM or imaging pipelines.

An answer file covers disk selection and custom partition layouts (LVM,
btrfs subvolumes, software RAID, LUKS with an optional passphrase prompt at
first boot), static networking (IPv4/IPv6, VLANs, wifi, 802.1X/EAP), apt
repositories, a custom CA trust store, an http(s) proxy, proprietary drivers,
flatpak apps, Timeshift snapshots and multi-layout keyboards. The answer
file can be a local file, or fetched over http(s), NFS or TFTP for PXE/netboot
installs.

## Layout

```
usr/bin/                       entry points (live-installer, oem-config, ...)
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

Without an answer file, the interactive GUI runs as before.

- [docs/getting-started.md](docs/getting-started.md): a walkthrough for
  trying it on a VM or a spare machine.
- [docs/automated-install.md](docs/automated-install.md): the reference,
  with every answer-file key, the delivery options, examples and security
  notes.
- [docs/comparison.md](docs/comparison.md): how it compares with kickstart,
  preseed and autoinstall.
- [docs/serial-console-and-luks.md](docs/serial-console-and-luks.md): how
  serial-console provisioning and encrypted (LUKS) boot work, and the
  live-system quirks they work around.
- [docs/pxe-netboot.md](docs/pxe-netboot.md): running the installer from a
  live system booted over the network (PXE/iPXE).

## Development & testing

Unit tests need only Python; integration tests perform real installs in a
VM. [tests/TESTING.md](tests/TESTING.md) covers how the suite is built, how
to run and extend it, and the known pitfalls.

```sh
# unit tests
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements-test.txt
.venv/bin/pytest tests/unit
```

## License

GPL-2+. See [COPYING](COPYING) and `debian/copyright`.
