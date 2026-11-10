# Automated installation

live-installer can install without anyone at the keyboard, from a YAML
answer file. The answer file holds what the GUI would otherwise ask for,
and the installation itself is done by the same engine (`installer.py`).

## Quick start

1. Write an answer file, `auto-install.yaml`:

   ```yaml
   version: 1
   locale: en_US.UTF-8
   timezone: America/Toronto
   hostname: office-01
   users:
     - name: admin
       gecos: Office Admin
       passwd: "$6$..."          # openssl passwd -6
   storage:
     target:
       match:
         first-non-removable: true
   ```

2. Put it at the root of the install media.

3. Boot the live media with `live-installer.auto=auto` added to the
   kernel command line. The installation starts on its own and prints
   `Automated installation complete` (or `Automated installation FAILED`)
   on the console when it is done. It does not reboot by itself, so the
   machine does not boot the install media again and reinstall.

## Starting an installation

The installation starts at boot when the kernel command line has
`live-installer.auto=<source>`:

- `live-installer.auto=auto` uses `auto-install.yaml` at the root of the
  install media (`/cdrom` on Linux Mint, `/run/live/medium` on LMDE).
- `live-installer.auto=/path/to/file.yaml` uses that file.

A file on the media on its own never starts an installation; the kernel
argument is required.

The installer can also be run by hand from the live session:

```sh
sudo python3 -OO /usr/lib/live-installer/auto_installer.py --config answers.yaml
```

`--check` validates the answer file and exits. `--dry-run` also finds the
target disk, and exits without touching it.

## Answer file

| Key | Required | Meaning |
|---|---|---|
| `version` | yes | Always `1`. |
| `locale` | yes | A UTF-8 locale, `en_US.UTF-8` or `en_US`. |
| `timezone` | yes | An IANA timezone, `America/Toronto`. |
| `users` | yes | One user (see below). |
| `storage` | yes | The target disk and layout (see below). |
| `hostname` | no | Defaults to `mint`. |
| `keyboard` | no | `model` (`pc105`), `layout` (`us`) and `variant` (empty). |
| `late_commands` | no | Shell commands run in the installed system at the end. |

Unknown keys are errors.

### users

```yaml
users:
  - name: admin             # required
    passwd: "$6$..."        # required, a crypt(5) hash
    gecos: Office Admin     # full name, defaults to the user name
    autologin: false
    ecryptfs_home: false
```

The password must be a hash. Generate one with `openssl passwd -6` or
`mkpasswd -m yescrypt`. Plaintext passwords are rejected. As in the GUI,
the user is an administrator.

### storage

```yaml
storage:
  target:
    match:
      by-id: "nvme-Samsung_SSD_980_*"
  layout: luks              # simple (default) or luks
  luks:
    keyfile: luks.key       # relative to the answer file, or absolute
  swapfile: true            # default
  swapfile_size: 8GB        # default: the size the GUI recommends
```

The whole target disk is erased and partitioned like the GUI's "Erase
disk" option. With `layout: luks` the root partition is encrypted with the
passphrase in `keyfile` (its trailing newline is ignored).

#### Selecting the disk

Kernel names like `/dev/sda` change from one boot to the next, so the
disk is selected by stable attributes. All matchers given must agree, and
exactly one disk must match. Otherwise the installation stops and lists
the disks it found. It never guesses.

| Matcher | Example | Matches |
|---|---|---|
| `by-id` | `nvme-Samsung_SSD_980_*` | A name in `/dev/disk/by-id` (glob). |
| `by-path` | `pci-0000:00:17.0-ata-1` | A name in `/dev/disk/by-path` (glob). |
| `model` | `Samsung SSD*` | The disk model (glob). |
| `size-min` | `200GB` | Disks at least this big. |
| `first-non-removable` | `true` | Non-removable disks only. |

To find the names, boot the live media and run `ls -l /dev/disk/by-id
/dev/disk/by-path`. `first-non-removable` suits machines with a single
internal disk; with two, it is ambiguous and the installation stops.

### late_commands

```yaml
late_commands:
  - apt-get update && apt-get install -y vim
  - systemctl enable ssh
```

Each command runs with `/bin/sh -c` inside the installed system (a chroot)
after everything else is done. The installation stops at the first
command that fails. YAML reads a bare `true`, `false` or number as that
type, not a string, so quote such commands.

## Security

The answer file holds the password hash, and the LUKS keyfile holds the
disk passphrase. Anyone who can read the install media can read both.
Treat the media accordingly, and change the passphrase after the
installation if it was shared (`cryptsetup luksChangeKey`).

## Tests

The unit tests cover the answer file, disk selection and the driver.
They run without root, GTK or a live session:

```sh
python3 -m pytest tests
```

They need `pytest`, `python3-yaml`, `python3-pydantic` and `python3-distro`.
