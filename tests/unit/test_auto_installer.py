"""Unit tests for the headless driver: config mapping, answer-file
acquisition, and the post-engine failure-policy machinery."""

import os
import socket
import textwrap
import threading
import types

import pytest

import auto_installer
import schema
from test_engine_commands import RecordingRunner


def _start_tftp_server(content, *, send_error=False, oack_blksize=None,
                       reject_options=False):
    """One-shot localhost TFTP read server for tests. Returns (host, port).

    Modes:
      - default: ignores any options, serves plain RFC 1350 (512-byte blocks).
        This exercises the client's option-negotiation FALLBACK path.
      - oack_blksize=N: negotiates blksize=N via an OACK (RFC 2347/2348).
      - reject_options: answers an options request with ERROR code 8, then
        serves the client's bare retry as plain RFC 1350.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def send_blocks(client, blksize):
        blocks = [content[i:i + blksize] for i in range(0, len(content), blksize)]
        if not blocks or len(blocks[-1]) == blksize:
            blocks.append(b"")  # short/empty block terminates the transfer
        for n, chunk in enumerate(blocks, 1):
            srv.sendto(b"\x00\x03" + n.to_bytes(2, "big") + chunk, client)
            try:
                srv.recvfrom(2048)  # ACK
            except OSError:
                break

    def serve():
        srv.settimeout(5)
        try:
            rrq, client = srv.recvfrom(2048)
        except OSError:
            srv.close()
            return
        if send_error:
            srv.sendto(b"\x00\x05\x00\x01File not found\x00", client)
            srv.close()
            return
        has_options = b"blksize\x00" in rrq
        if reject_options and has_options:
            srv.sendto(b"\x00\x05\x00\x08option rejected\x00", client)
            try:
                _retry, client = srv.recvfrom(2048)  # the bare retry
            except OSError:
                srv.close()
                return
            send_blocks(client, 512)
        elif oack_blksize is not None and has_options:
            srv.sendto(b"\x00\x06blksize\x00%d\x00" % oack_blksize, client)
            try:
                srv.recvfrom(2048)  # ACK of block 0
            except OSError:
                srv.close()
                return
            send_blocks(client, oack_blksize)
        else:
            send_blocks(client, 512)  # ignore options -> RFC 1350 fallback
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return "127.0.0.1", port


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
        ("nfs://host/export/dir/a.yaml", ("host", "/export/dir", "a.yaml", None)),
        ("nfs://host:2049/export/a.yaml", ("host", "/export", "a.yaml", None)),
        ("nfs://10.0.0.1/srv/cfg/host.yaml",
         ("10.0.0.1", "/srv/cfg", "host.yaml", None)),
        # IPv6 literals: urlsplit strips brackets and any :port
        ("nfs://[2001:db8::1]/srv/cfg/host.yaml",
         ("2001:db8::1", "/srv/cfg", "host.yaml", None)),
        ("nfs://[2001:db8::1]:2049/export/a.yaml",
         ("2001:db8::1", "/export", "a.yaml", None)),
        # explicit protocol version
        ("nfs://host/export/a.yaml?vers=3", ("host", "/export", "a.yaml", "3")),
        ("nfs://[2001:db8::1]/export/a.yaml?vers=4.2",
         ("2001:db8::1", "/export", "a.yaml", "4.2")),
    ])
    def test_parse_nfs_url(self, url, expected):
        assert auto_installer._parse_nfs_url(url) == expected

    def test_parse_nfs_url_malformed(self):
        with pytest.raises(schema.ConfigError):
            auto_installer._parse_nfs_url("nfs://hostonly")

    def test_parse_nfs_url_bad_vers(self):
        with pytest.raises(schema.ConfigError) as exc:
            auto_installer._parse_nfs_url("nfs://host/export/a.yaml?vers=5")
        assert "vers" in str(exc.value)

    def test_nfs_mount_passes_vers_option(self, monkeypatch):
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            class R:  # noqa: D401 - minimal CompletedProcess stand-in
                returncode = 0
                stderr = ""
            return R()
        monkeypatch.setattr(auto_installer.subprocess, "run", fake_run)
        monkeypatch.setattr(auto_installer.os, "rmdir", lambda p: None)
        auto_installer._nfs_mount("host", "/export", vers="4.2")
        opts = captured["argv"][captured["argv"].index("-o") + 1]
        assert "vers=4.2" in opts
        # default (no vers) must not inject a version
        auto_installer._nfs_mount("host", "/export")
        opts = captured["argv"][captured["argv"].index("-o") + 1]
        assert "vers=" not in opts

    @pytest.mark.parametrize("host,export,expected", [
        ("host", "/export", "host:/export"),
        ("10.0.0.1", "/srv", "10.0.0.1:/srv"),
        # an IPv6 literal must be bracketed or mount.nfs reads it as host:port
        ("2001:db8::1", "/export", "[2001:db8::1]:/export"),
    ])
    def test_nfs_mount_source_brackets_ipv6(self, host, export, expected):
        assert auto_installer._nfs_mount_source(host, export) == expected

    def test_fetch_nfs_mounts_reads_unmounts(self, tmp_path, monkeypatch):
        # Stand in a real dir for the "mount", assert it is read and unmounted.
        export = tmp_path / "export"
        export.mkdir()
        (export / "a.yaml").write_text("version: 1\n")
        umounted = []
        monkeypatch.setattr(auto_installer, "_nfs_mount",
                            lambda host, d, vers=None: str(export))
        monkeypatch.setattr(auto_installer, "_nfs_umount",
                            lambda mp: umounted.append(mp))
        text = auto_installer.fetch_answer_file(
            "nfs://host/export/a.yaml", insecure=True)
        assert text == "version: 1\n"
        assert umounted == [str(export)]  # always unmounts


class TestTftpFetch:
    @pytest.mark.parametrize("url,expected", [
        ("tftp://host/install.yaml", ("host", 69, "install.yaml")),
        ("tftp://host:6900/sub/dir/a.yaml", ("host", 6900, "sub/dir/a.yaml")),
        ("tftp://[2001:db8::1]/x.yaml", ("2001:db8::1", 69, "x.yaml")),
    ])
    def test_parse(self, url, expected):
        assert auto_installer._parse_tftp_url(url) == expected

    def test_parse_malformed(self):
        with pytest.raises(schema.ConfigError):
            auto_installer._parse_tftp_url("tftp://hostonly")

    @pytest.mark.parametrize("content", [
        b"version: 1\n",          # one short block
        b"x" * 1500,              # three blocks
        b"y" * 512,               # exact block boundary (needs the EOF block)
        b"",                      # empty file
    ])
    def test_fetch_roundtrip(self, content):
        host, port = _start_tftp_server(content)
        text = auto_installer.fetch_answer_file(
            f"tftp://{host}:{port}/anything", insecure=True)
        assert text == content.decode("utf-8")

    def test_refused_without_insecure(self):
        with pytest.raises(schema.ConfigError) as excinfo:
            auto_installer.fetch_answer_file("tftp://host/a.yaml")
        assert "TFTP" in str(excinfo.value)
        assert "--insecure" in str(excinfo.value)

    def test_server_error_packet(self):
        host, port = _start_tftp_server(b"", send_error=True)
        with pytest.raises(schema.ConfigError) as excinfo:
            auto_installer.fetch_answer_file(
                f"tftp://{host}:{port}/missing", insecure=True)
        assert "TFTP error" in str(excinfo.value)

    def test_large_transfer_warns(self, capsys):
        # RFC1350 512-byte blocks are slow for big files; warn past the
        # threshold (review #8). Still completes correctly.
        big = b"a" * (auto_installer._TFTP_WARN_BYTES + 2048)
        host, port = _start_tftp_server(big)
        text = auto_installer.fetch_answer_file(
            f"tftp://{host}:{port}/big", insecure=True)
        assert len(text) == len(big)
        assert "exceeds" in capsys.readouterr().out

    def test_negotiates_blksize_oack(self):
        # Server OACKs blksize=1024 and serves in 1024-byte blocks. The content
        # is chosen so its last block (700 bytes) is > 512: a client that did
        # NOT honour the OACK (still expecting 512) would treat that as a
        # non-terminal block and hang, so a clean roundtrip proves negotiation.
        content = b"z" * (1024 * 2 + 700)
        host, port = _start_tftp_server(content, oack_blksize=1024)
        text = auto_installer.fetch_answer_file(
            f"tftp://{host}:{port}/file", insecure=True)
        assert text == content.decode("utf-8")

    def test_option_rejection_falls_back_to_rfc1350(self):
        # Server answers the options request with ERROR code 8; the client must
        # retry without options and still complete over plain RFC 1350.
        content = b"version: 1\n" + b"k" * 2000
        host, port = _start_tftp_server(content, reject_options=True)
        text = auto_installer.fetch_answer_file(
            f"tftp://{host}:{port}/file", insecure=True)
        assert text == content.decode("utf-8")

    def test_parse_oack(self):
        opts = auto_installer._parse_oack(b"blksize\x001428\x00tsize\x004096\x00")
        assert opts == {"blksize": "1428", "tsize": "4096"}


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

    def test_keyboard_multilayout_and_locales(self):
        config = make_config(extra=textwrap.dedent("""\
            keyboard:
              layout: us
              additional_layouts:
                - {layout: ca, variant: fr}
                - {layout: gr}
              toggle: grp:alt_shift_toggle
            additional_locales: [fr_CA.UTF-8, de_DE.UTF-8]
        """))
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=False, is_mint=False)
        # comma-joined XKB lists, primary first
        assert setup.keyboard_layout == "us,ca,gr"
        assert setup.keyboard_variant == ",fr,"
        assert setup.keyboard_options == "grp:alt_shift_toggle"
        # codeset-stripped, like the primary language
        assert setup.additional_locales == ["fr_CA", "de_DE"]

    def test_keyboard_single_layout_no_variant_string(self):
        config = make_config()  # default keyboard, no extras
        setup = auto_installer.build_setup(
            config, disk="/dev/vda", efi=False, is_mint=False)
        assert setup.keyboard_layout == "us"
        assert setup.keyboard_variant == ""   # all-empty -> empty, not ","
        assert setup.keyboard_options is None

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

    def _luks_config(self, source_line=""):
        return schema.parse_config(textwrap.dedent(f"""\
            version: 1
            locale: en_CA.UTF-8
            timezone: America/Toronto
            users:
              - name: admin
                passwd: "$6$rounds=4096$salt$hash"
            storage:
              layout: lvm-on-luks
              luks:
                {source_line}
              target:
                match:
                  first-non-removable: true
        """)) if source_line else schema.parse_config(textwrap.dedent("""\
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

    def test_prompt_on_first_boot_generates_throwaway_key(self):
        # Default passphrase_source is prompt-on-first-boot.
        setup = auto_installer.build_setup(
            self._luks_config(), disk="/dev/vda", efi=False, is_mint=False)
        assert setup.luks is True
        assert setup.luks_rekey_on_first_boot is True
        # A random, newline-free key, used for both slots.
        assert setup.passphrase1 and "\n" not in setup.passphrase1
        assert setup.passphrase1 == setup.passphrase2
        # Two installs must not share the throwaway key.
        other = auto_installer.build_setup(
            self._luks_config(), disk="/dev/vda", efi=False, is_mint=False)
        assert other.passphrase1 != setup.passphrase1

    def test_tpm2_passphrase_source_still_unimplemented(self):
        config = self._luks_config("passphrase_source: tpm2")
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


