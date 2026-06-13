"""Unit tests for stable-attribute disk resolution."""

import os
from types import SimpleNamespace

import pytest

import diskmatch
from diskmatch import DiskMatchError


def match(**kwargs):
    defaults = dict(by_id=None, by_path=None, model=None, size_min=None,
                    first_non_removable=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.fixture
def fake_tree(tmp_path):
    """A fake /sys/block + /dev/disk/by-id with three disks and a loop dev.

    nvme0n1: 1TB Samsung 980 PRO (fixed)
    sda:     500GB Samsung 860 EVO (fixed)
    sdb:     32GB Kingston USB stick (removable)
    loop0:   excluded device type
    """
    sys_block = tmp_path / "sys_block"
    by_id = tmp_path / "by-id"
    by_id.mkdir()

    disks = {
        "nvme0n1": (1_000_000_000_000, "0", "Samsung SSD 980 PRO 1TB"),
        "sda": (500_000_000_000, "0", "Samsung SSD 860 EVO 500GB"),
        "sdb": (32_000_000_000, "1", "Kingston DataTraveler 3.0"),
        "loop0": (10_000_000_000, "0", ""),
    }
    for name, (size_bytes, removable, model) in disks.items():
        d = sys_block / name
        (d / "device").mkdir(parents=True)
        (d / "size").write_text(str(size_bytes // 512))
        (d / "removable").write_text(removable)
        if model:
            (d / "device" / "model").write_text(model + "\n")

    links = {
        "nvme-Samsung_SSD_980_PRO_1TB_S5GXNX0T123456": "nvme0n1",
        "nvme-eui.0025385b21404566": "nvme0n1",  # alias for the same disk
        "nvme-Samsung_SSD_980_PRO_1TB_S5GXNX0T123456-part1": "nvme0n1p1",
        "ata-Samsung_SSD_860_EVO_500GB_S3Z8NB0K": "sda",
        "ata-Samsung_SSD_860_EVO_500GB_S3Z8NB0K-part1": "sda1",
        "usb-Kingston_DataTraveler_3.0": "sdb",
    }
    for link, target in links.items():
        os.symlink("../../" + target, by_id / link)

    return dict(sys_block=str(sys_block), by_id_dir=str(by_id),
                by_path_dir=str(tmp_path / "by-path-missing"))


class TestParseSize:
    @pytest.mark.parametrize("text,expected", [
        ("500GB", 500_000_000_000),
        ("1TB", 1_000_000_000_000),
        ("1.5TB", 1_500_000_000_000),
        ("250 GB", 250_000_000_000),
    ])
    def test_valid(self, text, expected):
        assert diskmatch.parse_size(text) == expected

    def test_invalid(self):
        with pytest.raises(DiskMatchError):
            diskmatch.parse_size("lots")


class TestListDisks:
    def test_excludes_virtual_devices(self, fake_tree):
        names = [d.name for d in diskmatch.list_disks(fake_tree["sys_block"])]
        assert names == ["nvme0n1", "sda", "sdb"]  # no loop0, sorted

    def test_reads_attributes(self, fake_tree):
        disks = {d.name: d for d in diskmatch.list_disks(fake_tree["sys_block"])}
        assert disks["sda"].size_bytes == 500_000_000_000
        assert disks["sda"].removable is False
        assert disks["sdb"].removable is True
        assert disks["nvme0n1"].model == "Samsung SSD 980 PRO 1TB"


class TestDescribeDisks:
    def test_collects_links_per_disk(self, fake_tree):
        described = {d["path"]: d for d in diskmatch.describe_disks(**fake_tree)}
        nvme = described["/dev/nvme0n1"]
        # both whole-disk aliases, never the -part1 symlink
        assert sorted(nvme["by_id"]) == [
            "nvme-Samsung_SSD_980_PRO_1TB_S5GXNX0T123456",
            "nvme-eui.0025385b21404566",
        ]
        assert described["/dev/sda"]["by_id"] == [
            "ata-Samsung_SSD_860_EVO_500GB_S3Z8NB0K"
        ]
        assert nvme["size_bytes"] == 1_000_000_000_000
        assert nvme["removable"] is False

    def test_missing_by_path_dir_is_empty_not_error(self, fake_tree):
        # fake_tree points by_path at a nonexistent dir
        for d in diskmatch.describe_disks(**fake_tree):
            assert d["by_path"] == []


class TestResolveDisk:
    def test_by_id_glob(self, fake_tree):
        result = diskmatch.resolve_disk(
            match(by_id="nvme-Samsung_SSD_980_PRO_*"), **fake_tree)
        assert result == "/dev/nvme0n1"

    def test_by_id_aliases_same_disk_not_ambiguous(self, fake_tree):
        # 'nvme-*' matches two symlinks, but they point at the same disk
        result = diskmatch.resolve_disk(match(by_id="nvme-*"), **fake_tree)
        assert result == "/dev/nvme0n1"

    def test_partition_symlinks_ignored(self, fake_tree):
        result = diskmatch.resolve_disk(
            match(by_id="ata-Samsung_SSD_860_EVO_500GB_*"), **fake_tree)
        assert result == "/dev/sda"

    def test_ambiguous_match_aborts(self, fake_tree):
        with pytest.raises(DiskMatchError) as excinfo:
            diskmatch.resolve_disk(match(model="Samsung*"), **fake_tree)
        assert "ambiguous" in str(excinfo.value)
        assert "Refusing to guess" in str(excinfo.value)

    def test_model_and_size_compose(self, fake_tree):
        result = diskmatch.resolve_disk(
            match(model="Samsung*", size_min="1TB"), **fake_tree)
        assert result == "/dev/nvme0n1"

    def test_size_min_excludes_small_disks(self, fake_tree):
        with pytest.raises(DiskMatchError) as excinfo:
            diskmatch.resolve_disk(
                match(model="Kingston*", size_min="100GB"), **fake_tree)
        assert "no disk matches" in str(excinfo.value)

    def test_non_removable_excludes_usb_but_refuses_to_guess(self, fake_tree):
        # The USB stick is never a candidate, but with two internal disks
        # left it must not pick one — that would be the enumeration-order
        # guess the module forbids. It aborts as ambiguous, like any other.
        with pytest.raises(DiskMatchError) as excinfo:
            diskmatch.resolve_disk(match(first_non_removable=True), **fake_tree)
        message = str(excinfo.value)
        assert "2 disks qualify" in message
        assert "/dev/nvme0n1" in message and "/dev/sda" in message
        assert "/dev/sdb" not in message  # the removable USB was excluded

    def test_non_removable_plus_discriminator_resolves(self, fake_tree):
        # Adding a matcher that narrows to one internal disk makes it unique.
        result = diskmatch.resolve_disk(
            match(first_non_removable=True, size_min="800GB"), **fake_tree)
        assert result == "/dev/nvme0n1"

    def test_no_match_lists_available_disks(self, fake_tree):
        with pytest.raises(DiskMatchError) as excinfo:
            diskmatch.resolve_disk(match(model="WDC*"), **fake_tree)
        message = str(excinfo.value)
        assert "available disks" in message
        assert "/dev/sda" in message

    def test_empty_system(self, tmp_path):
        with pytest.raises(DiskMatchError):
            diskmatch.resolve_disk(
                match(first_non_removable=True),
                sys_block=str(tmp_path / "nope"),
                by_id_dir=str(tmp_path / "nope2"),
                by_path_dir=str(tmp_path / "nope3"),
            )
