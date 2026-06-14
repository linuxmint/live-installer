#!/usr/bin/python3
# coding: utf-8
"""Answer-file schema for unattended installation (version 1).

Parses and strictly validates the YAML answer file that drives a
headless install.  Design rules:

  1. Raw device paths (/dev/sdX) are rejected — disks are selected by
     stable match expressions only (by-id, by-path, model, size-min,
     first-non-removable).
  2. Every failure mode has an explicit abort/continue policy, and the
     defaults fail closed (abort).
  3. The format is versioned from day one (`version: 1` is required).
  4. Passwords must be pre-hashed in crypt(5) format; plaintext is
     rejected outright, with no override.
  5. The config is data, not a program: unknown keys are errors and
     nothing is interpolated or templated.

Where this overlaps with cloud-init / Ubuntu autoinstall, it uses the
same key names and structure (top-level hostname/locale/timezone; users
with name/gecos/passwd/groups/ssh_authorized_keys; a flat packages
install list). Installer-only concerns that cloud-init has no
equivalent for (disk selection, partition layout, LUKS, kernel cmdline,
failure policy) keep their own shapes. Notable divergences:
  - `package_remove` is an extension: cloud-init has no declarative
    package removal.
  - `apt` mirrors cloud-init's `apt:` section (the `sources:` map, each with
    `source`/`key`/`keyid`/`keyserver`, plus a `key_url` extension). Repo
    config is deliberately backend-specific — cloud-init never unified
    apt/yum/zypper — and is executed by a swappable package backend
    (pkgbackend.py), not hardcoded into the driver.
  - `late_commands` runs in the target during install (in the chroot),
    matching Ubuntu autoinstall's `late-commands` and kickstart `%post`.
    This is deliberately NOT cloud-init's `runcmd`, which runs on first
    boot — a different lifecycle, so it gets a different name. `runcmd`
    is left unused, reserved for true first-boot semantics later.
  - `network` is a subset of netplan's v2 schema (the same shapes cloud-init
    uses for Network Config v2), but it is rendered to NetworkManager
    keyfiles by netconfig.py rather than via the netplan binary, which
    LMDE/Debian does not ship.

Strict validation deliberately defangs YAML's type-coercion footguns:
anything that does not parse cleanly into the declared types is an
error, never a guess.
"""

import ipaddress
import re
import urllib.parse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA_VERSION = 1

# crypt(5) hash prefixes: $6$ sha512crypt, $5$ sha256crypt, $y$/$7$
# yescrypt, $2a/2b/2y$ bcrypt
_CRYPT_RE = re.compile(r"^\$(6|5|y|7|2[aby])\$\S+$")
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_HOSTNAME_LABEL_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?(\.[A-Za-z0-9-]+)?$")
_SIZE_RE = re.compile(r"^\d+(\.\d+)?\s*[MGT]B$")
_MOUNT_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")
_FILESYSTEMS = ("ext4", "ext3", "ext2", "xfs", "btrfs", "vfat", "swap", "f2fs")
_PART_FLAGS = ("esp", "bios_grub", "swap")
_LV_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")
_SUBVOL_RE = re.compile(r"^[A-Za-z0-9@._-][A-Za-z0-9@._/-]*$")
# Linux interface names: up to 15 chars, no '/' or whitespace.
_IFNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,14}$")
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
# apt source-list filename component (no path separators, .list added by us).
_APT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# A GPG long key id / fingerprint: 8..40 hex digits (optional 0x prefix).
_KEYID_RE = re.compile(r"^(0x)?[0-9A-Fa-f]{8,40}$")


def _check_cidr(value):
    if "/" not in value:
        raise ValueError(
            f"{value!r} must include a prefix length (e.g. 192.168.1.10/24)"
        )
    try:
        ipaddress.ip_interface(value)
    except ValueError:
        raise ValueError(
            f"{value!r} is not a valid IP address with prefix "
            "(e.g. 192.168.1.10/24 or 2001:db8::5/64)"
        )
    return value


def _check_ip(value, version=None):
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(f"{value!r} is not a valid IP address")
    if version is not None and addr.version != version:
        raise ValueError(f"{value!r} is not an IPv{version} address")
    return value


