"""ISO manipulation for the integration harness.

Two jobs, both root-less:

1. build_dev_iso(): produce a test ISO whose live session contains the
   working tree's installer.  Instead of remastering the (root-owned)
   main squashfs, a small overlay squashfs with just our usr/ tree is
   added to the ISO's /live directory — Debian live-boot union-mounts
   every *.squashfs it finds there, so the overlay's files shadow the
   originals.  The overlay also carries the systemd unit that launches
   the installer when live-installer.auto= is on the kernel cmdline.

2. extract_boot_files(): pull vmlinuz/initrd out of an ISO so QEMU can
   direct-kernel-boot it with our kernel arguments (the stock bootloader
   menu can't be driven non-interactively).

Requires squashfs-tools and xorriso.
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Sorts after "filesystem.squashfs"; live-boot stacks images in sorted
# order with later images taking precedence in the union.
OVERLAY_NAME = "zz-installer-dev.squashfs"


class IsoToolsError(Exception):
    pass


def _require(binary, package_hint):
    if shutil.which(binary) is None:
        raise IsoToolsError(
            f"{binary} not found — install it (e.g. dnf install "
            f"{package_hint} / apt install {package_hint})"
        )


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise IsoToolsError(
            f"{cmd[0]} failed (rc={result.returncode}):\n"
            f"{result.stdout[-1000:]}\n{result.stderr[-1000:]}"
        )
    return result


def build_overlay_tree(source_tree, overlay_dir):
    """Stage the working tree's installer files for the overlay squashfs."""
    source_tree = Path(source_tree)
    overlay_dir = Path(overlay_dir)
    if overlay_dir.exists():
        shutil.rmtree(overlay_dir)
    for relative in ("usr/bin", "usr/lib/live-installer",
                     "usr/lib/systemd/system", "usr/share/live-installer"):
        src = source_tree / relative
        if not src.exists():
            raise IsoToolsError(f"missing {src} — wrong --source-tree?")
        shutil.copytree(src, overlay_dir / relative, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    return overlay_dir


# Runtime deps of the headless installer that the stock live ISO does not
# ship (they come from debian/control Depends when installed as a .deb).
# LMDE 7 is Debian 13: Python 3.13 on x86_64.
PYTHON_DEPS = ["pyyaml", "pydantic"]
TARGET_PYTHON = "313"
TARGET_PLATFORM = "manylinux_2_17_x86_64"


def bundle_python_deps(overlay_dir, cache_dir):
    """Download wheels for the target live system's Python and unpack them
    into the overlay's dist-packages, standing in for the .deb Depends."""
    wheel_dir = Path(cache_dir) / "wheels"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    if not any(wheel_dir.glob("*.whl")):
        _run([
            sys.executable, "-m", "pip", "download", "-q",
            "--only-binary", ":all:",
            "--python-version", TARGET_PYTHON,
            "--platform", TARGET_PLATFORM,
            "-d", str(wheel_dir),
            *PYTHON_DEPS,
        ])
    dist_packages = Path(overlay_dir) / "usr" / "lib" / "python3" / "dist-packages"
    dist_packages.mkdir(parents=True, exist_ok=True)
    for wheel in sorted(wheel_dir.glob("*.whl")):
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(dist_packages)
    # zipfile does not preserve the +x/read bits the live system needs on
    # shared objects; normalize everything to world-readable
    for path in dist_packages.rglob("*"):
        path.chmod(0o755 if path.is_dir() or path.suffix == ".so" else 0o644)


def build_dev_iso(source_iso, source_tree, output_iso, workdir):
    """Create output_iso = source_iso + overlay squashfs with our code."""
    _require("mksquashfs", "squashfs-tools")
    _require("xorriso", "xorriso")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    overlay_dir = build_overlay_tree(source_tree, workdir / "overlay")
    bundle_python_deps(overlay_dir, workdir)
    overlay_squash = workdir / OVERLAY_NAME
    overlay_squash.unlink(missing_ok=True)
    _run([
        "mksquashfs", str(overlay_dir), str(overlay_squash),
        "-all-root",          # files must be root-owned in the live system
        "-no-progress", "-quiet", "-comp", "zstd",
    ])

    output_iso = Path(output_iso)
    output_iso.unlink(missing_ok=True)
    # -boot_image any replay preserves the BIOS/UEFI boot records
    _run([
        "xorriso", "-indev", str(source_iso),
        "-outdev", str(output_iso),
        "-boot_image", "any", "replay",
        "-map", str(overlay_squash), f"/live/{OVERLAY_NAME}",
    ])
    return output_iso


def extract_boot_files(iso, outdir):
    """Extract the live kernel and initrd from the ISO's /live directory.

    Returns (kernel_path, initrd_path).
    """
    _require("xorriso", "xorriso")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    listing = _run([
        "xorriso", "-indev", str(iso), "-find", "/live", "-type", "f",
    ]).stdout
    kernel_name = initrd_name = None
    for line in listing.splitlines():
        name = line.strip().strip("'")
        base = name.rsplit("/", 1)[-1]
        if base.startswith("vmlinuz"):
            kernel_name = name
        elif base.startswith("initrd"):
            initrd_name = name
    if not kernel_name or not initrd_name:
        raise IsoToolsError(
            f"could not find vmlinuz/initrd under /live in {iso}; "
            f"listing:\n{listing}"
        )

    _run([
        "xorriso", "-osirrox", "on", "-indev", str(iso),
        "-extract", kernel_name, str(outdir / "vmlinuz"),
        "-extract", initrd_name, str(outdir / "initrd.img"),
    ])
    # xorriso preserves the ISO's read-only mode bits; make them readable
    for name in ("vmlinuz", "initrd.img"):
        (outdir / name).chmod(0o644)
    return outdir / "vmlinuz", outdir / "initrd.img"


def extract_netboot_assets(iso, outdir):
    """Extract vmlinuz, initrd, and every /live/*.squashfs for network boot.

    Unlike extract_boot_files (which only needs the kernel/initrd because the
    rootfs comes off the attached CD), a PXE boot has no media: the squashfs
    images must be served too, so live-boot can fetch them over HTTP.

    Returns (kernel_path, initrd_path, [squashfs_paths]) with the squashfs
    list in sorted (live-boot stacking) order.
    """
    _require("xorriso", "xorriso")
    outdir = Path(outdir)
    (outdir / "live").mkdir(parents=True, exist_ok=True)

    listing = _run([
        "xorriso", "-indev", str(iso), "-find", "/live", "-type", "f",
    ]).stdout
    kernel_name = initrd_name = None
    squashfs = []  # (iso_path, basename)
    for line in listing.splitlines():
        name = line.strip().strip("'")
        base = name.rsplit("/", 1)[-1]
        if base.startswith("vmlinuz"):
            kernel_name = name
        elif base.startswith("initrd"):
            initrd_name = name
        elif base.endswith(".squashfs"):
            squashfs.append((name, base))
    if not kernel_name or not initrd_name or not squashfs:
        raise IsoToolsError(
            f"could not find vmlinuz/initrd/*.squashfs under /live in {iso}; "
            f"listing:\n{listing}"
        )

    extract = [
        "xorriso", "-osirrox", "on", "-indev", str(iso),
        "-extract", kernel_name, str(outdir / "vmlinuz"),
        "-extract", initrd_name, str(outdir / "initrd.img"),
    ]
    for iso_path, base in squashfs:
        extract += ["-extract", iso_path, str(outdir / "live" / base)]
    _run(extract)

    (outdir / "vmlinuz").chmod(0o644)
    (outdir / "initrd.img").chmod(0o644)
    sq_paths = []
    for _iso_path, base in sorted(squashfs, key=lambda pair: pair[1]):
        path = outdir / "live" / base
        path.chmod(0o644)
        sq_paths.append(path)
    return outdir / "vmlinuz", outdir / "initrd.img", sq_paths


def build_combined_squashfs(iso, source_tree, outdir, workdir):
    """Build ONE squashfs = base rootfs + our dev code, for PXE.

    On CD/USB boot live-boot stacks every *.squashfs on the medium, so our
    code can ride in a small separate overlay squashfs. PXE can't do that:
    live-boot's fetch= takes a single URL, so the two images must be merged.
    We unsquashfs the base, lay the overlay tree on top (it shadows the base
    exactly as the union mount would), and re-squash with -all-root so the
    result is root-owned without needing real root.

    Returns (kernel_path, initrd_path, combined_squashfs_path).
    """
    _require("unsquashfs", "squashfs-tools")
    _require("mksquashfs", "squashfs-tools")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    kernel, initrd, squashes = extract_netboot_assets(iso, workdir / "assets")
    base = next((p for p in squashes if p.name == "filesystem.squashfs"), None)
    if base is None:
        raise IsoToolsError(
            f"no filesystem.squashfs to merge in {iso}; found "
            f"{[p.name for p in squashes]}"
        )

    overlay = build_overlay_tree(source_tree, workdir / "overlay")
    bundle_python_deps(overlay, workdir)

    root = workdir / "root"
    if root.exists():
        shutil.rmtree(root)
    # unsquashfs runs unprivileged: -no-xattrs skips security.* capability
    # xattrs it cannot write as non-root (the installer runs as root and needs
    # no file caps); it also skips device nodes (the live rootfs has none,
    # devtmpfs populates /dev at boot); -all-root fixes ownership at re-squash.
    _run(["unsquashfs", "-d", str(root), "-no-progress", "-no-xattrs",
          str(base)])
    base.unlink(missing_ok=True)  # free the ~GB base image before re-squashing
    _run(["cp", "-a", f"{overlay}/.", f"{root}/"])

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    combined = outdir / "filesystem.squashfs"
    combined.unlink(missing_ok=True)
    _run([
        "mksquashfs", str(root), str(combined), "-all-root", "-no-xattrs",
        "-no-progress", "-quiet", "-comp", "zstd", "-noappend",
    ])
    shutil.rmtree(root, ignore_errors=True)  # reclaim the unpacked tree
    return kernel, initrd, combined


def write_ipxe_script(path, http_base, squashfs_names, answer_url,
                      console="ttyS0"):
    """Write a #!ipxe boot script for the PXE scenario.

    The kernel and initrd come over TFTP (QEMU's built-in server); live-boot
    then fetches the squashfs images over HTTP, and the installer fetches its
    answer file over HTTP. So the whole boot is network-delivered, no media.
    """
    fetch = ",".join(f"{http_base}/live/{name}" for name in squashfs_names)
    # ip=dhcp: live-boot's fetch= runs in the initramfs, which has its own
    # network stack separate from iPXE's — it must DHCP an interface before it
    # can pull the squashfs, or it fails with "Unable to find a live file
    # system on the network".
    cmdline = (
        f"boot=live components ip=dhcp console={console} "
        f"fetch={fetch} "
        f"live-installer.auto={answer_url} live-installer.auto-insecure"
    )
    Path(path).write_text(
        "#!ipxe\n"
        f"kernel vmlinuz initrd=initrd.img {cmdline}\n"
        "initrd initrd.img\n"
        "boot\n"
    )
    return Path(path)
