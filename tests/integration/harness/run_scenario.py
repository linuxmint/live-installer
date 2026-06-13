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
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isotools  # noqa: E402
import verify_install  # noqa: E402
import vm  # noqa: E402

HERE = Path(__file__).resolve().parent
INTEGRATION_DIR = HERE.parent
SOURCE_TREE = INTEGRATION_DIR.parent.parent  # repo root: usr/lib/live-installer/…
DEFAULT_ISO = INTEGRATION_DIR / "fixtures" / "lmde-7-cinnamon-64bit.iso"
DEFAULT_DEV_ISO = INTEGRATION_DIR / "fixtures" / "lmde-7-dev.iso"
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


# Strings live-boot/the kernel print when the system never reaches the
# installer; treated as an immediate install failure so a broken boot does
# not wait out the full install timeout.
BOOT_FAILED_MARKERS = [
    "Unable to find a live file system",
    "BOOT FAILED",
    "Kernel panic",
]


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


def generate_ssh_key(workdir):
    """Per-run keypair; the public key is injected into the answer file."""
    key = workdir / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(key),
         "-C", "live-installer-harness"],
        check=True,
    )
    return key, (workdir / "id_ed25519.pub").read_text().strip()


def stage_answer_file(scenario_dir, answer_rel, serve_dir, pubkey, base_url):
    """Copy the scenario's answer file into the served directory: the
    {server} placeholder is expanded to the harness HTTP server's base URL
    (for auxiliary files like LUKS keyfiles), and the harness SSH public
    key is added to the first user so verify can log in.  Auxiliary files
    next to the answer file are copied verbatim."""
    target = serve_dir / answer_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    text = (scenario_dir / answer_rel).read_text().replace("{server}", base_url)
    answer = yaml.safe_load(text)
    user = answer["users"][0]
    user.setdefault("ssh_authorized_keys", []).append(pubkey)
    with open(target, "w") as f:
        yaml.safe_dump(answer, f, sort_keys=False)
    for aux in (scenario_dir / answer_rel).parent.iterdir():
        if aux.is_file() and aux.name != Path(answer_rel).name:
            shutil.copyfile(aux, target.parent / aux.name)
    return serve_dir