class TestEnsureDns:
    def _driver(self):
        driver, _runner, _engine = make_driver(make_config())
        return driver

    def test_repairs_placeholder_from_dhcp_lease(self, tmp_path):
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver dhcp\n")  # the netboot placeholder
        (tmp_path / "net-enp0s3.conf").write_text(
            "DEVICE=enp0s3\nIPV4DNS0=10.0.2.3\nIPV4DNS1=0.0.0.0\n")
        self._driver()._ensure_dns(str(resolv), str(tmp_path / "net-*.conf"))
        assert resolv.read_text() == "nameserver 10.0.2.3\n"  # 0.0.0.0 dropped

    def test_noop_when_already_valid(self, tmp_path):
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver 192.0.2.1\n")
        (tmp_path / "net-x.conf").write_text("IPV4DNS0=10.0.2.3\n")
        self._driver()._ensure_dns(str(resolv), str(tmp_path / "net-*.conf"))
        assert resolv.read_text() == "nameserver 192.0.2.1\n"  # left untouched

    def test_left_alone_when_no_lease_dns(self, tmp_path):
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver dhcp\n")
        self._driver()._ensure_dns(str(resolv), str(tmp_path / "absent-*.conf"))
        assert resolv.read_text() == "nameserver dhcp\n"  # nothing to repair with


