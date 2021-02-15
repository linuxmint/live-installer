import yaml
import os
import sys
import subprocess
from glob import glob

sys.path.insert(1, '/usr/lib/live-installer')
if (os.path.isdir("/usr/lib/live-installer")):
    os.chdir("/usr/lib/live-installer")

def load_config(config_path):
    if os.path.isfile(config_path):
        file = open(config_path, "r")
    else:
        exit("{} doesn't exists. Please create config file!".format(config_path))

    try:
        contents = yaml.load(file, Loader=yaml.FullLoader)
    except:
        contents = yaml.load(file)
    return contents


# Define subconfigs
main = load_config("configs/config.yaml")
distro=None
pm=None
initramfs=None

if(main["distribution"] == "auto"):
    if os.path.exists("/etc/debian_version"):
        distro = load_config("configs/distribution/debian.yaml")
    elif os.path.exists("/var/lib/pacman"):
        distro = load_config("configs/distribution/arch.yaml")
else:
    distro = load_config("configs/distribution/{}.yaml".format(main["distribution"]))

if(main["initramfs_system"] == "auto"):
    if os.path.exists("/etc/debian_version"):
        initramfs = load_config("configs/initramfs_systems/initramfs_tools.yaml")
    elif os.path.exists("/var/lib/pacman"):
        initramfs = load_config("configs/initramfs_systems/mkinitcpio.yaml")
else:
    distro = load_config("configs/initramfs_systems/{}.yaml".format(main["initramfs_system"]))
# Package Manager
for package_manager in glob("configs/package_managers/*"):
        pm_contents = load_config(package_manager)   

        if os.path.exists(pm_contents["check_this_dir"]):
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
            kernel_version= subprocess.getoutput("uname -r")
            command = command.replace('{kernel_version}', kernel_version)

            commands.append(command)
        else:
            commands.append(command)

    return commands

