#!/usr/bin/python3
import sys
import os
import subprocess
import gettext

gettext.install("live-installer", "/usr/share/locale")

sys.path.insert(1, '/usr/lib/live-installer')
if (os.path.isdir("/usr/lib/live-installer")):
    os.chdir("/usr/lib/live-installer")
os.environ['GTK_THEME']="Adwaita"

from frontend.gtk_interface import InstallerWindow

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

# main entry
if __name__ == "__main__":

	if ("--expert-mode" in sys.argv):
		win = InstallerWindow(expert_mode=True)
	else:
		win = InstallerWindow()
	if ("--fullscreen" in sys.argv):
		win.fullscreen()
	Gtk.main()
