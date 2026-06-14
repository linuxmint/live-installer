#!/usr/bin/python3
# coding: utf-8
"""Answer-file auto-discovery for netboot/PXE.

Templating one PXE entry per machine does not scale. Instead a single
boot entry can point the installer at a *base* and let it find its own
answer file from the machine's identity:

    live-installer.auto=auto:https://cfg.example.com/configs/

The installer then tries, in order of specificity:

    <base>/by-mac/<mac>.yaml      (one per NIC, in interface order)
    <base>/by-serial/<serial>.yaml
    <base>/by-uuid/<uuid>.yaml
    <base>/default.yaml           (fleet-wide fallback)

and finally the well-known local-media paths, so a USB/remastered-ISO
boot (live-installer.auto=auto, no base) still works offline. The first
candidate that both fetches and validates wins; everything else is
reported so a miss is debuggable.

Identity is read from sysfs/DMI, all paths parameterized so the logic is
unit-testable against a fake tree. Reading DMI needs root, which the live
session has.
"""

import os

AUTO = "auto"
AUTO_PREFIX = "auto:"


class DiscoveryError(Exception):
    """No candidate source yielded a valid answer file."""

DEFAULT_MEDIA_DIRS = ("/cdrom", "/run/live/medium")
WELL_KNOWN_NAME = "auto-install.yaml"

# Manufacturers ship machines with placeholder DMI values. A by-uuid/ or
# by-serial/ answer file built from one of these would match every defective
# unit in a fleet, so discovery never keys on them. Compared case-folded.
_SENTINEL_UUIDS = frozenset({
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "03000200-0400-0500-0006-000700080009",  # a known QEMU/SMBIOS default
})
_SENTINEL_SERIALS = frozenset({
    "system serial number", "default string", "to be filled by o.e.m.",
    "not specified", "not applicable", "none", "0", "123456789",
})


def parse_auto_trigger(source):
    """Return (is_discovery, base_or_None) for an answer-file source.

    'auto'                 -> (True, None)      local media only
    'auto:https://host/x/' -> (True, base)      base + local media
    anything else          -> (False, None)
    """
    if source == AUTO:
        return True, None
    if source.startswith(AUTO_PREFIX):
        base = source[len(AUTO_PREFIX):].strip()
        return True, (base or None)
    return False, None


def _read(path):
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return ""


def read_machine_identity(net_dir="/sys/class/net", dmi_dir="/sys/class/dmi/id",
                          log=lambda _m: None):
    """Collect the stable identifiers a per-machine answer file is keyed on:
    wired ethernet MAC(s), SMBIOS serial, SMBIOS UUID. Sentinel/placeholder
    DMI values are dropped (and logged) so discovery never keys on garbage."""
    macs = []
    try:
        names = sorted(os.listdir(net_dir))
    except OSError:
        names = []
    for name in names:
        if name == "lo":
            continue
        # ARPHRD_ETHER (type 1) excludes loopback/bridges/tunnels but NOT wifi
        # (wifi is also type 1), so filter wifi explicitly: a wireless NIC has
        # a wireless/ subdir. by-mac discovery is wired-only — a laptop's wifi
        # MAC is a poor fleet key (often down in the installer, may randomise).
        if _read(os.path.join(net_dir, name, "type")) != "1":
            continue
        if os.path.isdir(os.path.join(net_dir, name, "wireless")):
            continue
        addr = _read(os.path.join(net_dir, name, "address")).lower()
        if addr and addr != "00:00:00:00:00:00" and addr not in macs:
            macs.append(addr)
    serial = _read(os.path.join(dmi_dir, "product_serial")) or None
    if serial and serial.strip().lower() in _SENTINEL_SERIALS:
        log(f"auto-discovery: ignoring placeholder product_serial {serial!r}")
        serial = None
    uuid = _read(os.path.join(dmi_dir, "product_uuid")) or None
    if uuid and uuid.strip().lower() in _SENTINEL_UUIDS:
        log(f"auto-discovery: ignoring placeholder product_uuid {uuid!r}")
        uuid = None
    return {"macs": macs, "serial": serial, "uuid": uuid}


def candidate_sources(base=None, *, macs=(), serial=None, uuid=None,
                      media_dirs=DEFAULT_MEDIA_DIRS,
                      well_known_name=WELL_KNOWN_NAME):
    """Ordered list of answer-file sources to try, most specific first."""
    candidates = []
    if base:
        root = base.rstrip("/")
        for mac in macs:
            candidates.append(f"{root}/by-mac/{mac}.yaml")
        if serial:
            candidates.append(f"{root}/by-serial/{serial}.yaml")
        if uuid:
            candidates.append(f"{root}/by-uuid/{uuid}.yaml")
        candidates.append(f"{root}/default.yaml")
    for directory in media_dirs:
        candidates.append(f"{directory.rstrip('/')}/{well_known_name}")
    return candidates


def discover(candidates, *, fetch, validate, log=lambda _m: None):
    """Try each candidate in order; return (source, text) for the first that
    both fetches and validates. Raises the supplied error type via fetch/
    validate semantics if none work.

    fetch(source)   -> text, or raises (treated as "not present")
    validate(text)  -> parses, or raises (treated as "present but invalid")
    """
    tried = []
    for source in candidates:
        try:
            text = fetch(source)
        except Exception as exc:  # noqa: BLE001 - fetch defines its own error
            tried.append(f"{source}  (not found: {exc})")
            continue
        try:
            validate(text)
        except Exception as exc:  # noqa: BLE001 - validate defines its own error
            tried.append(f"{source}  (invalid: {exc})")
            continue
        log(f"auto-discovery: using {source}")
        return source, text
    if tried:
        raise DiscoveryError(
            "auto-discovery found no valid answer file. Tried:\n  "
            + "\n  ".join(tried)
        )
    raise DiscoveryError(
        "auto-discovery had nothing to try (no base URL, no local media)."
    )