class ConfigError(Exception):
    """Raised when an answer file is malformed or fails validation."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


_XKB_LAYOUT_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_XKB_VARIANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_XKB_TOGGLE_RE = re.compile(r"^[a-z0-9_]+:[a-z0-9_]+$")


def _check_xkb_layout(value):
    if not _XKB_LAYOUT_RE.match(value):
        raise ValueError(f"{value!r} is not a valid keyboard layout code")
    return value


def _check_xkb_variant(value):
    if value and not _XKB_VARIANT_RE.match(value):
        raise ValueError(f"{value!r} is not a valid keyboard variant")
    return value


class KbLayout(_StrictModel):
    """An additional keyboard layout (XKB layout + optional variant)."""

    layout: str
    variant: str = ""

    _v_layout = field_validator("layout")(_check_xkb_layout)
    _v_variant = field_validator("variant")(_check_xkb_variant)


class Keyboard(_StrictModel):
    model: str = "pc105"
    layout: str = "us"
    variant: str = ""
    # Extra layouts to switch between (en_CA + fr_CA, etc.); `layout`/`variant`
    # is the primary/first. `toggle` is the XKB switch option, e.g.
    # grp:alt_shift_toggle — only meaningful with at least one extra layout.
    additional_layouts: list[KbLayout] = Field(default_factory=list)
    toggle: str = None

    _v_layout = field_validator("layout")(_check_xkb_layout)
    _v_variant = field_validator("variant")(_check_xkb_variant)

    @field_validator("toggle")
    @classmethod
    def _v_toggle(cls, value):
        if value is not None and not _XKB_TOGGLE_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid XKB toggle option (e.g. "
                "grp:alt_shift_toggle)")
        return value

    @model_validator(mode="after")
    def _consistency(self):
        if self.toggle and not self.additional_layouts:
            raise ValueError(
                "keyboard.toggle only applies when additional_layouts are set")
        return self


class User(_StrictModel):
    # cloud-init key names: name, gecos, passwd, groups, ssh_authorized_keys
    name: str
    passwd: str
    gecos: str = ""
    groups: list[str] = Field(default_factory=list)
    ssh_authorized_keys: list[str] = Field(default_factory=list)
    # extensions (no cloud-init equivalent)
    autologin: bool = False
    ecryptfs_home: bool = False

    @field_validator("name")
    @classmethod
    def _check_name(cls, value):
        if not _USERNAME_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid username (lowercase, must start "
                "with a letter or underscore, max 32 chars)"
            )
        return value

    @field_validator("passwd")
    @classmethod
    def _check_crypted(cls, value):
        if not _CRYPT_RE.match(value):
            raise ValueError(
                "passwd must be a crypt(5) hash (e.g. sha512crypt starting "
                "with $6$). Plaintext passwords are not accepted; generate a "
                "hash with: openssl passwd -6"
            )
        return value

    @field_validator("groups")
    @classmethod
    def _check_groups(cls, value):
        for group in value:
            if not _GROUP_RE.match(group):
                raise ValueError(f"{group!r} is not a valid group name")
        return value

    @field_validator("ssh_authorized_keys")
    @classmethod
    def _check_ssh_keys(cls, value):
        for key in value:
            if not key.startswith(("ssh-", "ecdsa-", "sk-")):
                raise ValueError(
                    f"{key[:40]!r}... does not look like an OpenSSH public "
                    "key (expected ssh-ed25519/ssh-rsa/ecdsa-.../sk-...)"
                )
        return value

    @property
    def sudo(self):
        """True if this user is granted admin via the sudo group."""
        return "sudo" in self.groups


class DiskMatch(_StrictModel):
    """Stable-attribute disk selector.  Raw /dev paths are rejected."""

    by_id: str = Field(None, alias="by-id")
    by_path: str = Field(None, alias="by-path")
    model: str = None
    size_min: str = Field(None, alias="size-min")
    first_non_removable: bool = Field(False, alias="first-non-removable")

    @field_validator("by_id", "by_path", "model")
    @classmethod
    def _no_raw_device_paths(cls, value):
        if value is not None and value.startswith("/dev/"):
            raise ValueError(
                f"{value!r} looks like a raw device path. Device enumeration "
                "order is not stable; select the disk by stable attributes "
                "instead (by-id, by-path, model, size-min, "
                "first-non-removable)"
            )
        return value

    @field_validator("size_min")
    @classmethod
    def _check_size(cls, value):
        if value is not None and not _SIZE_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid size (expected e.g. 500GB, 1TB)"
            )
        return value

    @model_validator(mode="after")
    def _at_least_one_matcher(self):
        if not any(
            [self.by_id, self.by_path, self.model, self.size_min,
             self.first_non_removable]
        ):
            raise ValueError(
                "storage.target.match must contain at least one matcher "
                "(by-id, by-path, model, size-min, first-non-removable)"
            )
        return self


class StorageTarget(_StrictModel):
    match: DiskMatch
    on_no_match: str = "abort"

    @field_validator("on_no_match")
    @classmethod
    def _check_on_no_match(cls, value):
        if value != "abort":
            raise ValueError(
                f"on_no_match: {value!r} is not supported in schema version "
                f"{SCHEMA_VERSION} (only 'abort')"
            )
        return value


class Luks(_StrictModel):
    passphrase_source: str = "prompt-on-first-boot"
    keyfile: str = None

    @field_validator("passphrase_source")
    @classmethod
    def _check_source(cls, value):
        allowed = ("prompt-on-first-boot", "tpm2", "keyfile")
        if value not in allowed:
            raise ValueError(
                f"passphrase_source must be one of {', '.join(allowed)}"
            )
        return value

    @model_validator(mode="after")
    def _keyfile_consistency(self):
        if self.passphrase_source == "keyfile" and not self.keyfile:
            raise ValueError(
                "passphrase_source: keyfile requires a 'keyfile' path"
            )
        if self.passphrase_source != "keyfile" and self.keyfile:
            raise ValueError(
                "'keyfile' is only valid with passphrase_source: keyfile"
            )
        return self


def _check_size(value):
    if value != "rest" and not _SIZE_RE.match(value):
        raise ValueError(f"size {value!r} must be 'rest' or like 512MB / 40GB / 1TB")
    return value


def _check_filesystem(value):
    if value is not None and value not in _FILESYSTEMS:
        raise ValueError(
            f"filesystem {value!r} is not one of {', '.join(_FILESYSTEMS)}")
    return value


def _check_mount(value):
    if value is not None and value != "swap" and not _MOUNT_RE.match(value):
        raise ValueError(f"mount {value!r} must be an absolute path or 'swap'")
    return value


class Subvolume(_StrictModel):
    """A btrfs subvolume (e.g. @ at /, @home at /home) on a btrfs partition
    or logical volume."""
    name: str
    mount: str

    @field_validator("name")
    @classmethod
    def _v_name(cls, value):
        if not _SUBVOL_RE.match(value):
            raise ValueError(f"{value!r} is not a valid btrfs subvolume name")
        return value

    _v_mount = field_validator("mount")(_check_mount)

    @model_validator(mode="after")
    def _consistency(self):
        if not self.mount or self.mount == "swap":
            raise ValueError("a subvolume needs an absolute mount point")
        return self


class CustomPartition(_StrictModel):
    """One partition in a custom layout. It is either mounted (mount +
    filesystem), a btrfs filesystem split into subvolumes, an LVM physical
    volume (lvm_pv), or a flag-only special partition (e.g. bios_grub)."""
    size: str
    mount: str = None
    filesystem: str = None
    flags: list[str] = Field(default_factory=list)
    lvm_pv: str = None
    subvolumes: list[Subvolume] = Field(default_factory=list)

    _v_size = field_validator("size")(_check_size)
    _v_fs = field_validator("filesystem")(_check_filesystem)
    _v_mount = field_validator("mount")(_check_mount)

    @field_validator("flags")
    @classmethod
    def _v_flags(cls, value):
        for flag in value:
            if flag not in _PART_FLAGS:
                raise ValueError(
                    f"partition flag {flag!r} is not one of "
                    f"{', '.join(_PART_FLAGS)}")
        return value

    @model_validator(mode="after")
    def _consistency(self):
        if self.subvolumes:
            if self.filesystem != "btrfs":
                raise ValueError("subvolumes require filesystem: btrfs")
            if self.mount or self.lvm_pv or "bios_grub" in self.flags:
                raise ValueError(
                    "a partition with subvolumes takes no mount/lvm_pv/"
                    "bios_grub (the subvolumes provide the mounts)")
            return self
        if self.lvm_pv is not None:
            if self.mount or self.filesystem:
                raise ValueError(
                    "an LVM PV partition (lvm_pv) takes no mount/filesystem")
        elif "bios_grub" in self.flags:
            if self.mount or self.filesystem:
                raise ValueError(
                    "a bios_grub partition takes no mount/filesystem")
        elif not self.mount or not self.filesystem:
            raise ValueError(
                "a partition needs both mount and filesystem (or lvm_pv, "
                "subvolumes, or the bios_grub flag)")
        if "esp" in self.flags:
            if self.filesystem != "vfat" or self.mount != "/boot/efi":
                raise ValueError(
                    "an esp partition must be filesystem: vfat mounted at "
                    "/boot/efi")
        elif self.mount == "/boot/efi":
            # The reverse: /boot/efi without the esp flag would create a GPT
            # entry lacking the EFI System Partition type GUID. The OS mounts
            # it fine and GRUB writes to it, so the install looks healthy —
            # but UEFI firmware won't recognise it as an ESP, and the machine
            # fails to boot (often only surfacing on other hardware or after a
            # firmware update). Require the flag so this can't slip through.
            raise ValueError(
                "the /boot/efi partition must carry the 'esp' flag, so its "
                "GPT entry gets the EFI System Partition type GUID that UEFI "
                "firmware boots from")
        if (self.mount == "swap") != (self.filesystem == "swap"):
            raise ValueError("mount: swap and filesystem: swap go together")
        return self


class LvmVolume(_StrictModel):
    """A logical volume in a custom layout, on a VG backed by an lvm_pv
    partition."""
    vg: str
    lv: str
    size: str
    mount: str = None
    filesystem: str = None
    subvolumes: list[Subvolume] = Field(default_factory=list)

    _v_size = field_validator("size")(_check_size)
    _v_fs = field_validator("filesystem")(_check_filesystem)
    _v_mount = field_validator("mount")(_check_mount)

    @field_validator("lv")
    @classmethod
    def _v_lv(cls, value):
        if not _LV_NAME_RE.match(value):
            raise ValueError(f"{value!r} is not a valid logical-volume name")
        return value

    @model_validator(mode="after")
    def _consistency(self):
        if self.subvolumes:
            if self.filesystem != "btrfs":
                raise ValueError("subvolumes require filesystem: btrfs")
            if self.mount:
                raise ValueError(
                    "an LVM volume with subvolumes takes no mount (the "
                    "subvolumes provide the mounts)")
            return self
        if not self.mount or not self.filesystem:
            raise ValueError("an LVM volume needs both mount and filesystem")
        if (self.mount == "swap") != (self.filesystem == "swap"):
            raise ValueError("mount: swap and filesystem: swap go together")
        return self


class Storage(_StrictModel):
    target: StorageTarget
    layout: str = "simple"
    luks: Luks = None
    partitions: list[CustomPartition] = Field(default_factory=list)
    lvm: list[LvmVolume] = Field(default_factory=list)

    @field_validator("layout")
    @classmethod
    def _check_layout(cls, value):
        allowed = ("simple", "lvm", "lvm-on-luks", "custom")
        if value not in allowed:
            raise ValueError(f"layout must be one of {', '.join(allowed)}")
        return value

    @model_validator(mode="after")
    def _consistency(self):
        if self.layout == "lvm-on-luks" and self.luks is None:
            self.luks = Luks()  # fail-safe default: prompt on first boot
        if self.layout != "lvm-on-luks" and self.luks is not None:
            raise ValueError("'luks' is only valid with layout: lvm-on-luks")

        if self.layout != "custom":
            if self.partitions or self.lvm:
                raise ValueError(
                    "'partitions'/'lvm' are only valid with layout: custom")
            return self

        if not self.partitions:
            raise ValueError(
                "layout: custom requires a non-empty 'partitions' list")
        mounts = ([p.mount for p in self.partitions if p.mount]
                  + [sv.mount for p in self.partitions for sv in p.subvolumes]
                  + [v.mount for v in self.lvm if v.mount]
                  + [sv.mount for v in self.lvm for sv in v.subvolumes])
        if mounts.count("/") != 1:
            raise ValueError("custom layout needs exactly one '/' mount point")
        dupes = sorted({m for m in mounts
                        if m != "swap" and mounts.count(m) > 1})
        if dupes:
            raise ValueError(f"duplicate mount point(s): {', '.join(dupes)}")
        if sum(1 for p in self.partitions if p.size == "rest") > 1:
            raise ValueError("at most one partition may use size: rest")

        pv_vgs = {p.lvm_pv for p in self.partitions if p.lvm_pv}
        lv_vgs = {v.vg for v in self.lvm}
        if lv_vgs - pv_vgs:
            raise ValueError(
                "lvm volume(s) reference VG(s) with no lvm_pv partition: "
                + ", ".join(sorted(lv_vgs - pv_vgs)))
        if pv_vgs - lv_vgs:
            raise ValueError(
                "lvm_pv partition(s) reference VG(s) with no logical volumes: "
                + ", ".join(sorted(pv_vgs - lv_vgs)))
        for vg in lv_vgs:
            if sum(1 for v in self.lvm if v.vg == vg and v.size == "rest") > 1:
                raise ValueError(f"at most one LV in VG {vg} may use size: rest")
            names = [v.lv for v in self.lvm if v.vg == vg]
            dup_lvs = sorted({n for n in names if names.count(n) > 1})
            if dup_lvs:
                raise ValueError(
                    f"duplicate logical-volume name(s) in VG {vg}: "
                    + ", ".join(dup_lvs))
        return self


class AptSource(_StrictModel):
    """One entry in cloud-init's `apt.sources` map. Same shapes as cloud-init
    (`source`, `key`, `keyid`, `keyserver`), plus a `key_url` extension and an
    optional `filename` override. The signing key may be given exactly one of:
    `key` (inline ASCII-armored), `keyid` (fetched from `keyserver`), or
    `key_url` (fetched over https). cloud-init keeps repo config per-backend
    (it never unified apt/yum/zypper), so this stays apt-specific by design."""

    source: str                          # sources.list line, e.g.
                                         # "deb https://repo trixie main"
    key: str = None                      # inline ASCII-armored public key
    keyid: str = None                    # key id/fingerprint to fetch
    keyserver: str = "keyserver.ubuntu.com"
    key_url: str = None                  # extension: fetch key over https
    filename: str = None                 # override the .list basename

    @field_validator("source")
    @classmethod
    def _check_source(cls, value):
        if not (value.startswith("deb ") or value.startswith("deb-src ")):
            raise ValueError(
                "apt source must be a sources.list line starting with 'deb ' "
                "or 'deb-src ' (ppa: shorthand is not supported)"
            )
        return value

    @field_validator("key_url")
    @classmethod
    def _https_only(cls, value):
        if value is not None and not value.startswith("https://"):
            raise ValueError(
                "apt source key_url must use https:// — a key fetched over "
                "plain HTTP can be tampered with in transit"
            )
        return value

    @field_validator("keyid")
    @classmethod
    def _check_keyid(cls, value):
        if value is not None and not _KEYID_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid GPG key id (8..40 hex digits)"
            )
        return value

    @field_validator("filename")
    @classmethod
    def _check_filename(cls, value):
        if value is not None and not _APT_NAME_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid filename (letters, digits, '.', "
                "'_', '-'; no path separators)"
            )
        return value

    @model_validator(mode="after")
    def _consistency(self):
        given = [k for k in ("key", "keyid", "key_url")
                 if getattr(self, k) is not None]
        if len(given) > 1:
            raise ValueError(
                "give at most one signing key per source (key, keyid, or "
                f"key_url) — got {', '.join(given)}"
            )
        return self


class Apt(_StrictModel):
    """cloud-init's `apt:` section (the `sources` subset we support)."""

    sources: dict[str, AptSource] = Field(default_factory=dict)

    @field_validator("sources")
    @classmethod
    def _check_names(cls, value):
        for name in value:
            if not _APT_NAME_RE.match(name):
                raise ValueError(
                    f"{name!r} is not a valid apt source name (letters, "
                    "digits, '.', '_', '-')"
                )
        return value


