#!/usr/bin/env python
import os
import sys
import commands
import subprocess

# This allows the installer to run without the need for a desktop,
# i.e. for those who do not wish to run the LiveCD or are restrained
# by hardware limitations etc.
# Or.. whatever. Some people need it.

class StandaloneDM:
	
	def __init__(self):
		''' Init the StandaloneDM '''
		DISPLAY=":0" # represents which X..
		wCMD = None
		xCMD = "X -br :0"
		sCMD = "gnome-settings-daemon"
		if(os.path.exists("/usr/bin/metacity")):
			# assume gnome desktop.
			wCMD = "/usr/bin/metacity --sm-disable --display=%s &" % DISPLAY

		xpid = self.pid_open(xCMD)
		wpid = self.pid_open(wCMD)
		spid = self.pid_open(sCMD, wait=True)
		
		ppid = self.pid_open("/usr/lib/live-installer/main.py", wait=True)
		
		#os.kill(wpid)
		#os.kill(xpid)
		#os.kill(spid)
		os.system("pkill metacity")
		os.system("pkill gnome-settings-daemon")
		os.system("pkill X")
		sys.exit(0)
		
	def pid_open(self, proc, wait=False):
		''' open a process and return its pid '''
		p = subprocess.Popen(proc, shell=True)
		if(wait):
			p.wait()
		return p.pid
		
# main entry point
if __name__ == "__main__":
	if "install" in commands.getoutput("cat /proc/cmdline"):
		dm = StandaloneDM()