class TestApplyNetwork:
    NET = textwrap.dedent("""\
        network:
          version: 2
          ethernets:
            eth0:
              addresses: [192.168.1.10/24, 2001:db8::5/64]
              gateway4: 192.168.1.1
              gateway6: 2001:db8::1
          vlans:
            vlan100:
              id: 100
              link: eth0
              addresses: [10.100.0.5/24]
    """)

    def _run_apply(self, config, tmp_path, monkeypatch):
        import builtins
        driver, runner, _engine = make_driver(config)
        target = tmp_path / "target/etc/NetworkManager/system-connections"
        prefix = "/target/etc/NetworkManager/system-connections"
        real_open = builtins.open
        chmods = {}

        def redir(path, *a, **k):
            if isinstance(path, str) and path.startswith(prefix):
                local = tmp_path / ("target" + path[len("/target"):])
                local.parent.mkdir(parents=True, exist_ok=True)
                return real_open(local, *a, **k)
            return real_open(path, *a, **k)

        monkeypatch.setattr(builtins, "open", redir)
        monkeypatch.setattr(auto_installer.os, "chmod",
                            lambda p, m: chmods.__setitem__(os.path.basename(p), m))
        driver._apply_network()
        return target, chmods, runner

    def test_writes_keyfiles_0600(self, tmp_path, monkeypatch):
        config = make_config(extra=self.NET)
        target, chmods, runner = self._run_apply(config, tmp_path, monkeypatch)
        names = sorted(p.name for p in target.iterdir())
        assert names == ["eth0.nmconnection", "vlan100.nmconnection"]
        # Keyfiles must be 0600 or NetworkManager ignores them.
        assert chmods == {"eth0.nmconnection": 0o600,
                          "vlan100.nmconnection": 0o600}
        eth = (target / "eth0.nmconnection").read_text()
        assert "address1=192.168.1.10/24" in eth
        assert "address1=2001:db8::5/64" in eth
        assert any("mkdir -p" in c and "system-connections" in c
                   for c in runner.commands)

    def test_no_network_section_writes_nothing(self, tmp_path, monkeypatch):
        config = make_config()  # no network:
        target, chmods, _runner = self._run_apply(config, tmp_path, monkeypatch)
        assert not target.exists()
        assert chmods == {}

    WIFI = textwrap.dedent("""\
        network:
          version: 2
          wifis:
            wlan0:
              dhcp4: true
              access-points:
                "HomeNet": {password: "hunter2pass"}
    """)

    def test_wifi_psk_keyfile_is_0600(self, tmp_path, monkeypatch):
        # The wifi keyfile holds the PSK in cleartext, so 0600 is a security
        # requirement, not just an NM nicety.
        config = make_config(extra=self.WIFI)
        target, chmods, _runner = self._run_apply(config, tmp_path, monkeypatch)
        assert chmods == {"wlan0.nmconnection": 0o600}
        body = (target / "wlan0.nmconnection").read_text()
        assert "psk=hunter2pass" in body


