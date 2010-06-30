import os
import subprocess
from subprocess import Popen
import time
import shutil
import gettext
import stat
import commands
from configobj import ConfigObj

gettext.install("live-installer", "/usr/share/locale")
	   
class SystemUser:
    ''' Represents the main user '''
   
    def __init__(self, username=None, realname=None, password=None):
	''' create new SystemUser '''
	self.username = username
	self.realname = realname
	self.password = password	

class HostMachine:
	''' Used to probe information about the host '''
	
	def is_laptop(self):
		''' Returns True/False as to whether the host is a laptop '''
		ret = False
		try:
			p = Popen("laptop-detect", shell=True)
			p.wait() # we want the return code
			retcode = p.returncode
			if(retcode == 0):
				# its a laptop
				ret = True
		except:
			pass # doesn't matter, laptop-detect doesnt exist on the host
		return ret
		
	def get_model(self):
		''' return the model of the pooter '''
		ret = None
		try:
			model = commands.getoutput("dmidecode --string system-product-name")
			ret = model.rstrip("\r\n").lstrip()
		except:
			pass # doesn't matter.
		return ret
		
	def get_manufacturer(self):
		''' return the system manufacturer '''
		ret = None
		try:
			manu = commands.getoutput("dmidecode --string system-manufacturer")
			ret = manu.rstrip("\r\n ").lstrip()
		except:
			pass # doesn't matter
		return ret
			
