import os
import sys
import subprocess
import yaml
from glob import glob
from utils import log, err, inf

sys.path.insert(1, '/usr/lib/live-installer')
if (os.path.isdir("/usr/lib/live-installer")):
    os.chdir("/usr/lib/live-installer")


def load_config(config_path):
    """read config file"""
    if os.path.isfile(config_path):
        file = open(config_path, "r")
        content=file.read()
        inf("#Reading yaml file:"+config_path)
        log(content)
    else:
        err("{} doesn't exists. Please create config file!".format(config_path))
        return []

    try:
        return yaml.load(content, Loader=yaml.FullLoader) or {}
    except:
        return yaml.load(content) or {}


# Define subconfigs
main = load_config("configs/config.yaml")
if not main:
    main = []
distro = None
pm = None
initramfs = None
live = load_config("configs/live.yaml")
kernel_vars = {}

if not live:
    live=[]

def get(key,default=""):
    if key in kernel_vars:
        return kernel_vars[key]
    if key in live:
        return live[key]
    if key in main:
        return main[key]
    return default
    
if(get("distribution","auto") == "auto"):
    if os.path.exists("/etc/debian_version"):
        distro = load_config("configs/distribution/debian.yaml")
    elif os.path.exists("/var/lib/pacman"):
        distro = load_config("configs/distribution/arch.yaml")
    elif not os.path.exists("/home") and os.path.exists("/data/user"):
        distro = load_config("configs/distribution/sulin.yaml")
else:
    distro = load_config(
        "configs/distribution/{}.yaml".format(main["distribution"]))

if(get("initramfs_system","auto") == "auto"):
    if os.path.exists("/etc/debian_version"):
        initramfs = load_config(
            "configs/initramfs_systems/initramfs_tools.yaml")
    elif os.path.exists("/var/lib/pacman"):
        initramfs = load_config("configs/initramfs_systems/mkinitcpio.yaml")
    elif not os.path.exists("/home") and os.path.exists("/data/user"):
        initramfs = load_config("configs/initramfs_systems/initrd.yaml")
else:
    distro = load_config(
        "configs/initramfs_systems/{}.yaml".format(main["initramfs_system"]))
# Package Manager
for package_manager in glob("configs/package_managers/*"):
    pm_contents = load_config(package_manager)
    if  not pm_contents:
        err("Failed to load: "+package_manager)
    elif os.path.exists(pm_contents["check_this_dir"]):
        pm = pm_contents
        break


def package_manager(process, packages=[]):
    if process == "name":
        exit("You can't use this parameter!")
    if process in pm:
        pkgs = " ".join(str(p) for p in packages)
        cmd = (pm[process] + " ").replace("{packages}", pkgs)

        return cmd
    else:
        exit("Process doesn't exists on package manager's config file!")


# Update Initramfs
def update_initramfs():
    commands = []
    for command in initramfs["commands"]:
        if "{kernel_version}" in command:
            kernel_version = subprocess.getoutput("uname -r")
            command = command.replace('{kernel_version}', kernel_version)

            commands.append(command)
        else:
            commands.append(command)

    return commands

# Get variables from cmdline
cmdline = open("/proc/cmdline", "r")
data = cmdline.read().split(" ")
cmdline.close()
# Kernel vars
for i in data:
    if "=" in i:
        j = i.split("=")
        kernel_vars[j[0]] = j[1]
