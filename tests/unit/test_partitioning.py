"""Unit tests for the pure helpers in partitioning.py."""

import types

import pytest

import partitioning


class TestGetDeviceNamingSchemePrefix:
    @pytest.mark.parametrize(
        "device,expected",
        [
            ("/dev/sda", ""),
            ("/dev/sdb", ""),
            ("/dev/vda", ""),
            ("/dev/hda", ""),
            ("/dev/nvme0n1", "p"),
            ("/dev/mmcblk0", "p"),
            ("/dev/loop0", "p"),
            ("/dev/md0", "p"),
        ],
    )
    def test_prefix(self, device, expected):
        assert partitioning.get_device_naming_scheme_prefix(device) == expected


class TestToHumanReadable:
    @pytest.mark.parametrize(
        "size,expected",
        [
            (0, "0.0  "),
            (500, "500.0  "),
            (999, "999.0  "),
            (1000, "1.0 kB"),
            (1500, "1.5 kB"),
            (1_000_000, "1.0 MB"),
            (1_234_567, "1.2 MB"),
            (20_000_000_000, "20.0 GB"),
            (2_000_000_000_000, "2.0 TB"),
            (3_000_000_000_000_000, "3.0 PB"),
        ],
    )
    def test_decimal_units(self, size, expected):
        assert partitioning.to_human_readable(size) == expected


class TestIsEfiSupported:
    @pytest.fixture(autouse=True)
    def no_modprobe(self, monkeypatch):
        monkeypatch.setattr(partitioning.os, "system", lambda cmd: 0)

    def test_efi_present_sysfs(self, monkeypatch):
        monkeypatch.setattr(
            partitioning.os.path, "exists", lambda p: p == "/sys/firmware/efi"
        )
        assert partitioning.is_efi_supported() is True

    def test_efi_present_procfs(self, monkeypatch):
        monkeypatch.setattr(
            partitioning.os.path, "exists", lambda p: p == "/proc/efi"
        )
        assert partitioning.is_efi_supported() is True

    def test_efi_absent(self, monkeypatch):
        monkeypatch.setattr(partitioning.os.path, "exists", lambda p: False)
        assert partitioning.is_efi_supported() is False


def _fake_parted_partition(
    *,
    path="/dev/sda1",
    number=1,
    ptype=0,  # parted.PARTITION_NORMAL in the stub
    fs_type="ext4",
    length_sectors=250_000,
    length_bytes=128_000_000_000,
    disk_length_sectors=1_000_000,
    active=False,
):
    device = types.SimpleNamespace(getLength=lambda unit=None: disk_length_sectors)
    disk = types.SimpleNamespace(device=device)
    partition = types.SimpleNamespace(
        path=path,
        number=number,
        type=ptype,
        disk=disk,
        active=active,
        getFlagsAsString=lambda: "",
    )
    partition.getLength = (
        lambda unit=None: length_bytes if unit == "B" else length_sectors
    )
    if fs_type is not None:
        partition.fileSystem = types.SimpleNamespace(type=fs_type)
    else:
        partition.fileSystem = None
    return partition


class TestPartitionSizeMath:
    """Partition.__init__ mixes pure math with mount/df side effects; the
    side effects are stubbed so the math and classification can be asserted."""

    @pytest.fixture(autouse=True)
    def no_side_effects(self, monkeypatch):
        monkeypatch.setattr(partitioning.os, "system", lambda cmd: 0)

    def test_mountable_ext4_partition(self, monkeypatch):
        # df output: blocks free used% mountpoint (1024B blocks)
        monkeypatch.setattr(
            partitioning,
            "getoutput",
            lambda cmd: "1000000 400000 60% /tmp/live-installer/tmpmount",
        )
        p = partitioning.Partition(_fake_parted_partition())

        # 80 * 250000 / 1000000, floored at 1
        assert p.size_percent == 20.0
        assert p.type == "ext4"
        assert p.name == "/dev/sda1"
        assert p.html_name == "sda1"
        # df path recalculates size from 1024B blocks
        assert p.raw_size == 1_000_000 * 1024
        assert p.size == "1.0 GB"
        assert p.free_space == "409.6 MB"
        assert p.used_percent == "60"
        assert p.mount_as == ""
        assert p.color == "#21619e"

    def test_swap_partition_is_normalized_and_assigned(self, monkeypatch):
        # Empty df output forces the ValueError fallback path
        monkeypatch.setattr(partitioning, "getoutput", lambda cmd: "")
        p = partitioning.Partition(
            _fake_parted_partition(fs_type="linux-swap(v1)")
        )

        assert p.type == "swap"
        assert p.mount_as == partitioning.SWAP_MOUNT_POINT
        assert p.description == "swap"
        assert p.used_percent == 0

    def test_tiny_partition_keeps_minimum_one_percent(self, monkeypatch):
        monkeypatch.setattr(partitioning, "getoutput", lambda cmd: "")
        p = partitioning.Partition(
            _fake_parted_partition(
                length_sectors=10, disk_length_sectors=1_000_000_000
            )
        )
        assert p.size_percent == 1

    def test_metadata_partition_is_rejected(self):
        import parted

        with pytest.raises(AssertionError):
            partitioning.Partition(
                _fake_parted_partition(ptype=parted.PARTITION_METADATA)
            )
