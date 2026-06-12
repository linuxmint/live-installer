#!/usr/bin/python3
# coding: utf-8
"""Answer-file schema for unattended installation (version 1).

Parses and strictly validates the YAML answer file that drives a
headless install.  Design rules (see the automated-install proposal):

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
_HOSTNAME_LABEL_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?(\.[A-Za-z0-9-]+)?$")
_SIZE_RE = re.compile(r"^\d+(\.\d+)?\s*[MGT]B$")


class ConfigError(Exception):
    """Raised when an answer file is malformed or fails validation."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Locale(_StrictModel):
    language: str
    timezone: str

    @field_validator("language")
    @classmethod
    def _check_language(cls, value):
        if not _LOCALE_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid locale (expected e.g. en_US.UTF-8)"
            )
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value):
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(value)
        except ImportError:  # minimal live environment without tzdata access
            if not re.match(r"^[A-Za-z_+-]+(/[A-Za-z0-9_+-]+)*$", value):
                raise ValueError(f"{value!r} is not a valid timezone")
        except Exception:
            raise ValueError(
                f"{value!r} is not a valid IANA timezone (expected e.g. America/Toronto)"
            )
        return value


class Keyboard(_StrictModel):
    model: str = "pc105"
    layout: str = "us"
    variant: str = ""


class Network(_StrictModel):
    hostname: str = None

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, value):
        if value is None:
            return value
        if len(value) > 253 or not all(
            _HOSTNAME_LABEL_RE.match(label) for label in value.split(".")
        ):
            raise ValueError(f"{value!r} is not a valid hostname")
        return value


class User(_StrictModel):
    username: str
    password_crypted: str
    full_name: str = ""
    autologin: bool = False
    sudo: bool = False
    ecryptfs_home: bool = False
    ssh_authorized_keys: list[str] = Field(default_factory=list)

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

    @field_validator("username")
    @classmethod
    def _check_username(cls, value):
        if not _USERNAME_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid username (lowercase, must start "
                "with a letter or underscore, max 32 chars)"
            )
        return value

    @field_validator("password_crypted")
    @classmethod
    def _check_crypted(cls, value):
        if not _CRYPT_RE.match(value):
            raise ValueError(
                "password_crypted must be a crypt(5) hash (e.g. sha512crypt "
                "starting with $6$). Plaintext passwords are not accepted; "
                "generate a hash with: openssl passwd -6"
            )
        return value


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


class Storage(_StrictModel):
    target: StorageTarget
    layout: str = "simple"
    luks: Luks = None

    @field_validator("layout")
    @classmethod
    def _check_layout(cls, value):
        allowed = ("simple", "lvm", "lvm-on-luks")
        if value == "custom":
            raise ValueError(
                "layout: custom is not supported for unattended installs in "
                f"schema version {SCHEMA_VERSION}; use the GUI installer for "
                "custom partition layouts"
            )
        if value not in allowed:
            raise ValueError(f"layout must be one of {', '.join(allowed)}")
        return value

    @model_validator(mode="after")
    def _luks_consistency(self):
        if self.layout == "lvm-on-luks" and self.luks is None:
            self.luks = Luks()  # fail-safe default: prompt on first boot
        if self.layout != "lvm-on-luks" and self.luks is not None:
            raise ValueError("'luks' is only valid with layout: lvm-on-luks")
        return self


class Packages(_StrictModel):
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class ShellStep(_StrictModel):
    shell: str


class AptKeyStep(_StrictModel):
    apt_key_url: str

    @field_validator("apt_key_url")
    @classmethod
    def _https_only(cls, value):
        if not value.startswith("https://"):
            raise ValueError(
                "apt_key_url must use https:// — keys fetched over plain "
                "HTTP can be tampered with in transit"
            )
        return value


class AptSourceStep(_StrictModel):
    apt_source: str


class Kernel(_StrictModel):
    # Appended to GRUB_CMDLINE_LINUX_DEFAULT on the installed system —
    # serial console, driver blacklists, etc. for headless/fleet hosts.
    cmdline_extra: str = ""


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


class AutoInstallConfig(_StrictModel):
    """Top-level answer file."""

    version: int
    locale: Locale
    storage: Storage
    users: list[User] = Field(min_length=1)
    keyboard: Keyboard = Field(default_factory=Keyboard)
    network: Network = Field(default_factory=Network)
    packages: Packages = Field(default_factory=Packages)
    post_install: list[ShellStep | AptKeyStep | AptSourceStep] = Field(
        default_factory=list
    )
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

    @model_validator(mode="after")
    def _check_users(self):
        names = [user.username for user in self.users]
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
