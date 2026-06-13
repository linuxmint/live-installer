"""Unit tests for the headless driver: config mapping, answer-file
acquisition, and the post-engine failure-policy machinery."""

import textwrap

import pytest

import auto_installer
import schema
from test_engine_commands import RecordingRunner


def make_config(extra="", logging_dest="/tmp/test-auto-install.log"):
    return schema.parse_config(textwrap.dedent(f"""\
        version: 1
        hostname: ws-01
        locale: en_CA.UTF-8
        timezone: America/Toronto
        users:
          - name: admin
            gecos: The Admin
            passwd: "$6$rounds=4096$salt$hash"
            autologin: true
            groups: [sudo]
        storage:
          target:
            match:
              first-non-removable: true
        logging:
          destination: {logging_dest}
    """) + textwrap.dedent(extra))


class TestFetchAnswerFile:
    def test_local_path(self, tmp_path):
        f = tmp_path / "answer.yaml"
        f.write_text("version: 1\n")
        assert auto_installer.fetch_answer_file(str(f)) == "version: 1\n"

    def test_missing_path(self):
        with pytest.raises(schema.ConfigError):
            auto_installer.fetch_answer_file("/nonexistent/answer.yaml")

    def test_plain_http_refused(self):
        with pytest.raises(schema.ConfigError) as excinfo:
            auto_installer.fetch_answer_file("http://example.com/a.yaml")
        assert "plain HTTP" in str(excinfo.value)
        assert "--insecure" in str(excinfo.value)

    def test_nfs_refused_without_insecure(self):
        with pytest.raises(schema.ConfigError) as excinfo:
            auto_installer.fetch_answer_file("nfs://host/export/a.yaml")
        assert "NFS" in str(excinfo.value)
        assert "--insecure" in str(excinfo.value)


class TestNfsFetch:
    @pytest.mark.parametrize("url,expected", [
        ("nfs://host/export/dir/a.yaml", ("host", "/export/dir", "a.yaml")),
        ("nfs://host:2049/export/a.yaml", ("host", "/export", "a.yaml")),
        ("nfs://10.0.0.1/srv/cfg/host.yaml", ("10.0.0.1", "/srv/cfg", "host.yaml")),
    ])
    def test_parse_nfs_url(self, url, expected):
        assert auto_installer._parse_nfs_url(url) == expected

    def test_parse_nfs_url_malformed(self):
        with pytest.raises(schema.ConfigError):
            auto_installer._parse_nfs_url("nfs://hostonly")

    def test_fetch_nfs_mounts_reads_unmounts(self, tmp_path, monkeypatch):
        # Stand in a real dir for the "mount", assert it is read and unmounted.
        export = tmp_path / "export"
        export.mkdir()
        (export / "a.yaml").write_text("version: 1\n")
        umounted = []
        monkeypatch.setattr(auto_installer, "_nfs_mount",
                            lambda host, d: str(export))
        monkeypatch.setattr(auto_installer, "_nfs_umount",
                            lambda mp: umounted.append(mp))
        text = auto_installer.fetch_answer_file(
            "nfs://host/export/a.yaml", insecure=True)
        assert text == "version: 1\n"
        assert umounted == [str(export)]  # always unmounts


class TestCmdlineSource:
    def test_present(self, tmp_path):
        cmdline = tmp_path / "cmdline"
        cmdline.write_text(
            "boot=live quiet live-installer.auto=https://cfg/a.yaml splash\n"
        )
        assert auto_installer.cmdline_source(str(cmdline)) == "https://cfg/a.yaml"

    def test_absent(self, tmp_path):
        cmdline = tmp_path / "cmdline"
        cmdline.write_text("boot=live quiet splash\n")
        assert auto_installer.cmdline_source(str(cmdline)) is None

    def test_insecure_flag(self, tmp_path):
        cmdline = tmp_path / "cmdline"
        cmdline.write_text(
            "boot=live live-installer.auto=http://h/a.yaml "
            "live-installer.auto-insecure\n"
        )
        assert auto_installer.cmdline_insecure(str(cmdline)) is True

    def test_insecure_flag_absent(self, tmp_path):
        cmdline = tmp_path / "cmdline"
        cmdline.write_text("boot=live live-installer.auto=https://h/a.yaml\n")
        assert auto_installer.cmdline_insecure(str(cmdline)) is False


