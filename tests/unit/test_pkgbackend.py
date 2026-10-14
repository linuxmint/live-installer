"""Unit tests for the swappable package backend (apt today)."""

import pytest

import pkgbackend
import schema
from commandrunner import CommandRunner


class FakeRunner(CommandRunner):
    """Records every command; can be told to fail commands matching a
    substring (so failure-policy paths are exercisable)."""

    def __init__(self, fail_substrings=()):
        super().__init__(log=lambda *a: None)
        self.commands = []
        self.host_commands = []     # run() (not chroot-wrapped)
        self.fail_substrings = list(fail_substrings)

    def run(self, command, check=False, secrets=(), stdin=None):
        self.commands.append(command)
        if "chroot " not in command:
            self.host_commands.append(command)
        for bad in self.fail_substrings:
            if bad in command:
                return 1
        return 0

    def output(self, command, check=False):
        self.commands.append(command)
        return ""


def _apt_config(yaml_sources):
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
    return schema.parse_config(base + yaml_sources)


def _backend(runner, target="/target", policy=None):
    calls = []
    policy = policy or (lambda mode, msg: calls.append((mode, msg)))
    backend = pkgbackend.AptBackend(runner, policy, lambda *a: None,
                                    target=target)
    backend._policy_calls = calls
    return backend


def _joined(runner):
    return "\n".join(runner.commands)


class TestGetBackend:
    def test_debian_is_apt(self):
        b = pkgbackend.get_backend(FakeRunner(), lambda *a: None, lambda *a: None)
        assert isinstance(b, pkgbackend.AptBackend)

    def test_unknown_family_raises(self):
        with pytest.raises(ValueError):
            pkgbackend.get_backend(FakeRunner(), lambda *a: None,
                                   lambda *a: None, family="redhat")


class TestAptBackend:
    def test_source_with_key_url(self):
        cfg = _apt_config(
            "apt:\n"
            "  sources:\n"
            "    vendor:\n"
            '      source: "deb https://example.com/repo trixie main"\n'
            "      key_url: https://example.com/repo.gpg\n"
        )
        runner = FakeRunner()
        _backend(runner).apply(cfg.apt, ["openssh-server"], [])
        joined = _joined(runner)
        assert "wget -O /etc/apt/trusted.gpg.d/vendor.asc" in joined
        assert "https://example.com/repo.gpg" in joined
        # source line written to a per-name .list
        assert "/etc/apt/sources.list.d/vendor.list" in joined
        assert "deb https://example.com/repo trixie main" in joined
        assert "apt-get update" in joined
        assert "apt-get install -y openssh-server" in joined

    def test_source_with_keyid_uses_keyserver(self):
        cfg = _apt_config(
            "apt:\n"
            "  sources:\n"
            "    ks:\n"
            '      source: "deb https://other.example/deb stable main"\n'
            '      keyid: "0xABCDEF0123456789"\n'
            "      keyserver: keys.example.org\n"
        )
        runner = FakeRunner()
        _backend(runner).apply(cfg.apt, [], [])
        joined = _joined(runner)
        assert "--keyserver keys.example.org" in joined
        assert "--recv-keys 0xABCDEF0123456789" in joined
        assert "/etc/apt/trusted.gpg.d/ks.gpg" in joined

    def test_inline_key_written_to_target(self, tmp_path):
        cfg = _apt_config(
            "apt:\n"
            "  sources:\n"
            "    inline:\n"
            '      source: "deb https://example.com/repo trixie main"\n'
            "      key: |\n"
            "        -----BEGIN PGP PUBLIC KEY BLOCK-----\n"
            "        abc\n"
            "        -----END PGP PUBLIC KEY BLOCK-----\n"
        )
        runner = FakeRunner()
        _backend(runner, target=str(tmp_path)).apply(cfg.apt, [], [])
        keyfile = tmp_path / "etc/apt/trusted.gpg.d/inline.asc"
        assert keyfile.exists()
        assert "BEGIN PGP PUBLIC KEY" in keyfile.read_text()
        # no key fetch over the network for an inline key
        assert "wget" not in _joined(runner)
        assert "recv-keys" not in _joined(runner)

    def test_filename_override(self):
        cfg = _apt_config(
            "apt:\n"
            "  sources:\n"
            "    vendor:\n"
            '      source: "deb https://example.com/repo trixie main"\n'
            "      filename: custom-name\n"
        )
        runner = FakeRunner()
        _backend(runner).apply(cfg.apt, [], [])
        joined = _joined(runner)
        assert "/etc/apt/sources.list.d/custom-name.list" in joined
        assert "/etc/apt/sources.list.d/vendor.list" not in joined

    def test_packages_only_still_updates(self):
        runner = FakeRunner()
        _backend(runner).apply(None, ["htop"], [])
        joined = _joined(runner)
        assert "apt-get update" in joined
        assert "apt-get install -y htop" in joined

    def test_remove(self):
        runner = FakeRunner()
        _backend(runner).apply(None, [], ["hexchat"])
        assert "apt-get remove --purge -y hexchat" in _joined(runner)
        # no source/update work when only removing
        assert "apt-get update" not in _joined(runner)

    def test_noop_when_nothing_to_do(self):
        runner = FakeRunner()
        _backend(runner).apply(None, [], [])
        assert not any("apt-get" in c for c in runner.commands)

    def test_update_failure_invokes_policy(self):
        runner = FakeRunner(fail_substrings=["apt-get update"])
        backend = _backend(runner)
        backend.apply(None, ["htop"], [])
        assert ("network_unavailable", "apt-get update failed") \
            in backend._policy_calls

    def test_install_failure_invokes_policy(self):
        runner = FakeRunner(fail_substrings=["apt-get install -y"])
        backend = _backend(runner)
        backend.apply(None, ["htop"], [])
        assert ("package_install_failure", "package installation failed") \
            in backend._policy_calls

    def test_key_fetch_failure_invokes_policy(self):
        cfg = _apt_config(
            "apt:\n"
            "  sources:\n"
            "    vendor:\n"
            '      source: "deb https://example.com/repo trixie main"\n'
            "      key_url: https://example.com/repo.gpg\n"
        )
        runner = FakeRunner(fail_substrings=["wget"])
        backend = _backend(runner)
        backend.apply(cfg.apt, [], [])
        assert any(mode == "network_unavailable"
                   for mode, _ in backend._policy_calls)
