#!/usr/bin/env python

try:
	import pygtk
	pygtk.require("2.0")
	import gtk
	import gtk.glade
	import gettext
	import os
	import commands
	import subprocess
	import sys
	sys.path.append('/usr/lib/live-installer')
	import pango
	from installer import InstallerEngine, fstab, fstab_entry, SystemUser, HostMachine
	import threading
	import xml.dom.minidom
	from xml.dom.minidom import parse
	import gobject
	import time
except Exception, detail:
	print detail

gettext.install("live-installer", "/usr/share/locale")
gtk.gdk.threads_init()

''' Handy. Makes message dialogs easy :D '''
class MessageDialog(object):

        def __init__(self, title, message, style):
                self.title = title
                self.message = message
                self.style = style

        ''' Show me on screen '''
        def show(self):
                
                dialog = gtk.MessageDialog(None, gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, self.style, gtk.BUTTONS_OK, self.message)
                dialog.set_title(self.title)
                dialog.set_position(gtk.WIN_POS_CENTER)
                dialog.set_icon_from_file("/usr/share/icons/live-installer.png")
                dialog.run()
                dialog.destroy()
                
class InstallerWindow:

	def __init__(self):
		self.resource_dir = '/usr/share/live-installer/'
		#self.glade = 'interface.glade'
		self.glade = os.path.join(self.resource_dir, 'interface.glade')
		self.wTree = gtk.glade.XML(self.glade, 'main_window')

		# should be set early
		self.done = False
		self.fail = False

		# here cometh the installer engine
		self.installer = InstallerEngine()
		# the distribution name
		DISTRIBUTION_NAME = self.installer.get_distribution_name()
		# load the window object
		self.window = self.wTree.get_widget("main_window")
		self.window.set_title(DISTRIBUTION_NAME + " " +  _("Installer"))
		self.window.connect("destroy", self.quit_cb)

		# now make it sexy
		path = os.path.join(self.resource_dir, 'background.jpg')
		pixbuf = gtk.gdk.pixbuf_new_from_file(path)
		pixmap, mask = pixbuf.render_pixmap_and_mask()
		width, height = pixmap.get_size()
		del pixbuf

		self.window.set_app_paintable(True)
		self.window.resize(width, height)
		self.window.realize()
		self.window.window.set_back_pixmap(pixmap, False)
		del pixmap

		# set the step names
		self.wTree.get_widget("label_step_1").set_markup(_("Select language"))
		self.wTree.get_widget("label_step_2").set_markup(_("Choose partitions"))
		self.wTree.get_widget("label_step_3").set_markup(_("Who are you?"))
		self.wTree.get_widget("label_step_4").set_markup(_("Advanced Options"))
		self.wTree.get_widget("label_step_5").set_markup(_("Keyboard Layout"))
		self.wTree.get_widget("label_step_6").set_markup(_("About to install"))
		self.wTree.get_widget("label_step_7").set_markup(_("Install system"))
		# make first step label bolded.
		label = self.wTree.get_widget("label_step_1")
		text = label.get_label()
		attrs = pango.AttrList()
		nattr = pango.AttrWeight(pango.WEIGHT_BOLD, 0, len(text))
		attrs.insert(nattr)
		label.set_attributes(attrs)
		
		# set the button events (wizard_cb)
		self.wTree.get_widget("button_next").connect("clicked", self.wizard_cb, False)
		self.wTree.get_widget("button_back").connect("clicked", self.wizard_cb, True)
		self.wTree.get_widget("button_quit").connect("clicked", self.quit_cb)
		
		# language view
		self.wTree.get_widget("label_select_language").set_markup(_("Please select the language you wish to use\nfor this installation from the list below"))

		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn("Languages", ren)
		column.add_attribute(ren, "text", 0)
		self.wTree.get_widget("treeview_language_list").append_column(column)

		# build the language list
		self.build_lang_list()

		# disk view
		self.wTree.get_widget("label_select_partition").set_markup(_("Please edit your filesystem mount points using the options below:\nRemember partitioning <b>may cause data loss!</b>"))
		self.wTree.get_widget("button_edit").connect("clicked", self.edit_partition)
		
		# device
		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn(_("Device"), ren)
		column.add_attribute(ren, "text", 0)
		self.wTree.get_widget("treeview_disks").append_column(column)
		# filesystem
		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn(_("Filesystem"), ren)
		column.add_attribute(ren, "text", 1)
		self.wTree.get_widget("treeview_disks").append_column(column)
		# format
		ren = gtk.CellRendererToggle()
		column = gtk.TreeViewColumn(_("Format"), ren)
		column.add_attribute(ren, "active", 2)
		self.wTree.get_widget("treeview_disks").append_column(column)
		# boot flag
		ren = gtk.CellRendererToggle()
		column = gtk.TreeViewColumn(_("Boot"), ren)
		column.add_attribute(ren, "active", 5)
		self.wTree.get_widget("treeview_disks").append_column(column)		
		# mount point
		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn(_("Mount Point"), ren)
		column.add_attribute(ren, "text", 3)
		self.wTree.get_widget("treeview_disks").append_column(column)
		# size
		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn(_("Size"), ren)
		column.add_attribute(ren, "text", 4)
		self.wTree.get_widget("treeview_disks").append_column(column) 
		
		# about you
		self.wTree.get_widget("label_setup_user").set_markup(_("You will now need to enter details for your user account\nThis is the account you will use after the installation has completed."))
		self.wTree.get_widget("label_your_name").set_markup("<b>%s</b>" % _("Your full name"))
		self.wTree.get_widget("label_your_name_help").set_label(_("This will be shown in the About Me application"))
		self.wTree.get_widget("label_username").set_markup("<b>%s</b>" % _("Your username"))
		self.wTree.get_widget("label_username_help").set_label(_("This is the name you will use to login to your computer"))
		self.wTree.get_widget("label_choose_pass").set_markup("<b>%s</b>" % _("Your password"))
		self.wTree.get_widget("label_pass_help").set_label(_("Please enter your password twice to ensure it is correct"))
		self.wTree.get_widget("label_hostname").set_markup("<b>%s</b>" % _("Hostname"))
		self.wTree.get_widget("label_hostname_help").set_label(_("This hostname will be the computers name on the network"))
		self.wTree.get_widget("checkbutton_sudo").set_label("%s" % _("Add as administrator (/etc/sudoers)"))
		
		# try to set the hostname
		machine = HostMachine()
		model = machine.get_model()
		hostname = ""
		if(model is not None):
			model = model.replace(" ", "").lower()
			hostname = model + "-"
		if(machine.is_laptop()):
			hostname += _("laptop")
		else:
			hostname += _("desktop")
		self.wTree.get_widget("entry_hostname").set_text(hostname)
		
		# events for detecting password mismatch..
		entry1 = self.wTree.get_widget("entry_userpass1")
		entry2 = self.wTree.get_widget("entry_userpass2")
		entry1.connect("changed", self.pass_mismatcher)
		entry2.connect("changed", self.pass_mismatcher)
		
		# root passwords mismatch..
		entry1 = self.wTree.get_widget("entry_root_pass1")
		entry2 = self.wTree.get_widget("entry_root_pass2")
		entry1.connect("changed", self.pass_mismatcher_root)
		entry2.connect("changed", self.pass_mismatcher_root)
		
		# advanced page
		self.wTree.get_widget("label_advanced").set_markup(_("On this page you can select advanced options regarding your installation\nRemember that the root password is mandatory"))
		# root pass
		self.wTree.get_widget("label_root_pass").set_markup("<b>%s</b>" % _("Root password"))
		self.wTree.get_widget("label_root_help").set_label(_("The root account is the unlimited account, used to administrate the system"))
		self.wTree.get_widget("label_root_mismatch_help").set_label(_("Please enter your password twice to ensure it is correct"))
		# grub password
		self.wTree.get_widget("label_grub").set_markup("<b>%s</b>" % _("Bootloader"))
		self.wTree.get_widget("checkbutton_grub").set_label(_("Install GRUB"))
		self.wTree.get_widget("label_grub_help").set_label(_("GRUB is a bootloader used to load the Linux kernel"))
		
		# link the checkbutton to the combobox
		grub_check = self.wTree.get_widget("checkbutton_grub")
		grub_box = self.wTree.get_widget("combobox_grub")
		grub_check.connect("clicked", lambda x: grub_box.set_sensitive(x.get_active()))
		
		# keyboard page
		self.wTree.get_widget("label_keyboard").set_markup(_("Please choose your keyboard model and keyboard layout using the\noptions below"))
		self.wTree.get_widget("label_test_kb").set_label(_("Use this box to test your keyboard layout"))
		# kb models
		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn(_("Model"), ren)
		column.add_attribute(ren, "text", 0)
		self.wTree.get_widget("treeview_models").append_column(column)
		self.wTree.get_widget("treeview_models").connect("cursor-changed", self.cb_change_kb_model)
		# kb layouts
		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn(_("Layout"), ren)
		column.add_attribute(ren, "text", 0)
		self.wTree.get_widget("treeview_layouts").append_column(column)
		self.wTree.get_widget("treeview_layouts").connect("cursor-changed", self.cb_change_kb_layout)
		self.build_kb_lists()
		
		# 'about to install' aka overview
		self.wTree.get_widget("label_overview").set_markup(_("Please review your options below before going any further.\nPress the finish button to install %s to your hard drive" % DISTRIBUTION_NAME))
		ren = gtk.CellRendererText()
		column = gtk.TreeViewColumn(_("Overview"), ren)
		column.add_attribute(ren, "markup", 0)
		self.wTree.get_widget("treeview_overview").append_column(column)
		# install page
		self.wTree.get_widget("label_installing").set_markup(_("%s is now being installed on your computer\nThis may take some time so please be patient" % DISTRIBUTION_NAME))
		self.wTree.get_widget("label_install_progress").set_markup("<i>%s</i>" % _("Calculating file indexes..."))
		
		# build partition list
		self.build_disk_list()

		# make everything visible.
		self.window.show()
		self.should_pulse = False

	def quit_cb(self, widget, data=None):
		''' ask whether we should quit. because touchpads do happen '''
		gtk.main_quit()

	def edit_partition(self, widget, data=None):
		''' edit the partition ... '''
		disks = self.wTree.get_widget("treeview_disks")
		model = disks.get_model()
		active = disks.get_selection().get_selected_rows()
		if(len(active) < 1):
			return
		if(len(active[1]) < 1):
			return
		active = active[1][0]
		if(active is None):
			return
		row = model[active]
		stabber = fstab_entry(row[0], row[3], row[1], None)
		stabber.format = row[2]
		dlg = PartitionDialog(stabber)
		stabber = dlg.show()
		if(stabber is None):
			return
		# now set the model as shown..
		row[0] = stabber.device
		row[3] = stabber.mountpoint
		row[1] = stabber.filesystem
		row[2] = stabber.format
		model[active] = row
		
	def build_lang_list(self):
		cur_lang = os.environ['LANG'] # detect the language. sneaky eh?
		if("." in cur_lang):
			cur_lang = cur_lang.split(".")[0]
		if("_" in cur_lang):
			cur_lang = cur_lang.split("_")[0]
		model = gtk.ListStore(str,str)
		model.set_sort_column_id(0, gtk.SORT_ASCENDING)
		path = os.path.join(self.resource_dir, 'locales')
		locales = open(path, "r")
		cur_index = -1 # find the locale :P
		set_index = None
		for line in locales:
			cur_index += 1
			if "=" in line:
				line = line.rstrip("\r\n")
				split = line.split("=")
				iter = model.append([split[1], split[0]])
				if(split[0] == cur_lang):
					set_index = iter
			else:
				iter = model.append([line,line])
		treeview = self.wTree.get_widget("treeview_language_list")
		treeview.set_model(model)
		if(set_index is not None):
			column = treeview.get_column(0)
			path = model.get_path(set_index)
			treeview.set_cursor(path, focus_column=column)
			treeview.scroll_to_cell(path, column=column)
		treeview.set_search_column(0)
		

	def build_disk_list(self):
		model = gtk.ListStore(str,str,bool,str,str,bool)
		model2 = gtk.ListStore(str)
		pro = subprocess.Popen("parted -lms", stdout=subprocess.PIPE, shell=True)
		last_name = ""
		for line in pro.stdout:
			line = line.rstrip("\r\n")
			if ":" in line:
				split = line.split(":")
				if(len(split) <= 3):
					continue
				if(line.startswith("/dev")):
					last_name = split[0]
					model2.append([last_name])
					continue
				# device list
				sections = line.split(" ")
				device = "%s%s" % (last_name, split[0])
				filesystem = split[4]
				# hack. love em eh? ...
				if(filesystem.startswith("linux-swap")):
					filesystem = "swap"
				size_ = split[3]
				if(len(split) > 5):
					boot  = split[6]
					if(boot == "boot;"):
						model.append([device, filesystem, False, None, size_, True])
					else:
						model.append([device, filesystem, False, None, size_, False])
				else:
					model.append([device, filesystem, False, None, size_, False])
				print "%s - %s" % (device, filesystem)
		self.wTree.get_widget("treeview_disks").set_model(model)
		self.wTree.get_widget("combobox_grub").set_model(model2)
		self.wTree.get_widget("combobox_grub").set_active(0)
		
	def build_kb_lists(self):
		''' Do some xml kung-fu and load the keyboard stuffs '''
		
		# firstly we'll determine the layouts in use
		p = subprocess.Popen("setxkbmap -print",shell=True,stdout=subprocess.PIPE)
		for line in p.stdout:
			# strip it
			line = line.rstrip("\r\n")
			line = line.replace("{","")
			line = line.replace("}","")
			line = line.replace(";","")
			if("xkb_symbols" in line):
				# decipher the layout in use
				section = line.split("\"")[1] # split by the " mark
				self.keyboard_layout = section.split("+")[1]
			if("xkb_geometry" in line):
				first_bracket = line.index("(") +1
				substr = line[first_bracket:]
				last_bracket = substr.index(")")
				substr = substr[0:last_bracket]
				self.keyboard_geom = substr
		p.poll()
		
		xml_file = '/usr/share/X11/xkb/rules/xorg.xml'
		model_models = gtk.ListStore(str,str)
		model_models.set_sort_column_id(0, gtk.SORT_ASCENDING)
		model_layouts = gtk.ListStore(str,str)
		model_layouts.set_sort_column_id(0, gtk.SORT_ASCENDING)
		dom = parse(xml_file)
		
		# if we find the users keyboard info we can set it in the list
		set_keyboard_model = None
		set_keyboard_layout = None
		
		# grab the root element
		root = dom.getElementsByTagName('xkbConfigRegistry')[0]
		# build the list of models
		root_models = root.getElementsByTagName('modelList')[0]
		for element in root_models.getElementsByTagName('model'):
			conf = element.getElementsByTagName('configItem')[0]
			name = conf.getElementsByTagName('name')[0]
			desc = conf.getElementsByTagName('description')[0]
			#vendor = conf.getElementsByTagName('vendor')[0] # presently unused..
			iter_model = model_models.append([self.getText(desc.childNodes), self.getText(name.childNodes)])
			item = self.getText(name.childNodes)
			if(item == self.keyboard_geom):
				set_keyboard_model = iter_model
		root_layouts = root.getElementsByTagName('layoutList')[0]
		for element in root_layouts.getElementsByTagName('layout'):
			conf = element.getElementsByTagName('configItem')[0]
			name = conf.getElementsByTagName('name')[0]
			desc = conf.getElementsByTagName('description')[0]
			iter_layout = model_layouts.append([self.getText(desc.childNodes), self.getText(name.childNodes)])
			item = self.getText(name.childNodes)
			if(item == self.keyboard_layout):
				set_keyboard_layout = iter_layout
		# now set the model
		self.wTree.get_widget("treeview_models").set_model(model_models)
		self.wTree.get_widget("treeview_layouts").set_model(model_layouts)
		
		if(set_keyboard_layout is not None):
			# show it in the list
			treeview = self.wTree.get_widget("treeview_layouts")
			model = treeview.get_model()
			column = treeview.get_column(0)
			path = model.get_path(set_keyboard_layout)
			treeview.set_cursor(path, focus_column=column)
			treeview.scroll_to_cell(path, column=column)
		if(set_keyboard_model is not None):
			# show it in the list
			treeview = self.wTree.get_widget("treeview_models")
			model = treeview.get_model()
			column = treeview.get_column(0)
			path = model.get_path(set_keyboard_model)
			treeview.set_cursor(path, focus_column=column)
			treeview.scroll_to_cell(path, column=column)
			
	def getText(self, nodelist):
	    rc = []
	    for node in nodelist:
	        if node.nodeType == node.TEXT_NODE:
	            rc.append(node.data)
	    return ''.join(rc)

	def cb_change_kb_model(self, treeview, data=None):
		''' Called whenever someone updates the keyboard model '''
		model = treeview.get_model()
		active = treeview.get_selection().get_selected_rows()
		if(len(active) < 1):
			return
		active = active[1][0]
		if(active is None):
			return
		row = model[active]
		os.system("setxkbmap -model %s" % row[1])
		self.keyboard_model = row[1]
		self.keyboard_model_desc = row[0]

	def cb_change_kb_layout(self, treeview, data=None):
		''' Called whenever someone updates the keyboard layout '''
		model = treeview.get_model()
		active = treeview.get_selection().get_selected_rows()
		if(len(active) < 1):
			return
		active = active[1][0]
		if(active is None):
			return
		row = model[active]
		os.system("setxkbmap -layout %s" % row[1])
		self.keyboard_layout = row[1]
		self.keyboard_layout_desc = row[0]

	def pass_mismatcher(self, widget):
		''' Someone typed into the entry '''
		w = self.wTree.get_widget("entry_userpass1")
		w2 = self.wTree.get_widget("entry_userpass2")
		txt1 = w.get_text()
		txt2 = w2.get_text()
		if(txt1 == "" and txt2 == ""):
			self.wTree.get_widget("image_mismatch").hide()
			self.wTree.get_widget("label_mismatch").hide()
		else:
			self.wTree.get_widget("image_mismatch").show()
			self.wTree.get_widget("label_mismatch").show()
		if(txt1 != txt2):
			img = self.wTree.get_widget("image_mismatch")
			img.set_from_stock(gtk.STOCK_NO, gtk.ICON_SIZE_BUTTON)
			label = self.wTree.get_widget("label_mismatch")
			label.set_label(_("Passwords do not match"))
		else:
			img = self.wTree.get_widget("image_mismatch")
			img.set_from_stock(gtk.STOCK_OK, gtk.ICON_SIZE_BUTTON)
			label = self.wTree.get_widget("label_mismatch")
			label.set_label(_("Passwords match"))
			
	def pass_mismatcher_root(self, widget):
		''' Root password match tester '''
		w = self.wTree.get_widget("entry_root_pass1")
		w2 = self.wTree.get_widget("entry_root_pass2")
		txt1 = w.get_text()
		txt2 = w2.get_text()
		if(txt1 == "" and txt2 == ""):
			self.wTree.get_widget("image_root_mismatch").hide()
			self.wTree.get_widget("label_root_mismatch").hide()
		else:
			self.wTree.get_widget("image_root_mismatch").show()
			self.wTree.get_widget("label_root_mismatch").show()
		if(txt1 != txt2):
			img = self.wTree.get_widget("image_root_mismatch")
			img.set_from_stock(gtk.STOCK_NO, gtk.ICON_SIZE_BUTTON)
			label = self.wTree.get_widget("label_root_mismatch")
			label.set_label(_("Passwords do not match"))
		else:
			img = self.wTree.get_widget("image_root_mismatch")
			img.set_from_stock(gtk.STOCK_OK, gtk.ICON_SIZE_BUTTON)
			label = self.wTree.get_widget("label_root_mismatch")
			label.set_label(_("Passwords match"))
			
	def wizard_cb(self, widget, goback, data=None):
		''' wizard buttons '''
		nb = self.wTree.get_widget("notebook1")
		sel = nb.get_current_page() 
		# check each page for errors
		if(not goback):
			if(sel == 1):
				# partitions page
				model = self.wTree.get_widget("treeview_disks").get_model()
				found_root = False
				# found_swap = False
				for row in model:
					mountpoint = row[3]
					if(mountpoint == "/"):
						found_root = True
				if(not found_root):
					dlg = MessageDialog(_("Installation Tool"), _("Please select a root (/) partition before proceeding"), gtk.MESSAGE_ERROR)
					dlg.show()
					return
			elif(sel == 2):
				# about me page
				username = self.wTree.get_widget("entry_username").get_text()
				if(username == ""):
					MessageDialog(_("Installation Tool"), _("Please provide a username"), gtk.MESSAGE_ERROR).show()
					return
				# username valid?
				for char in username:
					if(char.isupper()):
						MessageDialog(_("Installation Tool"), _("Your username must be lower case"), gtk.MESSAGE_WARNING).show()
						return
					if(char.isspace()):
						MessageDialog(_("Installation Tool"), _("Your username may not contain whitespace"), gtk.MESSAGE_WARNING).show()
						return
				password1 = self.wTree.get_widget("entry_userpass1").get_text()
				if(password1 == ""):
					MessageDialog(_("Installation Tool"), _("Please provide a password for your user account"), gtk.MESSAGE_WARNING).show()
					return
				password2 = self.wTree.get_widget("entry_userpass2").get_text()
				if(password1 != password2):
					MessageDialog(_("Installation Tool"), _("Your passwords do not match"), gtk.MESSAGE_ERROR).show()
					return
			elif(sel == 3):
				# Advanced options n whatnot.
				password1 = self.wTree.get_widget("entry_root_pass1").get_text()
				password2 = self.wTree.get_widget("entry_root_pass2").get_text()
				if(password1 == ""):
					MessageDialog(_("Installation Tool"), _("Please provide a password the root account"), gtk.MESSAGE_WARNING).show()
					return
				if(password1 != password2):
					MessageDialog(_("Installation Tool"), _("Your passwords do not match"), gtk.MESSAGE_ERROR).show()
					return
		label = self.wTree.get_widget("label_step_" + str(sel+1))
		text = label.get_label()
		attrs = pango.AttrList()
		nattr = pango.AttrWeight(pango.WEIGHT_NORMAL, 0, len(text))
		attrs.insert(nattr)
		label.set_attributes(attrs)
		label.set_sensitive(False)
		if(goback):
			sel-=1
			if(sel == 0):
				self.wTree.get_widget("button_back").set_sensitive(False)
		else:
			sel+=1
			self.wTree.get_widget("button_back").set_sensitive(True)
			
		label = self.wTree.get_widget("label_step_" + str(sel+1))
		attrs = pango.AttrList()
		text = label.get_label()
		battr = pango.AttrWeight(pango.WEIGHT_BOLD, 0, len(text))
		attrs.insert(battr)
		label.set_attributes(attrs)
		label.set_sensitive(True)
		self.wTree.get_widget("notebook1").set_current_page(sel)

		if(sel == 5):
			self.show_overview()
			self.wTree.get_widget("treeview_overview").expand_all()
		if(sel == 6):
			# do install
			self.wTree.get_widget("button_next").hide()
			self.wTree.get_widget("button_back").hide()
			thr = threading.Thread(name="live-install", group=None, args=(), kwargs={}, target=self.do_install)
			thr.start()
			
	def show_overview(self):
		''' build the summary page '''
		model = gtk.TreeStore(str)
		top = model.append(None)
		model.set(top, 0, "Filesystem operations")
		disks = self.wTree.get_widget("treeview_disks").get_model()
		for item in disks:
			if(item[2]):
				# format it
				iter = model.append(top)
				model.set(iter, 0, "<b>%s</b>" % (_("Format %s (%s) as %s" % (item[0], item[4], item[1]))))
			if(item[3] is not None and item[3] is not ""):
				# mount point
				iter = model.append(top)
				model.set(iter, 0, "<b>%s</b>" % (_("Mount %s as %s" % (item[0], item[3]))))
		install_grub = self.wTree.get_widget("checkbutton_grub").get_active()
		grub_box = self.wTree.get_widget("combobox_grub")
		grub_active = grub_box.get_active()
		grub_model = grub_box.get_model()
		iter = model.append(top)
		if(install_grub):
			model.set(iter, 0, _("Install bootloader to %s" % ("<b>%s</b>" % grub_model[grub_active][0])))
		else:
			model.set(iter, 0, _("Do not install bootloader"))
		# now we show the system info
		top = model.append(None)
		model.set(top, 0, _("User settings"))
		username = self.wTree.get_widget("entry_username").get_text()
		realname = self.wTree.get_widget("entry_your_name").get_text()
		sudo = self.wTree.get_widget("checkbutton_sudo").get_active()
		iter = model.append(top)
		model.set(iter, 0, "Username: <b>%s</b>" % username)
		iter = model.append(top)
		model.set(iter, 0, "Real name: <b>%s</b>" % realname)
		iter = model.append(top)
		model.set(iter, 0, "Administrator: <b>%s</b>" % str(sudo))
		
		# keyboard cruft
		top = model.append(None)
		model.set(top, 0, _("Keyboard"))
		iter = model.append(top)
		model.set(iter, 0, "Layout: <b>%s</b>" % self.keyboard_layout_desc)
		iter = model.append(top)
		model.set(iter, 0, "Model: <b>%s</b>" % self.keyboard_model_desc)
		
		# system stuff
		# todo; probe
		hostname = self.wTree.get_widget("entry_hostname").get_text()
		language = "English"
		top = model.append(None)
		model.set(top, 0, _("System settings"))
		iter = model.append(top)
		model.set(iter, 0, "Hostname: <b>%s</b>" % hostname)
		#iter = model.append(top)
		#model.set(iter, 0, "Locale: <b>%s</b>" % locale)
		iter = model.append(top)
		model.set(iter, 0, "Language: <b>%s</b>" % language)
		self.wTree.get_widget("treeview_overview").set_model(model)
		
	def do_install(self):
		''' Actually perform the installation .. '''
		inst = self.installer
		
		# Create fstab
		files = fstab()
		model = self.wTree.get_widget("treeview_disks").get_model()
		for row in model:
			if(row[2] or row[3] is not None): # format or mountpoint specified.
				files.add_mount(device=row[0], mountpoint=row[3], filesystem=row[1], format=row[2])
		inst.fstab = files # need to add set_fstab() to InstallerEngine
		
		# set up the system user
		username = self.wTree.get_widget("entry_username").get_text()
		password = self.wTree.get_widget("entry_userpass1").get_text()
		realname = self.wTree.get_widget("entry_your_name").get_text()
		hostname = self.wTree.get_widget("entry_hostname").get_text()
		sudo = self.wTree.get_widget("checkbutton_sudo").get_active()
		user = SystemUser(username=username, password=password, realname=realname, sudo=sudo)
		inst.set_main_user(user)
		inst.set_hostname(hostname)
		
		# set root password
		root_password = self.wTree.get_widget("entry_root_pass1").get_text()
		inst.set_root_password(root_password)
		
		# set keyboard crap
		inst.set_keyboard_options(layout=self.keyboard_layout, model=self.keyboard_model)

		# grub?
		do_grub = self.wTree.get_widget("checkbutton_grub").get_active()
		if(do_grub):
			grub_box = self.wTree.get_widget("combobox_grub")
			grub_location = grub_box.get_model()[grub_box.get_active()][0]
			inst.set_install_bootloader(device=grub_location)
		inst.set_progress_hook(self.update_progress)

		# do we dare? ..
		inst.install()
		
		# show a message dialog thingum
		while(not self.done):
			time.sleep(0.1)
		gtk.gdk.threads_enter()
		MessageDialog(_("Installation Tool"), _("Installation is now complete. Please restart your computer to use the new system"), gtk.MESSAGE_INFO).show()
		gtk.gdk.threads_leave()
		# safe??
		gtk.main_quit()
		# you are now..
		sys.exit(0)

	def update_progress(self, fail=False, done=False, pulse=False, total=0,current=0,message=""):
		# TODO: ADD FAIL CHECKS..
		if(pulse):
			gtk.gdk.threads_enter()
			self.wTree.get_widget("label_install_progress").set_label(message)
			gtk.gdk.threads_leave()
			self.do_progress_pulse(message)
			return
		if(done):
			# cool, finished :D
			self.should_pulse = False
			self.done = done
			gtk.gdk.threads_enter()
			self.wTree.get_widget("progressbar").set_fraction(1)
			self.wTree.get_widget("label_install_progress").set_label(message)
			gtk.gdk.threads_leave()
			return
		self.should_pulse = False
		_total = float(total)
		_current = float(current)
		pct = float(_current/_total)
		szPct = int(pct)
		# thread block
		gtk.gdk.threads_enter()
		self.wTree.get_widget("progressbar").set_fraction(pct)
		self.wTree.get_widget("label_install_progress").set_label(message)
		gtk.gdk.threads_leave()
		
		# end thread block
	
	def do_progress_pulse(self, message):
		def pbar_pulse():
			if(not self.should_pulse):
				return False
			self.wTree.get_widget("progressbar").pulse()
			return self.should_pulse
		if(not self.should_pulse):
			self.should_pulse = True
			gobject.timeout_add(100, pbar_pulse)
		else:
			# asssume we're "pulsing" already
			self.should_pulse = True
			gtk.gdk.threads_enter()
			pbar_pulse()
			gtk.gdk.threads_leave()
		
