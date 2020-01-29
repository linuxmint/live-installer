#!/usr/bin/env python

from installer import InstallerEngine, Setup, NON_LATIN_KB_LAYOUTS
from dialogs import MessageDialog, QuestionDialog, ErrorDialog, WarningDialog
import timezones
import partitioning
import gettext
import os
import re
import commands
import sys
import threading
import time
import parted
import cairo

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GObject, WebKit2, Pango, GLib

gettext.install("live-installer", "/usr/share/linuxmint/locale")

LOADING_ANIMATION = '/usr/share/live-installer/loading.gif'

# Used as a decorator to run things in the background
def async(func):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper

# Used as a decorator to run things in the main loop, from another thread
def idle(func):
    def wrapper(*args, **kwargs):
        GObject.idle_add(func, *args, **kwargs)
    return wrapper

class WizardPage:

    def __init__(self, help_text, icon, question):
        self.help_text = help_text
        self.icon = icon
        self.question = question

class InstallerWindow:
    # Cancelable timeout for keyboard preview generation, which is
    # quite expensive, so avoid drawing it if only scrolling through
    # the keyboard layout list
    kbd_preview_generation = -1

    def __init__(self, expert_mode=False):

        self.expert_mode = expert_mode

        #Disable the screensaver
        if not __debug__:
            os.system("killall cinnamon-screen")

        #Build the Setup object (where we put all our choices)
        self.setup = Setup()

        self.resource_dir = '/usr/share/live-installer/'
        glade_file = os.path.join(self.resource_dir, 'interface.ui')
        self.builder = Gtk.Builder()
        self.builder.add_from_file(glade_file)

        # should be set early
        self.done = False
        self.fail = False
        self.paused = False
        self.showing_last_dialog = False

        # here comes the installer engine
        self.installer = InstallerEngine()

        # load the window object
        self.window = self.builder.get_object("main_window")
        self.window.connect("delete-event", self.quit_cb)

        # Wizard pages
        (self.PAGE_WELCOME,
         self.PAGE_LANGUAGE,
         self.PAGE_TIMEZONE,
         self.PAGE_KEYBOARD,
         self.PAGE_USER,
         self.PAGE_PARTITIONS,
         self.PAGE_ADVANCED,
         self.PAGE_OVERVIEW,
         self.PAGE_INSTALL,
         self.PAGE_CUSTOMWARNING,
         self.PAGE_CUSTOMPAUSED) = range(11)

        # set the button events (wizard_cb)
        self.builder.get_object("button_next").connect("clicked", self.wizard_cb, False)
        self.builder.get_object("button_back").connect("clicked", self.wizard_cb, True)
        self.builder.get_object("button_quit").connect("clicked", self.quit_cb)

        col = Gtk.TreeViewColumn("", Gtk.CellRendererPixbuf(), pixbuf=2)
        self.builder.get_object("treeview_language_list").append_column(col)
        ren = Gtk.CellRendererText()
        self.language_column = Gtk.TreeViewColumn(_("Language"), ren, text=0)
        self.language_column.set_sort_column_id(0)
        self.language_column.set_expand(True)
        self.language_column.set_resizable(True)
        ren.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object("treeview_language_list").append_column(self.language_column)

        ren = Gtk.CellRendererText()
        self.country_column = Gtk.TreeViewColumn(_("Country"), ren, text=1)
        self.country_column.set_sort_column_id(1)
        self.country_column.set_expand(True)
        self.country_column.set_resizable(True)
        ren.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object("treeview_language_list").append_column(self.country_column)

        self.builder.get_object("treeview_language_list").connect("cursor-changed", self.assign_language)

        # build the language list
        self.build_lang_list()

        # build timezones
        model = timezones.build_timezones(self)
        self.builder.get_object("button_timezones").set_label(_('Select timezone'))
        self.builder.get_object("event_timezones").connect('button-release-event', timezones.cb_map_clicked, model)

        # partitions
        self.builder.get_object("button_expert").connect("clicked", self.show_customwarning)
        self.builder.get_object("button_edit").connect("clicked", partitioning.manually_edit_partitions)
        self.builder.get_object("button_refresh").connect("clicked", lambda _: partitioning.build_partitions(self))
        self.builder.get_object("treeview_disks").get_selection().connect("changed", partitioning.update_html_preview)
        self.builder.get_object("treeview_disks").connect("row_activated", partitioning.edit_partition_dialog)
        self.builder.get_object("treeview_disks").connect("button-release-event", partitioning.partitions_popup_menu)
        text = Gtk.CellRendererText()
        for i in (partitioning.IDX_PART_PATH,
                  partitioning.IDX_PART_TYPE,
                  partitioning.IDX_PART_DESCRIPTION,
                  partitioning.IDX_PART_MOUNT_AS,
                  partitioning.IDX_PART_FORMAT_AS,
                  partitioning.IDX_PART_SIZE,
                  partitioning.IDX_PART_FREE_SPACE):
            col = Gtk.TreeViewColumn("", text, markup=i)  # real title is set in i18n()
            self.builder.get_object("treeview_disks").append_column(col)

        self.builder.get_object("entry_name").connect("notify::text", self.assign_realname)
        self.builder.get_object("entry_username").connect("notify::text", self.assign_username)
        self.builder.get_object("entry_hostname").connect("notify::text", self.assign_hostname)

        # events for detecting password mismatch..
        self.builder.get_object("entry_password").connect("changed", self.assign_password)
        self.builder.get_object("entry_confirm").connect("changed", self.assign_password)

        self.builder.get_object("radiobutton_passwordlogin").connect("toggled", self.assign_login_options)
        self.builder.get_object("checkbutton_encrypt_home").connect("toggled", self.assign_login_options)

        # link the checkbutton to the combobox
        grub_check = self.builder.get_object("checkbutton_grub")
        grub_box = self.builder.get_object("combobox_grub")
        grub_check.connect("toggled", self.assign_grub_install, grub_box)
        grub_box.connect("changed", self.assign_grub_device)

        # Install Grub by default
        grub_check.set_active(True)
        grub_box.set_sensitive(True)

        # kb models
        cell = Gtk.CellRendererText()
        cell.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object("combobox_kb_model").pack_start(cell, True)
        self.builder.get_object("combobox_kb_model").add_attribute(cell, 'text', 0)
        self.builder.get_object("combobox_kb_model").connect("changed", self.assign_keyboard_model)

        # kb layouts
        ren = Gtk.CellRendererText()
        self.column10 = Gtk.TreeViewColumn(_("Layout"), ren)
        self.column10.add_attribute(ren, "text", 0)
        self.builder.get_object("treeview_layouts").append_column(self.column10)
        self.builder.get_object("treeview_layouts").connect("cursor-changed", self.assign_keyboard_layout)

        ren = Gtk.CellRendererText()
        self.column11 = Gtk.TreeViewColumn(_("Variant"), ren)
        self.column11.add_attribute(ren, "text", 0)
        self.builder.get_object("treeview_variants").append_column(self.column11)
        self.builder.get_object("treeview_variants").connect("cursor-changed", self.assign_keyboard_variant)

        self.build_kb_lists()

        # 'about to install' aka overview
        ren = Gtk.CellRendererText()
        self.column12 = Gtk.TreeViewColumn("", ren)
        self.column12.add_attribute(ren, "markup", 0)
        self.builder.get_object("treeview_overview").append_column(self.column12)
        # install page
        self.builder.get_object("label_install_progress").set_text(_("Calculating file indexes ..."))

        #i18n
        self.i18n()

        # Pre-fill user details in debug mode
        if __debug__:
            self.builder.get_object("entry_name").set_text("John Boone")
            self.builder.get_object("entry_username").set_text("john")
            self.builder.get_object("entry_hostname").set_text("mars")
            self.builder.get_object("entry_password").set_text("dummy_password")
            self.builder.get_object("entry_confirm").set_text("dummy_password")

        # build partition list
        self.should_pulse = False

        # make sure we're on the right page (no pun.)
        self.activate_page(0)

        # Initiate the slide show
        # We have no significant browsing interface, so there isn't much point
        # in WebKit creating a memory-hungry cache.
        context = WebKit2.WebContext.get_default()
        context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

        self.partitions_browser = WebKit2.WebView()
        s = self.partitions_browser.get_settings()
        s.set_allow_file_access_from_file_urls(True)
        self.partitions_browser.show()
        self.partitions_browser.set_size_request(-1, 80)
        self.builder.get_object("scrolled_partitions").add(self.partitions_browser)

        self.window.show_all()

    def on_context_menu(self, unused_web_view, unused_context_menu,
                    unused_event, unused_hit_test_result):
        # True will not show the menu
        return True

    def i18n(self):

        window_title = "%s - %s" % (self.installer.get_distribution_name(), _("Installer"))
        if __debug__:
            window_title += ' (debug)'
        self.builder.get_object("button_expert").set_no_show_all(True)
        if self.expert_mode:
            window_title += ' (expert mode)'
            self.builder.get_object("button_expert").show()
        else:
            self.builder.get_object("button_expert").hide()
        self.window.set_title(window_title)

        # Header
        self.wizard_pages = range(11)
        self.wizard_pages[self.PAGE_WELCOME] = WizardPage(_("Welcome"), "mark-location-symbolic", "")
        self.wizard_pages[self.PAGE_LANGUAGE] = WizardPage(_("Language"), "preferences-desktop-locale-symbolic", _("What language would you like to use?"))
        self.wizard_pages[self.PAGE_TIMEZONE] = WizardPage(_("Timezone"), "mark-location-symbolic", _("Where are you?"))
        self.wizard_pages[self.PAGE_KEYBOARD] = WizardPage(_("Keyboard layout"), "preferences-desktop-keyboard-symbolic", _("What is your keyboard layout?"))
        self.wizard_pages[self.PAGE_USER] = WizardPage(_("User account"), "avatar-default-symbolic", _("Who are you?"))
        self.wizard_pages[self.PAGE_PARTITIONS] = WizardPage(_("Partitioning"), "drive-harddisk-system-symbolic", _("Where do you want to install LMDE?"))
        self.wizard_pages[self.PAGE_ADVANCED] = WizardPage(_("Advanced options"), "preferences-system-symbolic", "Configure the boot menu")
        self.wizard_pages[self.PAGE_OVERVIEW] = WizardPage(_("Summary"), "object-select-symbolic", "Check that everything is correct")
        self.wizard_pages[self.PAGE_INSTALL] = WizardPage(_("Installing"), "system-run-symbolic", "Please wait...")
        self.wizard_pages[self.PAGE_CUSTOMWARNING] = WizardPage(_("Expert mode"), "drive-harddisk-system-symbolic", "")
        self.wizard_pages[self.PAGE_CUSTOMPAUSED] = WizardPage(_("Installation paused"), "system-run-symbolic", "")

        # Buttons
        self.builder.get_object("button_quit").set_label(_("Quit"))
        self.builder.get_object("button_back").set_label(_("Back"))
        self.builder.get_object("button_next").set_label(_("Next"))

        # Welcome page
        self.builder.get_object("label_welcome1").set_text(_("Welcome to the LMDE Installer."))
        self.builder.get_object("label_welcome2").set_text(_("This program will ask you some questions and set up LMDE on your computer."))

        # Language page
        self.language_column.set_title(_("Language"))
        self.country_column.set_title(_("Country"))

        # Keyboard page
        self.builder.get_object("label_kb_model").set_label(_("Keyboard Model:"))
        self.column10.set_title(_("Layout"))
        self.column11.set_title(_("Variant"))
        self.builder.get_object("entry_test_kb").set_placeholder_text(_("Type here to test your keyboard layout"))
        self.builder.get_object("label_non_latin").set_text(_("* Your username, hostname and password should only contain Latin characters. In addition to your selected layout, English (US) is set as the default. You can switch layouts by pressing both Ctrl keys together."))

        # User page
        self.builder.get_object("label_name").set_text(_("Your name:"))
        self.builder.get_object("label_hostname").set_text(_("Your computer's name:"))
        self.builder.get_object("label_hostname_help").set_text(_("The name it uses when it talks to other computers."))
        self.builder.get_object("label_username").set_text(_("Pick a username:"))
        self.builder.get_object("label_password").set_text(_("Choose a password:"))
        self.builder.get_object("label_confirm").set_text(_("Confirm your password:"))

        self.builder.get_object("radiobutton_autologin").set_label(_("Log in automatically"))
        self.builder.get_object("radiobutton_passwordlogin").set_label(_("Require my password to log in"))
        self.builder.get_object("checkbutton_encrypt_home").set_label(_("Encrypt my home folder"))

        # Partitions page
        self.builder.get_object("button_edit").set_label(_("Edit partitions"))
        self.builder.get_object("button_refresh").set_label(_("Refresh"))
        self.builder.get_object("button_expert").set_label(_("Expert mode"))
        for col, title in zip(self.builder.get_object("treeview_disks").get_columns(),
                              (_("Device"),
                               _("Type"),
                               _("Operating system"),
                               _("Mount point"),
                               _("Format as"),
                               _("Size"),
                               _("Free space"))):
            col.set_title(title)

        # Advanced page
        self.builder.get_object("checkbutton_grub").set_label(_("Install the GRUB boot menu on:"))

        # custom install warning
        self.builder.get_object("label_custom_install_directions_1").set_label(_("You selected to manage your partitions manually, this feature is for ADVANCED USERS ONLY."))
        self.builder.get_object("label_custom_install_directions_2").set_label(_("Before continuing, mount your target filesystem(s) on /target."))
        self.builder.get_object("label_custom_install_directions_3").set_label(_("Do NOT mount virtual devices such as /dev, /proc, /sys, etc on /target/."))
        self.builder.get_object("label_custom_install_directions_4").set_label(_("During the install, you will be given time to chroot into /target and install any packages that will be needed to boot your new system."))
        self.builder.get_object("label_custom_install_directions_5").set_label(_("During the install, you will be required to write your own /etc/fstab."))

        # custom install installation paused directions
        self.builder.get_object("label_custom_install_paused_1").set_label(_("Do the following and then click Next to finish installation:"))
        self.builder.get_object("label_custom_install_paused_2").set_label(_("Create /target/etc/fstab for the filesystems as they will be mounted in your new system, matching those currently mounted at /target (without using the /target prefix in the mount paths themselves)."))
        self.builder.get_object("label_custom_install_paused_3").set_label(_("Install any packages that may be needed for first boot (mdadm, cryptsetup, dmraid, etc) by calling \"sudo chroot /target\" followed by the relevant apt-get/aptitude installations."))
        self.builder.get_object("label_custom_install_paused_4").set_label(_("Note that in order for update-initramfs to work properly in some cases (such as dm-crypt), you may need to have drives currently mounted using the same block device name as they appear in /target/etc/fstab."))
        self.builder.get_object("label_custom_install_paused_5").set_label(_("Double-check that your /target/etc/fstab is correct, matches what your new system will have at first boot, and matches what is currently mounted at /target."))

        # Refresh the current title and help question in the page header
        self.activate_page(self.PAGE_LANGUAGE)

    def assign_realname(self, entry, prop):
        self.setup.real_name = entry.props.text
        # Try to set the username (doesn't matter if it fails)
        try:
            text = entry.props.text.strip().lower()
            if " " in entry.props.text:
                elements = text.split()
                text = elements[0]
            self.setup.username = text
            self.builder.get_object("entry_username").set_text(text)
        except:
            pass
        if self.setup.real_name == "":
            self.builder.get_object("check_name").hide()
        else:
            self.builder.get_object("check_name").show()
        self.setup.print_setup()

    def assign_username(self, entry, prop):
        self.setup.username = entry.props.text
        errorFound = False
        for char in self.setup.username:
            if(char.isupper()):
                errorFound = True
                break
            elif(char.isspace()):
                errorFound = True
                break
        if errorFound or self.setup.username == "":
            self.builder.get_object("check_username").hide()
        else:
            self.builder.get_object("check_username").show()
        self.setup.print_setup()

    def assign_hostname(self, entry, prop):
        self.setup.hostname = entry.props.text
        errorFound = False
        for char in self.setup.hostname:
            if(char.isupper()):
                errorFound = True
                break
            elif(char.isspace()):
                errorFound = True
                break
        if errorFound or self.setup.hostname == "":
            self.builder.get_object("check_hostname").hide()
        else:
            self.builder.get_object("check_hostname").show()
        self.setup.print_setup()

    def assign_password(self, widget):
        self.setup.password1 = self.builder.get_object("entry_password").get_text()
        self.setup.password2 = self.builder.get_object("entry_confirm").get_text()

        if self.setup.password1 == "":
            self.builder.get_object("check_password").hide()
        else:
            self.builder.get_object("check_password").show()

        # Check the password confirmation
        if(self.setup.password1 == "" or self.setup.password2 == "" or self.setup.password1 != self.setup.password2):
            self.builder.get_object("check_confirm").hide()
        else:
            self.builder.get_object("check_confirm").show()

        self.setup.print_setup()

    def quit_cb(self, widget, data=None):
        if QuestionDialog(_("Quit?"), _("Are you sure you want to quit the installer?")):
            Gtk.main_quit()
            return False
        else:
            return True

    def show_customwarning(self, widget):
        self.activate_page(self.PAGE_CUSTOMWARNING)

    def build_lang_list(self):

        # Try to find out where we're located...
        try:
            from urllib import urlopen
        except ImportError:  # py3
            from urllib.request import urlopen
        try:
            lookup = str(urlopen('http://geoip.ubuntu.com/lookup').read())
            self.cur_country_code = re.search('<CountryCode>(.*)</CountryCode>', lookup).group(1)
            self.cur_timezone = re.search('<TimeZone>(.*)</TimeZone>', lookup).group(1)
            if self.cur_country_code == 'None': self.cur_country_code = "US"
            if self.cur_timezone == 'None': self.cur_timezone = "America/New_York"
        except:
            self.cur_country_code, self.cur_timezone = "US", "America/New_York"  # no internet connection

        #Load countries into memory
        countries = {}
        iso_standard = "3166"
        if os.path.exists("/usr/share/xml/iso-codes/iso_3166-1.xml"):
            iso_standard = "3166-1"
        for line in commands.getoutput("isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
            ccode, cname = line.split(None, 1)
            countries[ccode] = cname

        #Load languages into memory
        languages = {}
        iso_standard = "639"
        if os.path.exists("/usr/share/xml/iso-codes/iso_639-2.xml"):
            iso_standard = "639-2"
        for line in commands.getoutput("isoquery --iso %s | cut -f3,4-" % iso_standard).split('\n'):
            cols = line.split(None, 1)
            if len(cols) > 1:
                name = cols[1].replace(";", ",")
                languages[cols[0]] = name
        for line in commands.getoutput("isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
            cols = line.split(None, 1)
            if len(cols) > 1:
                if cols[0] not in languages.keys():
                    name = cols[1].replace(";", ",")
                    languages[cols[0]] = name

        # Construct language selection model
        model = Gtk.ListStore(str, str, GdkPixbuf.Pixbuf, str)
        set_iter = None
        flag_path = lambda ccode: self.resource_dir + '/flags/16/' + ccode.lower() + '.png'
        from utils import memoize
        flag = memoize(lambda ccode: GdkPixbuf.Pixbuf.new_from_file(flag_path(ccode)))
        for locale in commands.getoutput("awk -F'[@ .]' '/UTF-8/{ print $1 }' /usr/share/i18n/SUPPORTED | uniq").split('\n'):
            if '_' in locale:
                lang, ccode = locale.split('_')
                language = lang
                country = ccode
                try:
                    language = languages[lang]
                except:
                    pass
                try:
                    country = countries[ccode]
                except:
                    pass
            else:
                lang = locale
                try:
                    language = languages[lang]
                except:
                    pass
                country = ''
            pixbuf = flag(ccode) if not lang in 'eo ia' else flag('_' + lang)
            iter = model.append((language, country, pixbuf, locale))
            if (ccode == self.cur_country_code and
                (not set_iter or
                 set_iter and lang == 'en' or  # prefer English, or
                 set_iter and lang == ccode.lower())):  # fuzzy: lang matching ccode (fr_FR, de_DE, es_ES, ...)
                set_iter = iter

        # Sort by language then country
        model.set_sort_column_id(1, Gtk.SortType.ASCENDING)
        model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        # Set the model and pre-select the correct language
        treeview = self.builder.get_object("treeview_language_list")
        treeview.set_model(model)
        if set_iter:
            path = model.get_path(set_iter)
            treeview.set_cursor(path)
            treeview.scroll_to_cell(path)

    def build_kb_lists(self):
        ''' Do some xml kung-fu and load the keyboard stuffs '''
        # Determine the layouts in use
        (keyboard_geom,
         self.setup.keyboard_layout) = commands.getoutput("setxkbmap -query | awk '/^(model|layout)/{print $2}'").split()
        # Build the models
        from collections import defaultdict
        def _ListStore_factory():
            model = Gtk.ListStore(str, str)
            model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
            return model
        models = _ListStore_factory()
        layouts = _ListStore_factory()
        variants = defaultdict(_ListStore_factory)
        try:
            import xml.etree.cElementTree as ET
        except ImportError:
            import xml.etree.ElementTree as ET
        xml = ET.parse('/usr/share/X11/xkb/rules/xorg.xml')
        for node in xml.iterfind('.//modelList/model/configItem'):
            name, desc = node.find('name').text, node.find('description').text
            iterator = models.append((desc, name))
            if name == keyboard_geom:
                set_keyboard_model = iterator
        for node in xml.iterfind('.//layoutList/layout'):
            name, desc = node.find('configItem/name').text, node.find('configItem/description').text
            nonedesc = desc
            if name in NON_LATIN_KB_LAYOUTS:
                nonedesc = "English (US) + %s" % nonedesc
            variants[name].append((nonedesc, None))
            for variant in node.iterfind('variantList/variant/configItem'):
                var_name, var_desc = variant.find('name').text, variant.find('description').text
                var_desc = var_desc if var_desc.startswith(desc) else '{} - {}'.format(desc, var_desc)
                if name in NON_LATIN_KB_LAYOUTS and "Latin" not in var_desc:
                    var_desc = "English (US) + %s" % var_desc
                variants[name].append((var_desc, var_name))
            if name in NON_LATIN_KB_LAYOUTS:
                desc = desc + " *"
            iterator = layouts.append((desc, name))
            if name == self.setup.keyboard_layout:
                set_keyboard_layout = iterator
        # Set the models
        self.builder.get_object("combobox_kb_model").set_model(models)
        self.builder.get_object("treeview_layouts").set_model(layouts)
        self.layout_variants = variants
        # Preselect currently active keyboard info
        try:
            self.builder.get_object("combobox_kb_model").set_active_iter(set_keyboard_model)
        except NameError: pass  # set_keyboard_model not set
        try:
            treeview = self.builder.get_object("treeview_layouts")
            path = layouts.get_path(set_keyboard_layout)
            treeview.set_cursor(path)
            treeview.scroll_to_cell(path)
        except NameError: pass  # set_keyboard_layout not set

    def assign_language(self, treeview, data=None):
        ''' Called whenever someone updates the language '''
        model = treeview.get_model()
        selection = treeview.get_selection()
        if selection.count_selected_rows > 0:
            (model, iter) = selection.get_selected()
            if iter is not None:
                self.setup.language = model.get_value(iter, 3)
                self.setup.print_setup()
                gettext.translation('live-installer', "/usr/share/linuxmint/locale",
                                languages=[self.setup.language, self.setup.language.split('_')[0]],
                                fallback=True).install()  # Try e.g. zh_CN, zh, or fallback to hardcoded English
                try:
                    self.i18n()
                except:
                    pass # Best effort. Fails the first time as self.column1 doesn't exist yet.

    def assign_login_options(self, checkbox, data=None):
        if self.builder.get_object("radiobutton_passwordlogin").get_active():
            self.builder.get_object("checkbutton_encrypt_home").set_sensitive(True)
        else:
            self.builder.get_object("checkbutton_encrypt_home").set_active(False)
            self.builder.get_object("checkbutton_encrypt_home").set_sensitive(False)

        self.setup.ecryptfs = self.builder.get_object("checkbutton_encrypt_home").get_active()
        self.setup.autologin = self.builder.get_object("radiobutton_autologin").get_active()
        self.setup.print_setup()

    def assign_grub_install(self, checkbox, grub_box, data=None):
        grub_box.set_sensitive(checkbox.get_active())
        if checkbox.get_active():
            self.assign_grub_device(grub_box)
        else:
            self.setup.grub_device = None
        self.setup.print_setup()

    def assign_grub_device(self, combobox, data=None):
        ''' Called whenever someone updates the grub device '''
        model = combobox.get_model()
        active = combobox.get_active()
        if(active > -1):
            row = model[active]
            self.setup.grub_device = row[0]
        self.setup.print_setup()

    def assign_keyboard_model(self, combobox):
        ''' Called whenever someone updates the keyboard model '''
        model = combobox.get_model()
        active = combobox.get_active()
        (self.setup.keyboard_model_description, self.setup.keyboard_model) = model[active]
        if not __debug__:
            os.system('setxkbmap -model ' + self.setup.keyboard_model)
        self.setup.print_setup()

    def assign_keyboard_layout(self, treeview):
        ''' Called whenever someone updates the keyboard layout '''
        model, active = treeview.get_selection().get_selected_rows()
        if not active: return
        (self.setup.keyboard_layout_description,
         self.setup.keyboard_layout) = model[active[0]]
        # Set the correct variant list model ...
        model = self.layout_variants[self.setup.keyboard_layout]
        self.builder.get_object("treeview_variants").set_model(model)
        # ... and select the first variant (standard)
        self.builder.get_object("treeview_variants").set_cursor(0)

    def assign_keyboard_variant(self, treeview):
        ''' Called whenever someone updates the keyboard layout or variant '''
        #GObject.source_remove(self.kbd_preview_generation)  # stop previous preview generation, if any
        model, active = treeview.get_selection().get_selected_rows()
        if not active: return
        (self.setup.keyboard_variant_description,
         self.setup.keyboard_variant) = model[active[0]]

        if self.setup.keyboard_variant is None:
            self.setup.keyboard_variant = ""

        if self.setup.keyboard_layout in NON_LATIN_KB_LAYOUTS:
            # Add US layout for non-latin layouts
            self.setup.keyboard_layout = 'us,%s' % self.setup.keyboard_layout

        if "Latin" in self.setup.keyboard_variant_description:
            # Remove US layout for Latin variants
            self.setup.keyboard_layout = self.setup.keyboard_layout.replace("us,", "")

        if "us," in self.setup.keyboard_layout:
            # Add None variant for US layout
            self.setup.keyboard_variant = ',%s' % self.setup.keyboard_variant

        if "us," in self.setup.keyboard_layout:
            self.builder.get_object("label_non_latin").show()
        else:
            self.builder.get_object("label_non_latin").hide()

        command = "setxkbmap -layout '%s' -variant '%s' -option grp:ctrls_toggle" % (self.setup.keyboard_layout, self.setup.keyboard_variant)
        print(command)
        if not __debug__:
            os.system(command)
            self.setup.print_setup()

        # Set preview image
        self.builder.get_object("image_keyboard").set_from_file(LOADING_ANIMATION)
        self.kbd_preview_generation = GObject.timeout_add(500, self._generate_keyboard_layout_preview)

    def _generate_keyboard_layout_preview(self):
        filename = "/tmp/live-install-keyboard-layout.png"
        layout = self.setup.keyboard_layout.split(",")[-1]
        variant = self.setup.keyboard_variant.split(",")[-1]
        if variant == "":
            variant = None
        print("python /usr/lib/live-installer/frontend/generate_keyboard_layout.py %s %s %s" % (layout, variant, filename))
        os.system("python /usr/lib/live-installer/frontend/generate_keyboard_layout.py %s %s %s" % (layout, variant, filename))

        widget = self.builder.get_object("image_keyboard")
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(filename)
            surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, widget.get_scale_factor(), widget.get_window())
            widget.set_from_surface(surface)
        except GLib.Error as e:
            print("could not load keyboard layout: %s" % e.message)
        return False

    def activate_page(self, index):
        # progress images
        for i in range(9):
            img = self.builder.get_object("progress_%d" % i)
            if i <= index:
                img.set_from_file("/usr/share/icons/live-installer-progress-dot-on.png")
            else:
                img.set_from_file("/usr/share/icons/live-installer-progress-dot-off.png")
        help_text = _(self.wizard_pages[index].help_text)
        self.builder.get_object("help_label").set_markup("<big><b>%s</b></big>" % help_text)
        self.builder.get_object("help_icon").set_from_icon_name(self.wizard_pages[index].icon, Gtk.IconSize.LARGE_TOOLBAR)
        self.builder.get_object("help_question").set_text(self.wizard_pages[index].question)
        self.builder.get_object("notebook1").set_current_page(index)
        # TODO: move other page-depended actions from the wizard_cb into here below
        if index == self.PAGE_PARTITIONS:
            self.setup.skip_mount = False
        if index == self.PAGE_CUSTOMWARNING:
            self.setup.skip_mount = True

    def wizard_cb(self, widget, goback, data=None):
        ''' wizard buttons '''
        sel = self.builder.get_object("notebook1").get_current_page()
        self.builder.get_object("button_back").set_sensitive(True)

        # check each page for errors
        if(not goback):
            if (sel == self.PAGE_WELCOME):
                self.activate_page(self.PAGE_LANGUAGE)
            elif(sel == self.PAGE_LANGUAGE):
                if self.setup.language is None:
                    WarningDialog(_("Installation Tool"), _("Please choose a language"))
                else:
                    lang_country_code = self.setup.language.split('_')[-1]
                    for value in (self.cur_timezone,      # timezone guessed from IP
                                  self.cur_country_code,  # otherwise pick country from IP
                                  lang_country_code):     # otherwise use country from language selection
                        if not value:
                            continue
                        for row in timezones.timezones:
                            if value in row:
                                timezones.select_timezone(row)
                                break
                        break
                    self.activate_page(self.PAGE_TIMEZONE)
            elif (sel == self.PAGE_TIMEZONE):
                if ("_" in self.setup.language):
                    country_code = self.setup.language.split("_")[1]
                else:
                    country_code = self.setup.language
                treeview = self.builder.get_object("treeview_layouts")
                model = treeview.get_model()
                iter = model.get_iter_first()
                while iter is not None:
                    iter_country_code = model.get_value(iter, 1)
                    if iter_country_code.lower() == country_code.lower():
                        column = treeview.get_column(0)
                        path = model.get_path(iter)
                        treeview.set_cursor(path)
                        treeview.scroll_to_cell(path, column=column)
                        break
                    iter = model.iter_next(iter)
                self.activate_page(self.PAGE_KEYBOARD)
            elif(sel == self.PAGE_KEYBOARD):
                self.activate_page(self.PAGE_USER)
                self.builder.get_object("entry_name").grab_focus()
            elif(sel == self.PAGE_USER):
                errorFound = False
                errorMessage = ""

                if(self.setup.real_name is None or self.setup.real_name == ""):
                    errorFound = True
                    errorMessage = _("Please provide your full name.")
                elif(self.setup.username is None or self.setup.username == ""):
                    errorFound = True
                    errorMessage = _("Please provide a username.")
                elif(self.setup.password1 is None or self.setup.password1 == ""):
                    errorFound = True
                    errorMessage = _("Please provide a password for your user account.")
                elif(self.setup.password1 != self.setup.password2):
                    errorFound = True
                    errorMessage = _("Your passwords do not match.")
                elif(self.setup.hostname is None or self.setup.hostname == ""):
                    errorFound = True
                    errorMessage = _("Please provide a hostname.")
                else:
                    for char in self.setup.username:
                        if(char.isupper()):
                            errorFound = True
                            errorMessage = _("Your username must be lower case.")
                            break
                        elif(char.isspace()):
                            errorFound = True
                            errorMessage = _("Your username may not contain whitespace characters.")
                            break

                    for char in self.setup.hostname:
                        if(char.isupper()):
                            errorFound = True
                            errorMessage = _("The hostname must be lower case.")
                            break
                        elif(char.isspace()):
                            errorFound = True
                            errorMessage = _("The hostname may not contain whitespace characters.")
                            break

                if (errorFound):
                    WarningDialog(_("Installation Tool"), errorMessage)
                else:
                    self.activate_page(self.PAGE_PARTITIONS)
                    partitioning.build_partitions(self)
            elif(sel == self.PAGE_PARTITIONS):
                model = self.builder.get_object("treeview_disks").get_model()

                # Check for root partition
                found_root_partition = False
                for partition in self.setup.partitions:
                    if(partition.mount_as == "/"):
                        found_root_partition = True
                        if partition.format_as is None or partition.format_as == "":
                            ErrorDialog(_("Installation Tool"), _("Please indicate a filesystem to format the root (/) partition with before proceeding."))
                            return
                    if partition.mount_as == "/@":
                        if partition.format_as != "btrfs":
                            ErrorDialog(_("Installation Tool"), _("A root subvolume (/@) requires to format the partition with btrfs."))
                            return
                        found_root_partition = True
                    if partition.mount_as == "/@home":
                        if partition.format_as == "btrfs":
                            continue;
                        if partition.type == "btrfs" and (partition.format_as == None or partition.format_as == ""):
                            continue;
                        ErrorDialog(_("Installation Tool"), _("A home subvolume (/@home) requires the use of a btrfs formatted partition."))
                        return

                if not found_root_partition:
                    ErrorDialog(_("Installation Tool"), "<b>%s</b>" % _("Please select a root (/) partition."), _(
                        "A root partition is needed to install Linux Mint on.\n\n"
                        " - Mount point: /\n - Recommended size: 30GB\n"
                        " - Recommended filesystem format: ext4\n\n"
                        "Note: The timeshift btrfs snapshots feature requires the use of:\n"
                        " - subvolume Mount-point /@\n"
                        " - btrfs as filesystem format\n"))
                    return

                if self.setup.gptonefi:
                    # Check for an EFI partition
                    found_efi_partition = False
                    for partition in self.setup.partitions:
                        if(partition.mount_as == "/boot/efi"):
                            found_efi_partition = True
                            if not partition.partition.getFlag(parted.PARTITION_BOOT):
                                ErrorDialog(_("Installation Tool"), _("The EFI partition is not bootable. Please edit the partition flags."))
                                return
                            if int(float(partition.partition.getLength('MB'))) < 100:
                                ErrorDialog(_("Installation Tool"), _("The EFI partition is too small. It must be at least 100MB."))
                                return
                            if partition.format_as == None or partition.format_as == "":
                                # No partitioning
                                if partition.type != "vfat" and partition.type != "fat32" and partition.type != "fat16":
                                    ErrorDialog(_("Installation Tool"), _("The EFI partition must be formatted as vfat."))
                                    return
                            else:
                                if partition.format_as != "vfat":
                                    ErrorDialog(_("Installation Tool"), _("The EFI partition must be formatted as vfat."))
                                    return

                    if not found_efi_partition:
                        ErrorDialog(_("Installation Tool"), "<b>%s</b>" % _("Please select an EFI partition."),_("An EFI system partition is needed with the following requirements:\n\n - Mount point: /boot/efi\n - Partition flags: Bootable\n - Size: Larger than 100MB\n - Format: vfat or fat32\n\nTo ensure compatibility with Windows we recommend you use the first partition of the disk as the EFI system partition.\n "))
                        return

                partitioning.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)

            elif(sel == self.PAGE_CUSTOMWARNING):
                partitioning.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)
            elif(sel == self.PAGE_ADVANCED):
                self.activate_page(self.PAGE_OVERVIEW)
                self.show_overview()
                self.builder.get_object("treeview_overview").expand_all()
                self.builder.get_object("button_next").set_label(_("Install"))
            elif(sel == self.PAGE_OVERVIEW):
                self.activate_page(self.PAGE_INSTALL)
                self.builder.get_object("button_next").set_sensitive(False)
                self.builder.get_object("button_back").set_sensitive(False)
                self.builder.get_object("button_quit").set_sensitive(False)
                self.do_install()
                self.slideshow_browser = WebKit2.WebView()
                s = self.slideshow_browser.get_settings()
                s.set_allow_file_access_from_file_urls(True)
                s.set_property('enable-caret-browsing', False)
                self.slideshow_browser.load_uri("file:////usr/share/live-installer/slideshow/index.html#locale=%s" % self.setup.language)
                self.builder.get_object("scrolled_slideshow").add(self.slideshow_browser)
                self.builder.get_object("scrolled_slideshow").show_all()
                self.slideshow_browser.connect('context-menu', self.on_context_menu)
                self.builder.get_object("title_eventbox").hide()
                self.builder.get_object("button_eventbox").hide()
                self.window.resize(100, 100)
            elif(sel == self.PAGE_CUSTOMPAUSED):
                self.activate_page(self.PAGE_INSTALL)
                self.builder.get_object("button_next").hide()
                self.paused = False
        else:
            self.builder.get_object("button_back").set_sensitive(True)
            if(sel == self.PAGE_OVERVIEW):
                self.activate_page(self.PAGE_ADVANCED)
            elif(sel == self.PAGE_ADVANCED):
                if (self.setup.skip_mount):
                    self.activate_page(self.PAGE_CUSTOMWARNING)
                else:
                    self.activate_page(self.PAGE_PARTITIONS)
            elif(sel == self.PAGE_CUSTOMWARNING):
                self.activate_page(self.PAGE_PARTITIONS)
            elif(sel == self.PAGE_PARTITIONS):
                self.activate_page(self.PAGE_USER)
            elif(sel == self.PAGE_USER):
                self.activate_page(self.PAGE_KEYBOARD)
            elif(sel == self.PAGE_KEYBOARD):
                self.activate_page(self.PAGE_TIMEZONE)
            elif(sel == self.PAGE_TIMEZONE):
                self.activate_page(self.PAGE_LANGUAGE)
            elif(sel == self.PAGE_LANGUAGE):
                self.activate_page(self.PAGE_WELCOME)

    def show_overview(self):
        bold = lambda str: '<b>' + str + '</b>'
        model = Gtk.TreeStore(str)
        self.builder.get_object("treeview_overview").set_model(model)
        top = model.append(None, (_("Localization"),))
        model.append(top, (_("Language: ") + bold(self.setup.language),))
        model.append(top, (_("Timezone: ") + bold(self.setup.timezone),))
        model.append(top, (_("Keyboard layout: ") +
                           "<b>%s - %s %s</b>" % (self.setup.keyboard_model_description, self.setup.keyboard_layout_description,
                                                  '(%s)' % self.setup.keyboard_variant_description if self.setup.keyboard_variant_description else ''),))
        top = model.append(None, (_("User settings"),))
        model.append(top, (_("Real name: ") + bold(self.setup.real_name),))
        model.append(top, (_("Username: ") + bold(self.setup.username),))
        model.append(top, (_("Automatic login: ") + bold(_("enabled") if self.setup.autologin else _("disabled")),))
        model.append(top, (_("Home encryption: ") + bold(_("enabled") if self.setup.ecryptfs else _("disabled")),))
        top = model.append(None, (_("System settings"),))
        model.append(top, (_("Hostname: ") + bold(self.setup.hostname),))
        top = model.append(None, (_("Filesystem operations"),))
        model.append(top, (bold(_("Install bootloader on %s") % self.setup.grub_device) if self.setup.grub_device else _("Do not install bootloader"),))
        if self.setup.skip_mount:
            model.append(top, (bold(_("Use already-mounted /target.")),))
            return
        for p in self.setup.partitions:
            if p.format_as:
                model.append(top, (bold(_("Format %(path)s as %(filesystem)s") % {'path':p.path, 'filesystem':p.format_as}),))
        for p in self.setup.partitions:
            if p.mount_as:
                model.append(top, (bold(_("Mount %(path)s as %(mount)s") % {'path': p.path, 'mount':p.mount_as}),))

    @idle
    def show_error_dialog(self, message, detail):
        ErrorDialog(message, detail)
        if self.showing_last_dialog:
            self.showing_last_dialog = False

    @idle
    def show_reboot_dialog(self):
        reboot = QuestionDialog(_("Installation finished"), _("The installation is now complete. Do you want to restart your computer to use the new system?"))
        if self.showing_last_dialog:
            self.showing_last_dialog = False
        if reboot:
            os.system('reboot')

    @idle
    def pause_installation(self):
        self.activate_page(self.PAGE_CUSTOMPAUSED)
        self.builder.get_object("button_next").show()
        self.builder.get_object("button_next").set_sensitive(True)
        self.builder.get_object("button_back").set_sensitive(True)
        self.builder.get_object("button_quit").set_sensitive(True)
        MessageDialog(_("Installation paused"), _("The installation is now paused. Please read the instructions on the page carefully before clicking Next to finish the installation."))
        self.builder.get_object("button_next").set_sensitive(True)

    @async
    def do_install(self):
        print " ## INSTALLATION "
        ''' Actually perform the installation .. '''
        inst = self.installer

        if __debug__:
            print " ## DEBUG MODE - INSTALLATION PROCESS NOT LAUNCHED"
            time.sleep(200)
            Gtk.main_quit()
            sys.exit(0)

        inst.set_progress_hook(self.update_progress)
        inst.set_error_hook(self.error_message)

        # do we dare? ..
        self.critical_error_happened = False

        # Start installing
        do_try_finish_install = True

        try:
            inst.init_install(self.setup)
        except Exception, detail1:
            print detail1
            do_try_finish_install = False
            self.show_error_dialog(_("Installation error"), str(detail1))

        if self.critical_error_happened:
            self.show_error_dialog(_("Installation error"), self.critical_error_message)
            do_try_finish_install = False

        if do_try_finish_install:
            if(self.setup.skip_mount):
                self.paused = True
                self.pause_installation()
                while(self.paused):
                    time.sleep(0.1)

            try:
                inst.finish_install(self.setup)
            except Exception, detail1:
                print detail1
                self.show_error_dialog(_("Installation error"), str(detail1))

            # show a message dialog thingum
            while(not self.done):
                time.sleep(0.1)

            self.showing_last_dialog = True
            if self.critical_error_happened:
                self.show_error_dialog(_("Installation error"), self.critical_error_message)
            else:
                self.show_reboot_dialog()

            while(self.showing_last_dialog):
                time.sleep(0.1)

            print " ## INSTALLATION COMPLETE "

        Gtk.main_quit()
        sys.exit(0)

    def error_message(self, message=""):
        self.critical_error_happened = True
        self.critical_error_message = message

    @idle
    def update_progress(self, current, total, pulse, done, message):
        if(pulse):
            self.builder.get_object("label_install_progress").set_label(message)
            self.do_progress_pulse(message)
            return
        if(done):
            self.should_pulse = False
            self.done = done
            self.builder.get_object("progressbar").set_fraction(1)
            self.builder.get_object("label_install_progress").set_label(message)
            return
        self.should_pulse = False
        _total = float(total)
        _current = float(current)
        pct = float(_current/_total)
        szPct = int(pct)
        self.builder.get_object("progressbar").set_fraction(pct)
        self.builder.get_object("label_install_progress").set_label(message)

    @idle
    def do_progress_pulse(self, message):
        def pbar_pulse():
            if(not self.should_pulse):
                return False
            self.builder.get_object("progressbar").pulse()
            return self.should_pulse
        if(not self.should_pulse):
            self.should_pulse = True
            GObject.timeout_add(100, pbar_pulse)
        else:
            # asssume we're "pulsing" already
            self.should_pulse = True
            pbar_pulse()
