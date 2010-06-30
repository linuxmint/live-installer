#!/usr/bin/env python

try:
	import pygtk
	pygtk.require("2.0")
	import gtk
	from frontend.gtk_interface import InstallerWindow
except Exception, detail:
	print detail
	
# main entry
if __name__ == "__main__":
	win = InstallerWindow()
	gtk.main()
