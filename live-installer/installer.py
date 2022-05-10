import os
import subprocess
import time
import gettext
import parted
import frontend.partitioning as partitioning
import config
from utils import run, set_governor, is_cmd
from logger import log, err, inf, set_logfile

gettext.install("live-installer", "/usr/share/locale")

NON_LATIN_KB_LAYOUTS = ['am', 'af', 'ara', 'ben', 'bd', 'bg', 'bn', 'bt', 'by', 'deva', 'et', 'ge', 'gh', 'gn', 'gr',
                        'guj', 'guru', 'id', 'il', 'iku', 'in', 'iq', 'ir', 'kan', 'kg', 'kh', 'kz', 'la', 'lao', 'lk',
                        'ma', 'mk', 'mm', 'mn', 'mv', 'mal', 'my', 'np', 'ori', 'pk', 'ru', 'rs', 'scc', 'sy', 'syr', 
                        'tel', 'th', 'tj', 'tam', 'tz', 'ua', 'uz']


class InstallerEngine:
    ''' This is central to the live installer '''

    def __init__(self, setup):
        self.setup = setup

        # change to performance governor
        set_governor("performance")
        # set umask value
        os.umask(0o022)

        # find the squashfs..
        self.media = config.get("loop_directory", "/dev/loop0")
        self.logfile = config.get("log_file", "/var/log/17g-installer.log")
        set_logfile(self.logfile)

        if(not os.path.exists(self.media)):
            err("Critical Error: Live medium (%s) not found!" % self.media)
            # sys.exit(1)
        inf("Using live medium: " + self.media)
        self.our_total = 0
        self.our_current = 0

    def set_progress_hook(self, progresshook):
        ''' Set a callback to be called on progress updates '''
        ''' i.e. def my_callback(progress_type, message, current_progress, total) '''
        ''' Where progress_type is any off PROGRESS_START, PROGRESS_UPDATE, PROGRESS_COMPLETE, PROGRESS_ERROR '''
        self.progresshook = progresshook
        self.update_progress()

    def set_error_hook(self, errorhook):
        ''' Set a callback to be called on errors '''
        self.error_message = errorhook

    def update_progress(self, message="", pulse=False, done=False, nolog=False):
        if len(message.strip())==0:
            return
        if done:
            self.our_total = 1
            self.our_current = 1
        if self.progresshook:
            self.progresshook(self.our_current,
                              self.our_total, pulse, done, message, nolog)

    def start_installation(self):

        # mount the media location.
        log(" --> Installation started")
        if(not os.path.exists("/target")):
            os.mkdir("/target")
        if(not os.path.exists("/source")):
            os.mkdir("/source")

        # Custom commands
        self.do_hook_commands("pre_install_hook")

        self.do_unmount("/source")
        self.do_unmount("/target/dev/shm")
        self.do_unmount("/target/dev/pts")
        self.do_unmount("/target/dev/")
        self.do_unmount("/target/sys/")
        self.do_unmount("/target/proc/")
        self.do_unmount("/target/run/")
        self.run("vgchange -an",vital=False)

        self.mount_source()

        if self.setup.expert_mode:
            log("  --> Expert mode detected")
        elif self.setup.automated:
            self.create_partitions()
        else:
            self.format_partitions()
            self.mount_partitions()
        if os.path.isdir("/lib/live-installer"):
            os.chdir("/lib/live-installer")
                    
        # Custom commands
        self.do_hook_commands("pre_rsync_hook")


        # Transfer the files
        SOURCE = "/source/"
        DEST = "/target/"

        self.our_current = 0
        # (Valid) assumption: num-of-files-to-copy ~= num-of-used-inodes-on-/
        self.our_total = int(subprocess.getoutput(
            "df --inodes /{src} | awk 'END{{ print $3 }}'".format(src=SOURCE.strip('/'))))
        log(" --> Copying {} files".format(self.our_total))

        if config.get("netinstall", False):
            self.run_and_update(config.package_manager("create_rootfs"))
            pkgs = open("branding/netinstall_packages.txt").read().split("\n")

        else:
            if config.get("use_rsync", True) and is_cmd("rsync"):
                EXCLUDE_DIRS = "dev/* proc/* sys/* tmp/* run/* mnt/* media/* lost+found source target".split()

                # Add optional entries to EXCLUDE_DIRS
                for dirvar in config.get(
                        "exclude_dirs", ["home/*", "data/user/*"]):
                    EXCLUDE_DIRS.append(dirvar)

                rsync_filter = ' '.join(
                    '--exclude=' + SOURCE + d for d in EXCLUDE_DIRS)
                rsync = subprocess.Popen("rsync --verbose --archive --no-D --acls "
                                         "--hard-links --xattrs {rsync_filter} "
                                         "{src}* {dst}".format(src=SOURCE,
                                                               dst=DEST, rsync_filter=rsync_filter),
                                         shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                while rsync.poll() is None:
                    line = str(rsync.stdout.readline().decode(
                        "utf-8").replace("\n", ""))
                    if not line:  # still copying the previous file, just wait
                        time.sleep(0.1)
                    else:
                        self.our_current = min(
                            self.our_current + 1, self.our_total)
                        self.update_progress(_("Copying /%s") % line,nolog=True)
                log(_("rsync exited with return code: %s") % str(rsync.poll()))
            elif config.get("use_unsquashfs", True) and is_cmd("unsquashfs"):
                pwd = os.getcwd()
                os.chdir("/target")
                self.update_progress(_("Extracting rootfs."), pulse=True)
                self.run("unsquashfs /dev/loop0")
                self.run("mv /target/squashfs-root/* /target")
                self.run("rm -rf /target/squashfs-root")
                os.chdir(pwd)
            else:
                cp = subprocess.Popen("cp -prvf {src}* {dst}".format(src=SOURCE, dst=DEST),
                                      shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                while cp.poll() is None:
                    line = str(cp.stdout.readline().decode(
                        "utf-8")).split("'")[1]
                    if not line:  # still copying the previous file, just wait
                        time.sleep(0.1)
                    else:
                        self.our_current = min(
                            self.our_current + 1, self.our_total)
                        self.update_progress(_("Copying /%s") % line)
               

        # Custom commands
        self.do_hook_commands("post_rsync_hook")
        
        # Steps:
        self.our_total = 12
        self.our_current = 0
        # chroot
        log(" --> Chrooting")
        self.update_progress(_("Entering the system ..."))
        self.run("mount --bind /dev/ /target/dev/")
        self.run("mount --bind /dev/shm /target/dev/shm")
        self.run("mount --bind /dev/pts /target/dev/pts")
        self.run("mount --bind /sys/ /target/sys/")
        self.run("mount --bind /proc/ /target/proc/")
        self.run("mount --bind /run/ /target/run/")
        if os.path.exists("/sys/firmware/efi"):
            self.run("mount --bind /sys/firmware/efi/efivars /target/sys/firmware/efi/efivars")
        self.run("rm -rf /target/etc/resolv.conf",False)
        self.run("cp -f /etc/resolv.conf /target/etc/resolv.conf",False)

        if config.get("netinstall", False):
            cmd = config.package_manager("install_package", pkgs)
            self.run_and_update("chroot /target {}".format(cmd))

        kernelversion = subprocess.getoutput("uname -r")
        if os.path.exists("/lib/modules/{0}/vmlinuz".format(kernelversion)):
            self.run(
                "cp /lib/modules/{0}/vmlinuz /target/boot/vmlinuz-{0}".format(kernelversion))

        # add new user
        self.our_current += 1
        log(" --> Adding new user")
        try:
            for cmd in config.distro["run_before_user_creation"]:
                self.run(cmd)
        except BaseException:
            err("This action not supported for your distribution.")
        if not config.get("skip_user", False):
            self.update_progress(_("Adding new user to the system"))
            # TODO: support encryption

            self.run('chroot||useradd -m -s {shell} -c \"{realname}\" {username}'.format(
                shell=config.get("using_shell", "/bin/bash"), realname=self.setup.real_name,
                username=self.setup.username))

            # Add user to additional groups
            for group in config.get("additional_user_groups", [
                                    "audio", "video", "netdev"]):
                self.run("chroot||usermod -aG {} {}".format(group, self.setup.username), False)

            if is_cmd("openssl") and config.get("use_usermod", True):
                fp = open("/target/tmp/.passwd", "w")
                fp.write(self.setup.password1+"\n")
                fp.close()
                self.run("chroot||usermod -p $(openssl passwd -6 -in /target/tmp/.passwd) {0}".format(self.setup.username))
                if config.get("set_root_password", True):
                    self.run("chroot||usermod -p $(openssl passwd -6 -in /target/tmp/.passwd) root")
            elif is_cmd("chpasswd") and config.get("use_chpasswd", True):
                fp = open("/target/tmp/.passwd", "w")
                fp.write(self.setup.username + ":" + self.setup.password1 + "\n")
                if config.get("set_root_password", True):
                    fp.write("root:" + self.setup.password1 + "\n")
                fp.close()
                self.run("chroot||cat /tmp/.passwd | chpasswd")
                self.run("chroot||rm -f /tmp/.passwd")
            else:
                fp = open("/target/tmp/.passwd", "w")
                fp.write(self.setup.password1+"\n"+self.setup.password2+"\n")
                fp.close()
                self.run("chroot||cat /tmp/.passwd | passwd {0}".format(self.setup.username))
                if config.get("set_root_password", True):
                    self.run("chroot||cat /tmp/.passwd | passwd")

            if config.get("keep_userdata", True):
                live_home = subprocess.getoutput("grep x:1000 /etc/passwd").split(":")[5]
                target_home = subprocess.getoutput("grep x:1000 /target/etc/passwd").split(":")[5]
                run("cp -prf /{}/* /target/{}/".format(live_home, target_home))
                run("find /target/{}/ -type f -iname live-installer.desktop | xargs rm -f".format(target_home))

            self.our_current += 1
            # Set autologin for user if they so elected
            if self.setup.autologin:
                # Auto Login Groups
                for i in config.display_manager["set_autologin"]:
                    self.run(i.replace("{user}", self.setup.username))

        # /etc/fstab, mtab and crypttab
        self.our_current += 1
        self.update_progress(
            _("Writing filesystem mount information to /etc/fstab"))
        self.write_fstab()

    def mount_source(self):
        # Mount the installation media
        log(" --> Mounting partitions")
        self.update_progress(_("Mounting %(partition)s on %(mountpoint)s") % {
                             'partition': self.media, 'mountpoint': "/source/"})
        log(" ------ Mounting %s on %s" % (self.media, "/source/"))
        self.do_mount(self.media, "/source/")

    def create_partitions(self):
        # Create partitions on the selected disk (automated installation)
        partition_prefix = ""
        self.max_part_num = 0
        if self.setup.disk.startswith("/dev/nvme"):
            partition_prefix = "p"
        def get_next():
            self.max_part_num +=1
            return self.setup.disk + partition_prefix + str(self.max_part_num)

        self.auto_boot_partition = None
        self.auto_efi_partition = None
        self.auto_swap_partition = None
        if self.setup.gptonefi:
            self.auto_efi_partition = get_next()
        if self.setup.luks:
            self.auto_boot_partition = get_next()
        elif self.setup.lvm:
            self.auto_swap_partition = None
        else:
            if config.get("use_swap",False) and self.setup.create_swap:
                self.auto_swap_partition = get_next()
        self.auto_root_partition = get_next()

        log("EFI:" + str(self.auto_efi_partition))
        log("BOOT:" + str(self.auto_boot_partition))
        log("Root:" + str(self.auto_root_partition))
        self.auto_root_physical_partition = self.auto_root_partition

        # Wipe HDD
        if self.setup.badblocks:
            self.update_progress(_(
                "Filling %s with random data (please be patient, this can take hours...)") % self.setup.disk)
            log(" --> Filling %s with random data" % self.setup.disk)
            self.run("badblocks -c 10240 -s -w -t random -v %s" %
                self.setup.disk)

        # Create partitions
        self.update_progress(_("Creating partitions on %s") % self.setup.disk)
        log(" --> Creating partitions on %s" % self.setup.disk)
        disk_device = parted.getDevice(self.setup.disk)
        # replae this with changeable function
        partitioning.full_disk_format(disk_device, create_boot=(
            self.auto_boot_partition is not None), create_swap=(self.auto_swap_partition is not None))

        # Encrypt root partition
        if self.setup.luks:
            log(" --> Encrypting root partition %s" %
                self.auto_root_partition)
            with open("/tmp/.lukspass","w") as f:
                f.write("{0}\n{0}\n".format(self.setup.passphrase1))
                f.flush()
            self.run("cat /tmp/.lukspass | cryptsetup --force-password luksFormat {}".format(self.auto_root_partition))
            log(" --> Opening root partition %s" % self.auto_root_partition)
            self.run("cat /tmp/.lukspass | cryptsetup luksOpen {} {}".format(self.auto_root_partition,config.get("distro_codename","linux")))
            self.auto_root_partition = "/dev/mapper/{}".format(config.get("distro_codename","linux"))

        # Setup LVM
        if self.setup.lvm:
            lvm = config.get("distro_codename","linux")
            log(" --> LVM: Creating PV")
            self.run("pvcreate -y {}".format(self.auto_root_partition))
            log(" --> LVM: Creating VG")
            self.run("vgcreate -y {} {}".format(lvm,self.auto_root_partition))
            log(" --> LVM: Creating LV root")
            self.run("lvcreate -y -n root -L 1GB {}".format(lvm))
            if config.get("use_swap",False) and self.setup.create_swap:
                log(" --> LVM: Creating LV swap")
                swap_size = int(round(int(subprocess.getoutput(
                    "awk '/^MemTotal/{ print $2 }' /proc/meminfo")) / 1024, 0))
                self.run("lvcreate -y -n swap -L {}MB {}".format(swap_size,lvm))
            log(" --> LVM: Extending LV root")
            self.run("lvextend -l 100\\%FREE /dev/{}/root".format(lvm))
            log(" --> LVM: Formatting LV root")
            self.run("mkfs.ext4 /dev/{}/root -FF".format(lvm))
            if config.get("use_swap",False) and self.setup.create_swap:
                log(" --> LVM: Formatting LV swap")
                self.run("mkswap -f /dev/{}/swap".format(lvm))
                log(" --> LVM: Enabling LV swap".format(lvm))
                self.run("swapon /dev/{}/swap".format(lvm))
                self.auto_swap_partition = "/dev/{}/swap".format(lvm)
            self.auto_root_partition = "/dev/{}/root".format(lvm)


        self.do_mount(self.auto_root_partition, "/target", "ext4", None)
        if (self.auto_boot_partition is not None):
            self.run("mkdir -p /target/boot")
            self.do_mount(self.auto_boot_partition,
                          "/target/boot", "ext4", None)
        if (self.auto_efi_partition is not None):
            if os.path.exists("/source/kernel/boot"):
                self.run("mkdir -p /target/kernel/boot/efi")
                self.do_mount(self.auto_efi_partition,
                              "/target/kernel/boot/efi", "vfat", None)
            else:
                self.run("mkdir -p /target/boot/efi")
                self.do_mount(self.auto_efi_partition,
                              "/target/boot/efi", "vfat", None)

    def format_partitions(self):
        for partition in self.setup.partitions:
            if(partition.format_as is not None and partition.format_as != ""):
                # report it. should grab the total count of filesystems to be
                # formatted ..
                self.update_progress(_("Formatting %(partition)s as %(format)s ...") % {
                                     'partition': partition.path, 'format': partition.format_as}, True)

                # Format it
                if partition.format_as == "swap":
                    cmd = "mkswap %s" % partition.path
                elif (partition.format_as in ['ext2', 'ext3', 'ext4']):
                    cmd = "mkfs.%s -F %s" % (partition.format_as,
                                             partition.path)
                elif (partition.format_as == "jfs"):
                    cmd = "mkfs.%s -q %s" % (partition.format_as,
                                             partition.path)
                elif (partition.format_as == "xfs"):
                    cmd = "mkfs.%s -f %s" % (partition.format_as,
                                             partition.path)
                elif (partition.format_as == "vfat"):
                    cmd = "mkfs.%s %s -F 32" % (partition.format_as,
                                                partition.path)
                elif (partition.format_as == "ntfs"):
                    cmd = "mkfs.%s -f %s " % (partition.format_as,
                                                partition.path)
                elif (partition.format_as == "none"):
                    cmd = "echo 'Format disabled for %s.'" % partition.path
                else:
                    # works with bfs, minix, msdos, ntfs, vfat
                    cmd = "mkfs.%s %s" % (
                        partition.format_as, partition.path)

                self.run(cmd)
                partition.type = partition.format_as

    def mount_partitions(self):
        # Sort partitions for mount order
        partitions_sorted = []
        mountpoint_sorted = []
        for partition in self.setup.partitions:
            mountpoint_sorted.append(partition.mount_as)
        mountpoint_sorted.sort()
        for dir in mountpoint_sorted:
            for partition in self.setup.partitions:
                if partition.mount_as == dir:
                    partitions_sorted.append(partition)
        self.setup.partitions = partitions_sorted
        # Mount the target partition
        for partition in self.setup.partitions:
            if(partition.mount_as not in ["", None, "swap"]):
                if partition.mount_as == "/":
                    self.update_progress(_("Mounting %(partition)s on %(mountpoint)s") % {
                                         'partition': partition.path, 'mountpoint': "/target/"})
                    log(" ------ Mounting partition %s on %s" %
                        (partition.path, "/target/"))
                    if 0 != self.do_mount(partition.path, "/target", partition.type, None):
                        self.error_message(
                            "Cannot mount rootfs (type: {}): {}".format(partition.type, partition.path))
                    break

        # move old files if available
        olds = os.listdir("/target/")
        if len(olds) > 0:
            target="/target/{}.old".format(config.get("distro_codename","linux"))
            if os.path.exists(target):
                 self.run("rm -rf {}".format(target),vital=False)
            os.mkdir(target)
            for old in olds:
                self.run("mv /target/{0} {1}/{0}".format(old,target),vital=False)


        # Mount the other partitions
        for partition in self.setup.partitions:
            if(partition.mount_as not in [ None, "/", "swap", ""]):
                log(" ------ Mounting %s on %s" %
                    (partition.path, "/target" + partition.mount_as))
                self.run("mkdir -p /target" + partition.mount_as)
                if partition.type == "fat16" or partition.type == "fat32":
                    fs = "vfat"
                else:
                    fs = partition.type
                self.do_mount(partition.path, "/target" +
                              partition.mount_as, fs, None)

    def get_blkid(self, path):
        uuid = path  # If we can't find the UUID we use the path
        blkid = subprocess.getoutput('blkid').split('\n')
        for blkid_line in blkid:
            blkid_elements = blkid_line.split(':')
            if blkid_elements[0] == path:
                blkid_mini_elements = blkid_line.split()
                for blkid_mini_element in blkid_mini_elements:
                    if "UUID=" in blkid_mini_element:
                        uuid = blkid_mini_element.replace('"', '').strip()
                        break
                break
        return uuid

    def write_fstab(self):
        # write the /etc/fstab
        log(" --> Writing fstab")
        # make sure fstab has default /proc and /sys entries
        if(not os.path.exists("/target/etc/fstab")):
            self.run(
                "echo \"#### Static Filesystem Table File\" > /target/etc/fstab")
        fstab = open("/target/etc/fstab", "a")
        fstab.write("proc /proc proc defaults 0 0\n")
        fstab.write("tmpfs /tmp tmpfs nosuid,nodev,noatime 0 0\n")
        if self.setup.expert_mode:
            log("  --> Expert mode detected")
        elif self.setup.automated:
            if self.setup.lvm:
                # Don't use UUIDs with LVM
                fstab.write("%s /  ext4 defaults 0 1\n" %
                            self.auto_root_partition)
                if self.auto_swap_partition:
                    fstab.write("%s none   swap sw 0 0\n" %
                                self.auto_swap_partition)
            else:
                fstab.write("# %s\n" % self.auto_root_partition)
                fstab.write("%s /  ext4 defaults 0 1\n" %
                            self.get_blkid(self.auto_root_partition))
                fstab.write("# %s\n" % self.auto_swap_partition)
                if self.auto_swap_partition:
                    fstab.write("%s none   swap sw 0 0\n" %
                                self.get_blkid(self.auto_swap_partition))
            if (self.auto_boot_partition is not None):
                fstab.write("# %s\n" % self.auto_boot_partition)
                fstab.write("%s /boot  ext4 defaults 0 1\n" %
                            self.get_blkid(self.auto_boot_partition))
            if (self.auto_efi_partition is not None):
                fstab.write("# %s\n" % self.auto_efi_partition)
                fstab.write("%s /boot/efi  vfat defaults 0 1\n" %
                            self.get_blkid(self.auto_efi_partition))
        else:
            for partition in self.setup.partitions:
                if (partition.mount_as is not None and partition.mount_as !=
                        "" and partition.mount_as != "None"):
                    fstab.write("# %s\n" % (partition.path))
                    if(partition.mount_as == "/"):
                        fstab_fsck_option = "1"
                    else:
                        fstab_fsck_option = "0"
                    rw="rw"
                    if partition.read_only:
                        rw="ro"
                    fstab_mount_options = "defaults,{}".format(rw)
                    if partition.type == "fat16" or partition.type == "fat32":
                        fs = "vfat"
                    else:
                        fs = partition.type

                    partition_uuid = self.get_blkid(partition.path)
                    if(fs == "swap"):
                        fstab.write("%s swap swap sw 0 0\n" %
                                    partition_uuid)
                    else:
                        fstab.write("%s %s %s %s %s %s\n" % (
                            partition_uuid, partition.mount_as, fs, fstab_mount_options, "0", fstab_fsck_option))

        if self.setup.luks:
            self.run("echo '{}   {}   none   luks,tries=3' >> /target/etc/crypttab".format(
                config.get("distro_codename","linux"),self.auto_root_physical_partition))
        inf(open("/target/etc/fstab", "r").read())
        fstab.close()

    def finish_installation(self):
        # Steps:
        self.our_total = 12
        self.our_current = 4

        # write host+hostname infos
        log(" --> Writing hostname")
        self.our_current += 1
        self.update_progress(_("Setting hostname"))
        hostnamefh = open("/target/etc/hostname", "w")
        hostnamefh.write("%s\n" % self.setup.hostname)
        hostnamefh.close()
        hostsfh = open("/target/etc/hosts", "w")
        hostsfh.write("127.0.0.1 localhost\n")
        hostsfh.write("127.0.1.1 %s\n" % self.setup.hostname)
        hostsfh.write(
            "# The following lines are desirable for IPv6 capable hosts\n")
        hostsfh.write("::1     localhost ip6-localhost ip6-loopback\n")
        hostsfh.write("fe00::0 ip6-localnet\n")
        hostsfh.write("ff00::0 ip6-mcastprefix\n")
        hostsfh.write("ff02::1 ip6-allnodes\n")
        hostsfh.write("ff02::2 ip6-allrouters\n")
        hostsfh.write("ff02::3 ip6-allhosts\n")
        # Append hosts file from branding
        if os.path.isfile("./branding/hosts"):
            f = open("./branding/hosts", "r").readlines()
            for line in f:
                hostsfh.write(line)
        hostsfh.close()

        # set the locale
        log(" --> Setting the locale")
        self.our_current += 1
        self.update_progress(_("Setting locale"))
        # locale-gen
        l = open("/target/etc/locale.gen", "a")
        l.write("%s.UTF-8 UTF-8\n" % self.setup.language)
        if self.setup.language != "en_US":
            l.write("en_US.UTF-8 UTF-8\n")
        l.close()
        self.run("chroot||locale-gen")
        # etc/default/locale
        l = open("/target/etc/default/locale", "w")
        l.write("LANG=%s.UTF-8\n" % self.setup.language)
        l.close()
        # localectl
        self.run("chroot||localectl set-locale LANG=\"%s.UTF-8\"" %
            self.setup.language,vital=False)
        # locale.conf
        l = open("/target/etc/locale.conf", "w")
        l.write("LANG=%s.UTF-8" % self.setup.language)
        l.close()
        # set the locale for gentoo / sulin
        if os.path.exists("/target/etc/env.d"):
            l = open("/target/etc/env.d/20language", "w")
            l.write("LANG={}.UTF-8".format(self.setup.language))
            l.write("LC_ALL={}.UTF-8".format(self.setup.language))
            l.flush()
            l.close()
            self.run("chroot||env-update")

        # set the timezone
        log(" --> Setting the timezone")
        self.our_current += 1
        self.update_progress(_("Setting timezone"))
        t = open("/target/etc/timezone","w")
        t.write("%s\n" % self.setup.timezone)
        t.close()
        if os.path.exists("/target/etc/localtime"):
            os.unlink("/target/etc/localtime")
            os.symlink("../usr/share/zoneinfo/%s" % self.setup.timezone, "/target/etc/localtime")

        # Keyboard settings X11
        self.update_progress(("Settings X11 keyboard options"))
        newconsolefh = None
        if os.path.exists("/target/etc/X11/xorg.conf.d"):
            newconsolefh = open(
                "/target/etc/X11/xorg.conf.d/10-keyboard.conf", "w")
        elif os.path.exists("/target/usr/share/X11/xorg.conf.d/"):
            newconsolefh = open(
                "/target/usr/share/X11/xorg.conf.d/10-keyboard.conf", "w")
        if newconsolefh:
            newconsolefh.write('Section "InputClass"\n')
            newconsolefh.write('Identifier "system-keyboard"\n')
            newconsolefh.write('MatchIsKeyboard "on"\n')
            newconsolefh.write('Option "XkbLayout" "{}"\n'.format(
                self.setup.keyboard_layout))
            newconsolefh.write('Option "XkbModel" "{}"\n'.format(
                self.setup.keyboard_model))
            newconsolefh.write('Option "XkbVariant" "{}"\n'.format(
                self.setup.keyboard_variant))
            if "," in self.setup.keyboard_layout:
                newconsolefh.write('Option "XkbOptions" ""\n')
            newconsolefh.write('EndSection\n')
            newconsolefh.close()

        # set the keyboard options..
        log(" --> Setting the keyboard")
        self.our_current += 1
        self.update_progress(_("Setting keyboard options"))
        if os.path.exists("/target/etc/default/console-setup"):
            consolefh = open("/target/etc/default/console-setup", "r")
            newconsolefh = open("/target/etc/default/console-setup.new", "w")
            for line in consolefh:
                line = line.rstrip("\r\n")
                if(line.startswith("XKBMODEL=")):
                    newconsolefh.write("XKBMODEL=\"%s\"\n" %
                                       self.setup.keyboard_model)
                elif(line.startswith("XKBLAYOUT=")):
                    newconsolefh.write("XKBLAYOUT=\"%s\"\n" %
                                       self.setup.keyboard_layout)
                elif(line.startswith("XKBVARIANT=") and self.setup.keyboard_variant != ""):
                    newconsolefh.write("XKBVARIANT=\"%s\"\n" %
                                       self.setup.keyboard_variant)
                else:
                    newconsolefh.write("%s\n" % line)
            consolefh.close()
            newconsolefh.close()
            self.run("chroot||rm /etc/default/console-setup")
            self.run("chroot||mv /etc/default/console-setup.new /etc/default/console-setup")

        # lfs like systems uses vconsole.conf (systemd)
        if os.path.exists("/target/etc/vconsole.conf"):
            consolefh = open("/target/etc/vconsole.conf", "r")
            newconsolefh = open("/target/etc/vconsole.conf.new", "w")
            for line in consolefh:
                line = line.rstrip("\r\n")
                if(line.startswith("KEYMAP=")):
                    if(self.setup.keyboard_variant != ""):
                        newconsolefh.write(
                            "KEYMAP=\"{0}-{1}\"\n".format(self.setup.keyboard_layout, self.setup.keyboard_variant))
                    else:
                        newconsolefh.write("KEYMAP=\"{0}\"\n".format(
                            self.setup.keyboard_layout))
                else:
                    newconsolefh.write("%s\n" % line)
            consolefh.close()
            newconsolefh.close()
            self.run("chroot||rm /etc/vconsole.conf")
            self.run("chroot||mv /etc/vconsole.conf.new /etc/vconsole.conf")

        # debian like systems uses this (systemd)
        if os.path.exists("/target/etc/default/keyboard"):
            consolefh = open("/target/etc/default/keyboard", "r")
            newconsolefh = open("/target/etc/default/keyboard.new", "w")
            for line in consolefh:
                line = line.rstrip("\r\n")
                if(line.startswith("XKBMODEL=")):
                    newconsolefh.write("XKBMODEL=\"%s\"\n" %
                                       self.setup.keyboard_model)
                elif(line.startswith("XKBLAYOUT=")):
                    newconsolefh.write("XKBLAYOUT=\"%s\"\n" %
                                       self.setup.keyboard_layout)
                elif(line.startswith("XKBVARIANT=") and self.setup.keyboard_variant != ""):
                    newconsolefh.write("XKBVARIANT=\"%s\"\n" %
                                       self.setup.keyboard_variant)
                elif(line.startswith("XKBOPTIONS=")):
                    newconsolefh.write("XKBOPTIONS=\"\"")
                else:
                    newconsolefh.write("%s\n" % line)
            consolefh.close()
            newconsolefh.close()
            self.run("chroot||rm /etc/default/keyboard")
            self.run("chroot||mv /etc/default/keyboard.new /etc/default/keyboard")

        # Keyboard settings openrc
        if os.path.exists("/target/etc/conf.d/keymaps"):
            newconsolefh = open("/target/etc/conf.d/keymaps", "w")
            if not self.setup.keyboard_layout:
                self.setup.keyboard_layout = "en"
            newconsolefh.write("keymap=\"{}{}\"\n".format(
                self.setup.keyboard_layout, self.setup.keyboard_variant))
            newconsolefh.close()

        # Keyboard settings (gnome)
        if os.path.exists("/target/usr/share/glib-2.0/schemas/org.gnome.desktop.input-sources.gschema.xml"):
            with open("/target/usr/share/glib-2.0/schemas/99_17g-gnome-keyboard-config.gschema.override", "w") as schema:
                layouts, variants = self.setup.keyboard_layout.split(","), self.setup.keyboard_variant.split(",")

                schema.write("[org.gnome.desktop.input-sources]\n")

                output = "sources = ["
                for i in range(2 if "," in self.setup.keyboard_layout else 1):
                    output += "('xkb', '" + layouts[i]
                    if variants[i]:
                        output += "+" + variants[i]
                    output += "')" + (", " if i == 0 and "," in self.setup.keyboard_layout else "")
                schema.write(output + "]")
            self.run("chroot||glib-compile-schemas /usr/share/glib-2.0/schemas/",vital=False)


        # Update if enabled
        if self.setup.minimal_installation:
            self.update_progress(_("Removing extra packages"), True)
            pkgs = open("branding/extra_packages.txt").read().split("\n")
            self.run_and_update("chroot||yes | {}".format(config.package_manager(
                "remove_package_with_unusing_deps", pkgs)))

        # Update if enabled
        if self.setup.install_updates:
            self.update_progress(_("Trying to install updates"), True)
            self.run_and_update(config.package_manager(
                "full_system_update"),True)

        # remove 17g
        self.update_progress(_("Clearing package manager"), True)
        log(" --> Clearing package manager")
        log(config.get("remove_packages", ["17g-installer"]))
        self.run("chroot||yes | {}".format(config.package_manager(
            "remove_package_with_unusing_deps", config.get("remove_packages", ["17g-installer"]))))
        self.run("chroot|| rm -rf /lib/live-installer",vital=False)

        #if self.setup.luks:
        #    with open("/target/etc/default/grub", "a") as f:
        #        f.write("\nGRUB_CMDLINE_LINUX_DEFAULT+=\" cryptdevice=%s:lvmlmde root=/dev/lvmlmde/root%s\"\n" %
        #                (self.get_blkid(self.auto_root_physical_partition), " resume=/dev/lvmlmde/swap" if self.auto_swap_partition else ""))
        #        f.write("GRUB_ENABLE_CRYPTODISK=y\n")

        # recreate initramfs (needed in case of skip_mount also, to include
        # things like mdadm/dm-crypt/etc in case its needed to boot a custom
        # install)
        log(" --> Configuring Initramfs")
        self.our_current += 1
        self.update_progress(_("Generating initramfs"), pulse=True)

        for command in config.update_initramfs():
            self.run("chroot||" + command)
        self.update_progress(
            _("Preparing bootloader installation"), pulse=True)
        try:
            grub_prepare_commands = config.distro["grub_prepare"]
            for command in grub_prepare_commands:
                self.run(command)
        except BaseException:
            err("Grub prepare process not available for your distribution!")

        # Enable LVM and LUKS for initramfs-systems
        if self.setup.lvm and "enable_lvm" in config.initramfs:
            for cmd in config.initramfs["enable_lvm"]:
                self.run(cmd)
        if self.setup.luks and "enable_luks" in config.initramfs:
            for cmd in config.initramfs["enable_luks"]:
                self.run(cmd)

        # install GRUB bootloader (EFI & Legacy)
        log(" --> Configuring Grub")
        self.our_current += 1
        if(self.setup.grub_device is not None):
            self.update_progress(_("Installing bootloader"), pulse=True)
            log(" --> running grub-install")

            if os.path.exists("/sys/firmware/efi"):
                grub_cmd = config.distro["grub_installation_efi"]
                self.run(grub_cmd.replace("{disk}", self.setup.grub_device))
            else:
                grub_cmd = config.distro["grub_installation_legacy"]
                self.run(grub_cmd.replace("{disk}", self.setup.grub_device))

            # fix not add windows grub entry
            self.run("chroot||grub-mkconfig -o /boot/grub/grub.cfg")
            self.update_progress(_("Configuring bootloader"), pulse=True)
            self.do_configure_grub()
            grub_retries = 0
            while (not self.do_check_grub()):
                self.do_configure_grub()
                grub_retries = grub_retries + 1
                if grub_retries >= 5:
                    self.error_message(message=_(
                        "WARNING: The grub bootloader was not configured properly! You need to configure it manually."))
                    break

        # Custom commands
        self.do_hook_commands("post_install_hook")

        # Customization with network
        if config.get("customizer_address","localhost") != "localhost":
            self.run("curl {} | chroot /target bash".format(config.get("customizer_address","localhost")),vital=False)

        # now unmount it
        log(" --> Unmounting partitions")
        self.do_unmount("/target/dev/shm")
        self.do_unmount("/target/dev/pts")
        if os.path.exists("/sys/firmware/efi"):
            self.do_unmount("/target/sys/firmware/efi/")
        if self.setup.gptonefi:
            self.do_unmount("/target/boot/efi")
            self.do_unmount("/target/media/cdrom")
        self.do_unmount("/target/boot")
        self.do_unmount("/target/dev/")
        self.do_unmount("/target/sys/")
        self.do_unmount("/target/proc/")
        self.do_unmount("/target/run/")
        for partition in self.setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                self.do_unmount("/target" + partition.mount_as)
        self.run("cat '{0}'> /target/'{0}'".format(self.logfile))
        os.sync()
        self.do_unmount("/target")
        self.do_unmount("/source")

        self.update_progress(_("Installation finished"), done=True)
        log(" --> All done")

    def do_configure_grub(self):
        log(" --> running grub-mkconfig")
        grub_output = subprocess.getoutput(
            "chroot /target/ /bin/sh -c \"grub-mkconfig -o /boot/grub/grub.cfg\"")
        log(grub_output)

    def do_hook_commands(self, hook=""):
        log(" --> {} running".format(str(hook)))
        for command in config.get(hook, []):
            cmd = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
            while cmd.poll() is None:
                line = str(cmd.stdout.readline().decode(
                    "utf-8").replace("\n", ""))
                if not line:
                    time.sleep(0.1)
                else:
                    self.update_progress(line)

    def do_check_grub(self):
        self.update_progress(_("Checking bootloader"), True)
        log(" --> Checking Grub configuration")
        if os.path.exists("/target/boot/grub/grub.cfg"):
            return True
        else:
            err("!No /target/boot/grub/grub.cfg file found!")
            return False

    def do_mount(self, device, dest, typevar="auto", options=None):
        ''' Mount a filesystem '''
        if typevar == "none" or typevar == "":
            return 0
        while not os.path.exists(device):
            os.sync()
            time.sleep(0.1)
        if(options is not None):
            cmd = "mount -o %s -t %s %s %s" % (options, typevar, device, dest)
        else:
            cmd = "mount -t %s %s %s" % (typevar, device, dest)
        return self.run(cmd)

    def do_unmount(self, mountpoint):
        ''' Unmount a filesystem '''
        os.system("umount -lf %s" % mountpoint)

    def run_and_update(self, cmd, pulse=False):
        p = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        while p.poll() is None:
            line = str(p.stdout.readline().decode("utf-8").replace("\n", ""))
            self.update_progress(line,pulse)

    def run(self,cmd,vital=True):
        if "{distro_codename}" in cmd:
            cmd = cmd.replace("{distro_codename}", config.get("distro_codename", "linux"))
        i = int(run(cmd,vital)/256)
        if 0 != i and vital:
            self.error_message(message=(_("Failed to run command (Exited with {}):")+"\n {}").format(
            str(i), cmd))
        return i
# Represents the choices made by the user


class Setup(object):
    language = None
    timezone = None
    keyboard_model = None
    keyboard_layout = None
    keyboard_variant = None
    partitions = []  # Array of PartitionSetup objects
    username = None
    hostname = None
    autologin = False
    ecryptfs = False
    password1 = None
    password2 = None
    real_name = None
    grub_device = None
    disks = []
    automated = True
    replace_windows = False
    expert_mode = False
    disk = None
    diskname = None
    passphrase1 = None
    passphrase2 = None
    lvm = False
    luks = False
    badblocks = False
    create_swap = False
    winroot = None
    winboot = None
    winefi = None
    gptonefi = partitioning.is_efi_supported()
    # Optionally skip all mouting/partitioning for advanced users with custom setups (raid/dmcrypt/etc)
    # Make sure the user knows that they need to:
    #  * Mount their target directory structure at /target
    #  * NOT mount /target/dev, /target/dev/shm, /target/dev/pts, /target/proc, and /target/sys
    #  * Manually create /target/etc/fstab after start_installation has completed and before finish_installation is called
    #  * Install cryptsetup/dmraid/mdadm/etc in target environment (using chroot) between start_installation and finish_installation
    #  * Make sure target is mounted using the same block device as is used in /target/etc/fstab (eg if you change the name of a dm-crypt device between now and /target/etc/fstab, update-initramfs will likely fail)
    skip_mount = False

    # Descriptions (used by the summary screen)
    keyboard_model_description = None
    keyboard_layout_description = None
    keyboard_variant_description = None

    # Additional options
    install_updates = False
    minimal_installation = False