class PartitionDialog:
	
	def __init__(self, item_to_edit):
		self.resource_dir = '/usr/share/live-installer/'
		self.glade = os.path.join(self.resource_dir, 'interface.glade')
		self.dTree = gtk.glade.XML(self.glade, 'dialog')
		self.window = self.dTree.get_widget("dialog")
		self.window.set_title(_("Edit partition"))

		self.cancelled = False
		self.dTree.get_widget("button_cancel").connect("clicked", self.cb_cancel)
		self.build_fs_model()
		self.build_mount_model()
		# this is the fstab entry we will return
		self.stab = item_to_edit
		# i18n
		self.dTree.get_widget("label_partition").set_markup("<b>%s</b>" % _("Device:"))
		self.dTree.get_widget("label_partition_value").set_label(self.stab.device)
		self.dTree.get_widget("label_use_as").set_markup("<b>%s</b>" % _("Use as:"))
		# set the correct filesystem in this dialog
		cur = -1
		model = self.dTree.get_widget("combobox_use_as").get_model()
		for item in model:
			cur += 1
			if(item[0] == self.stab.filesystem):
				self.dTree.get_widget("combobox_use_as").set_active(cur)
				break
		self.dTree.get_widget("label_mount_point").set_markup("<b>%s</b>" % _("Mount point:"))
		if(self.stab.mountpoint is not None):
			self.dTree.get_widget("comboboxentry_mount_point").child.set_text(self.stab.mountpoint)
		self.dTree.get_widget("checkbutton_format").set_label(_("Format"))
		self.dTree.get_widget("checkbutton_format").set_active(self.stab.format)
		
	def build_fs_model(self):
		''' Build supported filesystems list '''
		model = gtk.ListStore(str)
		model.append(["swap"])
		try:
			for item in os.listdir("/sbin"):
				if(item.startswith("mkfs.")):
					fstype = item.split(".")[1]
					model.append([fstype])
		except Exception, detail:
			print detail
			print _("Could not build supported filesystems list!")
		self.dTree.get_widget("combobox_use_as").set_model(model)
			
	def build_mount_model(self):
		mounts = ["/", "/boot", "/tmp", "/home", "/srv"]
		model = gtk.ListStore(str)
		for mount in mounts:
			model.append([mount])
		self.dTree.get_widget("comboboxentry_mount_point").set_model(model)
		
	def cb_cancel(self, w, data=None):
		self.cancelled = True

	def show(self):
		self.window.run()
		self.window.hide()
		if(self.cancelled):
			return None
		self.stab.format = self.dTree.get_widget("checkbutton_format").get_active()
		w = self.dTree.get_widget("comboboxentry_mount_point")
		if(w.child.get_text().replace(" ","") != ""):
			self.stab.mountpoint = w.child.get_text()
		w = self.dTree.get_widget("combobox_use_as")
		# find filesystem ..
		active = w.get_active()
		model = w.get_model()[active]
		self.stab.filesystem = model[0]
		return self.stab
