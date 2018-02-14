#!/usr/bin/python -OO

import sys
import commands
import gettext

gettext.install("live-installer", "/usr/share/linuxmint/locale")

sys.path.insert(1, '/usr/lib/live-installer')
from frontend.gtk_interface import InstallerWindow

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

# main entry
if __name__ == "__main__":
	if("install" in commands.getoutput("cat /proc/cmdline")):
		win = InstallerWindow(fullscreen=True)
	else:
		win = InstallerWindow(fullscreen=False)
	Gtk.main()
