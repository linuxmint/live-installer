"""SSH-based assertions against an installed system.

Used by run_scenario.py after rebooting into the installed disk.  The
answer file used during installation is expected to have created a test
user, installed openssh-server and authorized the harness's SSH key —
that wiring lands together with the headless installer driver.
"""

import socket
import subprocess
import time


def wait_for_ssh(host, port, timeout_s, poll_interval=3):
    """Wait until something speaking SSH accepts on host:port."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                banner = sock.recv(64)
                if banner.startswith(b"SSH-"):
                    return True
        except OSError:
            pass
        time.sleep(poll_interval)
    return False


def _ssh_base(host, port, ssh_cfg):
    cmd = [
        "ssh",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-o", "LogLevel=ERROR",
    ]
    if ssh_cfg.get("key"):
        cmd += ["-i", str(ssh_cfg["key"])]
    user = ssh_cfg.get("user", "testuser")
    return cmd + [f"{user}@{host}"]


def _run(host, port, ssh_cfg, command, timeout_s=60):
    return subprocess.run(
        _ssh_base(host, port, ssh_cfg) + [command],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def run_assertions(host, port, ssh_cfg, assertions):
    """Run scenario `verify` entries; returns [(name, ok, detail), ...].

    Supported assertion types:
      - command:      run a shell command; check expect_rc (default 0)
                      and/or expect_substring against stdout
      - file_exists:  path must exist on the installed system
      - mount_source: mountpoint must be backed by a source matching
                      a substring (e.g. /  on  /dev/mapper/ for LUKS)
    """
    results = []
    for index, entry in enumerate(assertions):
        kind = entry.get("type", "command")
        name = entry.get("name", f"{kind}-{index}")
        try:
            if kind == "command":
                proc = _run(host, port, ssh_cfg, entry["command"])
                ok = proc.returncode == entry.get("expect_rc", 0)
                detail = f"rc={proc.returncode}"
                want = entry.get("expect_substring")
                if ok and want is not None:
                    ok = want in proc.stdout
                    if not ok:
                        detail = f"{want!r} not in stdout: {proc.stdout[-500:]!r}"
            elif kind == "file_exists":
                proc = _run(host, port, ssh_cfg, f"test -e {entry['path']}")
                ok = proc.returncode == 0
                detail = f"{entry['path']} missing" if not ok else ""
            elif kind == "mount_source":
                proc = _run(
                    host, port, ssh_cfg,
                    f"findmnt -n -o SOURCE {entry['mountpoint']}",
                )
                ok = proc.returncode == 0 and entry["source_contains"] in proc.stdout
                detail = f"source={proc.stdout.strip()!r}"
            else:
                ok, detail = False, f"unknown assertion type {kind!r}"
        except subprocess.TimeoutExpired:
            ok, detail = False, "ssh command timed out"
        results.append((name, ok, detail))
    return results
