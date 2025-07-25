import os
import re
import subprocess
import time
import shutil
import gettext
import sys
import parted
import partitioning
import shlex
from functools import cmp_to_key

gettext.install("live-installer", "/usr/share/locale")

class InstallerEngine:
    ''' This is central to the live installer '''

    def __init__(self, setup):
        self.setup = setup
        if self.setup.is_mint:
            self.casper = "/cdrom/casper"
            self.pool = "/cdrom/pool"
            self.manifest = "/cdrom/casper/filesystem.manifest"
        else:
            self.casper = "/run/live/medium/live"
            self.pool = "/run/live/medium/pool"
            self.manifest = "/run/live/medium/live/filesystem.packages"

    def set_progress_hook(self, progresshook):
        ''' Set a callback to be called on progress updates '''
        ''' i.e. def my_callback(progress_type, message, current_progress, total) '''
        ''' Where progress_type is any off PROGRESS_START, PROGRESS_UPDATE, PROGRESS_COMPLETE, PROGRESS_ERROR '''
        self.update_progress = progresshook

    def set_error_hook(self, errorhook):
        ''' Set a callback to be called on errors '''
        self.error_message = errorhook

    def setup_user(self):
        print(" --> Adding new user")
        if self.setup.ecryptfs:
            # ecryptfs looks for the /sys mount point in /etc/mtab.. which doesn't exist during the installation.
            # it defaults to /sys anyway, so we just need to create an empty /etc/mtab file at this stage.
            self.do_run_in_chroot('touch /etc/mtab')
            self.do_run_in_chroot('modprobe ecryptfs')
            self.do_run_in_chroot('adduser --disabled-password --encrypt-home --gecos "{real_name}" {username}'.format(real_name=self.setup.real_name.replace('"', r'\"'), username=self.setup.username))
        else:
            self.do_run_in_chroot('adduser --disabled-password --gecos "{real_name}" {username}'.format(real_name=self.setup.real_name.replace('"', r'\"'), username=self.setup.username))
        for group in 'adm audio bluetooth cdrom dialout dip fax floppy fuse lpadmin netdev plugdev powerdev sambashare scanner sudo tape users vboxusers video'.split():
            self.do_run_in_chroot("adduser {user} {group}".format(user=self.setup.username, group=group))

        fp = open("/target/dev/shm/.passwd", "w")
        fp.write(self.setup.username +  ":" + self.setup.password1 + "\n")
        fp.close()
        self.do_run_in_chroot("cat /dev/shm/.passwd | chpasswd")
        os.system("rm -f /target/dev/shm/.passwd")

        # Set autologin
        if self.setup.oem_mode:
            os.system("cp /usr/share/live-installer/lightdm-oem-config.conf /target/etc/lightdm/lightdm.conf.d/90-oem-config.conf")
        elif self.setup.autologin:
            self.do_run_in_chroot(r"sed -i -r 's/^#?(autologin-user)\s*=.*/\1={user}/' /etc/lightdm/lightdm.conf".format(user=self.setup.username))

        # In OEM mode, set the GTK themes (since oem_config will run without a settings daemon)
        if self.setup.oem_mode:
            self.do_run_in_chroot("mkdir -p /home/oem/.config/gtk-3.0")
            self.do_run_in_chroot("echo '[Settings]' > /home/oem/.config/gtk-3.0/settings.ini")
            self.do_run_in_chroot("echo 'gtk-theme-name=Mint-Y-Aqua' >> /home/oem/.config/gtk-3.0/settings.ini")
            self.do_run_in_chroot("echo 'gtk-icon-theme-name=Mint-Y-Sand' >> /home/oem/.config/gtk-3.0/settings.ini")
            self.do_run_in_chroot("mkdir -p /root/.config/gtk-3.0")
            self.do_run_in_chroot("cp /home/oem/.config/gtk-3.0/settings.ini /root/.config/gtk-3.0/")

    def setup_hostname(self):
        print(" --> Writing hostname")
        hostnamefh = open("/target/etc/hostname", "w")
        hostnamefh.write("%s\n" % self.setup.hostname)
        hostnamefh.close()
        hostsfh = open("/target/etc/hosts", "w")
        hostsfh.write("127.0.0.1\tlocalhost\n")
        hostsfh.write("127.0.1.1\t%s\n" % self.setup.hostname)
        hostsfh.write("# The following lines are desirable for IPv6 capable hosts\n")
        hostsfh.write("::1     localhost ip6-localhost ip6-loopback\n")
        hostsfh.write("fe00::0 ip6-localnet\n")
        hostsfh.write("ff00::0 ip6-mcastprefix\n")
        hostsfh.write("ff02::1 ip6-allnodes\n")
        hostsfh.write("ff02::2 ip6-allrouters\n")
        hostsfh.write("ff02::3 ip6-allhosts\n")
        hostsfh.close()

    def setup_locale(self):
        print(" --> Setting the locale")
        os.system("echo \"%s.UTF-8 UTF-8\" >> /target/etc/locale.gen" % self.setup.language)
        self.do_run_in_chroot("locale-gen")
        os.system("echo \"\" > /target/etc/default/locale")
        self.do_run_in_chroot("update-locale LANG=\"%s.UTF-8\"" % self.setup.language)
        self.do_run_in_chroot("update-locale LANG=%s.UTF-8" % self.setup.language)

    def setup_timezone(self):
        print(" --> Setting the timezone")
        os.system("echo \"%s\" > /target/etc/timezone" % self.setup.timezone)
        os.system("rm -f /target/etc/localtime")
        os.system("ln -s /usr/share/zoneinfo/%s /target/etc/localtime" % self.setup.timezone)

    def setup_localization(self):
        print(" --> Localizing packages")
        if self.setup.oem_mode:
            # Copy the l10n pkgs from the ISO to the target so oem-config can install from it later on.
            os.system("mkdir -p /target/oem/l10n_pkgs")
            l10ns = subprocess.getoutput(f"find {self.pool} | grep 'l10n-\\|hunspell-'")
            for l10n in l10ns.split("\n"):
                os.system(f"cp {l10n} /target/oem/l10n_pkgs/")
        elif self.setup.language != "en_US":
            os.system("mkdir -p /target/debs")
            language_code = self.setup.language
            if "_" in self.setup.language:
                language_code = self.setup.language.split("_")[0]
            if self.setup.oem_config:
                l10ns = subprocess.getoutput("find /oem/l10n_pkgs | grep 'l10n-%s\\|hunspell-%s'" % (language_code, language_code))
            else:
                l10ns = subprocess.getoutput(f"find {self.pool} | grep 'l10n-%s\\|hunspell-%s'" % (language_code, language_code))
            for l10n in l10ns.split("\n"):
                os.system("cp %s /target/debs/" % l10n)
            self.do_run_in_chroot("dpkg -i /debs/*")
            os.system("rm -rf /target/debs")

    def setup_keyboard(self):
        print(" --> Setting the keyboard")
        consolefh = open("/target/etc/default/console-setup", "r")
        newconsolefh = open("/target/etc/default/console-setup.new", "w")
        for line in consolefh:
            line = line.rstrip("\r\n")
            if(line.startswith("XKBMODEL=")):
                newconsolefh.write("XKBMODEL=\"%s\"\n" % self.setup.keyboard_model)
            elif(line.startswith("XKBLAYOUT=")):
                newconsolefh.write("XKBLAYOUT=\"%s\"\n" % self.setup.keyboard_layout)
            elif(line.startswith("XKBVARIANT=") and self.setup.keyboard_variant is not None and self.setup.keyboard_variant != ""):
                newconsolefh.write("XKBVARIANT=\"%s\"\n" % self.setup.keyboard_variant)
            else:
                newconsolefh.write("%s\n" % line)
        consolefh.close()
        newconsolefh.close()
        self.do_run_in_chroot("rm /etc/default/console-setup")
        self.do_run_in_chroot("mv /etc/default/console-setup.new /etc/default/console-setup")

        consolefh = open("/target/etc/default/keyboard", "r")
        newconsolefh = open("/target/etc/default/keyboard.new", "w")
        for line in consolefh:
            line = line.rstrip("\r\n")
            if(line.startswith("XKBMODEL=")):
                newconsolefh.write("XKBMODEL=\"%s\"\n" % self.setup.keyboard_model)
            elif(line.startswith("XKBLAYOUT=")):
                newconsolefh.write("XKBLAYOUT=\"%s\"\n" % self.setup.keyboard_layout)
            elif(line.startswith("XKBVARIANT=") and self.setup.keyboard_variant is not None and self.setup.keyboard_variant != ""):
                newconsolefh.write("XKBVARIANT=\"%s\"\n" % self.setup.keyboard_variant)
            elif(line.startswith("XKBOPTIONS=")):
                newconsolefh.write("XKBOPTIONS=grp:win_space_toggle")
            else:
                newconsolefh.write("%s\n" % line)
        consolefh.close()
        newconsolefh.close()
        self.do_run_in_chroot("rm /etc/default/keyboard")
        self.do_run_in_chroot("mv /etc/default/keyboard.new /etc/default/keyboard")

    def clean_apt(self):
        print(" --> Cleaning APT")
        os.system("chroot /target/ /bin/sh -c \"dpkg --configure -a\"")
        self.do_run_in_chroot("sed -i 's/^deb cdrom/#deb cdrom/' /etc/apt/sources.list")
        self.do_run_in_chroot("apt-get -y --force-yes autoremove")

    def perform_oem_config(self):
        print(" --> Performing OEM config")

        # Setup a /target -> / link, this is so we can reuse code used for the live installation.
        os.system("ln -s / /target")

        self.update_progress(10, False, False, _("Adding new user to the system"))
        self.setup_user()

        self.update_progress(20, False, False, _("Setting hostname"))
        self.setup_hostname()

        self.update_progress(30, False, False, _("Setting locale"))
        self.setup_locale()

        self.update_progress(40, False, False, _("Setting timezone"))
        self.setup_timezone()

        self.update_progress(50, False, False, _("Localizing packages"))
        self.setup_localization()

        self.update_progress(60, False, False, _("Setting keyboard options"))
        self.setup_keyboard()

        self.update_progress(70, True, False, _("Cleaning APT"))
        self.clean_apt()

        # OEM Config cleanup
        print(" --> Cleaning up OEM config")
        self.update_progress(80, True, False, _("Cleaning OEM configuration"))
        os.system("rm -f /etc/lightdm/lightdm.conf.d/90-oem-config.conf")
        os.system("rm -rf /root/.config/gtk-3.0")
        os.system("apt-get remove --purge --yes --force-yes `cat /oem/live-packages.list`")
        os.system("rm -f /oem/live-packages.list")
        os.system("rm -f /target")
        os.system("touch /oem/done.flag") # Leave a flag for mintsystem to clean up the OEM user account

        self.update_progress(100, False, True, _("Installation finished"))
        print(" --> All done")


    def start_installation(self):

        # mount the media location.
        print(" --> Installation started")
        if(not os.path.exists("/target")):
            if (self.setup.skip_mount):
                self.error_message(message=_("ERROR: You must first manually mount your target filesystem(s) at /target to do a custom install!"))
                return
            os.mkdir("/target")
        if(not os.path.exists("/source")):
            os.mkdir("/source")

        os.system("umount --force /target/sys/firmware/efi/efivars")
        os.system("umount --force /target/dev/shm")
        os.system("umount --force /target/dev/pts")
        os.system("umount --force /target/dev/")
        os.system("umount --force /target/sys/")
        os.system("umount --force /target/proc/")
        os.system("umount --force /target/run/")

        # Mount the installation media
        print(" --> Mounting partitions")
        self.update_progress(50, False, False, _("Mounting partitions..."))
        media = f"{self.casper}/filesystem.squashfs"
        if not os.path.exists(media) and not __debug__:
            self.error_message(message=_("ERROR: Live medium not found!"))
            return
        print(" ------ Mounting %s on %s" % (media, "/source/"))
        self.do_mount(media, "/source/", "squashfs", options="loop")

        def rootiest_first(parta, partb):
            if parta.mount_as == None or parta.mount_as == "" or \
               partb.mount_as == None or partb.mount_as == "":
                return 0

            if len(parta.mount_as) > len(partb.mount_as):
                return 1
            elif len(partb.mount_as) > len(parta.mount_as):
                return -1
            else:
                return 0

        self.setup.partitions.sort(key=cmp_to_key(rootiest_first))

        print("********** Partition mount order ***")
        for part in self.setup.partitions:
            print(part.mount_as)
        print("************************************")

        if (not self.setup.skip_mount):
            if self.setup.automated:
                self.create_partitions()
            else:
                self.format_partitions()
                self.mount_partitions()

        # Transfer the files
        SOURCE = "/source/"
        DEST = "/target/"
        EXCLUDE_DIRS = "home/* dev/* proc/* sys/* tmp/* run/* mnt/* media/* lost+found source target".split()
        num_copied = 0
        # (Valid) assumption: num-of-files-to-copy ~= num-of-used-inodes-on-/
        num_files = int(subprocess.getoutput("df --inodes /{src} | awk 'END{{ print $3 }}'".format(src=SOURCE.strip('/'))))
        print(f" --> Copying {num_files} files")
        rsync_filter = ' '.join('--exclude=' + SOURCE + d for d in EXCLUDE_DIRS)
        rsync = subprocess.Popen("rsync --verbose --archive --no-D --acls "
                                 "--hard-links --xattrs {rsync_filter} "
                                 "{src}* {dst}".format(src=SOURCE, dst=DEST, rsync_filter=rsync_filter),
                                 shell=True, encoding='utf-8', errors='ignore', stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        while rsync.poll() is None:
            line = rsync.stdout.readline()
            if not line:  # still copying the previous file, just wait
                time.sleep(0.1)
            else:
                num_copied = min(num_copied + 1, num_files)
                percentage = int(float(num_copied) / float(num_files) * 100)
                self.update_progress(percentage, False, False, _("Copying files..."))
        print("rsync exited with returncode: " + str(rsync.poll()))

        # chroot
        print(" --> Chrooting")
        self.update_progress(10, False, False, _("Entering the system..."))
        os.system("mount --bind /dev/ /target/dev/")
        os.system("mount --bind /dev/shm /target/dev/shm")
        os.system("mount --bind /dev/pts /target/dev/pts")
        os.system("mount --bind /sys/ /target/sys/")
        os.system("mount --bind /proc/ /target/proc/")
        os.system("mount --bind /run/ /target/run/")
        os.system("mv /target/etc/resolv.conf /target/etc/resolv.conf.bk")
        os.system("cp -f /etc/resolv.conf /target/etc/resolv.conf")

        if os.path.exists("/sys/firmware/efi/efivars"):
            os.system("mkdir -p /target/sys/firmware/efi/efivars")
            os.system("mount --bind /sys/firmware/efi/efivars /target/sys/firmware/efi/efivars/")

        kernelversion= subprocess.getoutput("uname -r")
        os.system(f"cp {self.casper}/vmlinuz /target/boot/vmlinuz-{kernelversion}")
        found_initrd = False
        for initrd in [f"{self.casper}/initrd.img", f"{self.casper}/initrd.lz"]:
            if os.path.exists(initrd):
                os.system("cp %s /target/boot/initrd.img-%s" % (initrd, kernelversion))
                found_initrd = True
                break

        if not found_initrd:
            print("WARNING: No initrd found!!")

        if self.setup.grub_device and self.setup.gptonefi:
            print(" --> Installing signed boot loader")
            os.system("mkdir -p /target/debs")
            os.system(f"cp {self.pool}/main/g/grub2/grub-efi* /target/debs/")
            os.system(f"cp {self.pool}/main/g/grub-efi-amd64-signed/* /target/debs/")
            os.system(f"cp {self.pool}/main/s/shim*/* /target/debs/")
            self.do_run_in_chroot("DEBIAN_FRONTEND=noninteractive apt-get remove --purge --yes grub-pc")
            self.do_run_in_chroot("dpkg -i /debs/*")
            os.system("rm -rf /target/debs")

        # Prepare directory for oem_config
        if self.setup.oem_mode:
            os.system("mkdir -p /target/oem/")

        # remove live-packages (or w/e)
        print(" --> Removing live packages")
        self.update_progress(20, False, False, _("Removing live configuration (packages)"))
        with open(f"{self.manifest}-remove", "r") as fd:
            line = fd.read().replace('\n', ' ')
        if self.setup.oem_mode:
            # in OEM mode don't remove pkgs just yet
            self.do_run_in_chroot(f'echo "{line}" > /oem/live-packages.list')
            self.do_run_in_chroot("rm -f /usr/share/applications/debian-installer-launcher.desktop")
        else:
            self.do_run_in_chroot("apt-get remove --purge --yes --force-yes %s" % line)

        # remove live leftovers
        self.do_run_in_chroot("rm -rf /etc/live")
        self.do_run_in_chroot("rm -rf /lib/live")
        self.do_run_in_chroot("rm -rf /etc/casper*")
        self.do_run_in_chroot("rm -rf /lib/casper")
        self.do_run_in_chroot("rm -rf /usr/lib/casper")
        self.do_run_in_chroot("rm -rf /usr/share/casper")

        # add new user
        self.update_progress(30, False, False, _("Adding new user to the system"))
        self.setup_user()

        # Lock and delete root password
        self.do_run_in_chroot("passwd -dl root")

        # Set LightDM to show user list by default
        self.do_run_in_chroot(r"sed -i -r 's/^#?(greeter-hide-users)\s*=.*/\1=false/' /etc/lightdm/lightdm.conf")

        # /etc/fstab, mtab and crypttab
        self.update_progress(40, False, False, _("Writing filesystem mount information to /etc/fstab"))
        self.write_fstab()
        self.write_mtab()
        self.write_crypttab()

    def create_partitions(self):
        # Create partitions on the selected disk (automated installation)
        partition_prefix = partitioning.get_device_naming_scheme_prefix(self.setup.disk)
        if self.setup.luks:
            if self.setup.gptonefi:
                # EFI+LUKS/LVM
                # sdx1=EFI, sdx2=BOOT, sdx3=ROOT
                self.auto_efi_partition  = self.setup.disk + partition_prefix + "1"
                self.auto_boot_partition = self.setup.disk + partition_prefix + "2"
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "3"
            else:
                # BIOS+LUKS/LVM
                # sdx1=BOOT, sdx2=ROOT
                self.auto_efi_partition  = None
                self.auto_boot_partition = self.setup.disk + partition_prefix + "1"
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "2"
        elif self.setup.lvm:
            if self.setup.gptonefi:
                # EFI+LVM
                # sdx1=EFI, sdx2=ROOT
                self.auto_efi_partition  = self.setup.disk + partition_prefix + "1"
                self.auto_boot_partition = None
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "2"
            else:
                # BIOS+LVM:
                # sdx1=ROOT
                self.auto_efi_partition  = None
                self.auto_boot_partition = None
                self.auto_swap_partition = None
                self.auto_root_partition = self.setup.disk + partition_prefix + "1"
        else:
            if self.setup.gptonefi:
                # EFI
                # sdx1=EFI, sdx2=SWAP, sdx3=ROOT
                self.auto_efi_partition  = self.setup.disk + partition_prefix + "1"
                self.auto_boot_partition = None
                self.auto_swap_partition = self.setup.disk + partition_prefix + "2"
                self.auto_root_partition = self.setup.disk + partition_prefix + "3"
            else:
                # BIOS:
                # sdx1=SWAP, sdx2=ROOT
                self.auto_efi_partition  = None
                self.auto_boot_partition = None
                self.auto_swap_partition = self.setup.disk + partition_prefix + "1"
                self.auto_root_partition = self.setup.disk + partition_prefix + "2"

        self.auto_root_physical_partition = self.auto_root_partition

        # Wipe HDD
        if self.setup.badblocks:
            self.update_progress(25, False, False, _("Filling disk with random data (please be patient, this can take hours...)"))
            print(" --> Filling %s with random data" % self.setup.disk)
            os.system("badblocks -c 10240 -s -w -t random -v %s" % self.setup.disk)

        # Create partitions
        self.update_progress(25, False, False, _("Creating partitions on %s") % self.setup.disk)
        print(" --> Creating partitions on %s" % self.setup.disk)
        disk_device = parted.getDevice(self.setup.disk)
        partitioning.full_disk_format(disk_device, create_boot=(self.auto_boot_partition is not None), create_swap=(self.auto_swap_partition is not None))

        # Encrypt root partition
        if self.setup.luks:
            print(" --> Encrypting root partition %s" % self.auto_root_partition)
            os.system("echo -n %s | cryptsetup luksFormat -c aes-xts-plain64 -h sha256 -s 512 %s" % (shlex.quote(self.setup.passphrase1), self.auto_root_partition))
            print(" --> Opening root partition %s" % self.auto_root_partition)
            os.system("echo -n %s | cryptsetup luksOpen %s lvmlmde" % (shlex.quote(self.setup.passphrase1), self.auto_root_partition))
            self.auto_root_partition = "/dev/mapper/lvmlmde"

        # Setup LVM
        if self.setup.lvm:
            print(" --> LVM: Creating PV")
            os.system("pvcreate -y %s" % self.auto_root_partition)
            print(" --> LVM: Creating VG")
            os.system("vgcreate -y lvmlmde %s" % self.auto_root_partition)
            print(" --> LVM: Creating LV root")
            os.system("lvcreate -y -n root -L 1GB lvmlmde")
            print(" --> LVM: Creating LV swap")
            swap_size = int(round(int(subprocess.getoutput("awk '/^MemTotal/{ print $2 }' /proc/meminfo")) / 1024, 0))
            os.system("lvcreate -y -n swap -L %dMB lvmlmde" % swap_size)
            print(" --> LVM: Extending LV root")
            os.system("lvextend -l 100\%FREE /dev/lvmlmde/root")
            print(" --> LVM: Formatting LV root")
            os.system("mkfs.ext4 /dev/mapper/lvmlmde-root -FF")
            print(" --> LVM: Formatting LV swap")
            os.system("mkswap -f /dev/mapper/lvmlmde-swap")
            print(" --> LVM: Enabling LV swap")
            os.system("swapon /dev/mapper/lvmlmde-swap")
            self.auto_root_partition = "/dev/mapper/lvmlmde-root"
            self.auto_swap_partition = "/dev/mapper/lvmlmde-swap"

        self.do_mount(self.auto_root_partition, "/target", "ext4", None)
        if (self.auto_boot_partition is not None):
            os.system("mkdir -p /target/boot")
            self.do_mount(self.auto_boot_partition, "/target/boot", "ext4", None)
        if (self.auto_efi_partition is not None):
            os.system("mkdir -p /target/boot/efi")
            self.do_mount(self.auto_efi_partition, "/target/boot/efi", "vfat", None)

    def format_partitions(self):
        for partition in self.setup.partitions:
            if(partition.format_as is not None and partition.format_as != ""):
                # report it. should grab the total count of filesystems to be formatted ..
                self.update_progress(25, True, False, _("Formatting partitions..."))

                #Format it
                if partition.format_as == "swap":
                    cmd = "mkswap %s" % partition.path
                else:
                    if (partition.format_as in ['ext2', 'ext3', 'ext4']):
                        cmd = "mkfs.%s -F %s" % (partition.format_as, partition.path)
                    elif (partition.format_as == "jfs"):
                        cmd = "mkfs.%s -q %s" % (partition.format_as, partition.path)
                    elif (partition.format_as in ["btrfs", "xfs"]):
                        cmd = "mkfs.%s -f %s" % (partition.format_as, partition.path)
                    elif (partition.format_as == "vfat"):
                        cmd = "mkfs.%s %s -F 32" % (partition.format_as, partition.path)
                    else:
                        cmd = "mkfs.%s %s" % (partition.format_as, partition.path) # works with bfs, minix, msdos, ntfs, vfat

                print("EXECUTING: '%s'" % cmd)
                self.exec_cmd(cmd)
                partition.type = partition.format_as

    def mount_partitions(self):
        # Mount the target partition
        for partition in self.setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != ""):
                if partition.mount_as == "/":
                    self.update_progress(75, False, False, _("Mounting %(partition)s on %(mountpoint)s") % {'partition':partition.path, 'mountpoint':"/target/"})
                    print(" ------ Mounting partition %s on %s" % (partition.path, "/target/"))
                    fs = partition.type
                    if fs == "fat32":
                        fs = "vfat"
                    self.do_mount(partition.path, "/target", fs, None)
                    if fs == "btrfs":
                        # Create subvolumes for Btrfs
                        os.system("btrfs subvolume create /target/@")
                        os.system("btrfs subvolume list -p /target")
                        print(" ------ Umount btrfs to remount subvolume @")
                        os.system("umount --force /target")
                        self.do_mount(partition.path, "/target", fs, "subvol=@")
                        if not self.setup_has_dedicated_home():
                            # If there is no dedicated home partition, add a @home subvolume to /
                            os.system("mkdir -p /target/home")
                            self.do_mount(partition.path, "/target/home", fs, None)
                            os.system("btrfs subvolume create /target/home/@home")
                            os.system("btrfs subvolume list -p /target/home")
                            print(" ------- Umount btrfs to remount subvolume @home")
                            os.system("umount --force /target/home")
                            self.do_mount(partition.path, "/target/home", fs, "subvol=@home")
                    break

        # Mount the other partitions
        for partition in self.setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                print(" ------ Mounting %s on %s" % (partition.path, "/target" + partition.mount_as))
                os.system("mkdir -p /target" + partition.mount_as)
                fs = partition.type
                if fs == "fat16" or fs == "fat32":
                    fs = "vfat"
                self.do_mount(partition.path, "/target" + partition.mount_as, fs, None)
                if partition.mount_as == "/home" and fs == "btrfs":
                    # Dedicated home partition with Btrfs, needs a @home subvolume
                    os.system("btrfs subvolume create /target/home/@home")
                    os.system("btrfs subvolume list -p /target/home")
                    print(" ------- Umount btrfs to remount subvolume @home")
                    os.system("umount --force /target/home")
                    self.do_mount(partition.path, "/target/home", fs, "subvol=@home")

    def get_blkid(self, path):
        uuid = path # If we can't find the UUID we use the path
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

    def setup_has_dedicated_home(self):
        # Find out if there is a dedicated home partition
        has_dedicated_home = False
        for partition in self.setup.partitions:
            if partition.mount_as == "/home":
                has_dedicated_home = True
                break
        return has_dedicated_home

    def write_fstab(self, path="/target/etc/fstab"):
        # write the /etc/fstab
        print(" --> Writing fstab")
        # make sure fstab has default /proc and /sys entries
        fstab = open(path, "w")
        fstab.write("#### Static Filesystem Table File\n")
        fstab.write("proc\t/proc\tproc\tdefaults\t0\t0\n")
        if(not self.setup.skip_mount):
            if self.setup.automated:
                fstab.write("# %s\n" % self.auto_root_partition)
                fstab.write("%s /  ext4 defaults 0 1\n" % self.get_blkid(self.auto_root_partition))
                fstab.write("# %s\n" % self.auto_swap_partition)
                fstab.write("%s none   swap sw 0 0\n" % self.get_blkid(self.auto_swap_partition))
                if (self.auto_boot_partition is not None):
                    fstab.write("# %s\n" % self.auto_boot_partition)
                    fstab.write("%s /boot  ext4 defaults 0 1\n" % self.get_blkid(self.auto_boot_partition))
                if (self.auto_efi_partition is not None):
                    fstab.write("# %s\n" % self.auto_efi_partition)
                    fstab.write("%s /boot/efi  vfat defaults 0 1\n" % self.get_blkid(self.auto_efi_partition))
            else:
                for partition in self.setup.partitions:
                    if (partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "None"):
                        fs = partition.type
                        if fs == "fat16" or fs == "fat32":
                            fs = "vfat"
                        fstab.write("# %s\n" % (partition.path))
                        fstab_fsck_option = "0"
                        if fs != "btrfs" and partition.mount_as in ["/", "/boot", "/boot/efi"]:
                            fstab_fsck_option = "1"
                        if "ext" in fs:
                            fstab_mount_options = "rw,errors=remount-ro"
                        elif fs == "btrfs" and partition.mount_as == "/":
                            fstab_mount_options = "defaults,subvol=@"
                        elif fs == "btrfs" and partition.mount_as == "/home":
                            fstab_mount_options = "defaults,subvol=@home"
                        else:
                            fstab_mount_options = "defaults"

                        partition_uuid = self.get_blkid(partition.path)
                        if fs == "swap":
                            fstab.write("%s\tswap\tswap\tsw\t0\t0\n" % partition_uuid)
                        else:
                            fstab.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (partition_uuid, partition.mount_as, fs, fstab_mount_options, "0", fstab_fsck_option))

                        if fs == "btrfs" and partition.mount_as == "/" and not self.setup_has_dedicated_home():
                            # Special case, if / is btrfs and there is no dedicated /home, add the @home subvolume to /
                            fstab.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (partition_uuid, "/home", "btrfs", "defaults,subvol=@home", "0", "0"))
        fstab.close()

    def write_mtab(self, fstab="/target/etc/fstab", mtab="/target/etc/mtab"):
        if self.setup.lvm:
            os.system(f"grep -v swap {fstab} > {mtab}")

    def write_crypttab(self, path="/target/etc/crypttab"):
        if self.setup.luks:
            os.system(f"echo 'lvmlmde   {self.get_blkid(self.auto_root_physical_partition)}   none   luks,discard,tries=3' >> {path}")

    def finish_installation(self):

        self.update_progress(50, False, False, _("Setting hostname"))
        self.setup_hostname()

        self.update_progress(55, False, False, _("Setting locale"))
        self.setup_locale()

        self.update_progress(60, False, False, _("Setting timezone"))
        self.setup_timezone()

        self.update_progress(65, False, False, _("Localizing packages"))
        self.setup_localization()

        if os.path.exists("/etc/linuxmint/info"):
            # drivers
            print(" --> Installing drivers")
            self.update_progress(70, False, False, _("Installing drivers"))

            # Broadcom
            drivers = subprocess.getoutput("mint-drivers")
            if "broadcom-sta-dkms" in drivers:
                try:
                    os.system("mkdir -p /target/debs")
                    os.system(f"cp {self.pool}/non-free/b/broadcom-sta/*.deb /target/debs/")
                    self.do_run_in_chroot("dpkg -i /debs/*")
                    self.do_run_in_chroot("modprobe wl")
                    os.system("rm -rf /target/debs")
                except:
                    print("Failed to install Broadcom drivers")

            # NVIDIA
            driver = "/usr/share/live-installer/nvidia-driver.tar.gz"
            if os.path.exists(driver):
                if "install-nvidia" in subprocess.getoutput("cat /proc/cmdline"):
                    print(" --> Installing NVIDIA driver")
                    try:
                        self.do_run_in_chroot("tar zxvf %s" % driver)
                        self.do_run_in_chroot("DEBIAN_FRONTEND=noninteractive dpkg -i --force-depends nvidia-driver/*.deb")
                        self.do_run_in_chroot("rm -rf nvidia-driver")
                    except Exception as e:
                        print("Failed to install NVIDIA driver: ", e)

                os.system("rm -f /target/usr/share/live-installer/nvidia-driver.tar.gz")

        # set the keyboard options..
        self.update_progress(75, False, False, _("Setting keyboard options"))
        self.setup_keyboard()

        # Perform OS adjustments
        if os.path.exists("/target/usr/lib/linuxmint/mintSystem/mint-adjust.py"):
            self.do_run_in_chroot("/usr/lib/linuxmint/mintSystem/mint-adjust.py")

        if self.setup.luks:
            self.do_run_in_chroot("echo aes-i586 >> /etc/initramfs-tools/modules")
            self.do_run_in_chroot("echo aes_x86_64 >> /etc/initramfs-tools/modules")
            self.do_run_in_chroot("echo dm-crypt >> /etc/initramfs-tools/modules")
            self.do_run_in_chroot("echo dm-mod >> /etc/initramfs-tools/modules")
            self.do_run_in_chroot("echo xts >> /etc/initramfs-tools/modules")
            with open("/target/etc/default/grub.d/61_live-installer.cfg", "w") as f:
                f.write("#! /bin/sh\n")
                f.write("set -e\n\n")
                f.write('GRUB_CMDLINE_LINUX="cryptdevice=%s:lvmlmde root=/dev/mapper/lvmlmde-root resume=/dev/mapper/lvmlmde-swap"\n' % self.get_blkid(self.auto_root_physical_partition))
            self.do_run_in_chroot("echo \"power/disk = shutdown\" >> /etc/sysfs.d/local.conf")

        # write MBR (grub)
        print(" --> Configuring Grub")
        if(self.setup.grub_device is not None):
            self.update_progress(80, False, False, _("Installing bootloader"))
            print(" --> Running grub-install")
            self.do_run_in_chroot("grub-install --force %s" % self.setup.grub_device)
            # Remove memtest86+ package (it provides multiple memtest unwanted grub entries)
            self.do_run_in_chroot("apt-get remove --purge --yes --force-yes memtest86+")
            #fix not add windows grub entry
            self.do_run_in_chroot("update-grub")
            self.do_configure_grub()
            grub_retries = 0
            while (not self.do_check_grub()):
                self.do_configure_grub()
                grub_retries = grub_retries + 1
                if grub_retries >= 5:
                    self.error_message(message=_("WARNING: The grub bootloader was not configured properly! You need to configure it manually."))
                    break

        # recreate initramfs (needed in case of skip_mount also, to include things like mdadm/dm-crypt/etc in case its needed to boot a custom install)
        print(" --> Configuring Initramfs")
        self.do_run_in_chroot("/usr/sbin/update-initramfs -t -u -k all")
        self.do_run_in_chroot("/usr/share/debian-system-adjustments/systemd/adjust-grub-title")
        kernelversion= subprocess.getoutput("uname -r")
        self.do_run_in_chroot("/usr/bin/sha1sum /boot/initrd.img-%s > /var/lib/initramfs-tools/%s" % (kernelversion,kernelversion))

        # Clean APT
        self.update_progress(95, True, False, _("Cleaning APT"))
        self.clean_apt()

        # now unmount it
        print(" --> Unmounting partitions")

        if os.path.exists("/target/sys/firmware/efi/efivars"):
            os.system("umount --force /target/sys/firmware/efi/efivars")

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
        if(not self.setup.skip_mount):
            for partition in self.setup.partitions:
                if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                    self.do_unmount("/target" + partition.mount_as)

            # btrfs subvolumes are mounts, but will block unmounting /target. This will
            # unmount the submounts also.
            cmd = "umount -AR /target"
            print("Unmounting the target root: '%s'" % cmd)
            self.exec_cmd(cmd)
        self.do_unmount("/source")

        self.update_progress(100, False, True, _("Installation finished"))
        print(" --> All done")

    def do_run_in_chroot(self, command):
        command = command.replace('"', "'").strip()
        print("chroot /target/ /bin/sh -c \"%s\"" % command)
        os.system("chroot /target/ /bin/sh -c \"%s\"" % command)

    def do_configure_grub(self):
        self.update_progress(85, True, False, _("Configuring bootloader"))
        print(" --> Running grub-mkconfig")
        self.do_run_in_chroot("grub-mkconfig -o /boot/grub/grub.cfg")
        grub_output = subprocess.getoutput("chroot /target/ /bin/sh -c \"grub-mkconfig -o /boot/grub/grub.cfg\"")
        grubfh = open("/var/log/live-installer-grub-output.log", "w")
        grubfh.writelines(grub_output)
        grubfh.close()

    def do_check_grub(self):
        self.update_progress(90, True, False, _("Checking bootloader"))
        print(" --> Checking Grub configuration")
        time.sleep(5)
        found_entry = False
        if os.path.exists("/target/boot/grub/grub.cfg"):
            self.do_run_in_chroot("/usr/share/debian-system-adjustments/systemd/adjust-grub-title")
            grubfh = open("/target/boot/grub/grub.cfg", "r")
            for line in grubfh:
                line = line.rstrip("\r\n")
                if ("menuentry" in line and ("class linuxmint" in line or "Linux Mint" in line or "LMDE" in line)):
                    found_entry = True
                    print(" --> Found Grub entry: %s " % line)
            grubfh.close()
            return (found_entry)
        else:
            print("!No /target/boot/grub/grub.cfg file found!")
            return False

    def do_mount(self, device, dest, type, options=None):
        ''' Mount a filesystem '''
        p = None
        if(options is not None):
            cmd = "mount -o %s -t %s %s %s" % (options, type, device, dest)
        else:
            cmd = "mount -t %s %s %s" % (type, device, dest)
        print("EXECUTING: '%s'" % cmd)
        self.exec_cmd(cmd)

    def do_unmount(self, mountpoint):
        ''' Unmount a filesystem '''
        cmd = "umount %s" % mountpoint
        print("EXECUTING: '%s'" % cmd)
        self.exec_cmd(cmd)

    # Execute schell command and return output in a list
    def exec_cmd(self, cmd):
        p = subprocess.Popen(cmd, shell=True, encoding='utf-8', errors='ignore', stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lstOut = []
        for line in p.stdout.readlines():
            # Strip the line, also from null spaces (strip() only strips white spaces)
            line = line.strip().strip("\0")
            if line != '':
                lstOut.append(line)
        return lstOut

# Represents the choices made by the user
class Setup(object):
    is_mint = False
    oem_mode = False
    language = None
    timezone = None
    keyboard_model = None
    keyboard_layout = None
    keyboard_variant = None
    partitions = [] #Array of PartitionSetup objects
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
    gptonefi = False
    # Optionally skip all mouting/partitioning for advanced users with custom setups (raid/dmcrypt/etc)
    # Make sure the user knows that they need to:
    #  * Mount their target directory structure at /target
    #  * NOT mount /target/dev, /target/dev/shm, /target/dev/pts, /target/proc, and /target/sys
    #  * Manually create /target/etc/fstab after start_installation has completed and before finish_installation is called
    #  * Install cryptsetup/dmraid/mdadm/etc in target environment (using chroot) between start_installation and finish_installation
    #  * Make sure target is mounted using the same block device as is used in /target/etc/fstab (eg if you change the name of a dm-crypt device between now and /target/etc/fstab, update-initramfs will likely fail)
    skip_mount = False

    #Descriptions (used by the summary screen)
    keyboard_model_description = None
    keyboard_layout_description = None
    keyboard_variant_description = None

    def print_setup(self):
        if __debug__:
            print("-------------------------------------------------------------------------")
            print("Is Mint: %s" % self.is_mint)
            print("OEM mode: %s" % self.oem_mode)
            print("language: %s" % self.language)
            print("timezone: %s" % self.timezone)
            print("keyboard: %s - %s (%s) - %s - %s (%s)" % (self.keyboard_model, self.keyboard_layout, self.keyboard_variant, self.keyboard_model_description, self.keyboard_layout_description, self.keyboard_variant_description))
            print("user: %s (%s)" % (self.username, self.real_name))
            print("autologin: ", self.autologin)
            print("ecryptfs: ", self.ecryptfs)
            print("hostname: %s " % self.hostname)
            print("passwords: %s - %s" % (self.password1, self.password2))
            print("grub_device: %s " % self.grub_device)
            print("skip_mount: %s" % self.skip_mount)
            print("automated: %s" % self.automated)
            if self.automated:
                print("disk: %s (%s)" % (self.disk, self.diskname))
                print("luks: %s" % self.luks)
                print("badblocks: %s" % self.badblocks)
                print("lvm: %s" % self.lvm)
                print("passphrase: %s - %s" % (self.passphrase1, self.passphrase2))
            if (not self.skip_mount):
                print("target_disk: %s " % self.target_disk)
                if self.gptonefi:
                    print("GPT partition table: True")
                else:
                    print("GPT partition table: False")
                print("disks: %s " % self.disks)
                print("partitions:")
                for partition in self.partitions:
                    partition.print_partition()
            print("-------------------------------------------------------------------------")
