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
    apt:
      sources:
        example:
          source: "deb https://example.com/repo trixie main"
          key_url: https://example.com/repo.gpg
    late_commands:
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
        assert config.apt.sources["example"].source.startswith("deb https://")
        assert config.late_commands == ["/cdrom/scripts/join-domain.sh",
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

    def test_partitions_without_custom_layout_rejected(self):
        bad = VALID_MINIMAL.replace(
            "      first-non-removable: true",
            "      first-non-removable: true\n"
            "  partitions:\n"
            "    - {size: rest, mount: /, filesystem: ext4}",
        )
        _expect_error(bad, "only valid with layout: custom")

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


CUSTOM = textwrap.dedent("""\
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
      layout: custom
      partitions:
        - {size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}
        - {size: 2GB, mount: swap, filesystem: swap}
        - {size: rest, lvm_pv: vg0}
      lvm:
        - {vg: vg0, lv: root, size: 40GB, mount: /, filesystem: ext4}
        - {vg: vg0, lv: home, size: rest, mount: /home, filesystem: ext4}
""")


class TestCustomLayout:
    def test_valid_plain_plus_lvm(self):
        config = parse_config(CUSTOM)
        st = config.storage
        assert st.layout == "custom"
        assert st.partitions[0].flags == ["esp"]
        assert st.partitions[2].lvm_pv == "vg0"
        assert st.lvm[0].vg == "vg0" and st.lvm[0].mount == "/"

    def test_valid_plain_only(self):
        text = textwrap.dedent("""\
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
              layout: custom
              partitions:
                - {size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}
                - {size: 40GB, mount: /, filesystem: ext4}
                - {size: rest, mount: /home, filesystem: ext4}
        """)
        config = parse_config(text)
        assert config.storage.lvm == []
        assert any(p.mount == "/" for p in config.storage.partitions)

    def test_no_root_rejected(self):
        bad = CUSTOM.replace("lv: root, size: 40GB, mount: /,",
                             "lv: root, size: 40GB, mount: /srv,")
        _expect_error(bad, "exactly one '/' mount")

    def test_two_roots_rejected(self):
        bad = CUSTOM.replace("lv: home, size: rest, mount: /home,",
                             "lv: home, size: rest, mount: /,")
        _expect_error(bad, "exactly one '/' mount")

    def test_duplicate_mount_rejected(self):
        bad = CUSTOM.replace("lv: home, size: rest, mount: /home,",
                             "lv: home, size: rest, mount: /boot/efi,")
        _expect_error(bad, "duplicate mount")

    def test_two_rest_partitions_rejected(self):
        bad = CUSTOM.replace("- {size: 2GB, mount: swap, filesystem: swap}",
                             "- {size: rest, mount: /srv, filesystem: ext4}")
        _expect_error(bad, "one partition may use size: rest")

    def test_pv_vg_without_volumes_rejected(self):
        # add a PV for a VG that has no logical volumes
        bad = CUSTOM.replace(
            "    - {size: rest, lvm_pv: vg0}\n",
            "    - {size: 1GB, lvm_pv: vgEmpty}\n"
            "    - {size: rest, lvm_pv: vg0}\n")
        _expect_error(bad, "no logical volumes")

    def test_lvm_volume_without_pv_rejected(self):
        bad = CUSTOM.replace("vg: vg0, lv: home", "vg: vgOther, lv: home")
        _expect_error(bad, "no lvm_pv partition")

    def test_esp_must_be_vfat_at_boot_efi(self):
        bad = CUSTOM.replace(
            "{size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}",
            "{size: 512MB, mount: /boot/efi, filesystem: ext4, flags: [esp]}")
        _expect_error(bad, "esp partition must be filesystem: vfat")

    def test_boot_efi_without_esp_flag_rejected(self):
        # /boot/efi must carry the esp flag or the GPT entry lacks the ESP
        # type GUID and UEFI won't boot it (review finding #1).
        bad = CUSTOM.replace(
            "{size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}",
            "{size: 512MB, mount: /boot/efi, filesystem: vfat}")
        _expect_error(bad, "esp")

    def test_duplicate_lv_name_in_vg_rejected(self):
        bad = CUSTOM.replace("lv: home", "lv: root")
        _expect_error(bad, "duplicate logical-volume name")

    def test_bad_filesystem_rejected(self):
        bad = CUSTOM.replace("mount: /home, filesystem: ext4",
                             "mount: /home, filesystem: reiserfs")
        _expect_error(bad, "filesystem")

    def test_plain_partition_needs_mount_and_fs(self):
        bad = CUSTOM.replace("- {size: 2GB, mount: swap, filesystem: swap}",
                             "- {size: 2GB, filesystem: ext4}")
        _expect_error(bad, "needs both mount and filesystem")

    def test_swap_mount_needs_swap_fs(self):
        bad = CUSTOM.replace("- {size: 2GB, mount: swap, filesystem: swap}",
                             "- {size: 2GB, mount: swap, filesystem: ext4}")
        _expect_error(bad, "swap")


BTRFS = textwrap.dedent("""\
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
      layout: custom
      partitions:
        - {size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}
        - size: rest
          filesystem: btrfs
          subvolumes:
            - {name: "@", mount: /}
            - {name: "@home", mount: /home}
""")


class TestBtrfsSubvolumes:
    def test_valid(self):
        config = parse_config(BTRFS)
        part = config.storage.partitions[1]
        assert part.mount is None and part.filesystem == "btrfs"
        assert [(s.name, s.mount) for s in part.subvolumes] == [
            ("@", "/"), ("@home", "/home")]

    def test_valid_on_lvm(self):
        text = textwrap.dedent("""\
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
              layout: custom
              partitions:
                - {size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}
                - {size: rest, lvm_pv: vg0}
              lvm:
                - vg: vg0
                  lv: root
                  size: rest
                  filesystem: btrfs
                  subvolumes:
                    - {name: "@", mount: /}
                    - {name: "@home", mount: /home}
        """)
        config = parse_config(text)
        assert config.storage.lvm[0].subvolumes[0].name == "@"

    def test_subvolumes_require_btrfs(self):
        bad = BTRFS.replace("filesystem: btrfs", "filesystem: ext4")
        _expect_error(bad, "subvolumes require filesystem: btrfs")

    def test_subvol_partition_takes_no_mount(self):
        bad = BTRFS.replace(
            "    - size: rest\n      filesystem: btrfs\n",
            "    - size: rest\n      mount: /srv\n      filesystem: btrfs\n")
        _expect_error(bad, "takes no mount")

    def test_subvolume_provides_the_root_mount(self):
        bad = BTRFS.replace('        - {name: "@", mount: /}\n', "")
        _expect_error(bad, "exactly one '/' mount")

    def test_duplicate_subvol_mount_rejected(self):
        bad = BTRFS.replace('{name: "@home", mount: /home}',
                            '{name: "@home", mount: /boot/efi}')
        _expect_error(bad, "duplicate mount")


NETWORK = textwrap.dedent("""\
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
    network:
      version: 2
      ethernets:
        primary:
          match: {macaddress: "aa:bb:cc:dd:ee:ff"}
          addresses: [192.168.1.10/24, 2001:db8::5/64]
          gateway4: 192.168.1.1
          gateway6: 2001:db8::1
          nameservers:
            addresses: [192.168.1.53, 2001:db8::53]
            search: [example.com]
          routes:
            - {to: 10.0.0.0/8, via: 192.168.1.254}
      vlans:
        vlan100:
          id: 100
          link: primary
          addresses: [10.100.0.5/24]
""")


class TestNetwork:
    def test_valid(self):
        config = parse_config(NETWORK)
        net = config.network
        assert net.version == 2
        eth = net.ethernets["primary"]
        assert eth.match.macaddress == "aa:bb:cc:dd:ee:ff"
        assert eth.addresses == ["192.168.1.10/24", "2001:db8::5/64"]
        assert eth.gateway4 == "192.168.1.1"
        assert eth.nameservers.search == ["example.com"]
        assert eth.routes[0].to == "10.0.0.0/8"
        vlan = net.vlans["vlan100"]
        assert vlan.id == 100 and vlan.link == "primary"

    def test_absent_network_is_none(self):
        assert parse_config(VALID_MINIMAL).network is None

    def test_dhcp_only(self):
        text = VALID_MINIMAL + textwrap.dedent("""\
            network:
              version: 2
              ethernets:
                eth0: {dhcp4: true}
        """)
        config = parse_config(text)
        assert config.network.ethernets["eth0"].dhcp4 is True

    def test_wrong_version_rejected(self):
        _expect_error(NETWORK.replace("version: 2", "version: 3"),
                      "network.version must be 2")

    def test_bad_mac_rejected(self):
        _expect_error(NETWORK.replace("aa:bb:cc:dd:ee:ff", "nope"),
                      "valid MAC")

    def test_address_without_prefix_rejected(self):
        _expect_error(NETWORK.replace("192.168.1.10/24", "192.168.1.10"),
                      "prefix length")

    def test_gateway4_must_be_ipv4(self):
        _expect_error(NETWORK.replace("gateway4: 192.168.1.1",
                                      "gateway4: 2001:db8::1"),
                      "not an IPv4 address")

    def test_gateway_without_matching_address_rejected(self):
        bad = NETWORK.replace("addresses: [192.168.1.10/24, 2001:db8::5/64]",
                              "addresses: [2001:db8::5/64]")
        _expect_error(bad, "gateway4 set but no IPv4 address")

    def test_route_family_mismatch_rejected(self):
        bad = NETWORK.replace("{to: 10.0.0.0/8, via: 192.168.1.254}",
                              "{to: 10.0.0.0/8, via: 'fe80::1'}")
        _expect_error(bad, "cannot use an IPv6")

    def test_vlan_dangling_link_rejected(self):
        _expect_error(NETWORK.replace("link: primary", "link: missing0"),
                      "is not a defined")

    def test_vlan_id_range_rejected(self):
        _expect_error(NETWORK.replace("id: 100", "id: 5000"),
                      "between 0 and 4094")

    def test_bad_interface_id_rejected(self):
        _expect_error(NETWORK.replace("    primary:", '    "bad/if":'),
                      "valid interface id")

    def test_unknown_key_rejected(self):
        _expect_error(NETWORK.replace("  version: 2\n",
                                      "  version: 2\n  bogus: 1\n"),
                      "bogus")


APT = textwrap.dedent("""\
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
    apt:
      sources:
        vendor:
          source: "deb https://example.com/repo trixie main"
          key_url: https://example.com/repo.gpg
        keyserver-repo:
          source: "deb https://other.example/deb stable main"
          keyid: "0xABCDEF0123456789"
""")


class TestApt:
    def test_valid(self):
        config = parse_config(APT)
        srcs = config.apt.sources
        assert set(srcs) == {"vendor", "keyserver-repo"}
        assert srcs["vendor"].source.startswith("deb https://")
        assert srcs["vendor"].key_url == "https://example.com/repo.gpg"
        # keyserver defaults to cloud-init's
        assert srcs["keyserver-repo"].keyserver == "keyserver.ubuntu.com"
        assert srcs["keyserver-repo"].keyid == "0xABCDEF0123456789"

    def test_absent_apt_is_none(self):
        assert parse_config(VALID_MINIMAL).apt is None

    def test_inline_key(self):
        text = VALID_MINIMAL + textwrap.dedent("""\
            apt:
              sources:
                inline:
                  source: "deb https://example.com/repo trixie main"
                  key: |
                    -----BEGIN PGP PUBLIC KEY BLOCK-----
                    abc
                    -----END PGP PUBLIC KEY BLOCK-----
        """)
        config = parse_config(text)
        assert "BEGIN PGP PUBLIC KEY" in config.apt.sources["inline"].key

    def test_non_deb_source_rejected(self):
        _expect_error(APT.replace('"deb https://example.com/repo trixie main"',
                                  '"ppa:some/ppa"'),
                      "deb ")

    def test_http_key_url_rejected(self):
        _expect_error(APT.replace("https://example.com/repo.gpg",
                                  "http://example.com/repo.gpg"),
                      "https://")

    def test_two_key_sources_rejected(self):
        bad = APT.replace(
            '      key_url: https://example.com/repo.gpg',
            '      key_url: https://example.com/repo.gpg\n'
            '      keyid: "ABCDEF12"')
        _expect_error(bad, "at most one signing key")

    def test_bad_keyid_rejected(self):
        _expect_error(APT.replace('keyid: "0xABCDEF0123456789"',
                                  'keyid: "not-hex!"'),
                      "valid GPG key id")

    def test_bad_source_name_rejected(self):
        _expect_error(APT.replace("    vendor:", '    "bad/name":'),
                      "valid apt source name")

    def test_bad_filename_rejected(self):
        bad = APT.replace(
            '      key_url: https://example.com/repo.gpg',
            '      filename: "../escape"')
        _expect_error(bad, "valid filename")

    def test_unknown_key_rejected(self):
        _expect_error(APT.replace('      key_url: https://example.com/repo.gpg',
                                  '      bogus: 1'),
                      "bogus")


WIFI = textwrap.dedent("""\
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
    network:
      version: 2
      wifis:
        wlan0:
          match: {macaddress: "aa:bb:cc:dd:ee:01"}
          addresses: [10.0.0.5/24, 2001:db8::5/64]
          gateway4: 10.0.0.1
          access-points:
            "Corp-WPA":
              password: "supersecret123"
            "OpenGuest":
              hidden: true
""")


class TestWifi:
    def test_valid(self):
        config = parse_config(WIFI)
        wifi = config.network.wifis["wlan0"]
        assert wifi.match.macaddress == "aa:bb:cc:dd:ee:01"
        assert wifi.addresses == ["10.0.0.5/24", "2001:db8::5/64"]
        aps = wifi.access_points
        assert aps["Corp-WPA"].password == "supersecret123"
        assert aps["OpenGuest"].password is None      # open network
        assert aps["OpenGuest"].hidden is True

    def test_dhcp_psk_minimal(self):
        text = VALID_MINIMAL + textwrap.dedent("""\
            network:
              version: 2
              wifis:
                wlan0:
                  dhcp4: true
                  access-points:
                    "Home": {password: "passw0rd"}
        """)
        config = parse_config(text)
        assert config.network.wifis["wlan0"].dhcp4 is True

    def test_64_hex_psk_accepted(self):
        text = VALID_MINIMAL + textwrap.dedent("""\
            network:
              version: 2
              wifis:
                wlan0:
                  access-points:
                    "Raw": {password: "%s"}
        """ % ("a" * 64))
        assert parse_config(text).network.wifis["wlan0"].access_points["Raw"]

    def test_short_psk_rejected(self):
        _expect_error(WIFI.replace('"supersecret123"', '"short"'),
                      "8..63")

    def test_empty_ssid_rejected(self):
        _expect_error(WIFI.replace('"Corp-WPA"', '""'), "valid SSID")

    def test_overlong_ssid_rejected(self):
        _expect_error(WIFI.replace('"Corp-WPA"', '"%s"' % ("x" * 33)),
                      "valid SSID")

    def test_no_access_points_rejected(self):
        text = VALID_MINIMAL + textwrap.dedent("""\
            network:
              version: 2
              wifis:
                wlan0: {dhcp4: true}
        """)
        _expect_error(text, "at least one access-points")

    def test_unknown_ap_key_rejected(self):
        _expect_error(WIFI.replace('          hidden: true',
                                   '          hidden: true\n'
                                   '          bogus: 1'),
                      "bogus")

    def test_id_reused_across_ethernet_and_wifi_rejected(self):
        text = VALID_MINIMAL + textwrap.dedent("""\
            network:
              version: 2
              ethernets:
                shared: {dhcp4: true}
              wifis:
                shared:
                  access-points:
                    "N": {password: "passw0rd"}
        """)
        _expect_error(text, "used for both")