class TestBuildSetup:
    def test_basic_mapping(self):
        config = make_config()
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=False, is_mint=False)
        assert setup.automated is True
        assert setup.language == "en_CA"  # encoding suffix stripped
        assert setup.timezone == "America/Toronto"
        assert setup.hostname == "ws-01"
        assert setup.username == "admin"  # Setup field name unchanged
        assert setup.real_name == "The Admin"
        assert setup.password1 == "$6$rounds=4096$salt$hash"
        assert setup.password_is_crypted is True
        assert setup.autologin is True
        assert setup.lvm is False and setup.luks is False
        assert setup.disk == "/dev/vda"
        assert setup.diskname == "vda"
        assert setup.grub_device == "/dev/vda"
        assert setup.gptonefi is False
        assert setup.is_mint is False

    def test_lvm_on_luks_with_keyfile(self, tmp_path):
        keyfile = tmp_path / "luks.key"
        keyfile.write_text("sekrit-passphrase\n")
        config = schema.parse_config(textwrap.dedent(f"""\
            version: 1
            locale: en_CA.UTF-8
            timezone: America/Toronto
            users:
              - name: admin
                passwd: "$6$rounds=4096$salt$hash"
            storage:
              layout: lvm-on-luks
              luks:
                passphrase_source: keyfile
                keyfile: {keyfile}
              target:
                match:
                  first-non-removable: true
        """))
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=True, is_mint=False)
        assert setup.lvm is True and setup.luks is True
        assert setup.passphrase1 == "sekrit-passphrase"
        assert setup.gptonefi is True

    def test_keyfile_over_plain_http_refused(self):
        config = schema.parse_config(textwrap.dedent("""\
            version: 1
            locale: en_CA.UTF-8
            timezone: America/Toronto
            users:
              - name: admin
                passwd: "$6$rounds=4096$salt$hash"
            storage:
              layout: lvm-on-luks
              luks:
                passphrase_source: keyfile
                keyfile: http://server/luks.key
              target:
                match:
                  first-non-removable: true
        """))
        with pytest.raises(schema.ConfigError) as excinfo:
            auto_installer.build_setup(
                config, disk="/dev/vda", efi=False, is_mint=False)
        assert "plain HTTP" in str(excinfo.value)
        # and allowed when insecure is explicitly granted (fetch will then
        # fail on the unreachable host, which is a different error)
        with pytest.raises(schema.ConfigError) as excinfo2:
            auto_installer.build_setup(
                config, disk="/dev/vda", efi=False, is_mint=False,
                insecure=True)
        assert "plain HTTP" not in str(excinfo2.value)

    def test_unimplemented_passphrase_source_fails_early(self):
        config = schema.parse_config(textwrap.dedent("""\
            version: 1
            locale: en_CA.UTF-8
            timezone: America/Toronto
            users:
              - name: admin
                passwd: "$6$rounds=4096$salt$hash"
            storage:
              layout: lvm-on-luks
              target:
                match:
                  first-non-removable: true
        """))
        with pytest.raises(schema.ConfigError) as excinfo:
            auto_installer.build_setup(
                config, disk="/dev/vda", efi=False, is_mint=False)
        assert "not yet implemented" in str(excinfo.value)

    def test_default_hostname(self):
        config = schema.parse_config(textwrap.dedent("""\
            version: 1
            locale: en_US.UTF-8
            timezone: America/Toronto
            users:
              - name: admin
                passwd: "$6$rounds=4096$salt$hash"
            storage:
              target:
                match:
                  first-non-removable: true
        """))
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=False, is_mint=False)
        assert setup.hostname == "mint"


class FakeEngine:
    def __init__(self, fail_in=None):
        self.fail_in = fail_in
        self.calls = []
        self._error_hook = None

    def set_progress_hook(self, hook):
        pass

    def set_error_hook(self, hook):
        self._error_hook = hook

    def start_installation(self):
        self.calls.append("start")
        if self.fail_in == "start":
            self._error_hook(message="boom in start")

    def finish_installation(self, before_unmount_hook=None):
        self.calls.append("finish")
        if self.fail_in == "finish":
            self._error_hook(message="boom in finish")
            return
        # mirror the real engine: hook runs while the chroot is mounted
        if before_unmount_hook is not None:
            self.calls.append("hook")
            before_unmount_hook()


