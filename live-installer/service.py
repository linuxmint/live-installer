#!/usr/bin/python3

# 17g service
import os
import config
if os.getuid() != 0:
    print("You must be root!")
    exit(1)
# Get variables from cmdline
cmdline = open("/proc/cmdline", "r")
data = cmdline.read().split(" ")
cmdline.close()
kernel_vars = {}
for i in data:
    if "=" in i:
        j = i.split("=")
        kernel_vars[j[0]] = j[1]

# Check live mode and single run
if "boot" not in kernel_vars:
    exit(0)
if kernel_vars["boot"] != "live":
    exit(0)
if os.path.exists("/run/17g"):
    exit(0)
else:
    os.mkdir("/run/17g")

# Write live-installer into /etc/xprofile file
if config.main["welcome_screen"]:
    if os.path.exists("/etc/xprofile"):
        xprofile = open("/etc/xprofile", "a")
    else:
        xprofile = open("/etc/xprofile", "w")
    xprofile.write("live-installer --welcome")
    xprofile.close()

# live functions
# Ignore this function with debian (debian uses live-config package)
if config.live["enable_live"] and (0 != os.system("which live-config &>/dev/null")):
    if "live_user" in config.live:
        os.system("useradd \"{}\"".format(config.live["live_user"]))
        if "live_password" in config.live:
            if os.system("which chpasswd &>/dev/null") == 0:
                fp = open("/tmp/.passwd", "w")
                fp.write("{}:{}\n".format(
                    config.live["live_user"], config.live["live_password"]))
                fp.close()
                os.system("cat /tmp/.passwd | chpasswd ; rm -f /tmp/.passwd")
            else:
                os.system("echo -e \"{0}\\n{0}\\n\" | passwd {1}".format(
                    config.live["live_password"], config.live["live_user"]))
        if "live_groups" in config.live:
            for i in config.live["live_groups"]:
                os.system("usermod -aG \"{}\" \"{}\"".format(i,
                                                             config.live["live_user"]))

# call custom live commands
if config.live["custom_scripts"]:
    if os.path.isdir("/usr/lib/live-scripts"):
        for i in os.listdir("/usr/lib/live-scripts"):
            os.system("/usr/lib/live-scripts/{}".format(i))
