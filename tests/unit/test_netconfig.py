"""Unit tests for the netplan-v2 -> NetworkManager keyfile renderer."""

import configparser

import netconfig
import schema


def _render(network_yaml):
    base = (
        "version: 1\n"
        "locale: en_US.UTF-8\n"
        "timezone: America/Toronto\n"
        "users:\n"
        '  - {name: admin, passwd: "$6$rounds=4096$salt$hashhashhash"}\n'
        "storage:\n"
        "  target:\n"
        "    match: {first-non-removable: true}\n"
    )
    config = schema.parse_config(base + network_yaml)
    return netconfig.render(config.network)


def _parse_keyfile(text):
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return parser


class TestRender:
    def test_none_renders_nothing(self):
        assert netconfig.render(None) == {}

    def test_static_dual_stack_ethernet(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0:\n"
            "      addresses: [192.168.1.10/24, 2001:db8::5/64]\n"
            "      gateway4: 192.168.1.1\n"
            "      gateway6: 2001:db8::1\n"
            "      nameservers:\n"
            "        addresses: [192.168.1.53, 2001:db8::53]\n"
            "        search: [example.com]\n"
        )
        assert set(files) == {"eth0.nmconnection"}
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["connection"]["type"] == "ethernet"
        assert kf["connection"]["interface-name"] == "eth0"
        assert kf["ipv4"]["method"] == "manual"
        assert kf["ipv4"]["address1"] == "192.168.1.10/24"
        assert kf["ipv4"]["gateway"] == "192.168.1.1"
        assert kf["ipv4"]["dns"] == "192.168.1.53;"
        assert kf["ipv4"]["dns-search"] == "example.com;"
        assert kf["ipv6"]["method"] == "manual"
        assert kf["ipv6"]["address1"] == "2001:db8::5/64"
        assert kf["ipv6"]["gateway"] == "2001:db8::1"
        assert kf["ipv6"]["dns"] == "2001:db8::53;"

    def test_dhcp_only(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0: {dhcp4: true}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["ipv4"]["method"] == "auto"
        # No DHCPv6 / no v6 address -> link-local only, predictable default.
        assert kf["ipv6"]["method"] == "link-local"
        assert "address1" not in kf["ipv4"]

    def test_ipv4_only_disables_no_address(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0: {addresses: [10.0.0.2/24]}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["ipv4"]["method"] == "manual"
        assert kf["ipv6"]["method"] == "link-local"

    def test_match_by_mac_binds_by_hardware(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    primary:\n"
            '      match: {macaddress: "aa:bb:cc:dd:ee:ff"}\n'
            "      dhcp4: true\n"
        )
        kf = _parse_keyfile(files["primary.nmconnection"])
        # Bound by MAC: no interface-name, MAC uppercased in [ethernet].
        assert "interface-name" not in kf["connection"]
        assert kf["ethernet"]["mac-address"] == "AA:BB:CC:DD:EE:FF"

    def test_match_by_name_sets_interface_name(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    lan:\n"
            "      match: {name: enp3s0}\n"
            "      dhcp4: true\n"
        )
        kf = _parse_keyfile(files["lan.nmconnection"])
        assert kf["connection"]["interface-name"] == "enp3s0"

    def test_extra_route(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0:\n"
            "      addresses: [192.168.1.10/24]\n"
            "      routes:\n"
            "        - {to: 10.0.0.0/8, via: 192.168.1.254, metric: 50}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["ipv4"]["route1"] == "10.0.0.0/8,192.168.1.254,50"

    def test_default_route_expands_per_family(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0:\n"
            "      addresses: [192.168.1.10/24]\n"
            "      routes:\n"
            "        - {to: default, via: 192.168.1.1}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["ipv4"]["route1"] == "0.0.0.0/0,192.168.1.1"

    def test_vlan_references_parent_by_uuid(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0: {dhcp4: true}\n"
            "  vlans:\n"
            "    vlan100:\n"
            "      id: 100\n"
            "      link: eth0\n"
            "      addresses: [10.100.0.5/24]\n"
        )
        eth = _parse_keyfile(files["eth0.nmconnection"])
        vlan = _parse_keyfile(files["vlan100.nmconnection"])
        assert vlan["connection"]["type"] == "vlan"
        assert vlan["vlan"]["id"] == "100"
        # The parent reference is the ethernet's connection UUID, so it
        # resolves regardless of how the parent binds (name or MAC).
        assert vlan["vlan"]["parent"] == eth["connection"]["uuid"]
        assert vlan["ipv4"]["address1"] == "10.100.0.5/24"

    def test_ipv6_only_static(self):
        # No v4 address at all: v4 disabled, v6 carries the static config.
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0:\n"
            "      addresses: [2001:db8::10/64]\n"
            "      gateway6: 2001:db8::1\n"
            "      nameservers: {addresses: [2001:db8::53]}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["ipv4"]["method"] == "disabled"
        assert kf["ipv6"]["method"] == "manual"
        assert kf["ipv6"]["address1"] == "2001:db8::10/64"
        assert kf["ipv6"]["gateway"] == "2001:db8::1"
        assert kf["ipv6"]["dns"] == "2001:db8::53;"
        # A v4-disabled section must not carry DNS or addresses.
        assert "dns" not in kf["ipv4"]
        assert "address1" not in kf["ipv4"]

    def test_dhcp6_slaac_is_auto(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0: {dhcp6: true}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        # dhcp6/SLAAC maps to NM's ipv6 method=auto (accept RA + DHCPv6).
        assert kf["ipv6"]["method"] == "auto"
        assert kf["ipv4"]["method"] == "disabled"

    def test_ipv6_default_route(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0:\n"
            "      addresses: [2001:db8::10/64]\n"
            "      routes:\n"
            "        - {to: default, via: 2001:db8::1}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["ipv6"]["route1"] == "::/0,2001:db8::1"
        # The IPv6 default route must not leak into the IPv4 section.
        assert not any(k.startswith("route") for k in kf["ipv4"])

    def test_dual_stack_routes_split_by_family(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0:\n"
            "      addresses: [192.168.1.10/24, 2001:db8::10/64]\n"
            "      routes:\n"
            "        - {to: 10.0.0.0/8, via: 192.168.1.254}\n"
            "        - {to: 'fd00::/8', via: '2001:db8::254'}\n"
        )
        kf = _parse_keyfile(files["eth0.nmconnection"])
        assert kf["ipv4"]["route1"] == "10.0.0.0/8,192.168.1.254"
        assert kf["ipv6"]["route1"] == "fd00::/8,2001:db8::254"

    def test_vlan_carries_ipv6(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0: {dhcp4: true}\n"
            "  vlans:\n"
            "    vlan100:\n"
            "      id: 100\n"
            "      link: eth0\n"
            "      addresses: [2001:db8:100::5/64]\n"
            "      gateway6: 2001:db8:100::1\n"
        )
        kf = _parse_keyfile(files["vlan100.nmconnection"])
        assert kf["ipv6"]["method"] == "manual"
        assert kf["ipv6"]["address1"] == "2001:db8:100::5/64"
        assert kf["ipv6"]["gateway"] == "2001:db8:100::1"

    def test_wifi_wpa_psk(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  wifis:\n"
            "    wlan0:\n"
            "      dhcp4: true\n"
            "      access-points:\n"
            '        "HomeNet": {password: "hunter2pass"}\n'
        )
        assert set(files) == {"wlan0.nmconnection"}
        kf = _parse_keyfile(files["wlan0.nmconnection"])
        assert kf["connection"]["type"] == "wifi"
        assert kf["connection"]["id"] == "HomeNet"
        assert kf["connection"]["interface-name"] == "wlan0"
        assert kf["wifi"]["ssid"] == "HomeNet"
        assert kf["wifi"]["mode"] == "infrastructure"
        assert kf["wifi-security"]["key-mgmt"] == "wpa-psk"
        assert kf["wifi-security"]["psk"] == "hunter2pass"
        assert kf["ipv4"]["method"] == "auto"

    def test_wifi_open_network_has_no_security(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  wifis:\n"
            "    wlan0:\n"
            "      dhcp4: true\n"
            "      access-points:\n"
            '        "Cafe": {}\n'
        )
        kf = _parse_keyfile(files["wlan0.nmconnection"])
        assert not kf.has_section("wifi-security")

    def test_wifi_hidden_and_mac_bind(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  wifis:\n"
            "    wlan0:\n"
            '      match: {macaddress: "aa:bb:cc:dd:ee:01"}\n'
            "      dhcp4: true\n"
            "      access-points:\n"
            '        "Hidden": {password: "secretpass", hidden: true}\n'
        )
        kf = _parse_keyfile(files["wlan0.nmconnection"])
        assert kf["wifi"]["hidden"] == "true"
        # bound by MAC -> no interface-name, mac in [wifi]
        assert "interface-name" not in kf["connection"]
        assert kf["wifi"]["mac-address"] == "AA:BB:CC:DD:EE:01"

    def test_wifi_static_dual_stack(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  wifis:\n"
            "    wlan0:\n"
            "      addresses: [10.0.0.5/24, 2001:db8::5/64]\n"
            "      gateway4: 10.0.0.1\n"
            "      gateway6: 2001:db8::1\n"
            "      access-points:\n"
            '        "Net": {password: "passw0rd1"}\n'
        )
        kf = _parse_keyfile(files["wlan0.nmconnection"])
        assert kf["ipv4"]["address1"] == "10.0.0.5/24"
        assert kf["ipv6"]["address1"] == "2001:db8::5/64"
        assert kf["ipv6"]["gateway"] == "2001:db8::1"

    def test_wifi_multiple_access_points_get_suffixed_files(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  wifis:\n"
            "    wlan0:\n"
            "      dhcp4: true\n"
            "      access-points:\n"
            '        "First": {password: "firstpass"}\n'
            '        "Second": {password: "secondpass"}\n'
        )
        assert set(files) == {"wlan0-1.nmconnection", "wlan0-2.nmconnection"}
        ids = {_parse_keyfile(c)["connection"]["id"] for c in files.values()}
        assert ids == {"First", "Second"}
        # distinct uuids per access point
        uuids = {_parse_keyfile(c)["connection"]["uuid"] for c in files.values()}
        assert len(uuids) == 2

    def test_uuid_is_deterministic(self):
        net = (
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    eth0: {dhcp4: true}\n"
        )
        a = _render(net)["eth0.nmconnection"]
        b = _render(net)["eth0.nmconnection"]
        assert a == b
        assert _parse_keyfile(a)["connection"]["uuid"]


class TestEap8021x:
    def test_wired_peap(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  ethernets:\n"
            "    lan0:\n"
            "      dhcp4: true\n"
            "      auth:\n"
            "        method: peap\n"
            "        identity: host/ws.example.com\n"
            "        ca-certificate: /etc/ssl/certs/corp.pem\n"
            "        anonymous-identity: anon@example.com\n"
            "        phase2-auth: mschapv2\n"
            "        password: secret123\n"
        )
        kf = _parse_keyfile(files["lan0.nmconnection"])
        assert kf["802-1x"]["eap"] == "peap"
        assert kf["802-1x"]["identity"] == "host/ws.example.com"
        assert kf["802-1x"]["ca-cert"] == "/etc/ssl/certs/corp.pem"
        assert kf["802-1x"]["anonymous-identity"] == "anon@example.com"
        assert kf["802-1x"]["phase2-auth"] == "mschapv2"
        assert kf["802-1x"]["password"] == "secret123"
        assert kf["connection"]["type"] == "ethernet"

    def test_wifi_eap_tls(self):
        files = _render(
            "network:\n"
            "  version: 2\n"
            "  wifis:\n"
            "    wlan0:\n"
            "      access-points:\n"
            '        "CorpTLS":\n'
            "          auth:\n"
            "            method: tls\n"
            "            identity: ws01\n"
            "            ca-certificate: /etc/ssl/certs/corp.pem\n"
            "            client-certificate: /etc/ssl/certs/ws01.pem\n"
            "            client-key: /etc/ssl/private/ws01.key\n"
            "            client-key-password: keypass\n"
        )
        kf = _parse_keyfile(files["wlan0.nmconnection"])
        # EAP wifi uses wpa-eap, not wpa-psk
        assert kf["wifi-security"]["key-mgmt"] == "wpa-eap"
        assert "psk" not in kf["wifi-security"]
        assert kf["802-1x"]["eap"] == "tls"
        assert kf["802-1x"]["client-cert"] == "/etc/ssl/certs/ws01.pem"
        assert kf["802-1x"]["private-key"] == "/etc/ssl/private/ws01.key"
        assert kf["802-1x"]["private-key-password"] == "keypass"

    def test_no_8021x_section_without_auth(self):
        files = _render(
            "network:\n  version: 2\n  ethernets:\n    eth0: {dhcp4: true}\n")
        assert not _parse_keyfile(files["eth0.nmconnection"]).has_section("802-1x")
