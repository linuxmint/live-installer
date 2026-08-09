#!/usr/bin/python3
# coding: utf-8
"""Snapshot backends: configure a snapshot tool in the installed system.

Mirrors pkgbackend.py / catrust.py: the driver holds one backend (picked per
config), called through the CommandRunner chroot boundary, unit-testable with a
recording runner, no snapshot library imported at load time. v1 has one
concrete backend (Timeshift on btrfs); the seam keeps rsync/Snapper addable as
a new class + a get_snapshot_backend() branch, not a refactor.

Split across two phases (deliberately): install-time writes timeshift.json
(storage-coupled — it must agree with the btrfs @/@home layout decided in
storage); a first-boot one-shot registers the schedule timers and takes the
optional initial snapshot (Timeshift wants a booted system for that, and the
first snapshot is only meaningful after first boot). Both read the SAME
timeshift.json — only activation is split, never config authorship.

NOTE: the exact timeshift.json path and keys have shifted across Timeshift
releases (/etc/timeshift.json -> /etc/timeshift/timeshift.json). This targets
the current layout; the integration test validates it against the Timeshift
actually shipped on the test image.
"""

import json
import os

# First-boot one-shot: registers Timeshift's schedule from the config we wrote
# and (optionally) takes the initial snapshot, then disables itself.
_FIRSTBOOT_SERVICE = """\
[Unit]
Description=First-boot Timeshift setup (live-installer)
After=network-online.target
Wants=network-online.target
ConditionPathExists=/usr/local/sbin/li-timeshift-firstboot

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/li-timeshift-firstboot
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
"""


def _firstboot_script(initial_snapshot):
    take = ('timeshift --create --comments "Initial snapshot (live-installer)" '
            '--tags O || true\n') if initial_snapshot else ""
    return ("#!/bin/sh\n"
            "# Installed by live-installer (snapshots: timeshift-btrfs).\n"
            "# Registers Timeshift's schedule from /etc/timeshift/timeshift.json\n"
            "# and (optionally) takes the first snapshot, then self-disables.\n"
            "set -u\n"
            "timeshift --check || true\n"
            + take +
            "systemctl disable li-timeshift-firstboot.service\n"
            "rm -f /etc/systemd/system/li-timeshift-firstboot.service "
            "/usr/local/sbin/li-timeshift-firstboot\n")


class SnapshotBackend:
    def __init__(self, runner, policy, log, target="/target"):
        self.runner = runner
        self.policy = policy
        self.log = log
        self.target = target

    def is_compatible(self, storage):
        raise NotImplementedError

    def apply(self, snapshots, storage):
        raise NotImplementedError


def _has_btrfs_root(storage):
    """Duck-typed btrfs-@-root check (so this module needn't import schema)."""
    if getattr(storage, "layout", None) != "custom":
        return False
    for entry in list(getattr(storage, "partitions", []) or []) + \
            list(getattr(storage, "lvm", []) or []):
        if getattr(entry, "filesystem", None) == "btrfs":
            for sub in getattr(entry, "subvolumes", []) or []:
                if getattr(sub, "name", None) == "@" \
                        and getattr(sub, "mount", None) == "/":
                    return True
    return False


class TimeshiftBtrfsBackend(SnapshotBackend):
    CONFIG_DIR = "/etc/timeshift"

    def is_compatible(self, storage):
        return _has_btrfs_root(storage)

    def render_config(self, snapshots):
        """Build the timeshift.json dict (Timeshift stores bools/ints as
        strings). Public so the unit test can assert on it directly."""
        sched = snapshots.schedule
        def b(value):
            return "true" if value else "false"
        return {
            "btrfs_mode": "true",
            "include_btrfs_home_for_backup": "true",
            "stop_cron_emails": "true",
            "schedule_hourly": "false",
            "schedule_boot": b(sched.boot),
            "schedule_daily": b(sched.daily > 0),
            "schedule_weekly": b(sched.weekly > 0),
            "schedule_monthly": b(sched.monthly > 0),
            "count_hourly": "0",
            "count_boot": str(5 if sched.boot else 0),
            "count_daily": str(sched.daily),
            "count_weekly": str(sched.weekly),
            "count_monthly": str(sched.monthly),
        }

    def apply(self, snapshots, storage):
        # Timeshift may not be on the base image (it is on Mint, not
        # necessarily LMDE/minimal); make sure it is present.
        rc = self.runner.chroot(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y timeshift")
        if rc != 0:
            self.policy("package_install_failure",
                        "installing timeshift failed")
            return
        self.log(" --> Writing Timeshift configuration")
        conf_dir = self.target + self.CONFIG_DIR
        os.makedirs(conf_dir, exist_ok=True)   # package may not pre-create it
        with open(os.path.join(conf_dir, "timeshift.json"), "w") as f:
            json.dump(self.render_config(snapshots), f, indent=2)
            f.write("\n")
        # First-boot one-shot for schedule registration + initial snapshot.
        sbindir = self.target + "/usr/local/sbin"
        os.makedirs(sbindir, exist_ok=True)
        script = os.path.join(sbindir, "li-timeshift-firstboot")
        with open(script, "w") as f:
            f.write(_firstboot_script(snapshots.initial_snapshot))
        os.chmod(script, 0o755)
        os.makedirs(self.target + "/etc/systemd/system", exist_ok=True)
        with open(self.target + "/etc/systemd/system/"
                  "li-timeshift-firstboot.service", "w") as f:
            f.write(_FIRSTBOOT_SERVICE)
        self.runner.chroot("systemctl enable li-timeshift-firstboot.service")


def get_snapshot_backend(config, runner, policy, log, *, target="/target"):
    """Pick a snapshot backend, or raise with a specific reason. Only
    timeshift-btrfs today; the schema cross-validation has already guaranteed a
    btrfs root by the time we get here."""
    backend = config.snapshots.backend
    if backend == "timeshift-btrfs":
        b = TimeshiftBtrfsBackend(runner, policy, log, target=target)
        if not b.is_compatible(config.storage):
            raise ValueError(
                "backend timeshift-btrfs requires a btrfs @/@home root")
        return b
    raise ValueError(f"no snapshot backend for {backend!r}")
