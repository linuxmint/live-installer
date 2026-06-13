"""Unit tests for netboot answer-file auto-discovery."""

import os

import pytest

import discovery
from discovery import DiscoveryError


class TestParseAutoTrigger:
    def test_plain_auto(self):
        assert discovery.parse_auto_trigger("auto") == (True, None)

    def test_auto_with_base(self):
        assert discovery.parse_auto_trigger(
            "auto:https://cfg.example.com/x/") == (
            True, "https://cfg.example.com/x/")

    def test_auto_prefix_empty_base(self):
        assert discovery.parse_auto_trigger("auto:") == (True, None)

    def test_not_discovery(self):
        assert discovery.parse_auto_trigger(
            "https://h/file.yaml") == (False, None)
        assert discovery.parse_auto_trigger("/cdrom/x.yaml") == (False, None)


class TestCandidateSources:
    def test_order_most_specific_first(self):
        got = discovery.candidate_sources(
            "https://cfg/base/",
            macs=["aa:bb:cc:00:11:22", "aa:bb:cc:00:11:33"],
            serial="SN123", uuid="UUID-9",
            media_dirs=["/cdrom"])
        assert got == [
            "https://cfg/base/by-mac/aa:bb:cc:00:11:22.yaml",
            "https://cfg/base/by-mac/aa:bb:cc:00:11:33.yaml",
            "https://cfg/base/by-serial/SN123.yaml",
            "https://cfg/base/by-uuid/UUID-9.yaml",
            "https://cfg/base/default.yaml",
            "/cdrom/auto-install.yaml",
        ]

    def test_no_base_is_local_media_only(self):
        got = discovery.candidate_sources(
            None, macs=["aa:bb:cc:00:11:22"],
            media_dirs=["/cdrom", "/run/live/medium"])
        assert got == [
            "/cdrom/auto-install.yaml",
            "/run/live/medium/auto-install.yaml",
        ]

    def test_missing_serial_uuid_skipped(self):
        got = discovery.candidate_sources(
            "https://cfg", macs=[], serial=None, uuid=None, media_dirs=[])
        assert got == ["https://cfg/default.yaml"]

    def test_ipv6_base_url_preserved(self):
        # A bracketed IPv6 base must survive into the candidate URLs intact.
        got = discovery.candidate_sources(
            "https://[2001:db8::1]:8443/cfg/",
            macs=["aa:bb:cc:00:11:22"], serial="SN1", media_dirs=[])
        assert got[0] == (
            "https://[2001:db8::1]:8443/cfg/by-mac/aa:bb:cc:00:11:22.yaml")
        assert got[-1] == "https://[2001:db8::1]:8443/cfg/default.yaml"


class TestReadMachineIdentity:
    @pytest.fixture
    def fake_sys(self, tmp_path):
        net = tmp_path / "net"
        dmi = tmp_path / "dmi"
        # eth0: real ethernet; wlan0: wifi (type 803, excluded);
        # lo: loopback (excluded); bond0: shares eth0's MAC (deduped)
        for name, typ, addr in [
            ("eth0", "1", "AA:BB:CC:00:11:22"),
            ("wlan0", "803", "dd:ee:ff:00:11:22"),
            ("lo", "772", "00:00:00:00:00:00"),
            ("bond0", "1", "AA:BB:CC:00:11:22"),
        ]:
            d = net / name
            d.mkdir(parents=True)
            (d / "type").write_text(typ + "\n")
            (d / "address").write_text(addr + "\n")
        dmi.mkdir()
        (dmi / "product_serial").write_text("SN-12345\n")
        (dmi / "product_uuid").write_text("4C4C-ABCD\n")
        return dict(net_dir=str(net), dmi_dir=str(dmi))

    def test_collects_and_normalizes(self, fake_sys):
        ident = discovery.read_machine_identity(**fake_sys)
        # only ethernet, lowercased, deduped (bond0 shares eth0's MAC)
        assert ident["macs"] == ["aa:bb:cc:00:11:22"]
        assert ident["serial"] == "SN-12345"
        assert ident["uuid"] == "4C4C-ABCD"

    def test_missing_dmi_is_none(self, tmp_path):
        ident = discovery.read_machine_identity(
            net_dir=str(tmp_path / "none"), dmi_dir=str(tmp_path / "none2"))
        assert ident == {"macs": [], "serial": None, "uuid": None}


class TestDiscover:
    def test_first_valid_wins(self):
        store = {"b/default.yaml": "VALID"}

        def fetch(src):
            name = src
            if name in store:
                return store[name]
            raise KeyError("not found")

        used = []
        src, text = discovery.discover(
            ["b/by-mac/x.yaml", "b/default.yaml", "b/other.yaml"],
            fetch=fetch,
            validate=lambda t: used.append(t),  # validates anything
            log=lambda _m: None)
        assert src == "b/default.yaml"
        assert text == "VALID"

    def test_skips_present_but_invalid(self):
        store = {"a.yaml": "BAD", "b.yaml": "GOOD"}

        def validate(text):
            if text == "BAD":
                raise ValueError("schema error")

        src, text = discovery.discover(
            ["a.yaml", "b.yaml"],
            fetch=lambda s: store[s],
            validate=validate)
        assert (src, text) == ("b.yaml", "GOOD")

    def test_none_found_reports_attempts(self):
        with pytest.raises(DiscoveryError) as excinfo:
            discovery.discover(
                ["a.yaml", "b.yaml"],
                fetch=lambda s: (_ for _ in ()).throw(OSError("404")),
                validate=lambda t: None)
        msg = str(excinfo.value)
        assert "a.yaml" in msg and "b.yaml" in msg

    def test_nothing_to_try(self):
        with pytest.raises(DiscoveryError) as excinfo:
            discovery.discover([], fetch=lambda s: s, validate=lambda t: None)
        assert "nothing to try" in str(excinfo.value)
