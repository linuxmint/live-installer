"""Test scaffolding for importing live-installer modules outside a live session.

The installer modules import GTK (gi), pyparted and the project's own
dialogs module at import time.  None of those are needed by the pure
helpers under test, so lightweight stubs are installed into sys.modules
before the real modules are imported.  This keeps the unit tests runnable
on any machine with nothing but Python and pytest.
"""

import sys
import types
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parents[2] / "usr" / "lib" / "live-installer"


class _StubBase:
    """Stand-in for any GTK class used as a base class (e.g. Gtk.TreeStore)."""

    def __init__(self, *args, **kwargs):
        pass


def _make_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _install_stubs():
    if getattr(sys.modules.get("gi"), "_live_installer_test_stub", False):
        return

    gi = _make_module("gi", require_version=lambda *a, **k: None)
    gi._live_installer_test_stub = True

    gtk = types.SimpleNamespace(TreeStore=_StubBase)
    gdk = types.SimpleNamespace()
    glib = types.SimpleNamespace(idle_add=lambda func, *a, **k: func(*a, **k))
    repository = _make_module("gi.repository", Gtk=gtk, Gdk=gdk, GLib=glib)
    gi.repository = repository

    # pyparted: only the constants referenced by partitioning.py are needed.
    # The exact values are irrelevant to the code under test; they only have
    # to be distinct so they can serve as dict keys / comparison sentinels.
    _make_module(
        "parted",
        PARTITION_NORMAL=0,
        PARTITION_LOGICAL=1,
        PARTITION_EXTENDED=2,
        PARTITION_FREESPACE=4,
        PARTITION_METADATA=8,
        PARTITION_LVM=16,
        PARTITION_SWAP=32,
        PARTITION_RAID=64,
        PARTITION_PALO=128,
        PARTITION_PREP=256,
        PARTITION_HPSERVICE=512,
        PARTITION_MSFT_RESERVED=1024,
    )

    _make_module("dialogs", QuestionDialog=lambda *a, **k: True)


_install_stubs()

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


# partitioning.py reads /usr/share/live-installer/disk-partitions.html at
# import time.  Redirect reads of the installed resource path to the copy
# shipped in the repo, but only for the duration of the initial import.
RESOURCE_PREFIX = "/usr/share/live-installer/"
REPO_RESOURCE_DIR = Path(__file__).resolve().parents[2] / "usr" / "share" / "live-installer"


def _import_with_repo_resources():
    if "partitioning" in sys.modules:
        return
    import builtins

    real_open = builtins.open

    def redirecting_open(file, *args, **kwargs):
        if isinstance(file, str) and file.startswith(RESOURCE_PREFIX):
            file = str(REPO_RESOURCE_DIR / file[len(RESOURCE_PREFIX):])
        return real_open(file, *args, **kwargs)

    builtins.open = redirecting_open
    try:
        import partitioning  # noqa: F401
    finally:
        builtins.open = real_open


_import_with_repo_resources()
