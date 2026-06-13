#!/usr/bin/python3
# coding: utf-8
"""Stable-attribute disk resolution for unattended installs.

The answer file selects the target disk with match expressions (by-id,
by-path, model, size-min, first-non-removable) instead of raw /dev
paths, because kernel device enumeration order is not stable.  This
module turns a match expression into exactly one whole-disk device
path, or fails loudly.  Ambiguity is an error, never a guess.

All filesystem locations are parameterized so the resolver can be unit
tested against a fake /sys/block and /dev/disk tree.
"""

import fnmatch
import os
import re
from pathlib import Path

_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([MGT]B)$")
_SIZE_FACTOR = {"MB": 10**6, "GB": 10**9, "TB": 10**12}

# virtual/optical devices that are never installation targets
_EXCLUDED_PREFIXES = ("loop", "ram", "zram", "sr", "fd", "dm-", "md")


class DiskMatchError(Exception):
    """No disk (or more than one disk) matched the expression."""


def parse_size(text):
    """'500GB' -> 500_000_000_000 (decimal units, matching disk marketing)."""
    match = _SIZE_RE.match(text.strip())
    if not match:
        raise DiskMatchError(f"unparseable size {text!r} (expected e.g. 500GB)")
    return int(float(match.group(1)) * _SIZE_FACTOR[match.group(2)])


class DiskInfo:
    def __init__(self, name, size_bytes, removable, model):
        self.name = name              # e.g. "sda", "nvme0n1"
        self.path = "/dev/" + name
        self.size_bytes = size_bytes
        self.removable = removable
        self.model = model

    def __repr__(self):
        return (f"{self.path} (model={self.model!r}, "
                f"size={self.size_bytes // 10**9}GB, "
                f"removable={self.removable})")


def _read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def list_disks(sys_block="/sys/block"):
    """Enumerate whole-disk block devices, in stable (sorted) order."""
    disks = []
    try:
        names = sorted(os.listdir(sys_block))
    except OSError:
        return []
    for name in names:
        if name.startswith(_EXCLUDED_PREFIXES):
            continue
        entry = os.path.join(sys_block, name)
        size_sectors = _read(os.path.join(entry, "size"), "0")
        disks.append(DiskInfo(
            name=name,
            size_bytes=int(size_sectors or 0) * 512,
            removable=_read(os.path.join(entry, "removable"), "0") == "1",
            model=_read(os.path.join(entry, "device", "model")),
        ))
    return disks


def _links_for(disk_names, link_dir):
    """Map each whole-disk device name to the symlink names under link_dir
    that point at it (partitions and other devices excluded)."""
    result = {name: [] for name in disk_names}
    try:
        entries = sorted(os.listdir(link_dir))
    except OSError:
        return result
    for entry in entries:
        name = os.path.basename(os.path.realpath(os.path.join(link_dir, entry)))
        if name in result:
            result[name].append(entry)
    return result


def describe_disks(
    *,
    sys_block="/sys/block",
    by_id_dir="/dev/disk/by-id",
    by_path_dir="/dev/disk/by-path",
):
    """Enumerate installable disks with every stable attribute a match
    expression can use (the data behind --list-disks).  Returns a list of
    dicts, in the same stable order as list_disks."""
    disks = list_disks(sys_block)
    names = {d.name for d in disks}
    by_id = _links_for(names, by_id_dir)
    by_path = _links_for(names, by_path_dir)
    return [
        {
            "path": d.path,
            "model": d.model,
            "size_bytes": d.size_bytes,
            "removable": d.removable,
            "by_id": by_id.get(d.name, []),
            "by_path": by_path.get(d.name, []),
        }
        for d in disks
    ]


def _match_symlink_dir(pattern, link_dir, disk_names):
    """Resolve a glob over /dev/disk/by-id (or by-path) symlink names to
    the set of whole-disk device names they point at."""
    matched = set()
    try:
        entries = sorted(os.listdir(link_dir))
    except OSError:
        entries = []
    for entry in entries:
        if not fnmatch.fnmatch(entry, pattern):
            continue
        target = os.path.realpath(os.path.join(link_dir, entry))
        name = os.path.basename(target)
        if name in disk_names:  # whole disks only, never partitions
            matched.add(name)
    return matched


def resolve_disk(
    match,
    *,
    sys_block="/sys/block",
    by_id_dir="/dev/disk/by-id",
    by_path_dir="/dev/disk/by-path",
):
    """Resolve a DiskMatch (schema object or equivalent) to one device path.

    All present matchers must agree (logical AND).  Raises DiskMatchError
    if no disk or more than one disk satisfies the expression.
    """
    disks = list_disks(sys_block)
    if not disks:
        raise DiskMatchError("no block devices found")
    by_name = {disk.name: disk for disk in disks}
    candidates = set(by_name)

    tried = []
    if getattr(match, "by_id", None):
        tried.append(f"by-id={match.by_id!r}")
        candidates &= _match_symlink_dir(match.by_id, by_id_dir, set(by_name))
    if getattr(match, "by_path", None):
        tried.append(f"by-path={match.by_path!r}")
        candidates &= _match_symlink_dir(match.by_path, by_path_dir, set(by_name))
    if getattr(match, "model", None):
        tried.append(f"model={match.model!r}")
        candidates = {
            name for name in candidates
            if fnmatch.fnmatch(by_name[name].model, match.model)
        }
    if getattr(match, "size_min", None):
        tried.append(f"size-min={match.size_min!r}")
        minimum = parse_size(match.size_min)
        candidates = {
            name for name in candidates
            if by_name[name].size_bytes >= minimum
        }
    if getattr(match, "first_non_removable", False):
        tried.append("first-non-removable")
        fixed = [d for d in disks if d.name in candidates and not d.removable]
        candidates = {fixed[0].name} if fixed else set()

    if not candidates:
        raise DiskMatchError(
            "no disk matches [%s]; available disks:\n  %s"
            % (", ".join(tried),
               "\n  ".join(repr(d) for d in disks))
        )
    if len(candidates) > 1:
        raise DiskMatchError(
            "ambiguous match [%s] — %d disks qualify:\n  %s\n"
            "Refusing to guess; add more specific matchers."
            % (", ".join(tried), len(candidates),
               "\n  ".join(repr(by_name[n]) for n in sorted(candidates)))
        )
    return by_name[candidates.pop()].path
