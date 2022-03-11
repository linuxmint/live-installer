import os
import sys
import subprocess
import yaml
from glob import glob
from logger import log, err, inf, set_logfile

sys.path.insert(1, '/lib/live-installer')
if (os.path.isdir("/lib/live-installer")):
    os.chdir("/lib/live-installer")


def load_config(config_path):
    """read config file"""
    if os.path.isfile(config_path):
        file = open(config_path, "r")
        content = file.read()
        inf("#Reading yaml file:" + config_path)
        log("Loading: "+config_path)
    else:
        err("{} doesn't exists. Please create config file!".format(config_path))
        return {}

    try:
        return yaml.load(content, Loader=yaml.FullLoader) or {}
    except BaseException:
        return yaml.load(content) or {}


# Define subconfigs
main = load_config("configs/config.yaml")
if not main:
    main = []
distro = None
pm = None
display_manager = None
initramfs = None
live = load_config("configs/live.yaml")
kernel_vars = {}
os.environ["DEBIAN_FRONTEND"]="noninteractive"
if not live:
    live = []


def get(key, default=""):
    try:
        if key in kernel_vars:
            return kernel_vars[key]
        if key in live:
            return live[key]
        if key in main:
            return main[key]
    except BaseException:
        return default
    return default

set_logfile(get("log_file","/var/log/17g-installer.log"))

# Distribution
if(get("distribution", "auto") == "auto"):
    for distro_system in glob("configs/distribution/*"):
        distro = load_config(distro_system)
        if not distro:
            err("Failed to load: " + distro_system)
        elif "check_this_dir" in distro and os.path.exists(distro["check_this_dir"]):
            break
else:
    distro = load_config(
        "configs/distribution/{}.yaml".format(main["distribution"]))

# Initramfs system
if(get("initramfs_system", "auto") == "auto"):
    for initramfs_system in glob("configs/initramfs_systems/*"):
        initramfs = load_config(initramfs_system)
        if not initramfs:
            err("Failed to load: " + initramfs_system)
        elif "check_this_dir" in initramfs and os.path.exists(initramfs["check_this_dir"]):
            break
else:
    initramfs = load_config(
        "configs/initramfs_systems/{}.yaml".format(main["initramfs_system"]))


# Package Manager
if(get("package_manager", "auto") == "auto"):
    for package_manager in glob("configs/package_managers/*"):
        pm = load_config(package_manager)
        if not pm:
            err("Failed to load: " + package_manager)
        elif "check_this_dir" in pm and os.path.exists(pm["check_this_dir"]):
            break
else:
    pm = load_config(
        "configs/package_managers/{}.yaml".format(main["package_manager"]))

# Distribution
if(get("display_manager", "auto") == "auto"):
    for display_manager in glob("configs/display_managers/*"):
        display_manager = load_config(display_manager)
        if not display_manager:
            err("Failed to load: " + display_manager)
        elif "check_this_dir" in display_manager and os.path.exists(display_manager["check_this_dir"]):
            break
else:
    display_manager = load_config(
        "configs/display_managers/{}.yaml".format(main["display_manager"]))


def package_manager(process, packages=[]):
    if process == "name":
        exit("You can't use this parameter!")


    if process in pm:
        if "{packages}" not in pm[process]:
            return pm[process]
        elif len(packages) == 0:
            return ":" 
        pkgs = ""

        for p in packages:
            if len(p) > 0 and p[0] != "#":
                pkgs += " " + p

        cmd = (pm[process] + " ").replace("{packages}", pkgs)
        return cmd
    else:
        exit("Process doesn't exists on package manager's config file!")


# Update Initramfs
def update_initramfs():
    commands = []
    for command in initramfs["commands"]:
        log(initramfs)
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
