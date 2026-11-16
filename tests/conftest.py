"""Run the automated-installer tests outside a live session.

The engine modules (installer, partitioning) need GTK, pyparted and the
live system, so the tests replace them with small stand-ins. Everything
under test here is plain Python.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "live-installer"))


class Setup:
    """Stand-in for installer.Setup: the attributes build_setup() sets."""


installer = types.ModuleType("installer")
installer.Setup = Setup
sys.modules["installer"] = installer

partitioning = types.ModuleType("partitioning")
partitioning.is_efi_supported = lambda: True
partitioning.get_mounted_partitions_on_target_disk = lambda disk: []
partitioning.unmount_partitions = lambda paths: []
sys.modules["partitioning"] = partitioning
