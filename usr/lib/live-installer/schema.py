#!/usr/bin/python3
"""Answer-file schema for unattended installation (version 1).

The answer file describes what the GUI would otherwise ask for, so the
same engine can run without anyone at the keyboard. Validation is strict:

  1. Disks are selected by stable attributes (by-id, by-path, model,
     size-min, first-non-removable), never by raw /dev paths, because
     device enumeration order is not stable.
  2. Passwords must be crypt(5) hashes. Plaintext is rejected.
  3. Unknown keys are errors, and nothing is interpolated or guessed.
  4. The format is versioned (`version: 1` is required).

Where it overlaps with cloud-init (locale, timezone, hostname, users),
the key names follow cloud-init.
"""

import re

import yaml
from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator, model_validator)

SCHEMA_VERSION = 1

# crypt(5) prefixes: $6$ sha512crypt, $5$ sha256crypt, $y$/$7$ yescrypt,
# $2a$/$2b$/$2y$ bcrypt
_CRYPT_RE = re.compile(r"^\$(6|5|y|7|2[aby])\$\S+$")
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_HOSTNAME_LABEL_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
# The engine always installs the UTF-8 variant of the locale
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?(\.(UTF-8|utf8))?$")
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([MGT]B)$")
_SIZE_FACTOR = {"MB": 10**6, "GB": 10**9, "TB": 10**12}
_XKB_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigError(Exception):
    """The answer file is malformed or fails validation."""


def parse_size(text):
    """'500GB' -> 500000000000 (decimal units, like disk labels)."""
    match = _SIZE_RE.match(text.strip())
    if not match:
        raise ValueError(f"{text!r} is not a valid size (expected e.g. 500GB)")
    return int(float(match.group(1)) * _SIZE_FACTOR[match.group(2)])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Keyboard(_Strict):
    model: str = "pc105"
    layout: str = "us"
    variant: str = ""

    @field_validator("model", "layout", "variant")
    @classmethod
    def _check_xkb(cls, value):
        if value and not _XKB_RE.match(value):
            raise ValueError(f"{value!r} is not a valid XKB name")
        return value


class User(_Strict):
    name: str
    passwd: str
    gecos: str = ""
    autologin: bool = False
    ecryptfs_home: bool = False

    @field_validator("name")
    @classmethod
    def _check_name(cls, value):
        if not _USERNAME_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid username (lowercase, starting "
                "with a letter or underscore, at most 32 characters)")
        return value

    @field_validator("passwd")
    @classmethod
    def _check_crypted(cls, value):
        if not _CRYPT_RE.match(value):
            raise ValueError(
                "passwd must be a crypt(5) hash, for example the output of "
                "'openssl passwd -6'. Plaintext passwords are not accepted.")
        return value


class DiskMatch(_Strict):
    """Selects the target disk. Every matcher given must agree, and the
    result must be exactly one disk."""

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
                f"{value!r} is a device path. Device names are not stable "
                "between boots; use a by-id or by-path link name, the "
                "model, or size-min.")
        return value

    @field_validator("size_min")
    @classmethod
    def _check_size(cls, value):
        if value is not None:
            parse_size(value)
        return value

    @model_validator(mode="after")
    def _at_least_one(self):
        if not (self.by_id or self.by_path or self.model or self.size_min
                or self.first_non_removable):
            raise ValueError(
                "match needs at least one of by-id, by-path, model, "
                "size-min, first-non-removable")
        return self


class Target(_Strict):
    match: DiskMatch


class Luks(_Strict):
    # A file holding the passphrase, for example on the install media.
    keyfile: str


class Storage(_Strict):
    target: Target
    layout: str = "simple"
    luks: Luks = None
    swapfile: bool = True
    swapfile_size: str = None  # default: the installer's recommended size

    @field_validator("layout")
    @classmethod
    def _check_layout(cls, value):
        if value not in ("simple", "luks"):
            raise ValueError("layout must be 'simple' or 'luks'")
        return value

    @field_validator("swapfile_size")
    @classmethod
    def _check_swapfile_size(cls, value):
        if value is not None:
            parse_size(value)
        return value

    @model_validator(mode="after")
    def _consistency(self):
        if self.layout == "luks" and self.luks is None:
            raise ValueError("layout: luks needs a 'luks' section with a keyfile")
        if self.layout != "luks" and self.luks is not None:
            raise ValueError("'luks' is only valid with layout: luks")
        if self.swapfile_size is not None and not self.swapfile:
            raise ValueError("swapfile_size is set but swapfile is false")
        return self


class AutoInstallConfig(_Strict):
    version: int
    locale: str
    timezone: str
    storage: Storage
    # A list, as in cloud-init. This version creates exactly one user,
    # like the GUI does.
    users: list[User] = Field(min_length=1, max_length=1)
    hostname: str = None
    keyboard: Keyboard = Field(default_factory=Keyboard)
    # Shell commands run inside the installed system (chroot) at the end.
    late_commands: list[str] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _check_version(cls, value):
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported version {value!r}; this installer supports "
                f"version {SCHEMA_VERSION}")
        return value

    @field_validator("locale")
    @classmethod
    def _check_locale(cls, value):
        if not _LOCALE_RE.match(value):
            raise ValueError(f"{value!r} is not a valid UTF-8 locale (e.g. en_US.UTF-8)")
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value):
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(value)
        except Exception:
            raise ValueError(f"{value!r} is not a valid timezone (e.g. America/Toronto)")
        return value

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, value):
        if value is not None and (len(value) > 253 or not all(
                _HOSTNAME_LABEL_RE.match(label) for label in value.split("."))):
            raise ValueError(f"{value!r} is not a valid hostname")
        return value


def parse_config(text):
    """Parse and validate answer-file text. Raises ConfigError."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"The answer file is not valid YAML: {exc}")
    if not isinstance(data, dict):
        raise ConfigError("The answer file must be a YAML mapping")
    try:
        return AutoInstallConfig.model_validate(data)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            lines.append(f"  {location or '<root>'}: {error['msg']}")
        raise ConfigError("The answer file is not valid:\n" + "\n".join(lines))
