"""Component tests for InstallerEngine via an injected CommandRunner.

These exercise the engine's command construction without touching the
system: the recording runner captures every command instead of running
it, and returns canned output where the engine consumes it.
"""

import pytest

import installer
from commandrunner import CommandError, CommandRunner


class RecordingRunner(CommandRunner):
    """Captures commands; runs nothing.  Canned output by substring match."""

    def __init__(self, outputs=None):
        super().__init__(log=lambda *a: None)
        self.commands = []
        self.outputs = outputs or {}

    def run(self, command, check=False):
        self.commands.append(command)
        return 0

    def output(self, command, check=False):
        self.commands.append(command)
        for needle, canned in self.outputs.items():
            if needle in command:
                return canned
        return ""

    def popen(self, command):  # pragma: no cover - not used in these tests
        raise AssertionError(f"unexpected popen: {command}")


def make_engine(outputs=None, **setup_attrs):
    setup = installer.Setup()
    for key, value in setup_attrs.items():
        setattr(setup, key, value)
    runner = RecordingRunner(outputs)
    return installer.InstallerEngine(setup, runner=runner), runner


class TestEngineWiring:
    def test_default_runner_is_real(self):
        engine = installer.InstallerEngine(installer.Setup())
        assert isinstance(engine.runner, CommandRunner)

    def test_setup_timezone_commands(self):
        engine, runner = make_engine(timezone="America/Toronto")
        engine.setup_timezone()
        assert 'echo "America/Toronto" > /target/etc/timezone' in runner.commands
        assert "rm -f /target/etc/localtime" in runner.commands
        assert (
            "ln -s /usr/share/zoneinfo/America/Toronto /target/etc/localtime"
            in runner.commands
        )

    def test_do_run_in_chroot_quoting(self):
        engine, runner = make_engine()
        engine.do_run_in_chroot('echo "hello world"')
        assert runner.commands == [
            "chroot /target/ /bin/sh -c \"echo 'hello world'\""
        ]

    def test_get_blkid_finds_uuid(self):
        blkid_output = (
            '/dev/sda1: UUID="1111-2222" TYPE="vfat" PARTUUID="aa"\n'
            '/dev/mapper/lvmmint-root: UUID="abcd-ef01" TYPE="ext4"'
        )
        engine, runner = make_engine(outputs={"blkid": blkid_output})
        assert engine.get_blkid("/dev/mapper/lvmmint-root") == "UUID=abcd-ef01"

    def test_get_blkid_falls_back_to_path(self):
        engine, runner = make_engine(outputs={"blkid": ""})
        assert engine.get_blkid("/dev/sda9") == "/dev/sda9"


class TestEditionPaths:
    """The engine reads the live filesystem and grub-title script from
    different locations on Mint (Ubuntu/casper) vs LMDE (Debian/live-boot).
    Integration tests only ever exercise the LMDE branch, so pin both here
    — a wrong path on Mint 23 would otherwise go unnoticed until release."""

    def test_lmde_paths(self):
        setup = installer.Setup()
        setup.is_mint = False
        engine = installer.InstallerEngine(setup, runner=RecordingRunner())
        assert engine.casper == "/run/live/medium/live"
        assert engine.pool == "/run/live/medium/pool"
        assert engine.manifest == "/run/live/medium/live/filesystem.packages"
        assert "debian-system-adjustments" in engine.grub_adjustment_script

    def test_mint_paths(self):
        setup = installer.Setup()
        setup.is_mint = True
        engine = installer.InstallerEngine(setup, runner=RecordingRunner())
        assert engine.casper == "/cdrom/casper"
        assert engine.pool == "/cdrom/pool"
        assert engine.manifest == "/cdrom/casper/filesystem.manifest"
        assert "ubuntu-system-adjustments" in engine.grub_adjustment_script

    def test_squashfs_path_follows_edition(self):
        # the media mounted in start_installation is <casper>/filesystem.squashfs
        for is_mint, expected in [
            (False, "/run/live/medium/live/filesystem.squashfs"),
            (True, "/cdrom/casper/filesystem.squashfs"),
        ]:
            setup = installer.Setup()
            setup.is_mint = is_mint
            engine = installer.InstallerEngine(setup, runner=RecordingRunner())
            assert f"{engine.casper}/filesystem.squashfs" == expected


