import pytest

import auto_installer
from auto_installer import (HeadlessDriver, build_setup, cmdline_source,
                            find_answer_file, main, read_passphrase)
from schema import ConfigError, parse_config
from test_schema import HASH, MINIMAL


def test_cmdline_source(tmp_path):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("boot=live quiet live-installer.auto=/cdrom/a.yaml splash\n")
    assert cmdline_source(str(cmdline)) == "/cdrom/a.yaml"
    cmdline.write_text("boot=live quiet\n")
    assert cmdline_source(str(cmdline)) is None
    assert cmdline_source(str(tmp_path / "missing")) is None


def test_find_answer_file(tmp_path):
    first, second = tmp_path / "cdrom", tmp_path / "medium"
    first.mkdir()
    second.mkdir()
    (second / "auto-install.yaml").write_text("x")
    assert find_answer_file("auto", (str(first), str(second))) == str(second / "auto-install.yaml")
    assert find_answer_file("/some/path.yaml") == "/some/path.yaml"
    (second / "auto-install.yaml").unlink()
    with pytest.raises(ConfigError, match="no auto-install.yaml"):
        find_answer_file("auto", (str(first), str(second)))


@pytest.mark.parametrize("source", ["https://example.com/a.yaml", "nfs://host/a.yaml"])
def test_urls_are_not_supported(source):
    with pytest.raises(ConfigError, match="only local paths"):
        find_answer_file(source)


def test_read_passphrase(tmp_path):
    answer_file = tmp_path / "auto-install.yaml"
    (tmp_path / "luks.key").write_text("correct horse battery staple \n")
    # relative to the answer file, trailing newline only removed
    assert read_passphrase("luks.key", str(answer_file)) == "correct horse battery staple "
    assert read_passphrase(str(tmp_path / "luks.key"), "/elsewhere/a.yaml") == "correct horse battery staple "
    (tmp_path / "empty.key").write_text("\n")
    with pytest.raises(ConfigError, match="empty"):
        read_passphrase("empty.key", str(answer_file))


def test_build_setup(tmp_path):
    (tmp_path / "luks.key").write_text("secret\n")
    text = (MINIMAL.replace("en_US.UTF-8", "fr_CA.UTF-8")
            + "  layout: luks\n  luks:\n    keyfile: luks.key\n  swapfile_size: 4GB\n"
            + "hostname: box\nkeyboard:\n  layout: ca\n  variant: fr\n")
    config = parse_config(text)
    setup = build_setup(config, str(tmp_path / "a.yaml"),
                        disk="/dev/nvme0n1", efi=True, is_mint=True)
    assert setup.automated and setup.gptonefi and setup.is_mint
    assert setup.language == "fr_CA"
    assert setup.timezone == "America/Toronto"
    assert (setup.keyboard_model, setup.keyboard_layout, setup.keyboard_variant) == ("pc105", "ca", "fr")
    assert setup.hostname == "box"
    assert setup.username == setup.real_name == "admin"
    assert setup.password1 == HASH and setup.password_is_crypted
    assert (setup.disk, setup.diskname, setup.grub_device) == ("/dev/nvme0n1", "nvme0n1", "/dev/nvme0n1")
    assert setup.luks and setup.passphrase1 == setup.passphrase2 == "secret"
    assert setup.swapfile is True
    assert setup.swapfile_size == 4 * 10**9 // 2**20


def test_build_setup_defaults():
    setup = build_setup(parse_config(MINIMAL), "/x/a.yaml", disk="/dev/sda", efi=False, is_mint=False)
    assert setup.hostname == "mint"
    assert setup.luks is False
    assert not hasattr(setup, "swapfile_size")


class FakeEngine:
    def __init__(self, fail_in=None, error_in=None):
        self.fail_in = fail_in
        self.error_in = error_in
        self.calls = []

    def set_progress_hook(self, hook):
        self.progress = hook

    def set_error_hook(self, hook):
        self.error = hook

    def _step(self, name):
        self.calls.append(name)
        self.progress(10, False, False, name)
        self.progress(10, False, False, name)
        if self.fail_in == name:
            raise Exception(f"{name} broke")
        if self.error_in == name:
            self.error(message=f"{name} reported an error")

    def start_installation(self):
        self._step("start")

    def finish_installation(self, before_unmount_hook=None):
        self._step("finish")
        if before_unmount_hook:
            before_unmount_hook()


def driver(engine, late_commands=()):
    text = MINIMAL
    if late_commands:
        text += "late_commands:\n" + "".join(f"  - {c}\n" for c in late_commands)
    config = parse_config(text)
    setup = build_setup(config, "/x/a.yaml", disk="/dev/sda", efi=True, is_mint=True)
    return HeadlessDriver(config, setup, engine=engine)


def test_run_success(capsys):
    engine = FakeEngine()
    assert driver(engine).run() == 0
    out = capsys.readouterr().out
    assert engine.calls == ["start", "finish"]
    assert out.count("[ 10%] start") == 1  # repeated progress is printed once
    assert out.rstrip().endswith("Automated installation complete")


@pytest.mark.parametrize("kind", ["fail_in", "error_in"])
def test_start_failure_stops_before_finish(kind, capsys):
    engine = FakeEngine(**{kind: "start"})
    assert driver(engine).run() == 1
    assert engine.calls == ["start"]
    assert capsys.readouterr().out.rstrip().endswith("Automated installation FAILED")


def test_late_commands_run_in_target(monkeypatch):
    ran = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(argv):
        ran.append(argv)
        return Result(3 if argv[-1].startswith("exit") else 0)

    monkeypatch.setattr(auto_installer.subprocess, "run", fake_run)
    # passed to the shell unchanged (the engine's own chroot helper
    # rewrites double quotes, which would break commands like this one)
    assert driver(FakeEngine(), ["echo true", 'echo "true" > /etc/x']).run() == 0
    assert ran == [["chroot", "/target", "/bin/sh", "-c", "echo true"],
                   ["chroot", "/target", "/bin/sh", "-c", 'echo "true" > /etc/x']]

    ran.clear()
    assert driver(FakeEngine(), ["exit 3", "echo true"]).run() == 1
    assert len(ran) == 1  # stops at the first failure


def test_main_check(tmp_path, capsys):
    answer = tmp_path / "a.yaml"
    answer.write_text(MINIMAL)
    assert main(["--config", str(answer), "--check"]) == 0
    assert "OK" in capsys.readouterr().out
    answer.write_text(MINIMAL.replace("version: 1", "version: 9"))
    assert main(["--config", str(answer), "--check"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_engine_reads_only_setup_attributes_that_exist():
    # The tests replace installer.py with a stub, so check the real engine's
    # source: every self.setup.X it reads must be a Setup class attribute
    # (the GUI sets some, like oem_config, only on its own instance).
    import ast
    import re
    from pathlib import Path

    source = (Path(auto_installer.__file__).parent / "installer.py").read_text()
    setup_class = next(node for node in ast.parse(source).body
                       if isinstance(node, ast.ClassDef) and node.name == "Setup")
    defined = {target.id for node in setup_class.body if isinstance(node, ast.Assign)
               for target in node.targets}
    defined |= {node.name for node in setup_class.body if isinstance(node, ast.FunctionDef)}
    engine = source[:source.index("class Setup")]
    read = set(re.findall(r"self\.setup\.([A-Za-z_][A-Za-z0-9_]*)", engine))
    assert read - defined == set()
