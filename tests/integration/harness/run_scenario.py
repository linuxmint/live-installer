#!/usr/bin/env python3
"""Run a live-installer integration scenario in a QEMU VM.

Usage:
    run_scenario.py scenarios/bios-simple.yaml [--iso PATH] [--smoke]

A scenario boots the pinned ISO with an answer file served over HTTP,
waits for the installer's completion marker on the serial console, then
reboots into the installed disk and runs the scenario's verification
assertions over SSH.  Results are written as JUnit XML.

--smoke skips the install/verify phases: it boots the ISO, confirms the
VM stays up for a fixed period (i.e. KVM, firmware and ISO are sane),
and tears down.  This is the only mode that passes until the headless
installer driver exists.
"""

import argparse
import http.server
import shutil
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_install  # noqa: E402
import vm  # noqa: E402

HERE = Path(__file__).resolve().parent
INTEGRATION_DIR = HERE.parent
DEFAULT_ISO = INTEGRATION_DIR / "fixtures" / "lmde-7-cinnamon-64bit.iso"
WORK_ROOT = INTEGRATION_DIR / ".work"

DEFAULTS = {
    "firmware": "bios",
    "tpm": False,
    "memory_mb": 4096,
    "cpus": 2,
    "disk_gb": 25,
    "install_timeout_s": 1800,
    "boot_timeout_s": 300,
    # success/failure markers printed by the headless driver
    "expect": {
        "serial_markers": ["Automated installation complete"],
        "failure_markers": ["Automated installation FAILED"],
    },
    "verify": [],
}


class _QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass


def serve_directory(directory):
    """Serve `directory` over HTTP on an ephemeral localhost port."""
    handler = lambda *a, **kw: _QuietHTTPHandler(*a, directory=str(directory), **kw)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def load_scenario(path):
    scenario = dict(DEFAULTS)
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    scenario.update(data)
    scenario.setdefault("name", Path(path).stem)
    return scenario


def write_junit(path, suite_name, cases, elapsed_s):
    """cases: list of (name, ok, detail)"""
    failures = sum(1 for _, ok, _ in cases if not ok)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="{escape(suite_name)}" tests="{len(cases)}" '
        f'failures="{failures}" time="{elapsed_s:.1f}" '
        f'timestamp="{datetime.now(timezone.utc).isoformat()}">',
    ]
    for name, ok, detail in cases:
        lines.append(f'  <testcase name="{escape(name)}">')
        if not ok:
            lines.append(f"    <failure>{escape(detail or '')}</failure>")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    Path(path).write_text("\n".join(lines) + "\n")


def run_smoke(scenario, iso, workdir, smoke_seconds):
    machine = vm.VM(
        workdir,
        name=scenario["name"],
        memory_mb=scenario["memory_mb"],
        cpus=scenario["cpus"],
    )
    machine.create_disk(scenario["disk_gb"])
    machine.start(iso=iso, boot="cdrom", firmware=scenario["firmware"],
                  tpm=scenario["tpm"])
    try:
        print(f"[smoke] VM booted, holding for {smoke_seconds}s...")
        deadline = time.monotonic() + smoke_seconds
        while time.monotonic() < deadline:
            if not machine.alive():
                stderr = machine.process.stderr.read().decode(errors="replace")
                return [("smoke-boot", False,
                         f"VM exited prematurely: {stderr[-2000:]}")]
            time.sleep(2)
        return [("smoke-boot", True, "")]
    finally:
        machine.stop()


def run_full(scenario, iso, workdir, scenario_dir):
    cases = []
    httpd, http_port = serve_directory(scenario_dir)
    try:
        machine = vm.VM(
            workdir,
            name=scenario["name"],
            memory_mb=scenario["memory_mb"],
            cpus=scenario["cpus"],
        )
        machine.create_disk(scenario["disk_gb"])
        ssh_port = vm.free_port()

        # Phase 1: install from live media
        # NOTE: requires the headless driver + answer-file support in the
        # installer, plus direct-kernel boot to pass live-installer.auto=.
        answer = scenario.get("answer_file")
        append = None
        if answer:
            append = (
                "boot=live components quiet "
                f"live-installer.auto=http://10.0.2.2:{http_port}/{answer}"
            )
        machine.start(iso=iso, boot="cdrom", firmware=scenario["firmware"],
                      tpm=scenario["tpm"], ssh_port=ssh_port, append=append)
        try:
            success = scenario["expect"]["serial_markers"]
            failure = scenario["expect"].get("failure_markers", [])
            marker = machine.wait_serial(
                success + failure,
                scenario["install_timeout_s"],
            )
            if marker in failure:
                cases.append(("install", False,
                              f"installer reported failure: {marker}"))
                return cases
            cases.append(("install", True, f"matched: {marker}"))
        except (TimeoutError, vm.VMError) as exc:
            cases.append(("install", False, str(exc)))
            return cases
        finally:
            machine.stop()

        # Phase 2: boot the installed system and verify over SSH
        machine.start(boot="disk", firmware=scenario["firmware"],
                      tpm=scenario["tpm"], ssh_port=ssh_port)
        try:
            if not verify_install.wait_for_ssh("127.0.0.1", ssh_port,
                                               scenario["boot_timeout_s"]):
                cases.append(("first-boot", False,
                              "SSH never came up on installed system"))
                return cases
            cases.append(("first-boot", True, ""))
            cases.extend(
                verify_install.run_assertions(
                    "127.0.0.1", ssh_port, scenario.get("ssh", {}),
                    scenario["verify"],
                )
            )
        finally:
            machine.stop()
    finally:
        httpd.shutdown()
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", help="scenario YAML file")
    parser.add_argument("--iso", default=str(DEFAULT_ISO))
    parser.add_argument("--smoke", action="store_true",
                        help="boot the ISO and verify the VM stays up")
    parser.add_argument("--smoke-seconds", type=int, default=90)
    parser.add_argument("--junit", help="write JUnit XML to this path")
    parser.add_argument("--keep", action="store_true",
                        help="keep the work directory after the run")
    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    scenario = load_scenario(scenario_path)
    iso = Path(args.iso)
    if not iso.exists():
        sys.exit(f"ISO not found: {iso} (download it to fixtures/ first)")

    workdir = WORK_ROOT / scenario["name"]
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    started = time.monotonic()
    if args.smoke:
        cases = run_smoke(scenario, iso, workdir, args.smoke_seconds)
    else:
        cases = run_full(scenario, iso, workdir, scenario_path.parent)
    elapsed = time.monotonic() - started

    failed = False
    for name, ok, detail in cases:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {scenario['name']}/{name}" + (f": {detail}" if detail and not ok else ""))
        failed = failed or not ok

    if args.junit:
        write_junit(args.junit, scenario["name"], cases, elapsed)
    if not args.keep and not failed:
        shutil.rmtree(workdir, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
