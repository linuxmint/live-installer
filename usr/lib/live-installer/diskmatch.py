#!/usr/bin/python3
"""Resolve an answer file's disk match to exactly one whole disk.

Kernel device names (sda, nvme0n1) are not stable between boots, so the
answer file selects the target by stable attributes instead. No match, or
more than one, is an error: an unattended install never guesses which
disk to wipe.

The /sys and /dev/disk locations are parameters so the tests can point
them at a fake tree.
"""

import fnmatch
import os

from schema import parse_size

# Never installation targets
_EXCLUDED_PREFIXES = ("loop", "ram", "zram", "sr", "fd", "dm-", "md")


class DiskMatchError(Exception):
    """No disk, or more than one disk, matched."""


class Disk:
    def __init__(self, name, size_bytes, removable, model):
        self.name = name
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
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def list_disks(sys_block="/sys/block"):
    """Whole-disk block devices, sorted by name."""
    try:
        names = sorted(os.listdir(sys_block))
    except OSError:
        return []
    disks = []
    for name in names:
        if name.startswith(_EXCLUDED_PREFIXES):
            continue
        entry = os.path.join(sys_block, name)
        disks.append(Disk(
            name=name,
            size_bytes=int(_read(os.path.join(entry, "size"), "0") or 0) * 512,
            removable=_read(os.path.join(entry, "removable"), "0") == "1",
            model=_read(os.path.join(entry, "device", "model")),
        ))
    return disks


def _match_links(pattern, link_dir, disk_names):
    """Disks whose /dev/disk/by-id (or by-path) link names match a glob.
    Links to partitions are ignored."""
    try:
        entries = sorted(os.listdir(link_dir))
    except OSError:
        return set()
    matched = set()
    for entry in entries:
        if fnmatch.fnmatch(entry, pattern):
            name = os.path.basename(os.path.realpath(os.path.join(link_dir, entry)))
            if name in disk_names:
                matched.add(name)
    return matched


def resolve_disk(match, *, sys_block="/sys/block",
                 by_id_dir="/dev/disk/by-id", by_path_dir="/dev/disk/by-path"):
    """Return the device path of the one disk that satisfies every matcher."""
    disks = {disk.name: disk for disk in list_disks(sys_block)}
    if not disks:
        raise DiskMatchError("no disks found")
    candidates = set(disks)
    tried = []
    if match.by_id:
        tried.append(f"by-id={match.by_id!r}")
        candidates &= _match_links(match.by_id, by_id_dir, disks)
    if match.by_path:
        tried.append(f"by-path={match.by_path!r}")
        candidates &= _match_links(match.by_path, by_path_dir, disks)
    if match.model:
        tried.append(f"model={match.model!r}")
        candidates = {n for n in candidates
                      if fnmatch.fnmatch(disks[n].model, match.model)}
    if match.size_min:
        tried.append(f"size-min={match.size_min!r}")
        minimum = parse_size(match.size_min)
        candidates = {n for n in candidates if disks[n].size_bytes >= minimum}
    if match.first_non_removable:
        # A filter, not a tie-breaker: with two internal disks this is
        # ambiguous and fails below rather than picking one.
        tried.append("first-non-removable")
        candidates = {n for n in candidates if not disks[n].removable}

    if not candidates:
        raise DiskMatchError(
            "no disk matches [%s]. Disks found:\n  %s"
            % (", ".join(tried), "\n  ".join(repr(d) for d in disks.values())))
    if len(candidates) > 1:
        raise DiskMatchError(
            "[%s] matches %d disks:\n  %s\nAdd matchers so only one disk qualifies."
            % (", ".join(tried), len(candidates),
               "\n  ".join(repr(disks[n]) for n in sorted(candidates))))
    return disks[candidates.pop()].path
