import os
from types import SimpleNamespace

import pytest

from diskmatch import DiskMatchError, resolve_disk

DISKS = {
    # name: (size in GB, removable, model)
    "nvme0n1": (512, False, "Samsung SSD 980"),
    "sda": (2000, False, "WDC WD20EZRZ"),
    "sdb": (32, True, "SanDisk Ultra"),
}


@pytest.fixture
def tree(tmp_path):
    sys_block = tmp_path / "sys" / "block"
    by_id = tmp_path / "by-id"
    by_path = tmp_path / "by-path"
    dev = tmp_path / "dev"
    for directory in (by_id, by_path, dev):
        directory.mkdir(parents=True)
    for name, (size, removable, model) in DISKS.items():
        entry = sys_block / name
        (entry / "device").mkdir(parents=True)
        (entry / "size").write_text(str(size * 10**9 // 512))
        (entry / "removable").write_text("1" if removable else "0")
        (entry / "device" / "model").write_text(model)
        (dev / name).touch()
        (dev / (name + "-part1")).touch()
        os.symlink(dev / name, by_id / f"ata-{model.replace(' ', '_')}_SERIAL")
        os.symlink(dev / (name + "-part1"), by_id / f"ata-{model.replace(' ', '_')}_SERIAL-part1")
        os.symlink(dev / name, by_path / f"pci-0000:00:{name}")
    sys_block.joinpath("loop0").mkdir()
    return dict(sys_block=str(sys_block), by_id_dir=str(by_id), by_path_dir=str(by_path))


def match(**kwargs):
    fields = dict(by_id=None, by_path=None, model=None, size_min=None,
                  first_non_removable=False)
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def test_by_id_glob_ignores_partitions(tree):
    assert resolve_disk(match(by_id="ata-Samsung*"), **tree) == "/dev/nvme0n1"


def test_by_path(tree):
    assert resolve_disk(match(by_path="*:sda"), **tree) == "/dev/sda"


def test_matchers_combine(tree):
    assert resolve_disk(match(first_non_removable=True, size_min="1TB"), **tree) == "/dev/sda"


def test_ambiguous_match_fails(tree):
    with pytest.raises(DiskMatchError, match="matches 2 disks"):
        resolve_disk(match(first_non_removable=True), **tree)


def test_no_match_lists_disks(tree):
    with pytest.raises(DiskMatchError, match="(?s)no disk matches.*sda"):
        resolve_disk(match(model="Nonexistent*"), **tree)


def test_loop_devices_are_never_targets(tree):
    with pytest.raises(DiskMatchError):
        resolve_disk(match(by_path="*loop0"), **tree)
