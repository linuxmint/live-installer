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
  - `repositories` carries an apt sources.list line (cloud-init calls the
    equivalent `apt:`).
  - `late_commands` runs in the target during install (in the chroot),
    matching Ubuntu autoinstall's `late-commands` and kickstart `%post`.
    This is deliberately NOT cloud-init's `runcmd`, which runs on first
    boot — a different lifecycle, so it gets a different name. `runcmd`
    is left unused, reserved for true first-boot semantics later.

Strict validation deliberately defangs YAML's type-coercion footguns:
anything that does not parse cleanly into the declared types is an
error, never a guess.
"""

import re

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


class ConfigError(Exception):
    """Raised when an answer file is malformed or fails validation."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Keyboard(_StrictModel):
    model: str = "pc105"
    layout: str = "us"
    variant: str = ""


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


class CustomPartition(_StrictModel):
    """One partition in a custom layout. It is either mounted (mount +
    filesystem), an LVM physical volume (lvm_pv), or a flag-only special
    partition (e.g. bios_grub)."""
    size: str
    mount: str = None
    filesystem: str = None
    flags: list[str] = Field(default_factory=list)
    lvm_pv: str = None

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
                "a partition needs both mount and filesystem (or lvm_pv, or "
                "the bios_grub flag)")
        if "esp" in self.flags:
            if self.filesystem != "vfat" or self.mount != "/boot/efi":
                raise ValueError(
                    "an esp partition must be filesystem: vfat mounted at "
                    "/boot/efi")
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
                  + [v.mount for v in self.lvm if v.mount])
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
        return self


class Repository(_StrictModel):
    """An apt repository to add. The `repositories:` key borrows cloud-init's
    neutral concept, but the contents are apt-specific: on Debian/Mint
    `source` is a sources.list line. A non-apt backend would need a
    structured form (type/url/suite/components); that is not a goal today."""

    source: str                          # apt sources.list line, e.g.
                                         # "deb https://repo trixie main"
    key_url: str = None                  # optional signing key, https only

    @field_validator("key_url")
    @classmethod
    def _https_only(cls, value):
        if value is not None and not value.startswith("https://"):
            raise ValueError(
                "repositories[].key_url must use https:// — a key fetched "
                "over plain HTTP can be tampered with in transit"
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


class Logging(_StrictModel):
    destination: str = "/var/log/live-installer-auto.log"
    also_serial: str = None


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
    packages: list[str] = Field(default_factory=list)        # cloud-init: installs
    package_remove: list[str] = Field(default_factory=list)  # extension
    repositories: list[Repository] = Field(default_factory=list)
    late_commands: list[str] = Field(default_factory=list)   # autoinstall-style
    kernel: Kernel = Field(default_factory=Kernel)
    oem: Oem = Field(default_factory=Oem)
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