class Kernel(_StrictModel):
    # Appended to GRUB_CMDLINE_LINUX_DEFAULT on the installed system:
    # driver blacklists, sysctl-ish params, etc. for headless/fleet hosts.
    cmdline_extra: str = ""
    # Provision a full serial console on the installed system, e.g.
    # "ttyS0" or "ttyS0,115200". Drops quiet/splash (so the boot, and a
    # LUKS unlock prompt, is visible on serial rather than grabbed by
    # plymouth), adds console= to the kernel cmdline, and points GRUB's
    # terminal at the serial line. The channel an admin uses over IPMI
    # Serial-over-LAN on a headless box.
    serial_console: str = ""

    @field_validator("serial_console")
    @classmethod
    def _check_serial(cls, value):
        if value and not re.match(r"^ttyS\d+(,\d+)?$", value):
            raise ValueError(
                f"{value!r} is not a valid serial console "
                "(expected e.g. ttyS0 or ttyS0,115200)"
            )
        return value


class Oem(_StrictModel):
    enabled: bool = False


class Drivers(_StrictModel):
    """Third-party/restricted driver installation (autoinstall's
    `drivers: {install: true}`)."""

    install: bool = False


class OnFailure(_StrictModel):
    """Per-failure-mode policy.  Everything defaults to abort (fail closed)."""

    partition_mismatch: str = "abort"
    network_unavailable: str = "abort"
    package_install_failure: str = "abort"
    post_install_script_failure: str = "abort"

    @field_validator("*")
    @classmethod
    def _abort_or_continue(cls, value):
        if value not in ("abort", "continue"):
            raise ValueError("failure policy must be 'abort' or 'continue'")
        return value


