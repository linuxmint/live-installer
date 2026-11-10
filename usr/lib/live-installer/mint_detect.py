#!/usr/bin/python3
"""Linux Mint (Ubuntu base) or LMDE (Debian base)?

Shared by the GUI (main.py) and the automated installer, which cannot
import the GTK front end.
"""

import os

import distro


def is_mint():
    return (os.path.exists("/usr/share/doc/ubuntu-system-adjustments/copyright")
            or "ubuntu" in distro.like())
