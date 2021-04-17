import os
import subprocess
import time
import gettext
import parted
import frontend.partitioning as partitioning
import config
from utils import log, err, inf, run

gettext.install("live-installer", "/usr/share/locale")

NON_LATIN_KB_LAYOUTS = ['am', 'af', 'ara', 'ben', 'bd', 'bg', 'bn', 'bt', 'by', 'deva', 'et', 'ge', 'gh', 'gn', 'gr', 'guj', 'guru', 'id', 'il', 'iku', 'in', 'iq', 'ir', 'kan',
                        'kg', 'kh', 'kz', 'la', 'lao', 'lk', 'ma', 'mk', 'mm', 'mn', 'mv', 'mal', 'my', 'np', 'ori', 'pk', 'ru', 'rs', 'scc', 'sy', 'syr', 'tel', 'th', 'tj', 'tam', 'tz', 'ua', 'uz']


class InstallerEngine:
    ''' This is central to the live installer '''

    def __init__(self, setup):
        self.setup = setup

        # find the squashfs..
        self.media = config.get("loop_directory", "/dev/loop0")

        if(not os.path.exists(self.media)):
            err("Critical Error: Live medium (%s) not found!" % self.media)
            # sys.exit(1)
        inf("Using live medium: "+self.media)

    def set_progress_hook(self, progresshook):
        ''' Set a callback to be called on progress updates '''
        ''' i.e. def my_callback(progress_type, message, current_progress, total) '''
        ''' Where progress_type is any off PROGRESS_START, PROGRESS_UPDATE, PROGRESS_COMPLETE, PROGRESS_ERROR '''
        self.update_progress = progresshook

    def set_error_hook(self, errorhook):
        ''' Set a callback to be called on errors '''
        self.error_message = errorhook

    def start_installation(self):

        # mount the media location.
        log(" --> Installation started")
        if(not os.path.exists("/target")):
            os.mkdir("/target")
        if(not os.path.exists("/source")):
            os.mkdir("/source")

        os.system("umount --force /target/dev/shm")
        os.system("umount --force /target/dev/pts")
        os.system("umount --force /target/dev/")
        os.system("umount --force /target/sys/")
        os.system("umount --force /target/proc/")
        os.system("umount --force /target/run/")

        self.mount_source()

        if self.setup.automated:
            self.create_partitions()
        else:
            self.format_partitions()
            self.mount_partitions()

        # Transfer the files
        SOURCE = "/source/"
        DEST = "/target/"
        EXCLUDE_DIRS = "home/* dev/* proc/* sys/* tmp/* run/* mnt/* media/* lost+found source target".split()

        # Add optional entries to EXCLUDE_DIRS
        for dirvar in config.get("exclude_dirs", ["/home"]):
            EXCLUDE_DIRS.append(dirvar)

        our_current = 0
        # (Valid) assumption: num-of-files-to-copy ~= num-of-used-inodes-on-/
        our_total = int(subprocess.getoutput(
            "df --inodes /{src} | awk 'END{{ print $3 }}'".format(src=SOURCE.strip('/'))))
        log(" --> Copying {} files".format(our_total))
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
                our_current = min(our_current + 1, our_total)
                self.update_progress(our_current, our_total,
                                     False, False, _("Copying /%s") % line)
        err("rsync exited with returncode: " + str(rsync.poll()))

        # Steps:
        our_total = 11
        our_current = 0
        # chroot
        log(" --> Chrooting")
        self.update_progress(our_current, our_total, False,
                             False, _("Entering the system ..."))
        os.system("mount --bind /dev/ /target/dev/")
        os.system("mount --bind /dev/shm /target/dev/shm")
        os.system("mount --bind /dev/pts /target/dev/pts")
        os.system("mount --bind /sys/ /target/sys/")
        os.system("mount --bind /proc/ /target/proc/")
        os.system("mount --bind /run/ /target/run/")
        os.system("mv /target/etc/resolv.conf /target/etc/resolv.conf.bk")
        os.system("cp -f /etc/resolv.conf /target/etc/resolv.conf")

        kernelversion = subprocess.getoutput("uname -r")
        if os.path.exists("/lib/modules/{0}/vmlinuz".format(kernelversion)):
            os.system(
                "cp /lib/modules/{0}/vmlinuz /target/boot/vmlinuz-{0}".format(kernelversion))

        # add new user
        log(" --> Adding new user")
        our_current += 1
        try:
            for cmd in config.distro["run_before_user_creation"]:
                self.do_run_in_chroot(cmd)
        except:
            err("This action not supported for your distribution.")
        self.update_progress(our_current, our_total, False,
                             False, ("Adding new user to the system"))
        # TODO: support encryption

        self.do_run_in_chroot('useradd -m -s {shell} -c \"{realname}\" {username}'.format(
            shell=config.get("using_shell", "/bin/bash"), realname=self.setup.real_name,
            username=self.setup.username))

        # Add user to additional groups
        for group in config.get("additional_user_groups", ["audio", "video", "netdev"]):
            self.do_run_in_chroot(
                "usermod -aG {} {}".format(group, self.setup.username))

        if (os.system("which chpasswd &>/dev/null") == 0) and config.get("use_chpasswd", True):
            fp = open("/target/tmp/.passwd", "w")
            fp.write(self.setup.username + ":" + self.setup.password1 + "\n")
            if config.get("set_root_password", True):
                fp.write("root:" + self.setup.password1 + "\n")
            fp.close()
            self.do_run_in_chroot("cat /tmp/.passwd | chpasswd")
            self.do_run_in_chroot("rm -f /tmp/.passwd")
        else:
            self.do_run_in_chroot(
                "echo -e \"{0}\\n{0}\\n\" | passwd {1}".format(self.setup.password1, self.setup.username))
            if config.get("set_root_password", True):
                self.do_run_in_chroot(
                    "echo -e \"{0}\\n{0}\\n\" | passwd".format(self.setup.password1))

        # Set LightDM to show user list by default
        if config.get("list_users_when_auto_login", True):
            self.do_run_in_chroot(
                r"sed -i -r 's/^#?(greeter-hide-users)\s*=.*/\1=false/' /etc/lightdm/lightdm.conf")
        else:
            self.do_run_in_chroot(
                r"sed -i -r 's/^#?(greeter-hide-users)\s*=.*/\1=true/' /etc/lightdm/lightdm.conf")

        # Set autologin for user if they so elected
        if self.setup.autologin:
            # LightDM and Auto Login Groups
            self.do_run_in_chroot("""groupadd -r autologin && gpasswd -a {user} autologin
                                  && groupadd -r nopasswdlogin & & gpasswd -a {user} nopasswdlogin""".format(user=self.setup.username))

            self.do_run_in_chroot(
                r"sed -i -r 's/^#?(autologin-user)\s*=.*/\1={user}/' /etc/lightdm/lightdm.conf".format(user=self.setup.username))

        # /etc/fstab, mtab and crypttab
        our_current += 1
        self.update_progress(our_current, our_total, False, False, _(
            "Writing filesystem mount information to /etc/fstab"))
        self.write_fstab()

    def mount_source(self):
        # Mount the installation media
        log(" --> Mounting partitions")
        self.update_progress(2, 4, False, False, _("Mounting %(partition)s on %(mountpoint)s") % {
                             'partition': self.media, 'mountpoint': "/source/"})
        log(" ------ Mounting %s on %s" % (self.media, "/source/"))
        self.do_mount(self.media, "/source/", "squashfs", options="loop")

    def create_partitions(self):
        # Create partitions on the selected disk (automated installation)
        partition_prefix = ""
        if self.setup.disk.startswith("/dev/nvme"):
            partition_prefix = "p"
        if self.setup.luks:
            if self.setup.gptonefi:
                # EFI+LUKS/LVM
                # sdx1=EFI, sdx2=BOOT, sdx3=ROOT
                self.auto_efi_partition = self.setup.disk + partition_prefix + "1"
                self.auto_boot_partition = self.setup.disk + partition_prefix + "2"
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "3"
            else:
                # BIOS+LUKS/LVM
                # sdx1=BOOT, sdx2=ROOT
                self.auto_efi_partition = None
                self.auto_boot_partition = self.setup.disk + partition_prefix + "1"
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "2"
        elif self.setup.lvm:
            if self.setup.gptonefi:
                # EFI+LVM
                # sdx1=EFI, sdx2=ROOT
                self.auto_efi_partition = self.setup.disk + partition_prefix + "1"
                self.auto_boot_partition = None
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "2"
            else:
                # BIOS+LVM:
                # sdx1=ROOT
                self.auto_efi_partition = None
                self.auto_boot_partition = None
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "1"
        else:
            if self.setup.gptonefi:
                # EFI
                # sdx1=EFI, sdx2=ROOT
                self.auto_efi_partition = self.setup.disk + partition_prefix + "1"
                self.auto_boot_partition = None
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "2"
            else:
                # BIOS:
                # sdx1=ROOT
                self.auto_efi_partition = None
                self.auto_boot_partition = None
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "1"

        log("EFI:"+str(self.auto_efi_partition))
        log("BOOT:"+str(self.auto_boot_partition))
        log("Root:"+str(self.auto_root_partition))
        self.auto_root_physical_partition = self.auto_root_partition

        # Wipe HDD
        if self.setup.badblocks:
            self.update_progress(1, 4, False, False, _(
                "Filling %s with random data (please be patient, this can take hours...)") % self.setup.disk)
            log(" --> Filling %s with random data" % self.setup.disk)
            os.system("badblocks -c 10240 -s -w -t random -v %s" %
                      self.setup.disk)

        # Create partitions
        self.update_progress(1, 4, False, False, _(
            "Creating partitions on %s") % self.setup.disk)
        log(" --> Creating partitions on %s" % self.setup.disk)
        disk_device = parted.getDevice(self.setup.disk)
        # replae this with changeable function
        partitioning.full_disk_format(disk_device, create_boot=(
            self.auto_boot_partition is not None), create_swap=(self.auto_swap_partition is not None))

        # Encrypt root partition
        if self.setup.luks:
            log(" --> Encrypting root partition %s" %
                self.auto_root_partition)
            os.system("printf \"%s\" | cryptsetup luksFormat -c aes-xts-plain64 -h sha256 -s 512 %s" %
                      (self.setup.passphrase1, self.auto_root_partition))
            log(" --> Opening root partition %s" % self.auto_root_partition)
            os.system("printf \"%s\" | cryptsetup luksOpen %s lvmlmde" %
                      (self.setup.passphrase1, self.auto_root_partition))
            self.auto_root_partition = "/dev/mapper/lvmlmde"

        # Setup LVM
        if self.setup.lvm:
            log(" --> LVM: Creating PV")
            os.system("pvcreate -y %s" % self.auto_root_partition)
            log(" --> LVM: Creating VG")
            os.system("vgcreate -y lvmlmde %s" % self.auto_root_partition)
            log(" --> LVM: Creating LV root")
            os.system("lvcreate -y -n root -L 1GB lvmlmde")
            log(" --> LVM: Creating LV swap")
            swap_size = int(round(int(subprocess.getoutput(
                "awk '/^MemTotal/{ print $2 }' /proc/meminfo")) / 1024, 0))
            os.system("lvcreate -y -n swap -L %dMB lvmlmde" % swap_size)
            log(" --> LVM: Extending LV root")
            os.system("lvextend -l 100\%FREE /dev/lvmlmde/root")
            log(" --> LVM: Formatting LV root")
            os.system("mkfs.ext4 /dev/mapper/lvmlmde-root -FF")
            log(" --> LVM: Formatting LV swap")
            os.system("mkswap -f /dev/mapper/lvmlmde-swap")
            log(" --> LVM: Enabling LV swap")
            os.system("swapon /dev/mapper/lvmlmde-swap")
            self.auto_root_partition = "/dev/mapper/lvmlmde-root"
            self.auto_swap_partition = "/dev/mapper/lvmlmde-swap"

        self.do_mount(self.auto_root_partition, "/target", "ext4", None)
        if (self.auto_boot_partition is not None):
            os.system("mkdir -p /target/boot")
            self.do_mount(self.auto_boot_partition,
                          "/target/boot", "ext4", None)
        if (self.auto_efi_partition is not None):
            if os.path.exists("/source/kernel/boot"):
                os.system("mkdir -p /target/kernel/boot/efi")
                self.do_mount(self.auto_efi_partition,
                              "/target/kernel/boot/efi", "vfat", None)
            else:
                os.system("mkdir -p /target/boot/efi")
                self.do_mount(self.auto_efi_partition,
                              "/target/boot/efi", "vfat", None)

    def format_partitions(self):
        for partition in self.setup.partitions:
            if(partition.format_as is not None and partition.format_as != ""):
                # report it. should grab the total count of filesystems to be formatted ..
                self.update_progress(1, 4, True, False, _("Formatting %(partition)s as %(format)s ...") % {
                                     'partition': partition.path, 'format': partition.format_as})

                # Format it
                if partition.format_as == "swap":
                    cmd = "mkswap %s" % partition.path
                else:
                    if (partition.format_as in ['ext2', 'ext3', 'ext4']):
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
                    else:
                        # works with bfs, minix, msdos, ntfs, vfat
                        cmd = "mkfs.%s %s" % (
                            partition.format_as, partition.path)

                run(cmd)
                partition.type = partition.format_as

    def mount_partitions(self):
        # Mount the target partition
        for partition in self.setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != ""):
                if partition.mount_as == "/":
                    self.update_progress(3, 4, False, False, _("Mounting %(partition)s on %(mountpoint)s") % {
                                         'partition': partition.path, 'mountpoint': "/target/"})
                    log(" ------ Mounting partition %s on %s" %
                        (partition.path, "/target/"))
                    if partition.type == "fat32":
                        fs = "vfat"
                    else:
                        fs = partition.type
                    if 0 != self.do_mount(partition.path, "/target", fs, None):
                        self.error_message("Cannot mount rootfs: {}".format(partition.path))
                    break

        # Mount the other partitions
        for partition in self.setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                log(" ------ Mounting %s on %s" %
                    (partition.path, "/target" + partition.mount_as))
                os.system("mkdir -p /target" + partition.mount_as)
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
            os.system(
                "echo \"#### Static Filesystem Table File\" > /target/etc/fstab")
        fstab = open("/target/etc/fstab", "a")
        fstab.write("proc\t/proc\tproc\tdefaults\t0\t0\n")
        if self.setup.automated:
            if self.setup.lvm:
                # Don't use UUIDs with LVM
                fstab.write("%s /  ext4 defaults 0 1\n" %
                            self.auto_root_partition)
                fstab.write("%s none   swap sw 0 0\n" %
                            self.auto_swap_partition)
            else:
                fstab.write("# %s\n" % self.auto_root_partition)
                fstab.write("%s /  ext4 defaults 0 1\n" %
                            self.get_blkid(self.auto_root_partition))
                fstab.write("# %s\n" % self.auto_swap_partition)
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
                if (partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "None"):
                    fstab.write("# %s\n" % (partition.path))
                    if(partition.mount_as == "/"):
                        fstab_fsck_option = "1"
                    else:
                        fstab_fsck_option = "0"

                    if("ext" in partition.type):
                        fstab_mount_options = "defaults,rw"
                    else:
                        fstab_mount_options = "defaults"

                    if partition.type == "fat16" or partition.type == "fat32":
                        fs = "vfat"
                    else:
                        fs = partition.type

                    partition_uuid = self.get_blkid(partition.path)
                    if(fs == "swap"):
                        fstab.write("%s\tswap\tswap\tsw\t0\t0\n" %
                                    partition_uuid)
                    else:
                        fstab.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (
                            partition_uuid, partition.mount_as, fs, fstab_mount_options, "0", fstab_fsck_option))
            fstab.close()

        if self.setup.lvm:
            os.system("grep -v swap /target/etc/fstab > /target/etc/mtab")

        if self.setup.luks:
            os.system("echo 'lvmlmde   %s   none   luks,tries=3' >> /target/etc/crypttab" %
                      self.auto_root_physical_partition)

    def finish_installation(self):
        # Steps:
        our_total = 11
        our_current = 4

        # write host+hostname infos
        log(" --> Writing hostname")
        our_current += 1
        self.update_progress(our_current, our_total, False,
                             False, _("Setting hostname"))
        hostnamefh = open("/target/etc/hostname", "w")
        hostnamefh.write("%s\n" % self.setup.hostname)
        hostnamefh.close()
        hostsfh = open("/target/etc/hosts", "w")
        hostsfh.write("127.0.0.1\tlocalhost\n")
        hostsfh.write("127.0.1.1\t%s\n" % self.setup.hostname)
        hostsfh.write(
            "# The following lines are desirable for IPv6 capable hosts\n")
        hostsfh.write("::1     localhost ip6-localhost ip6-loopback\n")
        hostsfh.write("fe00::0 ip6-localnet\n")
        hostsfh.write("ff00::0 ip6-mcastprefix\n")
        hostsfh.write("ff02::1 ip6-allnodes\n")
        hostsfh.write("ff02::2 ip6-allrouters\n")
        hostsfh.write("ff02::3 ip6-allhosts\n")
        hostsfh.close()

        # set the locale
        log(" --> Setting the locale")
        our_current += 1
        self.update_progress(our_current, our_total, False,
                             False, _("Setting locale"))
        os.system("echo \"%s.UTF-8 UTF-8\" >> /target/etc/locale.gen" %
                  self.setup.language)
        self.do_run_in_chroot("locale-gen")
        os.system("echo \"\" > /target/etc/default/locale")
        self.do_run_in_chroot(
            "localectl set-locale LANG=\"%s.UTF-8\"" % self.setup.language)
        self.do_run_in_chroot(
            "localectl set-locale LANG=%s.UTF-8" % self.setup.language)
        open("/target/etc/locale.conf", "w").write("LANG=%s.UTF-8" %
                                                   self.setup.language)
        # set the locale for gentoo / sulin 
        if os.path.exists("/target/etc/env.d"):
            l = open("/target/etc/env.d/20language","w")
            l.write("LANG={}.UTF-8".format(self.setup.language))
            l.write("LC_ALL={}.UTF-8".format(self.setup.language))
            l.flush()
            l.close()
            self.do_run_in_chroot("env-update")

        # set the timezone
        log(" --> Setting the timezone")
        os.system("echo \"%s\" > /target/etc/timezone" % self.setup.timezone)
        os.system("rm -f /target/etc/localtime")
        os.system("ln -s /usr/share/zoneinfo/%s /target/etc/localtime" %
                  self.setup.timezone)

        # Keyboard settings X11
        if not self.setup.keyboard_variant:
            self.setup.keyboard_variant = ""
        self.update_progress(our_current, our_total, False,
                             False, ("Settings X11 keyboard options"))
        if os.path.exists("/target/etc/X11/xorg.conf.d"):
            newconsolefh = open(
                "/target/etc/X11/xorg.conf.d/10-keyboard.conf", "w")
        else:
            newconsolefh = open(
                "/target/usr/share/X11/xorg.conf.d/10-keyboard.conf", "w")
        newconsolefh.write('Section "InputClass"\n')
        newconsolefh.write('Identifier "system-keyboard"\n')
        newconsolefh.write('MatchIsKeyboard "on"\n')
        newconsolefh.write('Option "XkbLayout" "{}"\n'.format(
            self.setup.keyboard_layout))
        newconsolefh.write('Option "XkbModel" "{}"\n'.format(
            self.setup.keyboard_model))
        newconsolefh.write('Option "XkbVariant" "{}"\n'.format(
            self.setup.keyboard_variant))
        newconsolefh.write('#Option "XkbOptions" "grp:alt_shift_toggle"\n')
        newconsolefh.write('EndSection\n')
        newconsolefh.close()

        # set the keyboard options..
        log(" --> Setting the keyboard")
        our_current += 1
        self.update_progress(our_current, our_total, False,
                             False, _("Setting keyboard options"))
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
            self.do_run_in_chroot("rm /etc/default/console-setup")
            self.do_run_in_chroot(
                "mv /etc/default/console-setup.new /etc/default/console-setup")

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
            self.do_run_in_chroot("rm /etc/vconsole.conf")
            self.do_run_in_chroot(
                "mv /etc/vconsole.conf.new /etc/vconsole.conf")

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
                    newconsolefh.write("XKBOPTIONS=grp:ctrls_toggle")
                else:
                    newconsolefh.write("%s\n" % line)
            consolefh.close()
            newconsolefh.close()
            self.do_run_in_chroot("rm /etc/default/keyboard")
            self.do_run_in_chroot(
                "mv /etc/default/keyboard.new /etc/default/keyboard")

        # Keyboard settings openrc
        if os.path.exists("/target/etc/conf.d/keymaps"):
            newconsolefh = open("/target/etc/conf.d/keymaps", "w")
            if not self.setup.keyboard_layout:
                self.setup.keyboard_layout = "en"
            newconsolefh.write("keymap=\"{}{}\"\n".format(
                self.setup.keyboard_layout, self.setup.keyboard_variant))
            newconsolefh.close()

        # remove pacman
        self.update_progress(our_current, our_total, False,
                             False, _("Clearing package manager"))
        log(" --> Clearing package manager")
        log(config.get("remove_packages",["17g-installer"]))
        self.do_run_in_chroot("yes | {}".format(config.package_manager(
            "remove_package_with_unusing_deps", config.get("remove_packages",["17g-installer"]))))

        if self.setup.luks:
            with open("/target/etc/default/grub.d/61_live-installer.cfg", "w") as f:
                f.write("#! /bin/sh\n")
                f.write("set -e\n\n")
                f.write('GRUB_CMDLINE_LINUX="cryptdevice=%s:lvmlmde root=/dev/mapper/lvmlmde-root resume=/dev/mapper/lvmlmde-swap"\n' %
                        self.auto_root_physical_partition)
            self.do_run_in_chroot(
                "echo \"power/disk = shutdown\" >> /etc/sysfs.d/local.conf")

        # recreate initramfs (needed in case of skip_mount also, to include things like mdadm/dm-crypt/etc in case its needed to boot a custom install)
        log(" --> Configuring Initramfs")
        self.update_progress(our_current, our_total, False,
                             False, _("Generating initramfs"))
        our_current += 1

        for command in config.update_initramfs():
            self.do_run_in_chroot(command)

        try:
            grub_prepare_commands = config.distro["grub_prepare"]
            for command in grub_prepare_commands:
                os.system(command)
        except:
            err("Grub prepare process not available for your distribution!")

        # install GRUB bootloader (EFI & Legacy)
        log(" --> Configuring Grub")
        our_current += 1
        if(self.setup.grub_device is not None):
            self.update_progress(our_current, our_total,
                                 False, False, _("Installing bootloader"))
            log(" --> Running grub-install")

            if os.path.exists("/sys/firmware/efi"):
                grub_cmd = config.distro["grub_installation_efi"].split("|")
                if "{distro_codename}" in grub_cmd[1]:
                    if grub_cmd[0] == "chroot":
                        self.do_run_in_chroot(grub_cmd[1].replace(
                            "{distro_codename}", config.get("distro_codename", "17g")))
                    else:
                        os.system(grub_cmd[1].replace(
                            "{distro_codename}", config.get("distro_codename", "17g")))
                else:
                    if grub_cmd[0] == "chroot":
                        self.do_run_in_chroot(grub_cmd[1])
                    else:
                        os.system(grub_cmd[1])
            else:
                self.do_run_in_chroot(
                    "grub-install --force %s" % self.setup.grub_device)

            # fix not add windows grub entry
            self.do_run_in_chroot("grub-mkconfig -o /boot/grub/grub.cfg")
            self.do_configure_grub(our_total, our_current)
            grub_retries = 0
            while (not self.do_check_grub(our_total, our_current)):
                self.do_configure_grub(our_total, our_current)
                grub_retries = grub_retries + 1
                if grub_retries >= 5:
                    self.error_message(message=_(
                        "WARNING: The grub bootloader was not configured properly! You need to configure it manually."))
                    break

        # Custom commands
        self.do_post_install_commands(our_total, our_current)

        # now unmount it
        log(" --> Unmounting partitions")
        os.system("umount --force /target/dev/shm")
        os.system("umount --force /target/dev/pts")
        if self.setup.gptonefi:
            os.system("umount --force /target/boot/efi")
            os.system("umount --force /target/media/cdrom")
        os.system("umount --force /target/boot")
        os.system("umount --force /target/dev/")
        os.system("umount --force /target/sys/")
        os.system("umount --force /target/proc/")
        os.system("umount --force /target/run/")
        os.system("rm -f /target/etc/resolv.conf")
        os.system("mv /target/etc/resolv.conf.bk /target/etc/resolv.conf")
        for partition in self.setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                self.do_unmount("/target" + partition.mount_as)
        self.do_unmount("/target")
        self.do_unmount("/source")

        self.update_progress(0, 0, False, True, _("Installation finished"))
        log(" --> All done")

    def do_run_in_chroot(self, command, vital=False):
        command = command.replace('"', "'").strip()
        log("chroot /target/ /bin/sh -c \"%s\"" % command)
        if 0 != os.system("chroot /target/ /bin/sh -c \"%s\"" % command) and vital:
            self.error_message(message=command)

    def do_configure_grub(self, our_total, our_current):
        self.update_progress(our_current, our_total, True,
                             False, _("Configuring bootloader"))
        log(" --> Running grub-mkconfig")
        grub_output = subprocess.getoutput(
            "chroot /target/ /bin/sh -c \"grub-mkconfig -o /boot/grub/grub.cfg\"")
        log("grub_output")

    def do_post_install_commands(self, our_total, our_current):
        self.update_progress(our_current, our_total, True,
                             False, _("Post install commands running"))
        log(" --> Post install commands running")
        for command in config.get("post_install_commands", []):
            self.do_run_in_chroot(command)

    def do_check_grub(self, our_total, our_current):
        self.update_progress(our_current, our_total, True,
                             False, _("Checking bootloader"))
        log(" --> Checking Grub configuration")
        if os.path.exists("/target/boot/grub/grub.cfg"):
            return True
        else:
            err("!No /target/boot/grub/grub.cfg file found!")
            return False

    def do_mount(self, device, dest, typevar, options=None):
        ''' Mount a filesystem '''
        if(options is not None):
            cmd = "mount -o %s -t %s %s %s" % (options, typevar, device, dest)
        else:
            cmd = "mount -t %s %s %s" % (typevar, device, dest)
        return run(cmd)

    def do_unmount(self, mountpoint):
        ''' Unmount a filesystem '''
        return run("umount %s" % mountpoint)

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
    disk = None
    diskname = None
    passphrase1 = None
    passphrase2 = None
    lvm = False
    luks = False
    badblocks = False
    target_disk = None
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