class CaCerts(_StrictModel):
    """System CA trust store, mirroring cloud-init's `ca_certs:` module:
    inline PEM CA certificates added to /etc/ssl/certs (via the OS's
    update-ca-certificates). This is the SYSTEM trust store consulted by apt,
    curl, and TLS clients — distinct from any per-connection 802.1X/EAP trust."""

    remove_defaults: bool = False
    trusted: list[str] = Field(default_factory=list)

    @field_validator("trusted")
    @classmethod
    def _v_trusted(cls, value):
        for cert in value:
            if "PRIVATE KEY" in cert:
                raise ValueError(
                    "ca_certs.trusted must contain only CA certificates, never "
                    "a private key (a trust store holds public certs only)")
            if ("BEGIN CERTIFICATE" not in cert
                    or "END CERTIFICATE" not in cert):
                raise ValueError(
                    "ca_certs.trusted entries must be PEM certificates "
                    "(-----BEGIN CERTIFICATE----- ... -----END CERTIFICATE-----)")
        return value

    @model_validator(mode="after")
    def _consistency(self):
        if self.remove_defaults and not self.trusted:
            raise ValueError(
                "ca_certs.remove_defaults with no 'trusted' certs would leave "
                "an empty trust store and break TLS (apt-over-HTTPS, etc.)")
        return self