def make_driver(config, fail_in=None):
    runner = RecordingRunner()
    engine = FakeEngine(fail_in)
    driver = auto_installer.HeadlessDriver(
        config, runner=runner,
        engine_factory=lambda setup, r: engine,
    )
    return driver, runner, engine


def run_driver(config, fail_in=None, **setup_kwargs):
    import installer

    driver, runner, engine = make_driver(config, fail_in)
    setup = auto_installer.build_setup(
        config, disk="/dev/vda", efi=False, is_mint=False)
    rc = driver.run(setup=setup)
    return rc, runner, engine


class TestHeadlessDriver:
    def test_happy_path(self, tmp_path, capsys):
        config = make_config(logging_dest=str(tmp_path / "auto.log"))
        rc, runner, engine = run_driver(config)
        assert rc == 0
        assert engine.calls == ["start", "finish"]
        out = capsys.readouterr().out
        assert auto_installer.FINAL_MARKER in out

    def test_engine_error_aborts(self, tmp_path, capsys):
        config = make_config(logging_dest=str(tmp_path / "auto.log"))
        rc, runner, engine = run_driver(config, fail_in="start")
        assert rc == 1
        assert engine.calls == ["start"]  # finish never runs
        out = capsys.readouterr().out
        assert auto_installer.FAILURE_MARKER in out

    def test_progress_is_deduplicated(self, tmp_path, capsys):
        config = make_config(logging_dest=str(tmp_path / "auto.log"))
        driver, _, _ = make_driver(config)
        for _ in range(1000):
            driver.on_progress(7, False, False, "Copying files...")
        driver.on_progress(8, False, False, "Copying files...")
        out = capsys.readouterr().out
        assert out.count("[  7%] Copying files...") == 1
        assert out.count("[  8%] Copying files...") == 1

    def test_log_file_receives_output(self, tmp_path):
        log = tmp_path / "auto.log"
        config = make_config(logging_dest=str(log))
        rc, _, _ = run_driver(config)
        assert rc == 0
        assert auto_installer.FINAL_MARKER in log.read_text()

    def test_package_install_runs_in_chroot(self, tmp_path):
        config = make_config(
            extra="""\
                packages: [openssh-server]
            """,
            logging_dest=str(tmp_path / "auto.log"),
        )
        rc, runner, _ = run_driver(config)
        assert rc == 0
        chroots = [c for c in runner.commands if c.startswith("chroot")]
        assert any("apt-get update" in c for c in chroots)
        assert any("apt-get install -y openssh-server" in c for c in chroots)
        # dpkg state repair must run between update and install (the engine's
        # offline EFI bootloader install leaves unmet dependencies behind)
        fix = next(i for i, c in enumerate(chroots) if "install -f -y" in c)
        install = next(i for i, c in enumerate(chroots)
                       if "install -y openssh-server" in c)
        assert fix < install

    def _driver(self, kernel_yaml, tmp_path):
        config = make_config(extra=kernel_yaml,
                             logging_dest=str(tmp_path / "auto.log"))
        driver, runner, _ = make_driver(config)
        return driver, runner

    def test_cmdline_extra_snippet(self, tmp_path):
        driver, _ = self._driver(
            'kernel:\n  cmdline_extra: "mitigations=off ipv6.disable=1"\n',
            tmp_path)
        snippet = driver._build_grub_snippet()
        assert ('GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} '
                'mitigations=off ipv6.disable=1"' in snippet)

    def test_luks_regenerates_initramfs_with_medium_bound(self, tmp_path):
        config = schema.parse_config(textwrap.dedent(f"""\
            version: 1
            locale: en_US.UTF-8
            timezone: America/Toronto
            users:
              - name: admin
                passwd: "$6$rounds=4096$salt$hash"
            storage:
              layout: lvm-on-luks
              luks:
                passphrase_source: keyfile
                keyfile: {tmp_path / "k"}
              target:
                match:
                  first-non-removable: true
            logging:
              destination: {tmp_path / "auto.log"}
        """))
        (tmp_path / "k").write_text("pass\n")
        driver, runner, engine = make_driver(config)
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=False, is_mint=False)
        rc = driver.run(setup=setup)
        assert rc == 0
        joined = " ".join(runner.commands)
        # resolves the real (un-diverted) update-initramfs and runs it so
        # the crypttab lands in the initramfs
        assert "dpkg-divert --truename /usr/sbin/update-initramfs" in joined
        assert any("update-initramfs -u -k all" in c for c in runner.commands)

    def test_serial_console_snippet(self, tmp_path):
        driver, _ = self._driver(
            'kernel:\n  serial_console: "ttyS0,115200"\n', tmp_path)
        snippet = driver._build_grub_snippet()
        # strips plymouth grabbers, adds consoles + plymouth.enable=0,
        # configures GRUB's serial terminal — sourced last so it wins
        assert "s/ quiet / /g" in snippet and "s/ splash / /g" in snippet
        assert "console=tty0 console=ttyS0,115200n8 plymouth.enable=0" in snippet
        assert 'GRUB_TERMINAL="console serial"' in snippet
        assert 'GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"' in snippet

    def test_kernel_config_writes_snippet_and_runs_update_grub(self, tmp_path):
        target = tmp_path / "target"
        (target / "etc/default/grub.d").mkdir(parents=True)
        driver, runner = self._driver(
            'kernel:\n  serial_console: "ttyS0"\n', tmp_path)
        import builtins
        real_open = builtins.open

        def redir(path, *a, **k):
            if isinstance(path, str) and path.startswith("/target/"):
                path = str(target / path[len("/target/"):])
            return real_open(path, *a, **k)

        builtins.open = redir
        try:
            config = make_config(
                extra='kernel:\n  serial_console: "ttyS0"\n',
                logging_dest=str(tmp_path / "auto.log"))
            d2, runner2, _ = make_driver(config)
            setup = auto_installer.build_setup(
                config, disk="/dev/vda", efi=False, is_mint=False)
            rc = d2.run(setup=setup)
        finally:
            builtins.open = real_open
        assert rc == 0
        snippet = (target / "etc/default/grub.d/zz-live-installer.cfg").read_text()
        assert "console=ttyS0" in snippet
        assert any("update-grub" in c for c in runner2.commands)

    def test_package_failure_policy_abort(self, tmp_path):
        config = make_config(
            extra="""\
                packages: [doesnotexist]
            """,
            logging_dest=str(tmp_path / "auto.log"),
        )
        driver, runner, engine = make_driver(config)
        # make apt-get install fail
        original_chroot = runner.chroot

        def failing_chroot(command, check=False, target="/target"):
            runner.commands.append(command)
            return 100 if "apt-get install" in command else 0

        runner.chroot = failing_chroot
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=False, is_mint=False)
        rc = driver.run(setup=setup)
        assert rc == 1  # default policy is abort

    def test_package_failure_policy_continue(self, tmp_path):
        config = make_config(
            extra="""\
                packages: [doesnotexist]
                on_failure:
                  package_install_failure: continue
                  network_unavailable: continue
            """,
            logging_dest=str(tmp_path / "auto.log"),
        )
        driver, runner, engine = make_driver(config)

        def failing_chroot(command, check=False, target="/target"):
            runner.commands.append(command)
            return 100 if "apt-get" in command else 0

        runner.chroot = failing_chroot
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=False, is_mint=False)
        rc = driver.run(setup=setup)
        assert rc == 0  # policy says continue


