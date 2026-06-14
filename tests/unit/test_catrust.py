"""Unit tests for the CA-trust backend (Debian/update-ca-certificates)."""

import pytest

import catrust
import schema
from commandrunner import CommandRunner

CERT = ("-----BEGIN CERTIFICATE-----\n"
        "MIIDcorporaterootca\n"
        "-----END CERTIFICATE-----")


class FakeRunner(CommandRunner):
    def __init__(self, rc=0):
        super().__init__(log=lambda *a: None)
        self.commands = []
        self.rc = rc

    def run(self, command, check=False, secrets=(), stdin=None):
        self.commands.append(command)
        return self.rc


def _ca_config(yaml_block):
    base = (
        "version: 1\n"
        "locale: en_US.UTF-8\n"
        "timezone: America/Toronto\n"
        'users: [{name: a, passwd: "$6$rounds=4096$s$h"}]\n'
        "storage: {target: {match: {first-non-removable: true}}, layout: simple}\n"
    )
    return schema.parse_config(base + yaml_block).ca_certs


def _backend(runner, target, policy=None):
    calls = []
    policy = policy or (lambda m, msg: calls.append((m, msg)))
    b = catrust.DebianCaTrustBackend(runner, policy, lambda *a: None, target=target)
    b._policy_calls = calls
    return b


class TestGetBackend:
    def test_debian(self):
        b = catrust.get_ca_trust_backend(FakeRunner(), lambda *a: None,
                                         lambda *a: None)
        assert isinstance(b, catrust.DebianCaTrustBackend)

    def test_unknown_family_raises(self):
        with pytest.raises(ValueError):
            catrust.get_ca_trust_backend(FakeRunner(), lambda *a: None,
                                         lambda *a: None, family="redhat")


class TestDebianCaTrust:
    def test_writes_cert_and_updates(self, tmp_path):
        cfg = _ca_config('ca_certs:\n  trusted:\n    - "%s"\n'
                         % CERT.replace("\n", "\\n"))
        runner = FakeRunner()
        _backend(runner, str(tmp_path)).apply(cfg)
        crt = tmp_path / "usr/local/share/ca-certificates/li-ca-1.crt"
        assert crt.exists()
        body = crt.read_text()
        assert "BEGIN CERTIFICATE" in body and body.endswith("\n")
        assert (crt.stat().st_mode & 0o777) == 0o644
        # plain update (defaults kept)
        joined = "\n".join(runner.commands)
        assert "update-ca-certificates" in joined
        assert "--fresh" not in joined
        assert "ca-certificates.conf" not in joined

    def test_multiple_certs_indexed(self, tmp_path):
        cfg = _ca_config(
            "ca_certs:\n  trusted:\n    - \"%s\"\n    - \"%s\"\n"
            % (CERT.replace("\n", "\\n"), CERT.replace("\n", "\\n")))
        _backend(FakeRunner(), str(tmp_path)).apply(cfg)
        d = tmp_path / "usr/local/share/ca-certificates"
        assert sorted(p.name for p in d.iterdir()) == ["li-ca-1.crt", "li-ca-2.crt"]

    def test_remove_defaults_disables_and_refreshes(self, tmp_path):
        cfg = _ca_config(
            "ca_certs:\n  remove_defaults: true\n  trusted:\n    - \"%s\"\n"
            % CERT.replace("\n", "\\n"))
        runner = FakeRunner()
        _backend(runner, str(tmp_path)).apply(cfg)
        joined = "\n".join(runner.commands)
        assert "ca-certificates.conf" in joined        # defaults disabled
        assert "update-ca-certificates --fresh" in joined

    def test_update_failure_invokes_policy(self, tmp_path):
        cfg = _ca_config('ca_certs:\n  trusted:\n    - "%s"\n'
                         % CERT.replace("\n", "\\n"))
        runner = FakeRunner(rc=1)
        b = _backend(runner, str(tmp_path))
        b.apply(cfg)
        assert any(m == "post_install_script_failure" for m, _ in b._policy_calls)
