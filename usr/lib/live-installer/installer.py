import os
import subprocess
import time
import shutil
import gettext
import stat
import commands
import sys
import parted
from configobj import ConfigObj

gettext.install("live-installer", "/usr/share/locale")

CONFIG_FILE = '/etc/live-installer/live-installer.conf'

class InstallerEngine:
    ''' This is central to the live installer '''

    def __init__(self):
        # Set distribution name and version
        def _get_distro_info_from(file, section, name, version):
            config = ConfigObj(file, file_error=True)
            if section:
                return config[section][name], config[section][version]
            return config[name], config[version]
        for args in (('/etc/os-release', '', 'PRETTY_NAME', 'VERSION'),
                     ('/etc/lsb-release', '', 'DISTRIB_DESCRIPTION', 'DISTRIB_RELEASE'),
                     (CONFIG_FILE, 'distribution', 'DISTRIBUTION_NAME', 'DISTRIBUTION_VERSION')):
            try: name, version = _get_distro_info_from(*args)
            except (IOError, KeyError, TypeError): continue
            else: break
        else: name, version = 'Unknown GNU/Linux', '1.0'
        self.distribution_name, self.distribution_version = name, version
        # Set other configuration
        config = ConfigObj(CONFIG_FILE)
        self.live_user = config.get('install', {}).get('live_user', 'user')
        self.media = config.get('install', {}).get('live_media_source', '/lib/live/mount/medium/live/filesystem.squashfs')
        self.media_type = config.get('install', {}).get('live_media_type', 'squashfs')
        # Flush print when it's called    
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

    def set_progress_hook(self, progresshook):
        ''' Set a callback to be called on progress updates '''
        ''' i.e. def my_callback(progress_type, message, current_progress, total) '''
        ''' Where progress_type is any off PROGRESS_START, PROGRESS_UPDATE, PROGRESS_COMPLETE, PROGRESS_ERROR '''
        self.update_progress = progresshook
        
    def set_error_hook(self, errorhook):
        ''' Set a callback to be called on errors '''
        self.error_message = errorhook

    def get_distribution_name(self):
        return self.distribution_name

    def get_distribution_version(self):
        return self.distribution_version
        
    def step_format_partitions(self, setup):        
        for partition in setup.partitions:                    
            if(partition.format_as is not None and partition.format_as != ""):                
                # report it. should grab the total count of filesystems to be formatted ..
                self.update_progress(total=4, current=1, pulse=True, message=_("Formatting %(partition)s as %(format)s ..." % {'partition':partition.partition.path, 'format':partition.format_as}))
                
                #Format it
                if partition.format_as == "swap":
                    cmd = "mkswap %s" % partition.partition.path
                else:
                    if (partition.format_as == "jfs"):
                        cmd = "mkfs.%s -q %s" % (partition.format_as, partition.partition.path)
                    elif (partition.format_as == "xfs"):
                        cmd = "mkfs.%s -f %s" % (partition.format_as, partition.partition.path)
                    elif (partition.format_as == "vfat"):
                        cmd = "mkfs.%s %s -F 32" % (partition.format_as, partition.partition.path)
                    else:
                        cmd = "mkfs.%s %s" % (partition.format_as, partition.partition.path) # works with bfs, btrfs, ext2, ext3, ext4, minix, msdos, ntfs, vfat
					
                print "EXECUTING: '%s'" % cmd
                self.exec_cmd(cmd)
                partition.type = partition.format_as

    def step_mount_source(self, setup):
        # Mount the installation media
        print " --> Mounting partitions"
        self.update_progress(total=4, current=2, message=_("Mounting %(partition)s on %(mountpoint)s") % {'partition':self.media, 'mountpoint':"/source/"})
        print " ------ Mounting %s on %s" % (self.media, "/source/")
        self.do_mount(self.media, "/source/", self.media_type, options="loop")
                                        
    def step_mount_partitions(self, setup):  
        self.step_mount_source(setup)
      
        # Mount the target partition
        for partition in setup.partitions:                    
            if(partition.mount_as is not None and partition.mount_as != ""):   
                  if partition.mount_as == "/":
                        self.update_progress(total=4, current=3, message=_("Mounting %(partition)s on %(mountpoint)s") % {'partition':partition.partition.path, 'mountpoint':"/target/"})
                        print " ------ Mounting partition %s on %s" % (partition.partition.path, "/target/")
                        if partition.type == "fat32":
                            fs = "vfat"
                        else:
                            fs = partition.type
                        self.do_mount(partition.partition.path, "/target", fs, None)
                        break
        
        # Mount the other partitions        
        for partition in setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                print " ------ Mounting %s on %s" % (partition.partition.path, "/target" + partition.mount_as)
                os.system("mkdir -p /target" + partition.mount_as)
                if partition.type == "fat16" or partition.type == "fat32":
                    fs = "vfat"
                else:
                    fs = partition.type
                self.do_mount(partition.partition.path, "/target" + partition.mount_as, fs, None)

    def init_install(self, setup):        
        # mount the media location.
        print " --> Installation started"
        if(not os.path.exists("/target")):
            if (setup.skip_mount):
                self.error_message(message=_("ERROR: You must first manually mount your target filesystem(s) at /target to do a custom install!"))
                return
            os.mkdir("/target")
        if(not os.path.exists("/source")):
            os.mkdir("/source")
        # find the squashfs..
        if(not os.path.exists(self.media)):
            print "Base filesystem does not exist! Critical error (exiting)."
            self.error_message(message=_("ERROR: Something is wrong with the installation medium! This is usually caused by burning tools which are not compatible with LMDE (YUMI or other multiboot tools). Please burn the ISO image to DVD/USB using a different tool."))
            return

        os.system("umount --force /target/dev/shm")
        os.system("umount --force /target/dev/pts")
        os.system("umount --force /target/dev/")
        os.system("umount --force /target/sys/")
        os.system("umount --force /target/proc/")

        if (not setup.skip_mount):
            self.step_format_partitions(setup)
            self.step_mount_partitions(setup)
        else:
            self.step_mount_source(setup)

        # walk root filesystem
        SOURCE = "/source/"
        DEST = "/target/"
        directory_times = []
        our_total = 0
        our_current = -1
        os.chdir(SOURCE)
        # index the files
        print " --> Indexing files"
        self.update_progress(pulse=True, message=_("Indexing files to be copied.."))
        for top,dirs,files in os.walk(SOURCE, topdown=False):
            our_total += len(dirs) + len(files)
        our_total += 1 # safenessness
        print " --> Copying files"
        for top,dirs,files in os.walk(SOURCE):
            # Sanity check. Python is a bit schitzo
            dirpath = top
            if(dirpath.startswith(SOURCE)):
                dirpath = dirpath[len(SOURCE):]
            for name in dirs + files:
                # following is hacked/copied from Ubiquity
                rpath = os.path.join(dirpath, name)
                sourcepath = os.path.join(SOURCE, rpath)
                targetpath = os.path.join(DEST, rpath)
                st = os.lstat(sourcepath)
                mode = stat.S_IMODE(st.st_mode)

                # now show the world what we're doing
                our_current += 1
                self.update_progress(total=our_total, current=our_current, message=_("Copying %s" % rpath))

                if os.path.exists(targetpath):
                    if not os.path.isdir(targetpath):
                        os.remove(targetpath)
                if stat.S_ISLNK(st.st_mode):
                    if os.path.lexists(targetpath):
                        os.unlink(targetpath)
                    linkto = os.readlink(sourcepath)
                    os.symlink(linkto, targetpath)
                elif stat.S_ISDIR(st.st_mode):
                    if not os.path.isdir(targetpath):
                        os.mkdir(targetpath, mode)
                elif stat.S_ISCHR(st.st_mode):
                    os.mknod(targetpath, stat.S_IFCHR | mode, st.st_rdev)
                elif stat.S_ISBLK(st.st_mode):
                    os.mknod(targetpath, stat.S_IFBLK | mode, st.st_rdev)
                elif stat.S_ISFIFO(st.st_mode):
                    os.mknod(targetpath, stat.S_IFIFO | mode)
                elif stat.S_ISSOCK(st.st_mode):
                    os.mknod(targetpath, stat.S_IFSOCK | mode)
                elif stat.S_ISREG(st.st_mode):
                    # we don't do blacklisting yet..
                    try:
                        os.unlink(targetpath)
                    except:
                        pass
                    self.do_copy_file(sourcepath, targetpath)
                os.lchown(targetpath, st.st_uid, st.st_gid)
                if not stat.S_ISLNK(st.st_mode):
                    os.chmod(targetpath, mode)
                if stat.S_ISDIR(st.st_mode):
                    directory_times.append((targetpath, st.st_atime, st.st_mtime))
                # os.utime() sets timestamp of target, not link
                elif not stat.S_ISLNK(st.st_mode):
                    os.utime(targetpath, (st.st_atime, st.st_mtime))
            # Apply timestamps to all directories now that the items within them
            # have been copied.
        print " --> Restoring meta-info"
        for dirtime in directory_times:
            (directory, atime, mtime) = dirtime
            try:
                self.update_progress(pulse=True, message=_("Restoring meta-information on %s" % directory))
                os.utime(directory, (atime, mtime))
            except OSError:
                pass

        # Steps:
        our_total = 11
        our_current = 0
        # chroot
        print " --> Chrooting"
        self.update_progress(total=our_total, current=our_current, message=_("Entering the system ..."))
        os.system("mount --bind /dev/ /target/dev/")
        os.system("mount --bind /dev/shm /target/dev/shm")
        os.system("mount --bind /dev/pts /target/dev/pts")
        os.system("mount --bind /sys/ /target/sys/")
        os.system("mount --bind /proc/ /target/proc/")
        os.system("mv /target/etc/resolv.conf /target/etc/resolv.conf.bk")
        os.system("cp -f /etc/resolv.conf /target/etc/resolv.conf")

        kernelversion= commands.getoutput("uname -r")
        os.system("cp /lib/live/mount/medium/live/vmlinuz /target/boot/vmlinuz-%s" % kernelversion)
        os.system("cp /lib/live/mount/medium/live/initrd.img /target/boot/initrd.img-%s" % kernelversion)

        if (setup.gptonefi):
            os.system("mkdir -p /target/boot/efi/EFI/linuxmint")
            os.system("cp /lib/live/mount/medium/EFI/BOOT/grubx64.efi /target/boot/efi/EFI/linuxmint")
            os.system("mkdir -p /target/debs")
            os.system("cp /lib/live/mount/medium/pool/main/g/grub2/grub-efi* /target/debs/")
            os.system("cp /lib/live/mount/medium/pool/main/e/efibootmgr/efibootmgr* /target/debs/")
            self.do_run_in_chroot("dpkg -i /debs/*")
            os.system("rm -rf /target/debs")

        # Detect cdrom device
        # TODO : properly detect cdrom device
        # Mount it
        # os.system("mkdir -p /target/media/cdrom")
        # if (int(os.system("mount /dev/sr0 /target/media/cdrom"))):
        #     print " --> Failed to mount CDROM. Install will fail"
        # self.do_run_in_chroot("apt-cdrom -o Acquire::cdrom::AutoDetect=false -m add")

        # remove live-packages (or w/e)
        print " --> Removing live packages"
        our_current += 1
        self.update_progress(total=our_total, current=our_current, message=_("Removing live configuration (packages)"))
        with open("/lib/live/mount/medium/live/filesystem.packages-remove", "r") as fd:
            line = fd.read().replace('\n', ' ')
        self.do_run_in_chroot("apt-get remove --purge --yes --force-yes %s" % line)

        # add new user
        print " --> Adding new user"
        our_current += 1
        self.update_progress(total=our_total, current=our_current, message=_("Adding new user to the system"))
        self.do_run_in_chroot("useradd -s %s -c \'%s\' -G sudo,adm,dialout,audio,video,cdrom,floppy,dip,plugdev,lpadmin,sambashare -m %s" % ("/bin/bash", setup.real_name, setup.username))
        os.system("chroot /target/ /bin/bash -c \"shopt -s dotglob && cp -R /etc/skel/* /home/%s/\"" % setup.username)
        self.do_run_in_chroot("chown -R %s:%s /home/%s" % (setup.username, setup.username, setup.username))

        fp = open("/target/tmp/.passwd", "w")
        fp.write(setup.username +  ":" + setup.password1 + "\n")
        fp.write("root:" + setup.password1 + "\n")
        fp.close()
        self.do_run_in_chroot("cat /tmp/.passwd | chpasswd")
        os.system("rm -f /target/tmp/.passwd")

        # Set autologin for user if they so elected
        if setup.autologin:
            # LightDM
            self.do_run_in_chroot(r"sed -i -r 's/^#?(autologin-user)\s*=.*/\1={user}/' /etc/lightdm/lightdm.conf".format(user=setup.username))
            # MDM
            self.do_run_in_chroot(r"sed -i -r -e '/^AutomaticLogin(Enable)?\s*=/d' -e 's/^(\[daemon\])/\1\nAutomaticLoginEnable=true\nAutomaticLogin={user}/' /etc/mdm/mdm.conf".format(user=setup.username))
            # GDM3
            self.do_run_in_chroot(r"sed -i -r -e '/^(#\s*)?AutomaticLogin(Enable)?\s*=/d' -e 's/^(\[daemon\])/\1\nAutomaticLoginEnable=true\nAutomaticLogin={user}/' /etc/gdm3/daemon.conf".format(user=setup.username))
            # KDE4
            self.do_run_in_chroot(r"sed -i -r -e 's/^#?(AutomaticLoginEnable)\s*=.*/\1=true/' -e 's/^#?(AutomaticLoginUser)\s*.*/\1={user}/' /etc/kde4/kdm/kdmrc".format(user=setup.username))
            # LXDM
            self.do_run_in_chroot(r"sed -i -r -e 's/^#?(autologin)\s*=.*/\1={user}/' /etc/lxdm/lxdm.conf".format(user=setup.username))
            # SLiM
            self.do_run_in_chroot(r"sed -i -r -e 's/^#?(default_user)\s.*/\1  {user}/' -e 's/^#?(auto_login)\s.*/\1  yes/' /etc/slim.conf".format(user=setup.username))

        # Add user's face
        os.system("cp /tmp/live-installer-face.png /target/home/%s/.face" % setup.username)
        self.do_run_in_chroot("chown %s:%s /home/%s/.face" % (setup.username, setup.username, setup.username))

        # Make the new user the default user in KDM
        if os.path.exists('/target/etc/kde4/kdm/kdmrc'):
            defUsrCmd = "sed -i 's/^#DefaultUser=.*/DefaultUser=" + setup.username + "/g' " + kdmrcPath
            print defUsrCmd
            os.system(defUsrCmd)

        # write the /etc/fstab
        print " --> Writing fstab"
        our_current += 1
        self.update_progress(total=our_total, current=our_current, message=_("Writing filesystem mount information to /etc/fstab"))
        # make sure fstab has default /proc and /sys entries
        if(not os.path.exists("/target/etc/fstab")):
            os.system("echo \"#### Static Filesystem Table File\" > /target/etc/fstab")
        fstab = open("/target/etc/fstab", "a")
        fstab.write("proc\t/proc\tproc\tdefaults\t0\t0\n")
        if(not setup.skip_mount):
            for partition in setup.partitions:
                if (partition.mount_as is not None and partition.mount_as != "None"):
                    partition_uuid = partition.partition.path # If we can't find the UUID we use the path
                    blkid = commands.getoutput('blkid').split('\n')
                    for blkid_line in blkid:
                        blkid_elements = blkid_line.split(':')
                        if blkid_elements[0] == partition.partition.path:
                            blkid_mini_elements = blkid_line.split()
                            for blkid_mini_element in blkid_mini_elements:
                                if "UUID=" in blkid_mini_element:
                                    partition_uuid = blkid_mini_element.replace('"', '').strip()
                                    break
                            break

                    fstab.write("# %s\n" % (partition.partition.path))

                    if(partition.mount_as == "/"):
                        fstab_fsck_option = "1"
                    else:
                        fstab_fsck_option = "0"

                    if("ext" in partition.type):
                        fstab_mount_options = "rw,errors=remount-ro"
                    else:
                        fstab_mount_options = "defaults"

                    if partition.type == "fat16" or partition.type == "fat32":
                        fs = "vfat"
                    else:
                        fs = partition.type

                    if(fs == "swap"):
                        fstab.write("%s\tswap\tswap\tsw\t0\t0\n" % partition_uuid)
                    else:
                        fstab.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (partition_uuid, partition.mount_as, fs, fstab_mount_options, "0", fstab_fsck_option))
        fstab.close()


    def finish_install(self, setup):
        # Steps:
        our_total = 11
        our_current = 4

        # write host+hostname infos
        print " --> Writing hostname"
        our_current += 1
        self.update_progress(total=our_total, current=our_current, message=_("Setting hostname"))
        hostnamefh = open("/target/etc/hostname", "w")
        hostnamefh.write("%s\n" % setup.hostname)
        hostnamefh.close()
        hostsfh = open("/target/etc/hosts", "w")
        hostsfh.write("127.0.0.1\tlocalhost\n")
        hostsfh.write("127.0.1.1\t%s\n" % setup.hostname)
        hostsfh.write("# The following lines are desirable for IPv6 capable hosts\n")
        hostsfh.write("::1     localhost ip6-localhost ip6-loopback\n")
        hostsfh.write("fe00::0 ip6-localnet\n")
        hostsfh.write("ff00::0 ip6-mcastprefix\n")
        hostsfh.write("ff02::1 ip6-allnodes\n")
        hostsfh.write("ff02::2 ip6-allrouters\n")
        hostsfh.write("ff02::3 ip6-allhosts\n")
        hostsfh.close()

        # set the locale
        print " --> Setting the locale"
        our_current += 1
        self.update_progress(total=our_total, current=our_current, message=_("Setting locale"))
        os.system("echo \"%s.UTF-8 UTF-8\" >> /target/etc/locale.gen" % setup.language)
        self.do_run_in_chroot("locale-gen")
        os.system("echo \"\" > /target/etc/default/locale")
        self.do_run_in_chroot("update-locale LANG=\"%s.UTF-8\"" % setup.language)
        self.do_run_in_chroot("update-locale LANG=%s.UTF-8" % setup.language)

        # set the timezone
        print " --> Setting the timezone"
        os.system("echo \"%s\" > /target/etc/timezone" % setup.timezone_code)
        os.system("cp /target/usr/share/zoneinfo/%s /target/etc/localtime" % setup.timezone)

        # localizing
        print " --> Localizing packages"
        self.update_progress(total=our_total, current=our_current, message=_("Localizing packages"))
        if setup.language != "en_US":
            os.system("mkdir -p /target/debs")
            language_code = setup.language
            if "_" in setup.language:
                language_code = setup.language.split("_")[0]
            l10ns = commands.getoutput("find /lib/live/mount/medium/pool | grep 'l10n-%s\\|hunspell-%s'" % (language_code, language_code))
            for l10n in l10ns.split("\n"):
                os.system("cp %s /target/debs/" % l10n)
            self.do_run_in_chroot("dpkg -i /debs/*")
            os.system("rm -rf /target/debs")

        # set the keyboard options..
        print " --> Setting the keyboard"
        our_current += 1
        self.update_progress(total=our_total, current=our_current, message=_("Setting keyboard options"))
        consolefh = open("/target/etc/default/console-setup", "r")
        newconsolefh = open("/target/etc/default/console-setup.new", "w")
        for line in consolefh:
            line = line.rstrip("\r\n")
            if(line.startswith("XKBMODEL=")):
                newconsolefh.write("XKBMODEL=\"%s\"\n" % setup.keyboard_model)
            elif(line.startswith("XKBLAYOUT=")):
                newconsolefh.write("XKBLAYOUT=\"%s\"\n" % setup.keyboard_layout)
            elif(line.startswith("XKBVARIANT=") and setup.keyboard_variant is not None):
                newconsolefh.write("XKBVARIANT=\"%s\"\n" % setup.keyboard_variant)
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
                newconsolefh.write("XKBMODEL=\"%s\"\n" % setup.keyboard_model)
            elif(line.startswith("XKBLAYOUT=")):
                newconsolefh.write("XKBLAYOUT=\"%s\"\n" % setup.keyboard_layout)
            elif(line.startswith("XKBVARIANT=") and setup.keyboard_variant is not None):
                newconsolefh.write("XKBVARIANT=\"%s\"\n" % setup.keyboard_variant)
            else:
                newconsolefh.write("%s\n" % line)
        consolefh.close()
        newconsolefh.close()
        self.do_run_in_chroot("rm /etc/default/keyboard")
        self.do_run_in_chroot("mv /etc/default/keyboard.new /etc/default/keyboard")

        # write MBR (grub)
        print " --> Configuring Grub"
        our_current += 1
        if(setup.grub_device is not None):
            self.update_progress(pulse=True, total=our_total, current=our_current, message=_("Installing bootloader"))
            print " --> Running grub-install"
            self.do_run_in_chroot("grub-install --force %s" % setup.grub_device)
            self.do_configure_grub(our_total, our_current)
            grub_retries = 0
            while (not self.do_check_grub(our_total, our_current)):
                self.do_configure_grub(our_total, our_current)
                grub_retries = grub_retries + 1
                if grub_retries >= 5:
                    self.error_message(message=_("WARNING: The grub bootloader was not configured properly! You need to configure it manually."))
                    break

        # recreate initramfs (needed in case of skip_mount also, to include things like mdadm/dm-crypt/etc in case its needed to boot a custom install)
        print " --> Configuring Initramfs"
        our_current += 1
        self.do_run_in_chroot("/usr/sbin/update-initramfs -t -u -k all")
        kernelversion= commands.getoutput("uname -r")
        self.do_run_in_chroot("/usr/bin/sha1sum /boot/initrd.img-%s > /var/lib/initramfs-tools/%s" % (kernelversion,kernelversion))

        # Clean APT
        print " --> Cleaning APT"
        our_current += 1
        self.update_progress(pulse=True, total=our_total, current=our_current, message=_("Cleaning APT"))
        os.system("chroot /target/ /bin/sh -c \"dpkg --configure -a\"")
        self.do_run_in_chroot("sed -i 's/^deb cdrom/#deb cdrom/' /etc/apt/sources.list")
        self.do_run_in_chroot("apt-get -y --force-yes autoremove")

        # now unmount it
        print " --> Unmounting partitions"
        os.system("umount --force /target/dev/shm")
        os.system("umount --force /target/dev/pts")
        if setup.gptonefi:
            os.system("umount --force /target/boot/efi")
            os.system("umount --force /target/media/cdrom")
        os.system("umount --force /target/dev/")
        os.system("umount --force /target/sys/")
        os.system("umount --force /target/proc/")
        os.system("rm -f /target/etc/resolv.conf")
        os.system("mv /target/etc/resolv.conf.bk /target/etc/resolv.conf")
        if(not setup.skip_mount):
            for partition in setup.partitions:
                if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                    self.do_unmount("/target" + partition.mount_as)
            self.do_unmount("/target")
        self.do_unmount("/source")

        self.update_progress(done=True, message=_("Installation finished"))
        print " --> All done"


    def do_run_in_chroot(self, command):
        command = command.replace('"', "'").strip()
        print "chroot /target/ /bin/sh -c \"%s\"" % command
        os.system("chroot /target/ /bin/sh -c \"%s\"" % command)
        
    def do_configure_grub(self, our_total, our_current):
        self.update_progress(pulse=True, total=our_total, current=our_current, message=_("Configuring bootloader"))
        print " --> Running grub-mkconfig"
        self.do_run_in_chroot("grub-mkconfig -o /boot/grub/grub.cfg")
        grub_output = commands.getoutput("chroot /target/ /bin/sh -c \"grub-mkconfig -o /boot/grub/grub.cfg\"")
        grubfh = open("/var/log/live-installer-grub-output.log", "w")
        grubfh.writelines(grub_output)
        grubfh.close()
        
    def do_check_grub(self, our_total, our_current):
        self.update_progress(pulse=True, total=our_total, current=our_current, message=_("Checking bootloader"))
        print " --> Checking Grub configuration"
        time.sleep(5)
        found_theme = False
        found_entry = False
        if os.path.exists("/target/boot/grub/grub.cfg"):
            grubfh = open("/target/boot/grub/grub.cfg", "r")
            for line in grubfh:
                line = line.rstrip("\r\n")
                if("06_mint_theme" in line):
                    found_theme = True
                    print " --> Found Grub theme: %s " % line
                if ("menuentry" in line and "LinuxMint" in line):
                    found_entry = True
                    print " --> Found Grub entry: %s " % line
            grubfh.close()
            return (found_entry)
        else:
            print "!No /target/boot/grub/grub.cfg file found!"
            return False

    def do_mount(self, device, dest, type, options=None):
        ''' Mount a filesystem '''
        p = None
        if(options is not None):
            cmd = "mount -o %s -t %s %s %s" % (options, type, device, dest)            
        else:
            cmd = "mount -t %s %s %s" % (type, device, dest)
        print "EXECUTING: '%s'" % cmd
        self.exec_cmd(cmd)        

    def do_unmount(self, mountpoint):
        ''' Unmount a filesystem '''
        cmd = "umount %s" % mountpoint
        print "EXECUTING: '%s'" % cmd
        self.exec_cmd(cmd)        

    def do_copy_file(self, source, dest):
        # TODO: Add md5 checks. BADLY needed..
        BUF_SIZE = 16 * 1024
        input = open(source, "rb")
        dst = open(dest, "wb")
        while(True):
            read = input.read(BUF_SIZE)
            if not read:
                break
            dst.write(read)
        input.close()
        dst.close()
    
    # Execute schell command and return output in a list
    def exec_cmd(self, cmd):
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lstOut = []
        for line in p.stdout.readlines():
            # Strip the line, also from null spaces (strip() only strips white spaces)
            line = line.strip().strip("\0")
            if line != '':
                lstOut.append(line)
        return lstOut

