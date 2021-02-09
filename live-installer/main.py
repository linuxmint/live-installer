#!/usr/bin/python3
import sys
import os
import subprocess
import gettext
import config

gettext.install("live-installer", "/usr/share/locale")

sys.path.insert(1, '/usr/lib/live-installer')
if (os.path.isdir("/usr/lib/live-installer")):
    os.chdir("/usr/lib/live-installer")

if config.main["gtk_theme"] != "default":
	os.environ['GTK_THEME'] = config.main["gtk_theme"]
	
from frontend.gtk_interface import InstallerWindow

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

# main entry
if __name__ == "__main__":

	win = InstallerWindow()
	if ("--fullscreen" in sys.argv):
		win.fullscreen()
	Gtk.main()