class Logging(_StrictModel):
    destination: str = "/var/log/live-installer-auto.log"
    also_serial: str = None


# --- network: a subset of the netplan v2 schema --------------------------
# We adopt netplan's *shapes* (the same key names and structure) but render
# them ourselves to NetworkManager keyfiles, the format Mint/LMDE installed
# systems already use — we do NOT depend on the netplan binary (LMDE/Debian
# does not ship it). This configures the INSTALLED system's networking; the
# live session's own networking is unaffected.


class NameServers(_StrictModel):
    addresses: list[str] = Field(default_factory=list)
    search: list[str] = Field(default_factory=list)

    @field_validator("addresses")
    @classmethod
    def _v_addresses(cls, value):
        for addr in value:
            _check_ip(addr)
        return value


class Route(_StrictModel):
    to: str            # "default" or a CIDR (e.g. 10.0.0.0/24)
    via: str           # next-hop IP
    metric: int = None

    @field_validator("to")
    @classmethod
    def _v_to(cls, value):
        if value == "default":
            return value
        return _check_cidr(value)

    @field_validator("via")
    @classmethod
    def _v_via(cls, value):
        return _check_ip(value)

    @model_validator(mode="after")
    def _consistency(self):
        # default routes may be either family; a CIDR target and via must
        # agree on family (no IPv6 next-hop for an IPv4 destination).
        if self.to != "default":
            to_v = ipaddress.ip_network(self.to, strict=False).version
            via_v = ipaddress.ip_address(self.via).version
            if to_v != via_v:
                raise ValueError(
                    f"route to {self.to!r} (IPv{to_v}) cannot use an IPv{via_v} "
                    f"via {self.via!r}"
                )
        return self