class TestCheckMode:
    """--check is the ksvalidator analog: validate the schema and stop
    before any disk resolution, so it runs anywhere (CI, a dev laptop)."""

    def _valid_file(self, tmp_path):
        f = tmp_path / "answer.yaml"
        f.write_text(textwrap.dedent("""\
            version: 1
            locale: en_US.UTF-8
            timezone: America/Toronto
            users:
              - name: admin
                passwd: "$6$rounds=4096$salt$hash"
            storage:
              target:
                match:
                  first-non-removable: true
        """))
        return f

    def test_valid_file_passes(self, tmp_path, capsys):
        rc = auto_installer.main(["--check", "--config",
                                  str(self._valid_file(tmp_path))])
        assert rc == 0
        assert "OK" in capsys.readouterr().out

    def test_invalid_file_fails(self, tmp_path, capsys):
        f = tmp_path / "bad.yaml"
        f.write_text("version: 1\nbogus: yes\n")
        rc = auto_installer.main(["--check", "--config", str(f)])
        assert rc == 1
        assert "validation" in capsys.readouterr().out

    def test_check_resolves_no_disk(self, tmp_path, monkeypatch):
        # --check must never call into disk resolution.
        def boom(*a, **k):
            raise AssertionError("disk resolution must not run under --check")

        monkeypatch.setattr(auto_installer.diskmatch, "resolve_disk", boom)
        rc = auto_installer.main(["--check", "--config",
                                  str(self._valid_file(tmp_path))])
        assert rc == 0


