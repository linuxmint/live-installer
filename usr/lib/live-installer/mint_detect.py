"""Single source of truth for "are we on Mint vs LMDE/Debian".

Shared by the GTK front-end (main.py) and the headless driver
(auto_installer.py) so this detection lives in exactly one place. The
headless side must not import the GTK-coupled main.py, hence a tiny module
of its own rather than a constant in main.py.
"""

import os


def is_mint():
    """True on Linux Mint (Ubuntu-based), False on LMDE/Debian."""
    try:
        import distro
        like = distro.like()
    except ImportError:
        like = ""
    return (
        os.path.exists("/usr/share/doc/ubuntu-system-adjustments/copyright")
        or "ubuntu" in like
    )
