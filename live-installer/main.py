#!/usr/bin/python3
import sys
import traceback
from utils import *
from frontend import *
from frontend.dialogs import ErrorDialog

gettext.install("live-installer", "/usr/share/locale")
sys.path.insert(1, '/lib/live-installer')
if (os.path.isdir("/lib/live-installer")):
    os.chdir("/lib/live-installer")

if config.get("gtk_theme", "default") != "default":
    os.environ['GTK_THEME'] = config.get("gtk_theme")

# Force show mouse cursor & fix background
os.system("xsetroot -cursor_name left_ptr &>/dev/null")
os.system("xsetroot -solid black &>/dev/null")

VERSION = "4.1"


def exceptdebug(e, v, tb):
    sys.stderr.write("Error: {}\n".format(str(e)))
    sys.stderr.write("Value: {}\n".format(str(v)))
    traceback.print_tb(tb)


sys.excapthook = exceptdebug

# main entry
if __name__ == "__main__":
    if "--test" in sys.argv:
        os.environ["TEST"]="1"
    if "--expert" in sys.argv:
        os.environ["EXPERT_MODE"]="1"
    if not is_root() and "--test" not in sys.argv:
        ErrorDialog(config.get("distro_title", "17g"), _("You must be root!"))
        exit(1)
    elif ("--welcome" in sys.argv):
        if config.get("welcome_screen", True) or "--force" in sys.argv:
            from frontend.welcome import welcome
            win = welcome()
        else:
            err("Welcome screen disabled by config.")
            exit(0)
    elif "--tui" in sys.argv:
        from frontend.tui_interface import InstallerWindow
        term = InstallerWindow()
    elif "--version" in sys.argv:
        sys.stdout.write(VERSION + "\n")
        exit(0)
    else:
        os.system("gtk-update-icon-cache /usr/share/icons/hicolor/ || true")
        from frontend.gtk_interface import InstallerWindow
        win = InstallerWindow("--fullscreen" in sys.argv)
    Gtk.main()
