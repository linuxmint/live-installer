"""Make the installer modules importable for the real-NFS tests.

Unlike the unit conftest, no GTK/parted stubs are needed: auto_installer (the
only module these tests touch) imports cleanly on its own.
"""

import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parents[2] / "usr" / "lib" / "live-installer"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))