class TestLuksFirstBootRekey:
    def _setup(self, key="ThrowAwayKey_no_newline_1234567890"):
        return types.SimpleNamespace(
            passphrase1=key, luks_rekey_on_first_boot=True)

    def test_noop_when_flag_unset(self, tmp_path):
        driver, runner, _e = make_driver(make_config())
        driver._setup_luks_first_boot_rekey(
            types.SimpleNamespace(luks_rekey_on_first_boot=False),
            target=str(tmp_path))
        assert list(tmp_path.iterdir()) == []
        assert runner.commands == []

    def test_writes_keyfile_crypttab_hook_and_service(self, tmp_path):
        driver, runner, _e = make_driver(make_config())
        key = "ThrowAwayKey_no_newline_1234567890"
        driver._setup_luks_first_boot_rekey(self._setup(key), target=str(tmp_path))

        # 1. Keyfile holds the LUKS key EXACTLY (no trailing newline), 0600.
        keyfile = tmp_path / "etc/cryptsetup-keys.d/cryptroot.key"
        assert keyfile.read_bytes() == key.encode()  # byte-exact, no newline
        assert (keyfile.stat().st_mode & 0o777) == 0o600
        assert (keyfile.parent.stat().st_mode & 0o777) == 0o700

        # 2. initramfs hook copies the keyfile in and locks down the image.
        hook = (tmp_path / "etc/cryptsetup-initramfs/conf-hook").read_text()
        assert 'KEYFILE_PATTERN="/etc/cryptsetup-keys.d/*.key"' in hook
        conf = (tmp_path / "etc/initramfs-tools/initramfs.conf").read_text()
        assert "UMASK=0077" in conf

        # 3. Rekey oneshot installed (executable) and enabled.
        script = tmp_path / "usr/local/sbin/li-luks-rekey"
        assert script.exists() and (script.stat().st_mode & 0o111)
        body = script.read_text()
        assert "luksAddKey" in body and "luksRemoveKey" in body
        assert "systemctl reboot" in body
        # new key added with no trailing newline (matches the boot prompt)
        assert "printf '%s' \"$PASS\" | cryptsetup luksAddKey" in body
        unit = (tmp_path / "etc/systemd/system/li-luks-rekey.service").read_text()
        assert "ExecStart=/usr/local/sbin/li-luks-rekey" in unit
        assert any("systemctl enable li-luks-rekey.service" in c
                   for c in runner.commands)


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


class TestApplyProxy:
    def test_writes_apt_and_environment(self, tmp_path):
        config = make_config(extra="proxy: http://proxy.corp:3128\n")
        driver, _runner, _e = make_driver(config)
        driver._apply_proxy(target=str(tmp_path))
        apt = (tmp_path / "etc/apt/apt.conf.d/00proxy").read_text()
        assert 'Acquire::http::Proxy "http://proxy.corp:3128";' in apt
        assert 'Acquire::https::Proxy "http://proxy.corp:3128";' in apt
        env = (tmp_path / "etc/environment").read_text()
        assert "http_proxy=http://proxy.corp:3128" in env
        assert "HTTPS_PROXY=http://proxy.corp:3128" in env

    def test_noop_without_proxy(self, tmp_path):
        driver, _runner, _e = make_driver(make_config())
        driver._apply_proxy(target=str(tmp_path))
        assert list(tmp_path.iterdir()) == []
