#!/usr/bin/python3
import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gtk
from frontend.welcome import welcome
from frontend.gtk_interface import InstallerWindow
from dialogs import ErrorDialog
import sys
import os
import gettext
import config
from utils import log, err, is_root, file

gettext.install("live-installer", "/usr/share/locale")

sys.path.insert(1, '/usr/lib/live-installer')
if (os.path.isdir("/usr/lib/live-installer")):
    os.chdir("/usr/lib/live-installer")

if config.get("gtk_theme","default") != "default":
    os.environ['GTK_THEME'] = config.get("gtk_theme")

# Force show mouse cursor & fix background
os.system("xsetroot -cursor_name left_ptr")
os.system("xsetroot -solid black")


# main entry
if __name__ == "__main__":
    if not is_root() and "--test" not in sys.argv:
        ErrorDialog(config.get("distro_title","17g"),_("You must be root!"))
        exit(1)
    if ("--welcome" in sys.argv):
        if config.get("welcome_screen",True):
            win = welcome()
        else:
            err("Welcome screen disabled by config.")
            exit(0)
    else:
        win = InstallerWindow()
        if ("--fullscreen" in sys.argv):
            win.fullscreen()
    Gtk.main()
