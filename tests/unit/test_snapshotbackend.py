"""Unit tests for the snapshot backend (Timeshift on btrfs)."""

import json

import pytest

import schema
import snapshotbackend
from commandrunner import CommandRunner

BTRFS_STORAGE = """\
version: 1
locale: en_US.UTF-8
timezone: America/Toronto
users: [{name: a, passwd: "$6$rounds=4096$s$h"}]
storage:
  target: {match: {first-non-removable: true}}
  layout: custom
  partitions:
    - {size: 512MB, mount: /boot/efi, filesystem: vfat, flags: [esp]}
    - size: rest
      filesystem: btrfs
      subvolumes:
        - {name: "@", mount: /}
        - {name: "@home", mount: /home}
"""


class FakeRunner(CommandRunner):
    def __init__(self, rc=0):
        super().__init__(log=lambda *a: None)
        self.commands = []
        self.rc = rc

    def run(self, command, check=False, secrets=(), stdin=None):
        self.commands.append(command)
        return self.rc


def _config(snapshots_block):
    return schema.parse_config(BTRFS_STORAGE + snapshots_block)


def _backend(runner, target):
    calls = []
    b = snapshotbackend.TimeshiftBtrfsBackend(
        runner, lambda m, msg: calls.append((m, msg)), lambda *a: None,
        target=target)
    b._policy_calls = calls
    return b


class TestRenderConfig:
    def test_schedule_translation(self):
        cfg = _config("snapshots:\n  schedule: {boot: true, daily: 5, weekly: 3}\n")
        b = snapshotbackend.TimeshiftBtrfsBackend(None, None, None)
        j = b.render_config(cfg.snapshots)
        assert j["btrfs_mode"] == "true"
        assert j["schedule_boot"] == "true" and j["count_boot"] == "5"
        assert j["schedule_daily"] == "true" and j["count_daily"] == "5"
        assert j["schedule_weekly"] == "true" and j["count_weekly"] == "3"
        # monthly not requested -> disabled, count 0
        assert j["schedule_monthly"] == "false" and j["count_monthly"] == "0"


class TestApply:
    def test_writes_json_script_and_service(self, tmp_path):
        cfg = _config(
            "snapshots:\n  schedule: {daily: 5}\n  initial_snapshot: true\n")
        runner = FakeRunner()
        _backend(runner, str(tmp_path)).apply(cfg.snapshots, cfg.storage)
        # timeshift.json
        conf = tmp_path / "etc/timeshift/timeshift.json"
        data = json.loads(conf.read_text())
        assert data["btrfs_mode"] == "true"
        assert data["count_daily"] == "5"
        # first-boot one-shot installed + enabled, with the initial snapshot
        script = tmp_path / "usr/local/sbin/li-timeshift-firstboot"
        assert script.exists() and (script.stat().st_mode & 0o111)
        body = script.read_text()
        assert "timeshift --check" in body
        assert "timeshift --create" in body  # initial_snapshot: true
        unit = tmp_path / "etc/systemd/system/li-timeshift-firstboot.service"
        assert "ExecStart=/usr/local/sbin/li-timeshift-firstboot" in unit.read_text()
        joined = "\n".join(runner.commands)
        assert "apt-get install -y timeshift" in joined
        assert "systemctl enable li-timeshift-firstboot.service" in joined

    def test_no_initial_snapshot_omits_create(self, tmp_path):
        cfg = _config("snapshots:\n  schedule: {daily: 5}\n")
        _backend(FakeRunner(), str(tmp_path)).apply(cfg.snapshots, cfg.storage)
        body = (tmp_path / "usr/local/sbin/li-timeshift-firstboot").read_text()
        assert "timeshift --create" not in body

    def test_timeshift_install_failure_invokes_policy(self, tmp_path):
        cfg = _config("snapshots:\n  schedule: {daily: 5}\n")
        b = _backend(FakeRunner(rc=1), str(tmp_path))
        b.apply(cfg.snapshots, cfg.storage)
        assert any(m == "package_install_failure" for m, _ in b._policy_calls)
        # config not written when the package install failed
        assert not (tmp_path / "etc/timeshift/timeshift.json").exists()


class TestGetBackend:
    def test_btrfs_ok(self):
        cfg = _config("snapshots:\n  enabled: true\n")
        b = snapshotbackend.get_snapshot_backend(
            cfg, FakeRunner(), lambda *a: None, lambda *a: None)
        assert isinstance(b, snapshotbackend.TimeshiftBtrfsBackend)