class TestListDisks:
    def test_format_renders_attributes(self):
        out = auto_installer.format_disk_list([{
            "path": "/dev/nvme0n1", "model": "Samsung 980 PRO",
            "size_bytes": 1_000_000_000_000, "removable": False,
            "by_id": ["nvme-Samsung_SSD_980_PRO_1TB_S5GX"],
            "by_path": ["pci-0000:01:00.0-nvme-1"],
        }])
        assert "/dev/nvme0n1" in out
        assert "1.0TB" in out
        assert "nvme-Samsung_SSD_980_PRO_1TB_S5GX" in out
        assert "pci-0000:01:00.0-nvme-1" in out

    def test_format_handles_no_disks(self):
        assert "No installable disks" in auto_installer.format_disk_list([])

    def test_cli_needs_no_config(self, monkeypatch, capsys):
        # --list-disks is a pre-flight helper: it must run with no answer file.
        monkeypatch.setattr(auto_installer.diskmatch, "describe_disks",
                            lambda **k: [])
        rc = auto_installer.main(["--list-disks"])
        assert rc == 0
        assert "No installable disks" in capsys.readouterr().out


class TestAcquireAnswerText:
    MINIMAL = textwrap.dedent("""\
        version: 1
        locale: en_US.UTF-8
        timezone: America/Toronto
        users:
          - name: admin
            passwd: "$6$rounds=4096$salt$hash"
        storage:
          target:
            match:
              first-non-removable: true
    """)

    def test_plain_source_fetched_directly(self, tmp_path):
        f = tmp_path / "a.yaml"
        f.write_text(self.MINIMAL)
        text = auto_installer.acquire_answer_text(str(f), insecure=False)
        assert "version: 1" in text

    def test_auto_discovers_by_identity(self, monkeypatch):
        # The by-serial file exists; the by-mac one does not. Discovery must
        # walk past the miss and land on the serial-keyed file.
        served = {
            "https://cfg/by-serial/SN1.yaml": self.MINIMAL,
            "https://cfg/default.yaml": "version: 1\n",  # would be invalid
        }
        monkeypatch.setattr(
            auto_installer.discovery, "read_machine_identity",
            lambda *a, **k: {"macs": ["aa:bb:cc:dd:ee:ff"],
                             "serial": "SN1", "uuid": None})

        def fake_fetch(src, insecure=False):
            if src in served:
                return served[src]
            raise schema.ConfigError(f"not found: {src}")

        monkeypatch.setattr(auto_installer, "fetch_answer_file", fake_fetch)
        text = auto_installer.acquire_answer_text("auto:https://cfg/",
                                                  insecure=False)
        assert "admin" in text

    def test_auto_miss_raises_configerror(self, monkeypatch):
        monkeypatch.setattr(
            auto_installer.discovery, "read_machine_identity",
            lambda *a, **k: {"macs": [], "serial": None, "uuid": None})
        monkeypatch.setattr(
            auto_installer, "fetch_answer_file",
            lambda s, insecure=False: (_ for _ in ()).throw(
                schema.ConfigError("nope")))
        with pytest.raises(schema.ConfigError):
            auto_installer.acquire_answer_text("auto:https://cfg/",
                                               insecure=False)