# Represents the choices made by the user
class Setup(object):
    language = None
    timezone = None
    timezone_code = None
    keyboard_model = None    
    keyboard_layout = None    
    keyboard_variant = None    
    partitions = [] #Array of PartitionSetup objects
    username = None
    hostname = None
    autologin = False
    password1 = None
    password2 = None
    real_name = None    
    grub_device = None
    disks = []
    target_disk = None
    gptonefi = False
    # Optionally skip all mouting/partitioning for advanced users with custom setups (raid/dmcrypt/etc)
    # Make sure the user knows that they need to:
    #  * Mount their target directory structure at /target
    #  * NOT mount /target/dev, /target/dev/shm, /target/dev/pts, /target/proc, and /target/sys
    #  * Manually create /target/etc/fstab after init_install has completed and before finish_install is called
    #  * Install cryptsetup/dmraid/mdadm/etc in target environment (using chroot) between init_install and finish_install
    #  * Make sure target is mounted using the same block device as is used in /target/etc/fstab (eg if you change the name of a dm-crypt device between now and /target/etc/fstab, update-initramfs will likely fail)
    skip_mount = False
    
    #Descriptions (used by the summary screen)    
    keyboard_model_description = None
    keyboard_layout_description = None
    keyboard_variant_description = None
    
    def print_setup(self):
        if __debug__:
            print "-------------------------------------------------------------------------"
            print "language: %s" % self.language
            print "timezone: %s (%s)" % (self.timezone, self.timezone_code)        
            print "keyboard: %s - %s (%s) - %s - %s (%s)" % (self.keyboard_model, self.keyboard_layout, self.keyboard_variant, self.keyboard_model_description, self.keyboard_layout_description, self.keyboard_variant_description)        
            print "user: %s (%s)" % (self.username, self.real_name)
            print "autologin: ", self.autologin
            print "hostname: %s " % self.hostname
            print "passwords: %s - %s" % (self.password1, self.password2)        
            print "grub_device: %s " % self.grub_device
            print "skip_mount: %s" % self.skip_mount
            if (not self.skip_mount):
                print "target_disk: %s " % self.target_disk
                if self.gptonefi:
                    print "GPT partition table: True"
                else:
                    print "GPT partition table: False"
                print "disks: %s " % self.disks                       
                print "partitions:"
                for partition in self.partitions:
                    partition.print_partition()
            print "-------------------------------------------------------------------------"
