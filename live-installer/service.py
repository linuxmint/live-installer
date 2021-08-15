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

# live functions
# Ignore this function with debian (debian uses live-config package)
if config.get("enable_live", True) and (
        0 != os.system("which live-config &>/dev/null")):
    if config.get("live_user", "user"):
        os.system("useradd -m \"{}\" -s \"{}\"".format(config.get("live_user",
                                                                  "user"), config.get("using_shell", "/bin/bash")))
        if len(config.get("live_password", "")) > 0:
            if os.system("which chpasswd &>/dev/null") == 0:
                fp = open("/tmp/.passwd", "w")
                fp.write("{}:{}\n".format(
                    config.get("live_user", "user"), config.get("live_password", "live")))
                fp.close()
                os.system("cat /tmp/.passwd | chpasswd ; rm -f /tmp/.passwd")
            else:
                os.system("echo -e \"{0}\\n{0}\\n\" | passwd {1}".format(
                    config.get("live_password", "live"), config.get("live_user", "user")))
        else:
            os.system("passwd -d {}".format(config.get("live_user", "user")))
            
        for i in config.get("additional_user_groups", (["audio", "video", "netdev"])):
                os.system("usermod -aG \"{}\" \"{}\"".format(i, config.get("live_user", "user")))


# call custom live commands
if config.get("custom_scripts", True):
    if os.path.isdir("/lib/live-scripts"):
        for i in os.listdir("/lib/live-scripts"):
            os.system("/lib/live-scripts/{}".format(i))
