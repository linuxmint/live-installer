"""Unit tests for the unattended-install answer-file schema."""

import textwrap

import pytest

import schema
from schema import ConfigError, parse_config

VALID_MINIMAL = textwrap.dedent("""\
    version: 1
    locale: en_US.UTF-8
    timezone: America/Toronto
    users:
      - name: admin
        passwd: "$6$rounds=4096$salt$hashhashhash"
    storage:
      target:
        match:
          first-non-removable: true
""")

VALID_FULL = textwrap.dedent("""\
    version: 1
    hostname: mint-ws-01
    locale: en_CA.UTF-8
    timezone: America/Toronto
    keyboard:
      model: pc105
      layout: us
      variant: ""
    users:
      - name: admin
        gecos: Workstation Admin
        passwd: "$6$rounds=4096$salt$hashhashhash"
        groups: [sudo]
        ssh_authorized_keys:
          - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA admin@host"
    storage:
      target:
        match:
          by-id: "nvme-Samsung_SSD_980_PRO_*"
        on_no_match: abort
      layout: lvm-on-luks
      luks:
        passphrase_source: prompt-on-first-boot
    packages: [openssh-server, build-essential]
    package_remove: [hexchat]
    repositories:
      - source: "deb https://example.com/repo trixie main"
        key_url: https://example.com/repo.gpg
    runcmd:
      - /cdrom/scripts/join-domain.sh
      - systemctl enable ssh
    oem:
      enabled: false
    on_failure:
      partition_mismatch: abort
      network_unavailable: continue
      package_install_failure: abort
      post_install_script_failure: abort
    logging:
      destination: /var/log/live-installer-auto.log
      also_serial: ttyS0
""")


class TestValidConfigs:
    def test_minimal(self):
        config = parse_config(VALID_MINIMAL)
        assert config.version == 1
        assert config.users[0].name == "admin"
        assert config.locale == "en_US.UTF-8"
        assert config.timezone == "America/Toronto"
        assert config.storage.target.match.first_non_removable is True
        # fail-closed defaults
        assert config.storage.layout == "simple"
        assert config.on_failure.partition_mismatch == "abort"
        assert config.on_failure.network_unavailable == "abort"
        assert config.keyboard.layout == "us"

    def test_full(self):
        config = parse_config(VALID_FULL)
        assert config.hostname == "mint-ws-01"
        assert config.storage.target.match.by_id == "nvme-Samsung_SSD_980_PRO_*"
        assert config.storage.luks.passphrase_source == "prompt-on-first-boot"
        assert config.on_failure.network_unavailable == "continue"
        assert config.packages == ["openssh-server", "build-essential"]
        assert config.package_remove == ["hexchat"]
        assert config.repositories[0].source.startswith("deb https://")
        assert config.runcmd == ["/cdrom/scripts/join-domain.sh",
                                 "systemctl enable ssh"]

    def test_sudo_via_group(self):
        config = parse_config(VALID_FULL)
        assert config.users[0].groups == ["sudo"]
        assert config.users[0].sudo is True  # convenience property

    def test_no_sudo_when_not_in_group(self):
        config = parse_config(VALID_MINIMAL)
        assert config.users[0].sudo is False

    def test_lvm_on_luks_defaults_to_firstboot_prompt(self):
        config = parse_config(
            VALID_MINIMAL.replace(
                "storage:\n  target:",
                "storage:\n  layout: lvm-on-luks\n  target:",
            )
        )
        assert config.storage.luks is not None
        assert config.storage.luks.passphrase_source == "prompt-on-first-boot"

    def test_ssh_authorized_keys_accepted(self):
        config = parse_config(VALID_FULL)
        assert config.users[0].ssh_authorized_keys[0].startswith("ssh-ed25519")

    def test_scenario_fixture_parses(self):
        # the integration-test answer file must always track the schema
        from pathlib import Path

        fixture = (
            Path(__file__).resolve().parents[1]
            / "integration" / "scenarios" / "answers" / "bios-simple.yaml"
        )
        config = schema.load_config(fixture)
        assert config.hostname == "lmde-test-01"
        assert config.packages == ["openssh-server"]


def _expect_error(yaml_text, *fragments):
    with pytest.raises(ConfigError) as excinfo:
        parse_config(yaml_text)
    for fragment in fragments:
        assert fragment in str(excinfo.value)
    return excinfo.value