class InstallerEngine:
    ''' This is central to the live installer '''
   
    def __init__(self):
	# set up stuffhs
	self.conf_file = '/etc/live-installer/install.conf'
	configuration = ConfigObj(self.conf_file)
	distribution = configuration['distribution']
	install = configuration['install']
	self.distribution_name = distribution['DISTRIBUTION_NAME']
	self.distribution_version = distribution['DISTRIBUTION_VERSION']

	self.user = None
	self.live_user = install['LIVE_USER_NAME']
	self.set_install_media(media=install['LIVE_MEDIA_SOURCE'], type=install['LIVE_MEDIA_TYPE'])
	
	self.grub_device = None
	
    def set_main_user(self, user):
	''' Set the main user to be used by the installer '''
	if(user is not None):
		self.user = user
       
    def get_main_user(self):
	''' Return the main user '''
	return self.user
           
    def format_device(self, device, filesystem):
	''' Format the given device to the specified filesystem '''
	cmd = "mkfs -t %s %s" % (filesystem, device)
	p = Popen(cmd, shell=True)
	p.wait() # this blocks
	return p.returncode
       
    def set_install_media(self, media=None, type=None):
	''' Sets the location of our install source '''
	self.media = media
	self.media_type = type

    def set_keyboard_options(self, layout=None, model=None):
	''' Set the required keyboard layout and model with console-setup '''
	self.keyboard_layout = layout
	self.keyboard_model = model

    def set_hostname(self, hostname):
	''' Set the hostname on the target machine '''
	self.hostname = hostname

    def set_install_bootloader(self, device=None):
	''' The device to install grub to '''
	self.grub_device = device
		
    def add_to_blacklist(self, blacklistee):
	''' This will add a directory or file to the blacklist, so that '''
	''' it is not copied onto the new filesystem '''
	try:
		self.blacklist.index(blacklistee)
		self.blacklist.append(blacklistee)
	except:
	# We haven't got this item yet
		pass

    def set_progress_hook(self, progresshook):
	''' Set a callback to be called on progress updates '''
	''' i.e. def my_callback(progress_type, message, current_progress, total) '''
	''' Where progress_type is any off PROGRESS_START, PROGRESS_UPDATE, PROGRESS_COMPLETE, PROGRESS_ERROR '''
	self.update_progress = progresshook

    def get_distribution_name(self):
	return self.distribution_name
       
    def get_distribution_version(self):
	return self.distribution_version
       
    def get_locale(self):
	''' Return the locale we're setting '''
	return self.locale
   
    def set_locale(self, newlocale):
	''' Set the locale '''
	self.locale = newlocale
       
    def install(self):
	''' Install this baby to disk '''
	# mount the media location.
	try:
		if(not os.path.exists("/target")):
			os.mkdir("/target")
		if(not os.path.exists("/source")):
			os.mkdir("/source")
		# find the squashfs..
		root = self.media
		root_type = self.media_type
		if(not os.path.exists(root)):
			print _("Base filesystem does not exist! Bailing")
			sys.exit(1) # change to report
		root_device = None
		# format partitions as appropriate
		for item in self.fstab.get_entries():
			if(item.mountpoint == "/"):
				root_device = item
				item.format = True
			if(item.format):
				# well now, we gets to nuke stuff.
				# report it. should grab the total count of filesystems to be formatted ..
				self.update_progress(total=4, current=1, pulse=True, message=_("Formatting %s as %s..." % (item.device, item.filesystem)))
				self.format_device(item.device, item.filesystem)
		# mount filesystem
		self.update_progress(total=4, current=2, message=_("Mounting %s on %s") % (root, "/source/"))
		self.do_mount(root, "/source/", root_type, options="loop")
		self.update_progress(total=4, current=3, message=_("Mounting %s on %s") % (root_device.device, "/target/"))
		self.do_mount(root_device.device, "/target", root_device.filesystem, None)
		# walk root filesystem. we're too lazy though :P
		SOURCE = "/source/"
		DEST = "/target/"
		directory_times = []
		our_total = 0
		our_current = -1
		os.chdir(SOURCE)
		# index the files
		for top,dirs,files in os.walk(SOURCE, topdown=False):
			our_total += len(dirs) + len(files)
			self.update_progress(pulse=True, message=_("Indexing files to be copied.."))
		our_total += 1 # safenessness
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
					self.copy_file(sourcepath, targetpath)
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
		for dirtime in directory_times:
			(directory, atime, mtime) = dirtime
			try:
				self.update_progress(pulse=True, message=_("Restoring meta-information on %s" % directory))
				os.utime(directory, (atime, mtime))
			except OSError:
				pass
		# Steps:
		our_total = 8
		our_current = 0
		# chroot
		self.update_progress(total=our_total, current=our_current, message=_("Entering new system.."))
		os.system("mkfifo /target/tmp/INSTALL_PIPE")
		os.system("mount --bind /dev/ /target/dev/")
		os.system("mount --bind /dev/shm /target/dev/shm")
		os.system("mount --bind /dev/pts /target/dev/pts")
		os.system("mount --bind /sys/ /target/sys/")
		os.system("mount --bind /proc/ /target/proc/")
		child_pid = os.fork()
		if(child_pid == 0):
			# we be the child.
			os.chroot("/target/")
			# remove live user
			live_user = self.live_user
			our_current += 1
			self.sub_update_progress(total=our_total, current=our_current, message=_("Removing live configuration (user)"))
			os.system("deluser %s" % live_user)
			# can happen
			if(os.path.exists("/home/%s" % live_user)):
				os.system("rm -rf /home/%s" % live_user)
			# remove live-initramfs (or w/e)
			our_current += 1
			self.sub_update_progress(total=our_total, current=our_current, message=_("Removing live configuration (packages)"))
			os.system("apt-get remove --purge --yes --force-yes live-initramfs live-installer")
			# add new user
			our_current += 1
			self.sub_update_progress(total=our_total, current=our_current, message=_("Adding user to system"))
			user = self.get_main_user()
			os.system("useradd -s %s -c \"%s\" -G sudo -m %s" % ("/bin/bash", user.realname, user.username))			
			newusers = open("/tmp/newusers.conf", "w")
			newusers.write("%s:%s\n" % (user.username, user.password))
			newusers.write("root:%s\n" % user.password)
			newusers.close()
			os.system("cat /tmp/newusers.conf | chpasswd")
			os.system("rm -rf /tmp/newusers.conf")
			# write the /etc/fstab
			our_current += 1
			self.sub_update_progress(total=our_total, current=our_current, message=_("Writing filesystem mount information"))
			# make sure fstab has default /proc and /sys entries
			if(not os.path.exists("/etc/fstab")):
				os.system("echo \"#### Static Filesystem Table File\" > /etc/fstab")
			fstabber = open("/etc/fstab", "a")
			fstabber.write("proc\t/proc\tproc\tnodev,noexec,nosuid\t0\t0\n")
			for item in self.fstab.get_entries():
				if(item.options is None):
					item.options = "rw,errors=remount-ro"
				if(item.filesystem == "swap"):
					# special case..
					fstabber.write("%s\tswap\tswap\tsw\t0\t0\n" % item.device)
				else:
					fstabber.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (item.device, item.mountpoint, item.filesystem, item.options, "0", "0"))
			fstabber.close()
			# write host+hostname infos
			our_current += 1
			self.sub_update_progress(total=our_total, current=our_current, message=_("Setting hostname"))
			hostnamefh = open("/etc/hostname", "w")
			hostnamefh.write("%s\n" % self.hostname)
			hostnamefh.close()
			hostsfh = open("/etc/hosts", "w")
			hostsfh.write("127.0.0.1\tlocalhost\n")
			hostsfh.write("127.0.1.1\t%s\n" % self.hostname)
			hostsfh.write("# The following lines are desirable for IPv6 capable hosts\n")
			hostsfh.write("::1     localhost ip6-localhost ip6-loopback\n")
			hostsfh.write("fe00::0 ip6-localnet\n")
			hostsfh.write("ff00::0 ip6-mcastprefix\n")
			hostsfh.write("ff02::1 ip6-allnodes\n")
			hostsfh.write("ff02::2 ip6-allrouters\n")
			hostsfh.write("ff02::3 ip6-allhosts\n")
			hostsfh.close()

			# gdm overwrite (specific to Debian/live-initramfs)
			gdmconffh = open("/etc/gdm3/daemon.conf", "w")
			gdmconffh.write("# GDM configuration storage\n")
			gdmconffh.write("\n[daemon]\n")
			gdmconffh.write("\n[security]\n")
			gdmconffh.write("\n[xdmcp]\n")
			gdmconffh.write("\n[greeter]\n")
			gdmconffh.write("\n[chooser]\n")
			gdmconffh.write("\n[debug]\n")
			gdmconffh.close()
			
			# set the locale
			our_current += 1
			self.sub_update_progress(total=our_total, current=our_current, message=_("Setting locale"))
			os.system("echo \"%s.UTF-8 UTF-8\" >> /etc/locale.gen" % self.locale)
			os.system("locale-gen")
			os.system("echo \"\" > /etc/default/locale")
			os.system("update-locale LANG=\"%s\"" % self.locale)

			# set the keyboard options..
			our_current += 1
			self.sub_update_progress(total=our_total, current=our_current, message=_("Setting keyboard options"))
			consolefh = open("/etc/default/console-setup", "r")
			newconsolefh = open("/etc/default/console-setup.new", "w")
			for line in consolefh:
				line = line.rstrip("\r\n")
				if(line.startswith("XKBMODEL=")):
					newconsolefh.write("XKBMODEL=\"%s\"\n" % self.keyboard_model)
				elif(line.startswith("XKBLAYOUT=")):
					newconsolefh.write("XKBLAYOUT=\"%s\"\n" % self.keyboard_layout)
				else:
					newconsolefh.write("%s\n" % line)
			consolefh.close()
			newconsolefh.close()
			os.system("rm /etc/default/console-setup")
			os.system("mv /etc/default/console-setup.new /etc/default/console-setup")

			# notify that we be finished now.
			our_current += 1
			self.sub_update_progress(done=True, total=our_total, current=our_current, message=_("Done."))
		else:
			thepipe = open("/target/tmp/INSTALL_PIPE", "r")
			while(True):
				if(not os.path.exists("/target/tmp/INSTALL_PIPE")):
					break # file may disappear
				line = thepipe.readline()
				line = line.rstrip("\r\n")
				if (line.replace(" ", "") == ""):
					continue # skip blank lines
				if( line == "DONE" ):
					break
				self.update_progress(pulse=True, message=line)
			# now nuke the pipe
			if(os.path.exists("/target/tmp/INSTALL_PIPE")):
				os.unlink("/target/tmp/INSTALL_PIPE")
		   	
			# write MBR (grub)
			our_current += 1
			if(self.grub_device is not None):
				self.update_progress(pulse=True, total=our_total, current=our_current, message=_("Installing bootloader"))
				os.system("chroot /target/ /bin/sh -c \"grub-install %s\"" % self.grub_device)
				os.system("chroot /target/ /bin/sh -c \"grub-mkconfig -o /boot/grub/grub.cfg\"")

			# now unmount it
		   	os.system("umount --force /target/dev/shm")
			os.system("umount --force /target/dev/pts")
			os.system("umount --force /target/dev/")
			os.system("umount --force /target/sys/")
			os.system("umount --force /target/proc/")
			self.do_unmount("/target")
			self.do_unmount("/source")

			self.update_progress(done=True, message=_("Installation finished"))
	except Exception,detail:
		print detail

    def sub_update_progress(self, total=None,current=None,fail=False,done=False,message=None):
	''' Only called from the chroot '''
	if(fail or done):
		os.system("echo \"DONE\" >> /tmp/INSTALL_PIPE")
	else:
		os.system("echo \"%s\" >> /tmp/INSTALL_PIPE" % message)
			
    def do_mount(self, device, dest, type, options=None):
	''' Mount a filesystem '''
	p = None
	if(options is not None):
		p = Popen("mount -o %s -t %s %s %s" % (options, type, device, dest),shell=True)
	else:
		p = Popen("mount -t %s %s %s" % (type, device, dest),shell=True)
	p.wait()
	return p.returncode
       
    def do_unmount(self, mountpoint):
	''' Unmount a filesystem '''
	p = Popen("umount %s" % mountpoint, shell=True)
	p.wait()
	return p.returncode
       
    def copy_file(self, source, dest):
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
	   
class fstab(object):
    ''' This represents the filesystem table (/etc/fstab) '''
    def __init__(self):
	self.mapping = dict()
       
    def add_mount(self, device=None, mountpoint=None, filesystem=None, options=None,format=False):
	if(not self.mapping.has_key(device)):
		self.mapping[device] = fstab_entry(device, mountpoint, filesystem, options)
		self.mapping[device].format = format
   
    def remove_mount(self, device):
	if(self.mapping.has_key(device)):
		del self.mapping[device]

    def get_entries(self):
	''' Return our list '''
	return self.mapping.values()

    def has_device(self, device):
	return self.mapping.has_key(device)
		
    def has_mount(self, mountpoint):
	for item in self.get_entries():
		if(item.mountpoint == mountpoint):
			return True
	return False
		
class fstab_entry(object):
    ''' Represents an entry in fstab '''
   
    def __init__(self, device, mountpoint, filesystem, options):
	''' Creates a new fstab entry '''
	self.device = device
	self.mountpoint = mountpoint
	self.filesystem = filesystem
	self.options = options
	self.format = False
