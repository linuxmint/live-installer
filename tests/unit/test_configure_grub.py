"""Unit tests for the /etc/default/grub editor."""

import configure_grub_default as cg

DEFAULT = '''\
# comment
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_CMDLINE_LINUX=""
#GRUB_TERMINAL=console
'''


def test_append_cmdline_token():
    out = cg.apply(DEFAULT, ["console=ttyS0,115200n8"], set(), [])
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash console=ttyS0,115200n8"' in out


def test_remove_cmdline_tokens():
    out = cg.apply(DEFAULT, [], {"quiet", "splash"}, [])
    assert 'GRUB_CMDLINE_LINUX_DEFAULT=""' in out


def test_remove_and_append_together():
    out = cg.apply(DEFAULT, ["console=tty0", "console=ttyS0,115200n8"],
                   {"quiet", "splash"}, [])
    assert ('GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"'
            in out)


def test_append_is_idempotent():
    once = cg.apply(DEFAULT, ["console=tty0"], set(), [])
    twice = cg.apply(once, ["console=tty0"], set(), [])
    assert once == twice
    assert twice.count("console=tty0") == 1


def test_set_uncomments_and_replaces():
    out = cg.apply(DEFAULT, [], set(), [("GRUB_TERMINAL", "console serial")])
    assert 'GRUB_TERMINAL="console serial"' in out
    assert "#GRUB_TERMINAL=console" not in out


def test_set_appends_when_absent():
    out = cg.apply(DEFAULT, [], set(),
                   [("GRUB_SERIAL_COMMAND", "serial --unit=0 --speed=115200")])
    assert 'GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"' in out


def test_set_replaces_existing_active_key():
    out = cg.apply(DEFAULT, [], set(), [("GRUB_TIMEOUT", "2")])
    assert 'GRUB_TIMEOUT="2"' in out
    assert "GRUB_TIMEOUT=5" not in out


def test_cmdline_key_added_when_missing():
    text = "GRUB_DEFAULT=0\n"
    out = cg.apply(text, ["console=ttyS0"], set(), [])
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="console=ttyS0"' in out


def test_full_serial_provisioning_shape():
    # mirrors what the driver passes for serial_console: ttyS0,115200
    out = cg.apply(
        DEFAULT,
        ["console=tty0", "console=ttyS0,115200n8"],
        {"quiet", "splash"},
        [("GRUB_TERMINAL", "console serial"),
         ("GRUB_SERIAL_COMMAND", "serial --unit=0 --speed=115200")],
    )
    assert ('GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"'
            in out)
    assert 'GRUB_TERMINAL="console serial"' in out
    assert 'GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"' in out
    assert "quiet" not in out.split("GRUB_CMDLINE_LINUX_DEFAULT")[1].split("\n")[0]