class TestCommandRunner:
    def test_run_returns_exit_code(self):
        runner = CommandRunner(log=lambda *a: None)
        assert runner.run("true") == 0
        assert runner.run("false") == 1

    def test_run_check_raises(self):
        runner = CommandRunner(log=lambda *a: None)
        with pytest.raises(CommandError) as excinfo:
            runner.run("false", check=True)
        assert excinfo.value.returncode == 1

    def test_output_strips_single_trailing_newline(self):
        runner = CommandRunner(log=lambda *a: None)
        assert runner.output("echo hello") == "hello"
        assert runner.output("printf 'a\\nb\\n'") == "a\nb"

    def test_output_merges_stderr(self):
        runner = CommandRunner(log=lambda *a: None)
        assert runner.output("echo oops >&2") == "oops"

    def test_secrets_are_redacted_from_logs_but_executed(self, tmp_path):
        logged = []
        runner = CommandRunner(log=logged.append)
        out = tmp_path / "out"
        rc = runner.run(
            f"echo -n 's3cret pass' > {out}", secrets=["s3cret pass"]
        )
        assert rc == 0
        assert out.read_text() == "s3cret pass"  # command ran unredacted
        assert all("s3cret" not in line for line in logged)
        assert any("[REDACTED]" in line for line in logged)

    def test_stdin_feeds_command_without_logging_it(self, tmp_path):
        logged = []
        runner = CommandRunner(log=logged.append)
        out = tmp_path / "out"
        # cat reads the secret from stdin; the command line never carries it
        rc = runner.run(f"cat > {out}", stdin="luks-passphrase-xyz")
        assert rc == 0
        assert out.read_text() == "luks-passphrase-xyz"  # reached the command
        assert all("luks-passphrase-xyz" not in line for line in logged)

    def test_chroot_command_shape(self):
        captured = []
        runner = CommandRunner(log=lambda *a: None)
        runner.run = lambda cmd, check=False: captured.append(cmd) or 0
        runner.chroot('apt install "thing"')
        assert captured == ["chroot /target/ /bin/sh -c \"apt install 'thing'\""]


class TestCustomPartitions:
    def test_size_to_mb(self):
        f = installer.InstallerEngine._size_to_mb
        assert f("512MB") == 512
        assert f("40GB") == 40000
        assert f("1.5TB") == 1500000

    def test_custom_fstab_from_auto_mounts(self, tmp_path):
        engine, runner = make_engine(automated=True)
        runner.outputs = {"blkid": (
            '/dev/vda1: UUID="EFI" TYPE="vfat"\n'
            '/dev/vg0/root: UUID="ROOT" TYPE="ext4"\n'
            '/dev/vda2: UUID="SWAP" TYPE="swap"')}
        engine.auto_mounts = [
            ("/dev/vda1", "/boot/efi", "vfat"),
            ("/dev/vg0/root", "/", "ext4"),
            ("/dev/vda2", "swap", "swap"),
        ]
        path = tmp_path / "fstab"
        engine.write_fstab(str(path))
        text = path.read_text()
        assert "UUID=ROOT\t/\text4\trw,errors=remount-ro\t0\t1" in text
        assert "UUID=EFI\t/boot/efi\tvfat\tdefaults\t0\t1" in text
        assert "UUID=SWAP\tswap\tswap\tsw\t0\t0" in text

    def test_create_custom_partitions_command_sequence(self, monkeypatch):
        engine, runner = make_engine(
            automated=True, disk="/dev/vda", gptonefi=True,
            custom_partitions=[
                {"size": "512MB", "mount": "/boot/efi", "filesystem": "vfat",
                 "flags": ["esp"], "lvm_pv": None},
                {"size": "2GB", "mount": "swap", "filesystem": "swap",
                 "flags": [], "lvm_pv": None},
                {"size": "rest", "mount": None, "filesystem": None,
                 "flags": [], "lvm_pv": "vg0"},
            ],
            custom_lvm=[
                {"vg": "vg0", "lv": "root", "size": "40GB", "mount": "/",
                 "filesystem": "ext4"},
                {"vg": "vg0", "lv": "home", "size": "rest", "mount": "/home",
                 "filesystem": "ext4"},
            ])
        engine.set_progress_hook(lambda *a: None)
        mounts = []
        engine.do_mount = lambda dev, dest, fs, opts: mounts.append((dev, dest, fs))
        sys_cmds = []
        monkeypatch.setattr(installer.os, "system",
                            lambda cmd: sys_cmds.append(cmd) or 0)
        monkeypatch.setattr(installer.time, "sleep", lambda *a: None)
        monkeypatch.setattr(installer.os.path, "exists", lambda p: True)
        fake_dev = type("D", (), {
            "path": "/dev/vda",
            "getLength": lambda self, unit: 256 * 10**9,
            "sectorSize": 512})()
        monkeypatch.setattr(installer.parted, "getDevice", lambda p: fake_dev,
                            raising=False)
        monkeypatch.setattr(installer.partitioning,
                            "get_device_naming_scheme_prefix", lambda p: "")

        engine._create_custom_partitions()

        # LVM + filesystems through the runner
        assert "pvcreate -y /dev/vda3" in runner.commands
        assert "vgcreate -y vg0 /dev/vda3" in runner.commands
        assert "lvcreate -y -n root -L 40000M vg0" in runner.commands
        assert "lvcreate -y -n home -l 100%FREE vg0" in runner.commands
        assert "mkfs.vfat /dev/vda1 -F 32" in runner.commands
        assert "mkfs.ext4 -F /dev/vg0/root" in runner.commands
        assert "mkswap /dev/vda2" in runner.commands
        # parted through os.system
        assert any("mklabel gpt" in c for c in sys_cmds)
        assert any("set 1 esp on" in c for c in sys_cmds)
        # mounts recorded; root mounted before deeper paths
        targets = [m[1] for m in mounts]
        assert targets[0] == "/target"                 # / first
        assert "/target/home" in targets
        assert "/target/boot/efi" in targets
        assert ("/dev/vg0/root", "/", "ext4") in engine.auto_mounts
