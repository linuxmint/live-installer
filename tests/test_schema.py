import pytest

from schema import ConfigError, parse_config, parse_size

HASH = "$6$salt$" + "a" * 86

MINIMAL = f"""
version: 1
locale: en_US.UTF-8
timezone: America/Toronto
users:
  - name: admin
    passwd: "{HASH}"
storage:
  target:
    match:
      first-non-removable: true
"""


def parse(text=MINIMAL, **replace):
    for old, new in replace.items():
        text = text.replace(old, new)
    return parse_config(text)


def test_minimal_defaults():
    config = parse()
    assert config.storage.layout == "simple"
    assert config.storage.swapfile is True
    assert config.keyboard.layout == "us"
    assert config.hostname is None
    assert config.late_commands == []


def test_luks_needs_keyfile():
    with pytest.raises(ConfigError, match="keyfile"):
        parse_config(MINIMAL + "  layout: luks\n")
    config = parse_config(MINIMAL + "  layout: luks\n  luks:\n    keyfile: luks.key\n")
    assert config.storage.luks.keyfile == "luks.key"


def test_luks_section_without_luks_layout():
    with pytest.raises(ConfigError, match="only valid with layout: luks"):
        parse_config(MINIMAL + "  luks:\n    keyfile: luks.key\n")


def test_swapfile_size():
    config = parse_config(MINIMAL + "  swapfile_size: 4GB\n")
    assert config.storage.swapfile_size == "4GB"
    with pytest.raises(ConfigError, match="swapfile is false"):
        parse_config(MINIMAL + "  swapfile: false\n  swapfile_size: 4GB\n")
    with pytest.raises(ConfigError, match="not a valid size"):
        parse_config(MINIMAL + "  swapfile_size: lots\n")


@pytest.mark.parametrize("passwd", ["hunter2", "$1$md5$abc", ""])
def test_rejects_plaintext_and_weak_hashes(passwd):
    with pytest.raises(ConfigError, match="crypt"):
        parse(**{HASH: passwd})


def test_rejects_raw_device_path():
    with pytest.raises(ConfigError, match="device path"):
        parse(**{"first-non-removable: true": "by-id: /dev/sda"})


def test_match_needs_a_matcher():
    with pytest.raises(ConfigError, match="at least one"):
        parse(**{"first-non-removable: true": "first-non-removable: false"})


def test_unknown_keys_are_errors():
    with pytest.raises(ConfigError, match="packages"):
        parse_config(MINIMAL + "packages: [vim]\n")


def test_one_user_only():
    second = f'  - name: other\n    passwd: "{HASH}"\n'
    text = MINIMAL.replace("storage:", second + "storage:")
    with pytest.raises(ConfigError, match="users"):
        parse_config(text)


@pytest.mark.parametrize("field,value", [
    ("version: 1", "version: 2"),
    ("en_US.UTF-8", "en_US.ISO-8859-1"),
    ("America/Toronto", "Mars/Olympus_Mons"),
])
def test_rejects_bad_values(field, value):
    with pytest.raises(ConfigError):
        parse(**{field: value})


def test_rejects_bad_hostname():
    with pytest.raises(ConfigError, match="hostname"):
        parse_config(MINIMAL + "hostname: bad_name!\n")


@pytest.mark.parametrize("text", ["", "- a list", "key: [unclosed"])
def test_rejects_non_mapping_or_broken_yaml(text):
    with pytest.raises(ConfigError):
        parse_config(text)


def test_parse_size():
    assert parse_size("500GB") == 500 * 10**9
    assert parse_size("1.5TB") == 1500 * 10**9
    with pytest.raises(ValueError):
        parse_size("500")