class Match(_StrictModel):
    """Select the physical device. macaddress binds by hardware address
    (survives kernel interface renaming); name binds by interface name."""

    macaddress: str = None
    name: str = None

    @field_validator("macaddress")
    @classmethod
    def _v_mac(cls, value):
        if value is not None and not _MAC_RE.match(value):
            raise ValueError(f"{value!r} is not a valid MAC address")
        return value

    @field_validator("name")
    @classmethod
    def _v_name(cls, value):
        if value is not None and not _IFNAME_RE.match(value):
            raise ValueError(f"{value!r} is not a valid interface name")
        return value

    @model_validator(mode="after")
    def _consistency(self):
        if self.macaddress is None and self.name is None:
            raise ValueError("match must set at least one of macaddress, name")
        return self


class _IpConfig(_StrictModel):
    """IP settings shared by ethernets and vlans."""

    dhcp4: bool = False
    dhcp6: bool = False
    addresses: list[str] = Field(default_factory=list)
    gateway4: str = None
    gateway6: str = None
    nameservers: NameServers = Field(default_factory=NameServers)
    routes: list[Route] = Field(default_factory=list)

    @field_validator("addresses")
    @classmethod
    def _v_addresses(cls, value):
        for addr in value:
            _check_cidr(addr)
        return value

    @field_validator("gateway4")
    @classmethod
    def _v_gateway4(cls, value):
        if value is None:
            return value
        return _check_ip(value, version=4)

    @field_validator("gateway6")
    @classmethod
    def _v_gateway6(cls, value):
        if value is None:
            return value
        return _check_ip(value, version=6)

    @model_validator(mode="after")
    def _ip_consistency(self):
        has_v4 = any(":" not in a for a in self.addresses)
        has_v6 = any(":" in a for a in self.addresses)
        if self.gateway4 and not has_v4:
            raise ValueError("gateway4 set but no IPv4 address configured")
        if self.gateway6 and not has_v6:
            raise ValueError("gateway6 set but no IPv6 address configured")
        return self


