#!/usr/bin/python -OO

import sys
import commands

def uncaught_excepthook(*args):
    sys.__excepthook__(*args)
    if __debug__:
        try:
            import ipdb as pdb  # try to import the IPython debugger
        except ImportError:
            import pdb
        print '\nStarting interactive debug prompt ...'
        pdb.pm()

sys.excepthook = uncaught_excepthook

sys.path.insert(1, '/usr/lib/live-installer')
from frontend.gtk_interface import InstallerWindow

import pygtk
pygtk.require("2.0")
import gtk

# main entry
if __name__ == "__main__":
	if("install" in commands.getoutput("cat /proc/cmdline")):
		win = InstallerWindow(fullscreen=True)
	else:
		win = InstallerWindow(fullscreen=False)
	gtk.main()
