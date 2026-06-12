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
        locale:
          language: en_CA.UTF-8
          timezone: America/Toronto
        network:
          hostname: ws-01
        users:
          - username: admin
            full_name: The Admin
            password_crypted: "$6$rounds=4096$salt$hash"
            autologin: true
            sudo: true
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
        assert setup.username == "admin"
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
            locale:
              language: en_CA.UTF-8
              timezone: America/Toronto
            users:
              - username: admin
                password_crypted: "$6$rounds=4096$salt$hash"
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

    def test_unimplemented_passphrase_source_fails_early(self):
        config = schema.parse_config(textwrap.dedent("""\
            version: 1
            locale:
              language: en_CA.UTF-8
              timezone: America/Toronto
            users:
              - username: admin
                password_crypted: "$6$rounds=4096$salt$hash"
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
            locale:
              language: en_US.UTF-8
              timezone: America/Toronto
            users:
              - username: admin
                password_crypted: "$6$rounds=4096$salt$hash"
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

    def finish_installation(self):
        self.calls.append("finish")
        if self.fail_in == "finish":
            self._error_hook(message="boom in finish")


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
                packages:
                  add: [openssh-server]
            """,
            logging_dest=str(tmp_path / "auto.log"),
        )
        rc, runner, _ = run_driver(config)
        assert rc == 0
        chroots = [c for c in runner.commands if c.startswith("chroot")]
        assert any("apt-get update" in c for c in chroots)
        assert any("apt-get install -y openssh-server" in c for c in chroots)
        # chroot was mounted and unmounted around the steps
        assert any("mount --bind /proc/ /target/proc/" in c
                   for c in runner.commands)
        assert any("umount --force /target/proc/" in c
                   for c in runner.commands)

    def test_package_failure_policy_abort(self, tmp_path):
        config = make_config(
            extra="""\
                packages:
                  add: [doesnotexist]
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
                packages:
                  add: [doesnotexist]
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