class EthernetConfig(_IpConfig):
    match: Match = None


class VlanConfig(_IpConfig):
    id: int
    link: str

    @field_validator("id")
    @classmethod
    def _v_id(cls, value):
        if not 0 <= value <= 4094:
            raise ValueError("vlan id must be between 0 and 4094")
        return value

    @field_validator("link")
    @classmethod
    def _v_link(cls, value):
        if not _IFNAME_RE.match(value):
            raise ValueError(f"{value!r} is not a valid interface id")
        return value


def _check_ssid(value):
    # 802.11 SSIDs are 1..32 octets. NM keys the connection on the SSID, so an
    # empty or over-long one can never match a real network.
    if not 1 <= len(value.encode("utf-8")) <= 32:
        raise ValueError(
            f"{value!r} is not a valid SSID (1..32 bytes)"
        )
    return value


def _check_psk(value):
    # WPA-PSK: either an 8..63 character passphrase or a 64-hex-digit raw key.
    if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return value
    if 8 <= len(value) <= 63:
        return value
    raise ValueError(
        "wifi password must be an 8..63 character WPA passphrase or a "
        "64-hex-digit PSK"
    )


class AccessPoint(_StrictModel):
    """One wifi network (a netplan/cloud-init access-points entry). `password`
    None means an open network; otherwise WPA-PSK. `hidden` marks a
    non-broadcast SSID so the client probes for it actively."""

    password: str = None
    hidden: bool = False

    @field_validator("password")
    @classmethod
    def _v_password(cls, value):
        if value is None:
            return value
        return _check_psk(value)


class WifiConfig(_IpConfig):
    match: Match = None
    access_points: dict[str, AccessPoint] = Field(
        default_factory=dict, alias="access-points")

    @field_validator("access_points")
    @classmethod
    def _v_access_points(cls, value):
        for ssid in value:
            _check_ssid(ssid)
        return value

    @model_validator(mode="after")
    def _wifi_consistency(self):
        # Enforced here (not in the field validator) so an omitted
        # access-points key — which never triggers a field validator — is
        # still rejected.
        if not self.access_points:
            raise ValueError(
                "a wifi interface needs at least one access-points entry")
        return self


class Network(_StrictModel):
    """A subset of netplan's v2 schema: ethernets, wifis, and vlans with static
    or DHCP addressing. Bonds/bridges are intentionally not modelled yet."""

    version: int = 2
    ethernets: dict[str, EthernetConfig] = Field(default_factory=dict)
    wifis: dict[str, WifiConfig] = Field(default_factory=dict)
    vlans: dict[str, VlanConfig] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _v_version(cls, value):
        if value != 2:
            raise ValueError("network.version must be 2 (netplan v2 schema)")
        return value

    @field_validator("ethernets", "wifis", "vlans")
    @classmethod
    def _v_ids(cls, value):
        for name in value:
            if not _IFNAME_RE.match(name):
                raise ValueError(f"{name!r} is not a valid interface id")
        return value

    @model_validator(mode="after")
    def _consistency(self):
        groups = {"ethernet": self.ethernets, "wifi": self.wifis,
                  "vlan": self.vlans}
        seen = {}
        for kind, table in groups.items():
            for name in table:
                if name in seen:
                    raise ValueError(
                        f"interface id {name!r} used for both a {seen[name]} "
                        f"and a {kind}")
                seen[name] = kind
        known = set(seen)
        for name, vlan in self.vlans.items():
            if vlan.link not in known:
                raise ValueError(
                    f"vlan {name!r} link {vlan.link!r} is not a defined "
                    "ethernet, wifi, or vlan"
                )
            if vlan.link == name:
                raise ValueError(f"vlan {name!r} cannot link to itself")
        return self


