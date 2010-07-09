#!/usr/bin/env python
import sys
import commands
sys.path.append('/usr/lib/live-installer')
from frontend.gtk_interface import InstallerWindow

try:
	import pygtk
	pygtk.require("2.0")
	import gtk	
except Exception, detail:
	print detail
	
# main entry
if __name__ == "__main__":
	if("install" in commands.getoutput("cat /proc/cmdline")):
		win = InstallerWindow(fullscreen=True)
	else:
		win = InstallerWindow(fullscreen=False)
	gtk.main()
