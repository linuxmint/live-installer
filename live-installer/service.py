#!/usr/bin/python3

# 17g service
import os
import sys
import config
from utils import err, is_root, run
if not is_root():
    print("You must be root!")
    exit(1)

# Check live mode and single run
if config.get("boot", "normal") != "live":
    exit(0)
if os.path.exists("/run/17g") or "--force" in sys.argv:
    exit(0)
else:
    os.mkdir("/run/17g")

# Write live-installer into /etc/xprofile file
if config.get("welcome_screen", True):
    if os.path.exists("/etc/xprofile"):
        xprofile = open("/etc/xprofile", "a")
    else:
        xprofile = open("/etc/xprofile", "w")
    xprofile.write("live-installer --welcome")
    xprofile.close()

# live functions
# Ignore this function with debian (debian uses live-config package)
if config.get("enable_live", True) and (0 != os.system("which live-config &>/dev/null")):
    if config.get("live_user", "user"):
        os.system("useradd -m \"{}\" -s \"{}\"".format(config.get("live_user",
                                                                  "user"), config.get("using_shell", "/bin/bash")))
        if config.get("live_password", "empty"):
            if config.get("live_password", "empty") == "empty":
                os.system("passwd -d {}".format(config.get("live_user", "user")))
            elif os.system("which chpasswd &>/dev/null") == 0:
                fp = open("/tmp/.passwd", "w")
                fp.write("{}:{}\n".format(
                    config.get("live_user", "user"), config.get("live_password", "live")))
                fp.close()
                os.system("cat /tmp/.passwd | chpasswd ; rm -f /tmp/.passwd")
            else:
                os.system("echo -e \"{0}\\n{0}\\n\" | passwd {1}".format(
                    config.get("live_password", "live"), config.get("live_user", "user")))
            for i in config.get("additional_user_groups", (["audio", "video", "netdev"])):
                os.system("usermod -aG \"{}\" \"{}\"".format(i,
                                                             config.get("live_user", "user")))

# call custom live commands
if config.get("custom_scripts", True):
    if os.path.isdir("/usr/lib/live-scripts"):
        for i in os.listdir("/usr/lib/live-scripts"):
            os.system("/usr/lib/live-scripts/{}".format(i))
