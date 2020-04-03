#!/usr/bin/python -OO

import argparse
import sys
import commands
import gettext

gettext.install("live-installer", "/usr/share/locale")

sys.path.insert(1, '/usr/lib/live-installer')
from frontend.gtk_interface import InstallerWindow

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

# main entry
if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--expert-mode", action="store_true", help="Expert mode")
    parser.add_argument("--no-bootloader", dest="insatll_bootloader", action="store_false", help="Do no install bootloader")
    args = parser.parse_args()
    win = InstallerWindow(args.expert_mode, args.insatll_bootloader)
    Gtk.main()