def _check_hostname(value):
    if value is None:
        return value
    if len(value) > 253 or not all(
        _HOSTNAME_LABEL_RE.match(label) for label in value.split(".")
    ):
        raise ValueError(f"{value!r} is not a valid hostname")
    return value


def _check_locale(value):
    if not _LOCALE_RE.match(value):
        raise ValueError(
            f"{value!r} is not a valid locale (expected e.g. en_US.UTF-8)"
        )
    return value


def _check_timezone(value):
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
    except ImportError:  # minimal live environment without tzdata access
        if not re.match(r"^[A-Za-z_+-]+(/[A-Za-z0-9_+-]+)*$", value):
            raise ValueError(f"{value!r} is not a valid timezone")
    except Exception:
        raise ValueError(
            f"{value!r} is not a valid IANA timezone (expected e.g. "
            "America/Toronto)"
        )
    return value


class AutoInstallConfig(_StrictModel):
    """Top-level answer file."""

    version: int
    # cloud-init style: identity at the top level
    locale: str
    timezone: str
    storage: Storage
    users: list[User] = Field(min_length=1)
    hostname: str = None
    keyboard: Keyboard = Field(default_factory=Keyboard)
    additional_locales: list[str] = Field(default_factory=list)  # also generated
    proxy: str = None                                        # system http(s) proxy
    ca_certs: CaCerts = None                                  # cloud-init: ca_certs:
    network: Network = None                                   # netplan v2 subset
    packages: list[str] = Field(default_factory=list)        # cloud-init: installs
    package_remove: list[str] = Field(default_factory=list)  # extension
    apt: Apt = None                                          # cloud-init: apt:
    late_commands: list[str] = Field(default_factory=list)   # autoinstall-style
    kernel: Kernel = Field(default_factory=Kernel)
    oem: Oem = Field(default_factory=Oem)
    drivers: Drivers = Field(default_factory=Drivers)        # autoinstall: drivers:
    on_failure: OnFailure = Field(default_factory=OnFailure)
    logging: Logging = Field(default_factory=Logging)

    @field_validator("version")
    @classmethod
    def _check_version(cls, value):
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema version {value!r}; this installer "
                f"supports version {SCHEMA_VERSION}"
            )
        return value

    @field_validator("locale")
    @classmethod
    def _v_locale(cls, value):
        return _check_locale(value)

    @field_validator("additional_locales")
    @classmethod
    def _v_additional_locales(cls, value):
        for loc in value:
            _check_locale(loc)
        return value

    @field_validator("proxy")
    @classmethod
    def _v_proxy(cls, value):
        if value is None:
            return value
        parts = urllib.parse.urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(
                f"{value!r} is not a valid proxy URL (e.g. "
                "http://proxy.example.com:3128)")
        # apt.conf string and shell env values; forbid the chars that would
        # let it break out of either quoting context.
        if any(c in value for c in '"\'\n\r \t'):
            raise ValueError("proxy URL must not contain quotes or whitespace")
        return value

    @field_validator("timezone")
    @classmethod
    def _v_timezone(cls, value):
        return _check_timezone(value)

    @field_validator("hostname")
    @classmethod
    def _v_hostname(cls, value):
        return _check_hostname(value)

    @model_validator(mode="after")
    def _check_users(self):
        names = [user.name for user in self.users]
        if len(names) != len(set(names)):
            raise ValueError("duplicate usernames in 'users'")
        if sum(1 for user in self.users if user.autologin) > 1:
            raise ValueError("only one user may have autologin: true")
        return self


def _format_errors(exc):
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"  {location or '<root>'}: {error['msg']}")
    return "\n".join(lines)


def parse_config(text):
    """Parse and validate answer-file YAML text into AutoInstallConfig.

    Raises ConfigError with a human-readable message on any problem.
    The installer must treat that as fatal — never guess at intent.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Answer file is not valid YAML: {exc}")
    if not isinstance(data, dict):
        raise ConfigError("Answer file must be a YAML mapping")
    try:
        return AutoInstallConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            "Answer file failed validation:\n" + _format_errors(exc)
        )


def load_config(path):
    """Read and validate an answer file from disk."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise ConfigError(f"Cannot read answer file {path}: {exc}")
    return parse_config(text)