class TestRejections:
    def test_not_yaml(self):
        _expect_error("{:::", "not valid YAML")

    def test_not_a_mapping(self):
        _expect_error("- just\n- a\n- list", "must be a YAML mapping")

    def test_missing_version(self):
        _expect_error(VALID_MINIMAL.replace("version: 1\n", ""), "version")

    def test_wrong_version(self):
        _expect_error(
            VALID_MINIMAL.replace("version: 1", "version: 2"),
            "unsupported schema version",
        )

    def test_unknown_top_level_key(self):
        _expect_error(VALID_MINIMAL + "frobnicate: yes\n", "frobnicate")

    def test_plaintext_password_rejected(self):
        bad = VALID_MINIMAL.replace(
            '"$6$rounds=4096$salt$hashhashhash"', "hunter2"
        )
        _expect_error(bad, "crypt(5)", "Plaintext")

    def test_raw_device_path_rejected(self):
        bad = VALID_MINIMAL.replace(
            "first-non-removable: true", 'by-id: "/dev/sda"'
        )
        _expect_error(bad, "raw device path", "stable attributes")

    def test_empty_match_rejected(self):
        bad = VALID_MINIMAL.replace(
            "match:\n      first-non-removable: true",
            "match: {}",
        )
        _expect_error(bad, "at least one matcher")

    def test_custom_layout_rejected(self):
        bad = VALID_FULL.replace("layout: lvm-on-luks", "layout: custom")
        bad = bad.replace(
            "      luks:\n        passphrase_source: prompt-on-first-boot\n", ""
        )
        _expect_error(bad, "custom", "GUI installer")

    def test_luks_without_luks_layout_rejected(self):
        bad = VALID_FULL.replace("layout: lvm-on-luks", "layout: lvm")
        _expect_error(bad, "only valid with layout: lvm-on-luks")

    def test_keyfile_source_requires_path(self):
        bad = VALID_FULL.replace(
            "passphrase_source: prompt-on-first-boot",
            "passphrase_source: keyfile",
        )
        _expect_error(bad, "requires a 'keyfile' path")

    def test_invalid_on_no_match(self):
        bad = VALID_FULL.replace("on_no_match: abort", "on_no_match: use-first")
        _expect_error(bad, "on_no_match")

    def test_invalid_failure_policy(self):
        bad = VALID_FULL.replace(
            "network_unavailable: continue", "network_unavailable: retry"
        )
        _expect_error(bad, "abort", "continue")

    def test_no_users_rejected(self):
        bad = VALID_MINIMAL.replace(
            "users:\n  - name: admin\n"
            '    passwd: "$6$rounds=4096$salt$hashhashhash"\n',
            "users: []\n",
        )
        _expect_error(bad, "users")

    def test_duplicate_usernames_rejected(self):
        bad = VALID_MINIMAL.replace(
            "users:",
            "users:\n"
            "  - name: admin\n"
            '    passwd: "$6$rounds=4096$salt$other"',
        )
        _expect_error(bad, "duplicate usernames")

    def test_two_autologin_users_rejected(self):
        bad = VALID_MINIMAL.replace(
            "users:",
            "users:\n"
            "  - name: kiosk\n"
            '    passwd: "$6$rounds=4096$salt$other"\n'
            "    autologin: true",
        ).replace(
            'passwd: "$6$rounds=4096$salt$hashhashhash"',
            'passwd: "$6$rounds=4096$salt$hashhashhash"\n'
            "    autologin: true",
        )
        _expect_error(bad, "autologin")

    def test_bad_username_rejected(self):
        bad = VALID_MINIMAL.replace("name: admin", "name: Admin User")
        _expect_error(bad, "not a valid username")

    def test_bad_group_rejected(self):
        bad = VALID_FULL.replace("groups: [sudo]", "groups: [Bad Group]")
        _expect_error(bad, "not a valid group name")

    def test_bad_hostname_rejected(self):
        bad = VALID_FULL.replace("hostname: mint-ws-01", "hostname: -bad-")
        _expect_error(bad, "not a valid hostname")

    def test_bad_timezone_rejected(self):
        bad = VALID_MINIMAL.replace(
            "timezone: America/Toronto", "timezone: Mars/Olympus_Mons"
        )
        _expect_error(bad, "timezone")

    def test_http_repo_key_rejected(self):
        bad = VALID_FULL.replace(
            "key_url: https://example.com/repo.gpg",
            "key_url: http://example.com/repo.gpg",
        )
        _expect_error(bad, "https://")

    def test_garbage_ssh_key_rejected(self):
        bad = VALID_FULL.replace(
            '- "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA admin@host"',
            '- "not a key at all"',
        )
        _expect_error(bad, "OpenSSH public key")

    def test_norway_problem_is_defanged(self):
        # YAML 1.1 would coerce `no` to boolean false; a boolean is not a
        # valid keyboard layout string, so strict typing catches it.
        bad = VALID_FULL.replace("layout: us", "layout: no")
        _expect_error(bad, "keyboard")
