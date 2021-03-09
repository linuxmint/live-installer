#!/usr/bin/python3
import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gtk
from frontend.welcome import welcome
from frontend.gtk_interface import InstallerWindow
import sys
import os
import gettext
import config

gettext.install("live-installer", "/usr/share/locale")

sys.path.insert(1, '/usr/lib/live-installer')
if (os.path.isdir("/usr/lib/live-installer")):
    os.chdir("/usr/lib/live-installer")

if config.main["gtk_theme"] != "default":
    os.environ['GTK_THEME'] = config.main["gtk_theme"]

# Force show mouse cursor & fix background
os.system("xsetroot -cursor_name left_ptr")
os.system("xsetroot -solid black")


# main entry
if __name__ == "__main__":
    if ("--welcome" in sys.argv):
        if config.main["welcome_screen"]:
            win = welcome()
        else:
            print("Welcome screen disabled by config.")
            exit(0)
    else:
        win = InstallerWindow()
        if ("--fullscreen" in sys.argv):
            win.fullscreen()
    Gtk.main()