def run_full(scenario, iso, workdir, scenario_dir):
    cases = []
    answer = scenario.get("answer_file")
    if not answer:
        return [("install", False, "scenario has no answer_file")]

    # Failure-mode scenarios assert that bad input fails fast and cleanly:
    # the failure marker is the expected outcome and there is no phase 2.
    # The answer file is served verbatim (it may be deliberately malformed).
    expect_failure = scenario["expect"].get("outcome") == "failure"
    if expect_failure:
        ssh_key = None
        serve_dir = scenario_dir
        httpd, http_port = serve_directory(serve_dir)
    else:
        # the server starts first so its port can be substituted into the
        # answer file ({server} placeholder); files appear afterwards
        serve_dir = workdir / "serve"
        serve_dir.mkdir(parents=True, exist_ok=True)
        httpd, http_port = serve_directory(serve_dir)
        ssh_key, pubkey = generate_ssh_key(workdir)
        stage_answer_file(scenario_dir, answer, serve_dir, pubkey,
                          f"http://10.0.2.2:{http_port}")
    netboot = bool(scenario.get("netboot"))

    try:
        machine = vm.VM(
            workdir,
            name=scenario["name"],
            memory_mb=scenario["memory_mb"],
            cpus=scenario["cpus"],
        )
        # Disk topology. Default: one unnamed disk. A scenario may instead
        # declare `disks: [{size_gb, serial?}, ...]`; the first is primary.
        disks = scenario.get("disks")
        if disks:
            machine.create_disk(disks[0]["size_gb"], disks[0].get("serial"))
            for extra in disks[1:]:
                machine.add_disk(extra["size_gb"], extra.get("serial"))
        else:
            machine.create_disk(scenario["disk_gb"])
        ssh_port = vm.free_port()
        # auto-insecure: the answer file travels over QEMU's host-only user
        # network; there is no TLS endpoint to offer
        answer_url = f"http://10.0.2.2:{http_port}/{answer}"

        if netboot:
            # Phase 1 (PXE): no install media. The kernel/initrd come over
            # TFTP from QEMU's built-in server; live-boot fetches a single
            # squashfs over HTTP (live-boot's fetch= takes one URL, so our
            # dev code is merged into the base image, not a second squashfs);
            # the installer fetches its answer file over HTTP.
            kernel, initrd, squashfs = isotools.build_combined_squashfs(
                iso, SOURCE_TREE, serve_dir / "live", workdir / "netboot")
            tftp_dir = workdir / "tftp"
            tftp_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(kernel, tftp_dir / "vmlinuz")
            shutil.copyfile(initrd, tftp_dir / "initrd.img")
            isotools.write_ipxe_script(
                tftp_dir / "boot.ipxe", f"http://10.0.2.2:{http_port}",
                [squashfs.name], answer_url)
            # BIOS: the NIC's iPXE option ROM runs boot.ipxe directly. UEFI:
            # OVMF needs an EFI binary, so it loads ipxe.efi (built in
            # vm-setup), whose embedded script chainloads boot.ipxe over TFTP.
            if scenario["firmware"].startswith("uefi"):
                shutil.copyfile(INTEGRATION_DIR / "fixtures" / "ipxe.efi",
                                tftp_dir / "ipxe.efi")
                bootfile = "ipxe.efi"
            else:
                bootfile = "boot.ipxe"
            machine.start(boot="net", firmware=scenario["firmware"],
                          tpm=scenario["tpm"], ssh_port=ssh_port,
                          tftp_dir=str(tftp_dir), bootfile=bootfile)
        else:
            # Phase 1: direct-kernel boot of the live ISO (rootfs off the
            # attached CD) with the answer-file URL on the kernel cmdline.
            kernel, initrd = isotools.extract_boot_files(iso, workdir / "boot")
            append = (
                "boot=live components console=ttyS0 "
                f"live-installer.auto={answer_url} "
                "live-installer.auto-insecure"
            )
            machine.start(iso=iso, boot="cdrom", firmware=scenario["firmware"],
                          tpm=scenario["tpm"], ssh_port=ssh_port,
                          kernel=kernel, initrd=initrd, append=append)
        try:
            success = scenario["expect"]["serial_markers"]
            failure = scenario["expect"].get("failure_markers", [])
            marker = machine.wait_serial(
                success + failure + BOOT_FAILED_MARKERS,
                scenario["install_timeout_s"],
            )
            # A boot that dies before the installer runs (e.g. live-boot can't
            # fetch the rootfs over PXE) must fail fast, not wait out the full
            # install timeout for a marker that will never come.
            if marker in BOOT_FAILED_MARKERS:
                label = "fails-cleanly" if expect_failure else "install"
                cases.append((label, False,
                              f"boot failed before the installer ran: {marker}"))
                return cases
            if expect_failure:
                if marker in failure:
                    cases.append(("fails-cleanly", True, f"matched: {marker}"))
                else:
                    cases.append(("fails-cleanly", False,
                                  f"unexpectedly succeeded: {marker}"))
                return cases
            if marker in failure:
                cases.append(("install", False,
                              f"installer reported failure: {marker}"))
                return cases
            cases.append(("install", True, f"matched: {marker}"))
        except (TimeoutError, vm.VMError) as exc:
            name = "fails-cleanly" if expect_failure else "install"
            cases.append((name, False, str(exc)))
            return cases
        finally:
            machine.stop()

        # Scenarios whose installed system cannot boot unattended yet
        # (e.g. LUKS passphrase prompt at the initramfs) stop here until
        # the harness can interact with the serial console.
        if scenario.get("skip_boot_phase"):
            cases.append(("boot-verify", True,
                          "SKIPPED: " + str(scenario.get("skip_boot_reason",
                                                         "skip_boot_phase set"))))
            return cases

        # Preserve the install-phase serial log; phase 2 truncates it.
        if machine.serial_log.exists():
            shutil.copyfile(machine.serial_log,
                            machine.serial_log.with_suffix(".install.log"))

        # Phase 2: boot the installed system and verify over SSH. In a
        # multi-disk VM, boot the disk the OS landed on (boot_disk_serial).
        machine.start(boot="disk", firmware=scenario["firmware"],
                      tpm=scenario["tpm"], ssh_port=ssh_port,
                      boot_serial=scenario.get("boot_disk_serial"))
        try:
            # Encrypted installs prompt for the LUKS passphrase at the
            # initramfs; type it over serial as a real admin would via SOL.
            unlock = scenario.get("boot_unlock")
            if unlock:
                passphrase = (scenario_dir / unlock["passphrase_file"]).read_text()
                try:
                    machine.wait_serial([unlock["prompt"]],
                                        unlock.get("timeout_s", 180))
                    time.sleep(1)
                    machine.send_serial(passphrase.rstrip("\n") + "\n")
                    cases.append(("luks-unlock", True, ""))
                except (TimeoutError, vm.VMError) as exc:
                    cases.append(("luks-unlock", False,
                                  f"unlock prompt never appeared: {exc}"))
                    return cases
            if not verify_install.wait_for_ssh("127.0.0.1", ssh_port,
                                               scenario["boot_timeout_s"]):
                cases.append(("first-boot", False,
                              "SSH never came up on installed system"))
                return cases
            cases.append(("first-boot", True, ""))
            ssh_cfg = dict(scenario.get("ssh") or {})
            ssh_cfg["key"] = str(ssh_key)
            cases.extend(
                verify_install.run_assertions(
                    "127.0.0.1", ssh_port, ssh_cfg, scenario["verify"],
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
    parser.add_argument(
        "--iso", default=None,
        help="ISO to boot (default: stock ISO for --smoke, dev ISO "
             "from make_test_iso.py for full runs)",
    )
    parser.add_argument("--smoke", action="store_true",
                        help="boot the ISO and verify the VM stays up")
    parser.add_argument("--smoke-seconds", type=int, default=90)
    parser.add_argument("--junit", help="write JUnit XML to this path")
    parser.add_argument("--keep", action="store_true",
                        help="keep the work directory after the run")
    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    scenario = load_scenario(scenario_path)
    if args.iso:
        iso = Path(args.iso)
    elif args.smoke:
        iso = DEFAULT_ISO
    else:
        iso = DEFAULT_DEV_ISO
    if not iso.exists():
        hint = ("download it to fixtures/ first" if args.smoke or args.iso
                else "build it with harness/make_test_iso.py first")
        sys.exit(f"ISO not found: {iso} ({hint})")

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
