#!/usr/bin/python3

import frontend.timezones
import frontend.partitioning
import frontend.common
import threading
import time
import parted
from utils import *
from frontend import *
from frontend.dialogs import QuestionDialog, ErrorDialog, WarningDialog
from installer import InstallerEngine, Setup, NON_LATIN_KB_LAYOUTS

gettext.bindtextdomain('xkeyboard-config', '/usr/share/locale')
gettext.textdomain('xkeyboard-config')
l = gettext.gettext
gettext.install("live-installer", "/usr/share/locale")

LOADING_ANIMATION = './resources/loading.gif'


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

    def start(self):
        Gtk.main()

    def __init__(self, fullscreen=False):

        # build the setup object (where we put all our choices) and the
        # installer
        self.setup = Setup()
        self.installer = InstallerEngine(self.setup)
        self.testmode = "TEST" in os.environ

        self.resource_dir = './resources/'
        fullscreen = fullscreen or config.get("fullscreen", False)
        if fullscreen or config.get("set_alternative_ui", False):
            glade_file = os.path.join(self.resource_dir, 'interface2.ui')
        else:
            glade_file = os.path.join(self.resource_dir, 'interface.ui')
        self.builder = Gtk.Builder()
        self.builder.add_from_file(glade_file)
        screen = Gdk.Screen.get_default()
        cssProvider = Gtk.CssProvider()
        cssProvider.load_from_path('./branding/style.css')
        styleContext = Gtk.StyleContext()
        styleContext.add_provider_for_screen(
            screen, cssProvider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # should be set early
        self.done = False
        self.fail = False
        self.week_password = True
        self.showing_last_dialog = False
        self.ui_init = False

        # slide gtk images
        self.gtkimages = []
        self.gtkpixbufs = []

        # load the window object
        self.window = self.builder.get_object("main_window")
        self.window.connect("delete-event", self.quit_cb)

        # wizard pages
        (self.PAGE_WELCOME,
         self.PAGE_LANGUAGE,
         self.PAGE_KEYBOARD,
         self.PAGE_TIMEZONE,
         self.PAGE_TYPE,
         self.PAGE_PARTITIONS,
         self.PAGE_USER,
         self.PAGE_OVERVIEW,
         self.PAGE_INSTALL) = list(range(9))

        # set the button events (wizard_cb)
        self.builder.get_object("button_next").connect(
            "clicked", self.wizard_cb, False)
        self.builder.get_object("button_back").connect(
            "clicked", self.wizard_cb, True)
        self.builder.get_object("button_quit").connect(
            "clicked", self.quit_cb)

        col = Gtk.TreeViewColumn("", Gtk.CellRendererPixbuf(), pixbuf=2)
        self.builder.get_object("treeview_language_list").append_column(col)

        ren = Gtk.CellRendererText()
        self.country_column = Gtk.TreeViewColumn(_("Country"), ren, text=0)
        self.country_column.set_sort_column_id(0)
        self.country_column.set_expand(True)
        self.country_column.set_resizable(True)
        ren.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object(
            "treeview_language_list").append_column(self.country_column)

        ren = Gtk.CellRendererText()
        self.language_column = Gtk.TreeViewColumn(_("Language"), ren, text=1)
        self.language_column.set_sort_column_id(1)
        self.language_column.set_expand(True)
        self.language_column.set_resizable(True)
        ren.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object("treeview_language_list").append_column(
            self.language_column)

        self.builder.get_object("treeview_language_list").connect(
            "cursor-changed", self.assign_language)

        # build the language list
        self.build_lang_list()

        # build timezones
        timezones.build_timezones(self)

        # type page
        model = Gtk.ListStore(str, str)
        model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        disk_iterators = []
        for disk_path, disk_description in partitioning.get_disks():
            iterator = model.append(
                ("%s (%s)" % (disk_description, disk_path), disk_path))
            disk_iterators.append(iterator)
        self.builder.get_object("combo_disk").set_model(model)
        renderer_text = Gtk.CellRendererText()
        self.builder.get_object("combo_disk").pack_start(renderer_text, True)
        self.builder.get_object("combo_disk").add_attribute(
            renderer_text, "text", 0)
        if len(partitioning.get_disks()) == 1:
            self.builder.get_object("combo_disk").set_active_iter(disk_iterators[0])
            model = self.builder.get_object("combo_disk").get_model()
            row = model[0]
            self.setup.disk = row[1]
            self.setup.diskname = row[0]

        self.builder.get_object("entry_passphrase").connect(
            "changed", self.assign_passphrase)
        self.builder.get_object("entry_passphrase2").connect(
            "changed", self.assign_passphrase)
        self.builder.get_object("radio_automated").connect(
            "toggled", self.assign_type_options)
        self.builder.get_object("radio_manual").connect(
            "toggled", self.assign_type_options)
        self.builder.get_object("check_badblocks").connect(
            "toggled", self.assign_type_options)
        self.builder.get_object("check_encrypt").connect(
            "toggled", self.assign_type_options)
        self.builder.get_object("check_lvm").connect(
            "toggled", self.assign_type_options)
        self.builder.get_object("combo_disk").connect(
            "changed", self.assign_type_options)

        # options
        self.builder.get_object("check_updates").connect(
            "toggled", self.assign_options)
        # options
        self.builder.get_object("check_minimal").connect(
            "toggled", self.assign_options)

        # partitions
        self.builder.get_object("button_edit").connect(
            "clicked", partitioning.manually_edit_partitions)
        self.builder.get_object("button_refresh").connect(
            "clicked", lambda _: partitioning.build_partitions(self))
        self.builder.get_object("treeview_disks").connect(
            "row_activated", partitioning.edit_partition_dialog)
        self.builder.get_object("treeview_disks").connect(
            "button-release-event", partitioning.partitions_popup_menu)
        text = Gtk.CellRendererText()
        for i in (partitioning.IDX_PART_PATH,
                  partitioning.IDX_PART_TYPE,
                  partitioning.IDX_PART_DESCRIPTION,
                  partitioning.IDX_PART_MOUNT_AS,
                  partitioning.IDX_PART_FORMAT_AS,
                  partitioning.IDX_PART_SIZE,
                  partitioning.IDX_PART_FREE_SPACE):
            # real title is set in i18n()
            col = Gtk.TreeViewColumn("", text, markup=i)
            self.builder.get_object("treeview_disks").append_column(col)

        self.builder.get_object("entry_name").connect(
            "changed", self.assign_realname)
        self.builder.get_object("entry_username").connect(
            "changed", self.assign_username)
        self.builder.get_object("entry_hostname").connect(
            "changed", self.assign_hostname)
        self.builder.get_object("entry_hostname").set_text(os.uname()[1])

        # events for detecting password mismatch..
        self.builder.get_object("entry_password").connect(
            "changed", self.assign_password)
        self.builder.get_object("entry_confirm").connect(
            "changed", self.assign_password)
        self.builder.get_object("entry_password").connect(
            "icon-press", self.view_password_text)
        self.builder.get_object("entry_confirm").connect(
            "icon-press", self.view_password_text)
        self.builder.get_object("entry_password").connect(
            "icon-release", self.hide_password_text)
        self.builder.get_object("entry_confirm").connect(
            "icon-release", self.hide_password_text)

        self.builder.get_object("radiobutton_passwordlogin").connect(
            "toggled", self.assign_login_options)
        self.builder.get_object("checkbutton_encrypt_home").connect(
            "toggled", self.assign_login_options)

        # link the checkbutton to the combobox
        self.grub_check = self.builder.get_object("checkbutton_grub")
        self.grub_box = self.builder.get_object("combobox_grub")
        self.grub_check.connect("toggled", self.assign_grub_install)
        self.grub_box.connect("changed", self.assign_grub_device)

        # install Grub by default
        self.grub_check.set_active(True)
        self.grub_box.set_sensitive(True)

        # kb models
        cell = Gtk.CellRendererText()
        cell.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object("combobox_kb_model").pack_start(cell, True)
        self.builder.get_object(
            "combobox_kb_model").add_attribute(cell, 'text', 0)
        self.builder.get_object("combobox_kb_model").connect(
            "changed", self.assign_keyboard_model)

        # kb layouts
        ren = Gtk.CellRendererText()
        self.column10 = Gtk.TreeViewColumn(_("Layout"), ren)
        self.column10.add_attribute(ren, "text", 0)
        self.builder.get_object(
            "treeview_layouts").append_column(self.column10)
        self.builder.get_object("treeview_layouts").connect(
            "cursor-changed", self.assign_keyboard_layout)

        ren = Gtk.CellRendererText()
        self.column11 = Gtk.TreeViewColumn(_("Variant"), ren)
        self.column11.add_attribute(ren, "text", 0)
        self.builder.get_object(
            "treeview_variants").append_column(self.column11)
        self.builder.get_object("treeview_variants").connect(
            "cursor-changed", self.assign_keyboard_variant)

        self.build_kb_lists()

        # 'about to install' aka overview
        ren = Gtk.CellRendererText()
        self.column12 = Gtk.TreeViewColumn("", ren)
        self.column12.add_attribute(ren, "markup", 0)
        self.builder.get_object(
            "treeview_overview").append_column(self.column12)

        # install page
        self.builder.get_object("label_install_progress").set_text(
            _("Calculating file indexes ..."))
        img = self.builder.get_object("image_welcome")
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            "branding/welcome.png", 832, 468, False)
        img.set_from_pixbuf(pixbuf)
        self.gtkimages.append(img)
        self.gtkpixbufs.append(pixbuf)

        # build partition list
        self.should_pulse = False

        # build options page
        if config.get("skip_options", False):
            obox.hide()
        
        self.i18n()

        # make sure we're on the right page (no pun.)
        self.activate_page(0)
        self.builder.get_object("button_back").set_sensitive(False)
        self.slideshow()
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.show_all()
        if not fullscreen and config.get("set_alternative_ui", False):
            self.builder.get_object("button_quit").hide()
        if fullscreen:
            self.fullscreen()

        # Features
        if not config.get("auto_partition_enabled", True):
            self.builder.get_object("box_automated").hide()
        if "EXPERT_MODE" not in os.environ:
            # Expert mode is only used for debuging. Please do not enable !
            self.builder.get_object("box_expert").hide()
        if not config.get("manual_partition_enabled", True):
            self.builder.get_object("box_manual").hide()
        else:
            if not config.get("lvm_enabled", True):
                self.builder.get_object("box_lvm").hide()
            if not config.get("encryption_enabled", True):
                self.builder.get_object("box_encryption").hide()
            if not config.get("fill_disk_enabled", True):
                self.builder.get_object("box_fill").hide()
        if not config.get("autologin_enabled", True):
            self.builder.get_object("autologin_box").hide()

        if not config.get("minimal_instalation_enabled", True):
            self.builder.get_object("check_minimal").hide()

        # styling
        self.assign_entry("entry_name")
        self.assign_entry("entry_username")
        self.assign_entry("entry_hostname")
        self.assign_entry("entry_password")
        self.assign_entry("entry_confirm")

        self.assign_hostname(self.builder.get_object("entry_hostname"))

        self.builder.get_object("box_replace_win").hide()
        if config.get("replace_windows_enabled", True):
            if not os.path.exists("/tmp/winroot"):
                os.mkdir("/tmp/winroot")
            for disk_path in partitioning.get_partitions():
                log("Searching: {}".format(disk_path))
                if 0 == os.system(
                        "mount -o ro {} /tmp/winroot 2>/dev/null".format(disk_path)):
                    if os.path.exists(
                            "/tmp/winroot/Windows/System32/ntoskrnl.exe"):
                        self.setup.winroot = disk_path
                        log("Found windows rootfs: {}".format(disk_path))
                    elif os.path.exists("/tmp/winroot/EFI/Microsoft/Boot/bootmgfw.efi"):
                        self.setup.winefi = disk_path
                        log("Found windows efifs: {}".format(disk_path))
                    elif os.path.exists("/tmp/winroot/bootmgr"):
                        self.setup.winboot = disk_path
                        log("Found windows boot: {}".format(disk_path))
                os.system("umount -lf /tmp/winroot")
            if self.setup.winroot and (
                    not self.setup.gptonefi or self.setup.winefi):
                self.builder.get_object("box_replace_win").show_all()

        self.builder.get_object("label_copyright").set_label(
            config.get("copyright", "17g Developer Team"))

        if config.get("hide_keyboard_model", False):
            self.builder.get_object("hbox10").hide()

        self.ui_init = True


    def fullscreen(self):
        self.window.fullscreen()
        self.builder.get_object("button_quit").show()

    def i18n(self):

        window_title = _("Installer")
        try:
            window_title = config.get(
                "distro_title", "17g") + " - " + _("Installer")
        except BaseException:
            err("\"distro_title\" varible not found on config. Using default.")
        self.window.set_title(window_title)

        # Header
        self.wizard_pages = list(range(13))
        self.wizard_pages[self.PAGE_WELCOME] = WizardPage(
            _("Welcome"), "mark-location-symbolic", "")
        self.wizard_pages[self.PAGE_LANGUAGE] = WizardPage(
            _("Language"), "preferences-desktop-locale-symbolic", _("What language would you like to use?"))
        self.wizard_pages[self.PAGE_TIMEZONE] = WizardPage(
            _("Timezone"), "mark-location-symbolic", _("Where are you?"))
        self.wizard_pages[self.PAGE_KEYBOARD] = WizardPage(
            _("Keyboard layout"), "preferences-desktop-keyboard-symbolic", _("What is your keyboard layout?"))
        self.wizard_pages[self.PAGE_USER] = WizardPage(
            _("User account"), "avatar-default-symbolic", _("Who are you?"))
        self.wizard_pages[self.PAGE_TYPE] = WizardPage(
            _("Installation Type"), "drive-harddisk-system-symbolic", _("Where do you want to install system?"))
        self.wizard_pages[self.PAGE_PARTITIONS] = WizardPage(
            _("Partitioning"), "drive-harddisk-system-symbolic", _("Where do you want to install system?"))
        self.wizard_pages[self.PAGE_OVERVIEW] = WizardPage(
            _("Summary"), "object-select-symbolic", _("Check that everything is correct"))
        self.wizard_pages[self.PAGE_INSTALL] = WizardPage(
            _("Installing"), "system-run-symbolic", _("Please wait..."))

        # Buttons
        self.builder.get_object("button_quit").set_label(_("Quit"))
        self.builder.get_object("button_back").set_label(_("Back"))
        self.builder.get_object("button_next").set_label(_("Next"))

        # Welcome page
        self.builder.get_object("label_welcome1").set_text(
            _("Welcome to the %s Installer.") % config.get("distro_title", "17g"))
        self.builder.get_object("label_welcome2").set_text(
            _("This program will ask you some questions and set up system on your computer."))

        # Language page
        self.language_column.set_title(_("Language"))
        self.country_column.set_title(_("Country"))

        # Keyboard page
        self.builder.get_object("label_kb_model").set_label(
            _("Keyboard Model:"))
        self.builder.get_object("entry_test_kb").set_placeholder_text(
            _("Type here to test your keyboard layout"))

        # User page
        self.builder.get_object("label_name").set_text(_("Your name:"))
        self.builder.get_object("label_hostname").set_text(
            _("Your computer's name:"))
        self.builder.get_object("label_hostname_help").set_text(
            _("The name it uses when it talks to other computers."))
        self.builder.get_object("label_username").set_text(
            _("Pick a username:"))
        self.builder.get_object("label_password").set_text(
            _("Choose a password:"))
        self.builder.get_object("label_confirm").set_text(
            _("Confirm your password:"))

        self.builder.get_object("radiobutton_autologin").set_label(
            _("Log in automatically"))
        self.builder.get_object("radiobutton_passwordlogin").set_label(
            _("Require my password to log in"))
        self.builder.get_object("checkbutton_encrypt_home").set_label(
            _("Encrypt my home folder"))

        # Type page
        self.builder.get_object("label_automated").set_text(
            _("Automated Installation"))
        self.builder.get_object("label_automated2").set_text(
            _("Erase a disk and install system on it."))
        self.builder.get_object("label_disk").set_text(_("Disk:"))
        self.builder.get_object("label_encrypt").set_text(
            _("Encrypt the operating system"))
        self.builder.get_object(
            "entry_passphrase").set_placeholder_text(_("Passphrase"))
        self.builder.get_object("entry_passphrase2").set_placeholder_text(
            _("Confirm passphrase"))
        self.builder.get_object("label_lvm").set_text(
            _("Use LVM (Logical Volume Management)"))
        self.builder.get_object("label_manual").set_text(
            _("Manual Partitioning"))
        self.builder.get_object("label_manual2").set_text(
            _("Manually create, resize or choose partitions for system."))
        self.builder.get_object("label_expert").set_text(
            _("Expert Mode"))
        self.builder.get_object("label_expert2").set_text(
            _("The setup tool will not do partitioning"))
        self.builder.get_object("label_replace_win").set_text(
            _("Remove Windows & Install"))
        self.builder.get_object("label_replace_win2").set_text(
            _("Remove existsing windows and install system on it."))

        self.builder.get_object("label_badblocks").set_text(
            _("Fill the disk with random data"))
        self.builder.get_object("check_badblocks").set_tooltip_text(
            _("This provides extra security but it can take hours."))

        # Partitions page
        self.builder.get_object("button_edit").set_label(_("Edit partitions"))
        self.builder.get_object("button_refresh").set_label(_("Refresh"))
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
        self.builder.get_object("checkbutton_grub").set_label(
            _("Install the GRUB boot menu on:"))

        # Read only
        self.builder.get_object("checkbutton_readonly").set_label(_("Read only"))

        # Refresh the current title and help question in the page header
        self.activate_page(self.PAGE_LANGUAGE)

        # Options
        self.builder.get_object("label_update").set_text(_("Install system with updates"))
        self.builder.get_object("label_update2").set_text(_("If you connect internet, updates will install."))
        self.builder.get_object("label_minimal").set_text(_("Minimal installation"))
        self.builder.get_object("label_minimal2").set_text(_("This will only install a minimal desktop environment with a browser and utilities."))
        self.builder.get_object("label_donotturnoff").set_text(_("Please do not turn off your computer during the installation process."))

    def view_password_text(self,entry, icon_pos, event):
        entry.set_visibility(True)
        entry.set_icon_from_icon_name(0,"view-conceal-symbolic")
        
        
    def hide_password_text(self,entry, icon_pos, event):
        entry.set_visibility(False)
        entry.set_icon_from_icon_name(0,"view-reveal-symbolic")

    def assign_realname(self, entry):
        self.setup.real_name = entry.props.text
        errorFound = False
        # Try to set the username (doesn't matter if it fails)
        try:
            text = entry.props.text.strip().lower()
            if " " in entry.props.text:
                elements = text.split()
                text = elements[0]
            self.setup.username = text
            self.builder.get_object("entry_username").set_text(text)
        except BaseException:
            pass
        if self.setup.real_name == "":
            errorFound = True
        self.assign_entry("entry_name", errorFound)

    def assign_username(self, entry):
        self.setup.username = entry.props.text
        errorFound = False
        u = self.setup.username.replace("-", "")
        if len(self.setup.username) <= 0 or self.setup.username[0] in "-0123456789" \
           or not (u.isascii() and u.isalnum() and u.islower()):
            errorFound = True
        if self.setup.username == "":
            errorFound = True
        self.assign_entry("entry_username", errorFound)

    def assign_hostname(self, entry):
        self.setup.hostname = entry.props.text
        errorFound = False
        for char in self.setup.hostname:
            if(char.isupper()):
                errorFound = True
                break
            elif(char.isspace()):
                errorFound = True
                break
        if self.setup.hostname == "":
            errorFound = True
        self.assign_entry("entry_hostname", errorFound)

    def assign_password(self, widget):
        errorFound = False
        isWeek = False
        self.setup.password1 = self.builder.get_object(
            "entry_password").get_text()
        self.setup.password2 = self.builder.get_object(
            "entry_confirm").get_text()

        # Strong password
        if self.setup.password1 == "":
            errorFound = True
        if len(self.setup.password1) < config.get("min_password_length", 1):
            errorFound = True
        if self.setup.password1.isnumeric():
            isWeek = True
        if self.setup.password1.lower() == self.setup.password1:
            isWeek = True
        if self.setup.password1.upper() == self.setup.password1:
            isWeek = True
        if self.setup.password1 == self.setup.username:
            isWeek = True
        if len(self.setup.password1) < 8:
            isWeek = True
        if len(self.setup.password1) == 0:
            isWeek = False

        has_char = False
        has_num = False
        characters = "\"!'^+%&/()=?_<>#${[]}\\|-*"
        numbers = "0123456789"
        for c in numbers:
            if c in self.setup.password1:
                has_num = True
                break
        for c in characters:
            if c in self.setup.password1:
                has_char = True
                break
        if not has_char or not has_num:
            isWeek = True

        self.assign_entry("entry_password", errorFound ,isWeek)
        self.week_password = isWeek

        # Check the password confirmation
        if(self.setup.password1 == "" or self.setup.password2 == "" or self.setup.password1 != self.setup.password2):
            errorFound = True
        self.assign_entry("entry_confirm", errorFound,isWeek)

    def assign_options(self, widget, data=None):
        self.setup.install_updates = self.builder.get_object(
            "check_updates").get_active()
        self.setup.minimal_installation = self.builder.get_object(
            "check_minimal").get_active()

    def assign_type_options(self, widget, data=None):
        self.setup.automated = self.builder.get_object(
            "radio_automated").get_active()
        self.setup.replace_windows = self.builder.get_object(
            "radio_replace_win").get_active()
        self.setup.expert_mode = self.builder.get_object(
            "radio_expert_mode").get_active()
        self.builder.get_object("check_badblocks").set_sensitive(
            self.setup.automated)
        self.builder.get_object("check_encrypt").set_sensitive(
            self.setup.automated)
        self.builder.get_object("check_lvm").set_sensitive(
            self.setup.automated)
        self.builder.get_object("combo_disk").set_sensitive(
            self.setup.automated)
        self.builder.get_object("entry_passphrase").set_sensitive(
            self.setup.automated)
        self.builder.get_object("entry_passphrase2").set_sensitive(
            self.setup.automated)
        if not self.setup.automated:
            self.builder.get_object("check_badblocks").set_active(False)
            self.builder.get_object("check_encrypt").set_active(False)
            self.builder.get_object("check_lvm").set_active(False)
            self.builder.get_object("combo_disk").set_active(-1)
            self.builder.get_object("entry_passphrase").set_text("")
            self.builder.get_object("entry_passphrase2").set_text("")

        self.setup.lvm = self.builder.get_object("check_lvm").get_active()
        if not self.setup.lvm:
            # Force LVM for LUKs
            self.builder.get_object("check_badblocks").set_active(False)
            self.builder.get_object("check_encrypt").set_active(False)
            self.builder.get_object("entry_passphrase").set_text("")
            self.builder.get_object("entry_passphrase2").set_text("")
            self.builder.get_object("check_badblocks").set_sensitive(False)
            self.builder.get_object("check_encrypt").set_sensitive(False)

        self.setup.passphrase1 = self.builder.get_object(
            "entry_passphrase").get_text()
        self.setup.passphrase2 = self.builder.get_object(
            "entry_passphrase2").get_text()
        self.setup.luks = self.builder.get_object("check_encrypt").get_active()
        model = self.builder.get_object("combo_disk").get_model()
        active = self.builder.get_object("combo_disk").get_active()
        if(active > -1):
            row = model[active]
            self.setup.disk = row[1]
            self.setup.diskname = row[0]
        if not self.builder.get_object("check_encrypt").get_active():
            self.builder.get_object("entry_passphrase").set_text("")
            self.builder.get_object("entry_passphrase2").set_text("")
            self.builder.get_object("entry_passphrase").set_sensitive(False)
            self.builder.get_object("entry_passphrase2").set_sensitive(False)
            self.builder.get_object("check_badblocks").set_active(False)
            self.builder.get_object("check_badblocks").set_sensitive(False)
        else:
            self.builder.get_object("entry_passphrase").set_sensitive(True)
            self.builder.get_object("entry_passphrase2").set_sensitive(True)
            self.builder.get_object("check_badblocks").set_sensitive(True)

        self.setup.badblocks = self.builder.get_object(
            "check_badblocks").get_active()

    def assign_passphrase(self, widget):
        self.setup.passphrase1 = self.builder.get_object(
            "entry_passphrase").get_text()
        self.setup.passphrase2 = self.builder.get_object(
            "entry_passphrase2").get_text()

    def quit_cb(self, widget, data=None):
        if QuestionDialog(_("Quit?"), _(
                "Are you sure you want to quit the installer?")):
            Gtk.main_quit()
            return False
        else:
            return True

    def assign_entry(self, name="", isInvalid=True, isWeek=False):
        entry = self.builder.get_object(name)
        entry.set_icon_from_icon_name(1,"")
        if (isWeek and not isInvalid):
            entry.set_icon_from_icon_name(1,"password-status-warning-symbolic")
        if not isWeek and not isInvalid:
            entry.set_icon_from_icon_name(1,"password-status-ok-symbolic")

    def build_lang_list(self):

        self.cur_timezone = config.get('default_timezone', "America/New_York")

        self.cur_country_code = config.get('default_locale', "auto")
        if self.cur_country_code == "auto":
            self.cur_country_code = "en_US"  # fallback language
            lang = subprocess.getoutput("echo $LANG")
            lc_all = subprocess.getoutput("echo $LC_ALL")
            if lc_all == "":
                self.cur_country_code = lang.split(".")[0]
            elif lang == "":
                self.cur_country_code = lc_all.split(".")[0]

        self.setup.language = self.cur_country_code
        self.set_language(self.setup.language)

        # Load countries into memory
        ccodes = common.get_country_list()

        # Construct language selection model
        model = Gtk.ListStore(str, str, GdkPixbuf.Pixbuf, str)
        set_iter = None

        def flag_path(ccode): return self.resource_dir + \
            '/flags/16/' + ccode.lower() + '.png'
        from utils import memoize

        def flag(ccode):
            flag_image = memoize(
                lambda image: GdkPixbuf.Pixbuf.new_from_file(image))
            try:
                return flag_image(flag_path(ccode))
            except BaseException:
                return flag_image("./resources/flags/16/_United Nations.png")
        for c in ccodes:
            c = c.split(":")
            ccode = c[0]
            lang = c[1]
            country = c[2]
            locale = c[3]
            pixbuf = flag(ccode) if not lang in 'eo ia' else flag('_' + lang)
            itervar = model.append((country, lang, pixbuf, locale))
            if (locale == self.cur_country_code and
                (not set_iter or
                 set_iter and lang == 'en' or  # prefer English, or
                 set_iter and lang == ccode.lower())):  # fuzzy: lang matching ccode (fr_FR, de_DE, es_ES, ...)
                set_iter = itervar

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
        if config.get("allow_auto_novariant", True):
            self.setup.keyboard_variant = ""

    def build_kb_lists(self):
        ''' Do some xml kung-fu and load the keyboard stuffs '''
        # Determine the layouts in use
        keyboard_geom = subprocess.getoutput("setxkbmap -query | awk '/^(model)/{print $2}'")
        self.setup.keyboard_layout = subprocess.getoutput("setxkbmap -query | awk '/^(layout)/{print $2}'")
        self.setup.keyboard_variant = subprocess.getoutput("setxkbmap -query | awk '/^(variant)/{print $2}'")
        if "," in self.setup.keyboard_layout:
            self.setup.keyboard_layout = self.setup.keyboard_layout.split(",")[
                0]
        # Build the models
        from collections import defaultdict

        def _ListStore_factory():
            model = Gtk.ListStore(str, str)
            model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
            return model
        models = _ListStore_factory()
        layouts = _ListStore_factory()
        variants = defaultdict(_ListStore_factory)

        # Keyboard model
        for model in common.get_keyboard_model_list():
            iterator = models.append(model)
            if model[1] == keyboard_geom:
                set_keyboard_model = iterator

        # Keyboard layout
        lnames = []
        vnames = []
        for model in common.get_keyboard_layout_list():
            desc = l(model[0])
            nonedesc = model[0]
            name = model[1]
            if name in NON_LATIN_KB_LAYOUTS:
                nonedesc = "English (US) + %s" % nonedesc
            # Keyboard variant
            for variant in common.get_keyboard_variant_list(model):
                var_name = variant[0]
                var_desc = l(variant[1])
                var_desc = var_name if len(
                    var_desc) == 0 else var_desc
                if name in NON_LATIN_KB_LAYOUTS and "Latin" not in var_desc:
                    var_desc = "English (US) + %s" % var_desc
                if name+"-"+var_name not in vnames:
                    variants[name].append((var_desc, var_name))
                    vnames.append(name+"-"+var_name)
            if name in NON_LATIN_KB_LAYOUTS:
                desc = desc + " *"
            if name not in lnames:
                iterator = layouts.append((desc, name))
                lnames.append(name)
            if name == self.setup.keyboard_layout:
                set_keyboard_layout = iterator

        # Set the models
        self.builder.get_object("combobox_kb_model").set_model(models)
        self.builder.get_object("treeview_layouts").set_model(layouts)
        self.layout_variants = variants
        # Preselect currently active keyboard info
        try:
            self.builder.get_object(
                "combobox_kb_model").set_active_iter(set_keyboard_model)
        except NameError:
            pass  # set_keyboard_model not set
        try:
            treeview = self.builder.get_object("treeview_layouts")
            path = layouts.get_path(set_keyboard_layout)
            treeview.set_cursor(path)
            treeview.scroll_to_cell(path)
        except NameError:
            pass  # set_keyboard_layout not set

    def assign_language(self, treeview, data=None):
        ''' Called whenever someone updates the language '''
        if not self.ui_init:
            return
        if treeview:
            model = treeview.get_model()
            selection = treeview.get_selection()
            (model, itervar) = selection.get_selected()
            if itervar and model:
                self.setup.language = model.get_value(itervar, 3)
                self.set_language(self.setup.language)

    def set_language(self, language):
        if self.testmode:
            return
        gettext.translation('live-installer', "/usr/share/locale",
                            languages=[language, language.split('_')[
                                0]],
                            fallback=True).install()  # Try e.g. zh_CN, zh, or fallback to hardcoded English
        os.environ["LANG"] = "{}.UTF-8".format(language)
        os.environ["LANGUAGE"] = "{}.UTF-8".format(language)
        self.build_kb_lists()
        self.i18n()

    def assign_login_options(self, checkbox, data=None):
        if self.builder.get_object("radiobutton_passwordlogin").get_active():
            self.builder.get_object(
                "checkbutton_encrypt_home").set_sensitive(True)
        else:
            self.builder.get_object(
                "checkbutton_encrypt_home").set_active(False)
            self.builder.get_object(
                "checkbutton_encrypt_home").set_sensitive(False)

        self.setup.ecryptfs = self.builder.get_object(
            "checkbutton_encrypt_home").get_active()
        self.setup.autologin = self.builder.get_object(
            "radiobutton_autologin").get_active()

    def assign_grub_install(self, checkbox, data=None):
        self.grub_box.set_sensitive(checkbox.get_active())
        if checkbox.get_active():
            self.assign_grub_device(self.grub_box)
        else:
            self.setup.grub_device = None

    def assign_grub_device(self, combobox, data=None):
        ''' Called whenever someone updates the grub device '''
        model = combobox.get_model()
        active = combobox.get_active()
        if(active > -1):
            row = model[active]
            self.setup.grub_device = row[0]

    def assign_keyboard_model(self, combobox):
        ''' Called whenever someone updates the keyboard model '''
        model = combobox.get_model()
        active = combobox.get_active()
        (self.setup.keyboard_model_description,
         self.setup.keyboard_model) = model[active]
        os.system('setxkbmap -model ' + self.setup.keyboard_model)

    def assign_keyboard_layout(self, treeview):
        ''' Called whenever someone updates the keyboard layout '''
        if not self.ui_init:
            return
        model, active = treeview.get_selection().get_selected_rows()
        if not active:
            return
        (self.setup.keyboard_layout_description,
         self.setup.keyboard_layout) = model[active[0]]
        # Set the correct variant list model ...
        model = self.layout_variants[self.setup.keyboard_layout]
        self.builder.get_object("treeview_variants").set_model(model)
        # ... and select novariant (if enabled in config)
        if not config.get("allow_auto_novariant", True):
            return
        k = 0
        for i in model:
            if str(i[1]) == "":
                self.builder.get_object("treeview_variants").set_cursor(k)
            k += 1

    def assign_keyboard_variant(self, treeview):
        ''' Called whenever someone updates the keyboard layout or variant '''
        # GObject.source_remove(self.kbd_preview_generation)  # stop previous
        # preview generation, if any
        if not self.ui_init:
            return
        model, active = treeview.get_selection().get_selected_rows()
        if not active:
            return
        (self.setup.keyboard_variant_description,
         self.setup.keyboard_variant) = model[active[0]]

        if self.setup.keyboard_layout in NON_LATIN_KB_LAYOUTS:
            # Add US layout for non-latin layouts
            self.setup.keyboard_layout = 'us,%s' % self.setup.keyboard_layout

        if "Latin" in self.setup.keyboard_variant_description:
            # Remove US layout for Latin variants
            self.setup.keyboard_layout = self.setup.keyboard_layout.replace(
                "us,", "")

        if "us," in self.setup.keyboard_layout:
            # Add None variant for US layout
            self.setup.keyboard_variant = ',%s' % self.setup.keyboard_variant

        command = "setxkbmap -layout '%s' -variant '%s'" % (
            self.setup.keyboard_layout, self.setup.keyboard_variant)
        os.system(command)

    def activate_page(self, nex=0, index=0, goback=False):
        errorFound = False
        if self.testmode:
            self.builder.get_object("notebook1").set_visible_child_name(str(nex))
            return
        self.show_overview()
        
        if index == self.PAGE_LANGUAGE:
            if goback:
                True # Do nothing
            elif self.setup.language is None:
                WarningDialog(_("Installer"), _(
                    "Please choose a language"))
                return
            else:
                self.set_language(self.setup.language)
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
        elif index == self.PAGE_TIMEZONE:
            if ("_" in self.setup.language):
                country_code = self.setup.language.split("_")[1]
            else:
                country_code = self.setup.language
                treeview = self.builder.get_object("treeview_layouts")
                model = treeview.get_model()
                itervar = model.get_iter_first()
                while itervar is not None:
                    iter_country_code = model.get_value(itervar, 1)
                    if iter_country_code.lower() == country_code.lower():
                        column = treeview.get_column(0)
                        path = model.get_path(itervar)
                        treeview.set_cursor(path)
                        treeview.scroll_to_cell(path, column=column)
                        break
                    itervar = model.iter_next(itervar)
        elif index == self.PAGE_KEYBOARD:
            self.builder.get_object("entry_name").grab_focus()
            if not goback and self.setup.keyboard_variant is None:
                WarningDialog(_("Installer"), _(
                    "Please provide a kayboard layout for your computer."))
                return
        elif index == self.PAGE_USER:
            errorMessage = ""
            focus_widget = None
            if goback:
                errorFound = False
            elif(self.setup.real_name is None or self.setup.real_name == ""):
                errorFound = True
                errorMessage = _("Please provide your full name.")
                focus_widget = self.builder.get_object("entry_name")
            elif(self.setup.hostname is None or self.setup.hostname == ""):
                errorFound = True
                errorMessage = _(
                    "Please provide a name for your computer.")
                focus_widget = self.builder.get_object("entry_hostname")
            elif(self.setup.username is None or self.setup.username == ""):
                errorFound = True
                errorMessage = _("Please provide a username.")
                focus_widget = self.builder.get_object("entry_username")
            elif(self.setup.password1 is None or self.setup.password1 == ""):
                errorFound = True
                errorMessage = _(
                    "Please provide a password for your user account.")
                focus_widget = self.builder.get_object("entry_password")
            elif len(self.setup.password1) < config.get("min_password_length", 1):
                errorFound = True
                errorMessage = _("Your passwords is too short.")
                focus_widget = self.builder.get_object("entry_password")
            elif self.week_password:
                errorMessage = "{}\n{}".format(
                    _("Your passwords is not strong."),
                    _("Strong password requirements:\n")+
                    _("- Length must be minimum 8 characters\n")+
                    _("- Must have exclusive characters\n")+
                    _("- Must have big and small letters\n")+
                    _("- Must have number"))
                focus_widget = self.builder.get_object("entry_password")
                if config.get("allow_week_password", False):
                    errorMessage+="\n\n"+_("Are you sure?")
                    if not QuestionDialog(_("Warning"),errorMessage):
                        return
                else:
                    errorFound = True
            elif(self.setup.password1 != self.setup.password2):
                errorFound = True
                errorMessage = _("Your passwords do not match.")
                focus_widget = self.builder.get_object("entry_confirm")
            else:
                if self.setup.username[0] in "0123456789":
                    errorFound = True
                    errorMessage = _(
                        "Your username cannot start with numbers.")

                for char in self.setup.username:
                    if(char.isupper()):
                        errorFound = True
                        errorMessage = _(
                            "Your username must be lower case.")
                        focus_widget = self.builder.get_object(
                            "entry_username")
                        break
                    elif(char.isspace()):
                        errorFound = True
                        errorMessage = _(
                            "Your username may not contain whitespace characters.")
                        focus_widget = self.builder.get_object(
                            "entry_username")
                        break
                for char in self.setup.hostname:
                    if(char.isupper()) and not config.get("allow_uppercase_hostname", True):
                        errorFound = True
                        errorMessage = _(
                            "The computer's name must be lower case.")
                        focus_widget = self.builder.get_object(
                            "entry_hostname")
                        break
                    elif(char.isspace()):
                        errorFound = True
                        errorMessage = _(
                            "The computer's name may not contain whitespace characters.")
                        focus_widget = self.builder.get_object(
                            "entry_hostname")
                        break
            if (errorFound):
                WarningDialog(_("Installer"), errorMessage)
                if focus_widget is not None:
                    focus_widget.grab_focus()
            if not errorFound and not goback:
                self.builder.get_object("button_next").set_label(_("Install"))
                self.builder.get_object("button_next").get_style_context().add_class("suggested-action")

        elif index == self.PAGE_PARTITIONS:
            if not goback:
                model = self.builder.get_object("treeview_disks").get_model()

                # Check for root partition
                found_root_partition = False
                for partition in self.setup.partitions:
                    if(partition.mount_as == "/"):
                        found_root_partition = True
                        if partition.format_as is None or partition.format_as == "":
                            if not QuestionDialog(_("Installer"), _(
                                "Root filesystem type not specified. Installation will continue without disk formatting. Do you want to continue?")):
                                return

                if not found_root_partition:
                    ErrorDialog(_("Installer"), "<b>%s</b>" % _("Please select a root (/) partition."), _(
                        "A root partition is needed to install %s on.\n\n"
                        " - Mount point: /\n - Recommended size: 30GB\n"
                        " - Recommended filesystem format: ext4\n\n") % config.get("distro_title", "17g"))
                    return

                if self.setup.gptonefi:
                    # Check for an EFI partition
                    found_efi_partition = False
                    for partition in self.setup.partitions:
                        if(partition.mount_as == "/boot/efi"):
                            found_efi_partition = True
                            if not partition.partition.getFlag(
                                    parted.PARTITION_BOOT):
                                ErrorDialog(_("Installer"), _(
                                    "The EFI partition is not bootable. Please edit the partition flags."))
                                return
                            if int(float(partition.partition.getLength('MB'))) < 35:
                                ErrorDialog(_("Installer"), _(
                                    "The EFI partition is too small. It must be at least 35MB."))
                                return
                            if partition.format_as is None or partition.format_as == "":
                                # No partitioning
                                if partition.type != "vfat" and partition.type != "fat32" and partition.type != "fat16":
                                    ErrorDialog(_("Installer"), _(
                                        "The EFI partition must be formatted as vfat."))
                                    return
                            else:
                                if partition.format_as != "vfat":
                                    ErrorDialog(_("Installer"), _(
                                        "The EFI partition must be formatted as vfat."))
                                    return

                    if not found_efi_partition:
                        ErrorDialog(_("Installer"), "<b>%s</b>" % _("Please select an EFI partition."), _(
                            "An EFI system partition is needed with the following requirements:\n\n - Mount point: /boot/efi\n - Partition flags: Bootable\n - Size: at least 35MB (100MB or more recommended)\n - Format: vfat or fat32\n\nTo ensure compatibility with Windows we recommend you use the first partition of the disk as the EFI system partition.\n "))
                        return

        elif index == self.PAGE_OVERVIEW:
            if goback:
                self.builder.get_object("button_next").set_label(_("Next"))
                self.builder.get_object("button_next").get_style_context().remove_class("suggested-action")
            self.show_overview()
        elif index == self.PAGE_INSTALL:
            self.builder.get_object("button_next").set_sensitive(False)
            self.builder.get_object("button_back").set_sensitive(False)
            self.builder.get_object("button_quit").set_sensitive(False)
            self.builder.get_object("dot_box").hide()
            self.window.resize(0, 0)
            GLib.timeout_add(100, self.set_slide_page)
            self.do_install()
        if errorFound:
            return
        if nex == None:
            nex = 0
        # progress images
        for i in range(9):
            img = self.builder.get_object("progress_%d" % i)
            if i <= nex:
                img.set_from_file(
                    "./resources/icons/live-installer-progress-dot-on.svg")
            else:
                img.set_from_file(
                    "./resources/icons/live-installer-progress-dot-off.svg")
        help_text = _(self.wizard_pages[nex].help_text)
        self.builder.get_object("help_label").set_markup(
            "<big><b>%s</b></big>" % help_text)
        self.builder.get_object("help_icon").set_from_icon_name(
            self.wizard_pages[nex].icon, Gtk.IconSize.LARGE_TOOLBAR)
        self.builder.get_object("help_question").set_text(
            self.wizard_pages[nex].question)
        self.builder.get_object("notebook1").set_visible_child_name(str(nex))

    def activate_page_type(self):
        if self.testmode or self.setup.expert_mode:
            self.activate_page(self.PAGE_USER)
            return
        self.show_overview()
        if self.setup.automated:
            errorFound = False
            errorMessage = ""
            if self.setup.disk is None:
                errorFound = True
                errorMessage = _("Please select a disk.")
            self.setup.grub_device = self.setup.disk
            if self.setup.luks:
                if (self.setup.passphrase1 is None or self.setup.passphrase1 == ""):
                    errorFound = True
                    errorMessage = _(
                        "Please provide a passphrase for the encryption.")
                elif (self.setup.passphrase1 != self.setup.passphrase1):
                    errorFound = True
                    errorMessage = _("Your passphrases do not match.")
            if (errorFound):
                WarningDialog(_("Installer"), errorMessage)
            else:
                if QuestionDialog(_("Warning"), _(
                        "This will delete all the data on %s. Are you sure?") % self.setup.diskname):
                    partitioning.build_partitions(self)
                    partitioning.build_grub_partitions()
                    if config.get("skip_user", False):
                        self.activate_page(self.PAGE_OVERVIEW)
                    else:
                        self.activate_page(self.PAGE_USER)
        elif self.setup.replace_windows:
            rootfs = partitioning.PartitionBase()
            rootfs.path = self.setup.winroot
            rootfs.format_as = 'ext4'
            rootfs.mount_as = '/'
            self.setup.partitions.append(rootfs)
            if self.setup.gptonefi:
                efifs = partitioning.PartitionBase()
                efifs.path = self.setup.winefi
                efifs.format_as = 'vfat'
                efifs.mount_as = '/boot/efi'
                self.setup.partitions.append(efifs)
            self.setup.grub_device = partitioning.find_mbr(
                self.setup.winroot)
            if self.setup.winboot:
                boot = partitioning.PartitionBase()
                boot.path = self.setup.winboot
                boot.format_as = 'vfat'
                boot.mount_as = None
                self.setup.partitions.append(boot)
            if config.get("skip_user", False):
                self.activate_page(self.PAGE_OVERVIEW)
            else:
                    self.activate_page(self.PAGE_USER)
        else:
            partitioning.build_partitions(self)
            partitioning.build_grub_partitions()
            self.activate_page(self.PAGE_PARTITIONS)

    def wizard_cb(self, widget, goback, data=None):
        ''' wizard buttons '''
        sel = int(self.builder.get_object(
            "notebook1").get_visible_child_name())
        self.builder.get_object("button_back").set_sensitive(True)
        nex = None
        # check each page for errors
        if not goback:
            if sel == self.PAGE_WELCOME:
                nex = self.PAGE_LANGUAGE
                self.builder.get_object("button_back").set_sensitive(True)
                if config.get("skip_language", False):
                    sel = nex
            if sel == self.PAGE_LANGUAGE:
                nex = self.PAGE_KEYBOARD
                if config.get("skip_keyboard", False):
                    sel = nex
            if sel == self.PAGE_KEYBOARD:
                nex = self.PAGE_TIMEZONE
                if config.get("skip_timezone", False):
                    sel = nex
            if sel == self.PAGE_TIMEZONE:
                nex = self.PAGE_TYPE
            if sel == self.PAGE_USER:
                nex = self.PAGE_OVERVIEW
            if sel == self.PAGE_TYPE:
                self.activate_page_type()
                return
            if sel == self.PAGE_PARTITIONS:
                if self.grub_check.get_active() and \
                   not self.setup.grub_device and \
                   not self.testmode:
                    WarningDialog(_("Installer"), _(
                        "Please provide a device to install grub."))
                    return
                nex = self.PAGE_USER
                if config.get("skip_user", False):
                    nex = self.PAGE_OVERVIEW
            if sel == self.PAGE_OVERVIEW:
                nex = self.PAGE_INSTALL
                self.activate_page(nex, nex)
        else:
        
            if sel == self.PAGE_OVERVIEW:
                nex = self.PAGE_USER
            if sel == self.PAGE_PARTITIONS:
                nex = self.PAGE_TYPE
            if sel == self.PAGE_USER:
                nex = self.PAGE_TYPE
            if sel == self.PAGE_TYPE:
                nex = self.PAGE_TIMEZONE
                if config.get("skip_timezone", False):
                    sel = nex
            if sel == self.PAGE_TIMEZONE:
                nex = self.PAGE_KEYBOARD
                if config.get("skip_keyboard", False):
                    sel = nex
            if sel == self.PAGE_KEYBOARD:
                nex = self.PAGE_LANGUAGE
                if config.get("skip_language", False):
                    sel = nex
            if sel == self.PAGE_LANGUAGE:
                nex = self.PAGE_WELCOME
                self.builder.get_object("button_back").set_sensitive(False)
        self.activate_page(nex, sel, goback)

    def show_overview(self):
        def bold(strvar):
            return '<b>' + str(strvar) + '</b>'
        model = Gtk.TreeStore(str)
        self.builder.get_object("treeview_overview").set_model(model)
        top = model.append(None, (_("Localization"),))
        model.append(top, (_("Language: ") + bold(self.setup.language),))
        model.append(top, (_("Timezone: ") + bold(self.setup.timezone),))
        model.append(top, (_("Keyboard layout: ") +
                           "<b>%s - %s %s</b>" % (self.setup.keyboard_model_description, self.setup.keyboard_layout_description,
                                                  '(%s)' % self.setup.keyboard_variant_description if self.setup.keyboard_variant_description else ''),))
        if not config.get("skip_user", False):
            top = model.append(None, (_("User settings"),))
            model.append(top, (_("Real name: ") + bold(self.setup.real_name),))
            model.append(top, (_("Username: ") + bold(self.setup.username),))
            model.append(
                top, (_("Password: ") + bold(len(str(self.setup.password1)) * "*"),))
            if config.get("autologin_enabled", True):
                model.append(top, (_("Automatic login: ") + bold(_("enabled")
                                                             if self.setup.autologin else _("disabled")),))
            if config.get("encryption_enabled", True):
                model.append(top, (_("Home encryption: ") + bold(_("enabled")
                                                             if self.setup.ecryptfs else _("disabled")),))
        top = model.append(None, (_("System settings"),))
        model.append(top, (_("Computer's name: ") +
                           bold(self.setup.hostname),))
        if self.setup.gptonefi:
            model.append(top, (_("Bios type: ") + bold("UEFI"),))
        else:
            model.append(top, (_("Bios type: ") + bold("Legacy"),))
        if self.setup.install_updates:
            model.append(top, (_("Install updates after installation"),))
        top = model.append(None, (_("Filesystem operations"),))
        model.append(top, (bold(_("Install bootloader on %s") % self.setup.grub_device)
                           if self.setup.grub_device else _("Do not install bootloader"),))
        if self.setup.skip_mount:
            model.append(top, (bold(_("Use already-mounted /target.")),))
            return
        if self.setup.automated:
            self.setup.grub_device = self.setup.disk
            model.append(
                top, (bold(_("Automated installation on %s") % self.setup.diskname),))
        else:
            if config.get("lvm_enabled", True):
                model.append(top, (_("LVM: ") + bold(_("enabled")
                                                     if self.setup.lvm else _("disabled")),))
            if config.get("encryption_enabled", True):
                model.append(top, (_("Disk Encryption: ") +
                                   bold(_("enabled") if self.setup.luks else _("disabled")),))
            for p in self.setup.partitions:
                if p.format_as:
                    model.append(top, (bold(_("Format %(path)s as %(filesystem)s") % {
                                 'path': p.path, 'filesystem': p.format_as}),))
            for p in self.setup.partitions:
                if p.mount_as:
                    model.append(top, (bold(_("Mount %(path)s as %(mount)s") % {
                                 'path': p.path, 'mount': p.mount_as}),))
        self.builder.get_object("treeview_overview").expand_all()

    @idle
    def show_error_dialog(self, message, detail):
        ErrorDialog(message, detail)
        if self.showing_last_dialog:
            self.showing_last_dialog = False

    @idle
    def show_reboot_dialog(self):
        reboot = QuestionDialog(_("Installation finished"), _(
            "The installation is now complete. Do you want to restart your computer to use the new system?"))
        if self.showing_last_dialog:
            self.showing_last_dialog = False
        if reboot:
            if config.get("use_reboot", False):
                os.system('reboot')
            else:
                os.system('echo 1 > /proc/sys/kernel/sysrq')
                os.system('sync')
                os.system('echo b > /proc/sysrq-trigger')

    @asynchronous
    def do_install(self):
        log(" ## INSTALLATION ")
        ''' Actually perform the installation .. '''

        self.installer.set_progress_hook(self.update_progress)
        self.installer.set_error_hook(self.error_message)

        # do we dare? ..
        self.critical_error_happened = False
        self.critical_error_message = ""

        # Start installing
        do_try_finish_install = True

        try:
            self.installer.start_installation()
        except Exception as detail1:
            raise detail1
            do_try_finish_install = False
            self.show_error_dialog(_("Installation error"), str(detail1))

        if self.critical_error_happened:
            self.show_error_dialog(
                _("Installation error"), self.critical_error_message)
            do_try_finish_install = False

        if do_try_finish_install:

            try:
                self.installer.finish_installation()
            except Exception as detail1:
                raise detail1
                self.show_error_dialog(_("Installation error"), str(detail1))

            # show a message dialog thingum
            while(not self.done):
                time.sleep(0.1)

            self.showing_last_dialog = True
            if self.critical_error_happened:
                self.show_error_dialog(
                    _("Installation error"), self.critical_error_message)
            else:
                self.show_reboot_dialog()

            while(self.showing_last_dialog):
                time.sleep(0.1)

            log(" ## INSTALLATION COMPLETE ")

        Gtk.main_quit()
        sys.exit(0)

    def error_message(self, message=""):
        self.critical_error_happened = True
        self.critical_error_message += message + "\n"

    @idle
    def update_progress(self, current, total, pulse, done, message, nolog):
        if not nolog:
            log(message)
        if not current:
            current = 1
        if not total:
            total = 1
        if(pulse):
            self.builder.get_object(
                "label_install_progress").set_label(self.maxlen(message))
            self.do_progress_pulse(message)
            self.builder.get_object("label_install_percent").set_label("")
            return
        if(done):
            self.should_pulse = False
            self.done = done
            self.builder.get_object("progressbar").set_fraction(1)
            self.builder.get_object(
                "label_install_progress").set_label(self.maxlen(message))
            self.builder.get_object(
                "label_install_percent").set_label("100.0%")
            return
        self.should_pulse = False
        _total = float(total)
        _current = float(current)
        pct = float(_current / _total)
        self.builder.get_object("progressbar").set_fraction(pct)
        self.builder.get_object("label_install_progress").set_label(self.maxlen(message))
        self.builder.get_object("label_install_percent").set_label(
            str(int(pct * 1000) / 10) + "%")

    def maxlen(self,string):
        string = str(string)
        if len(string) > 75:
            return string[0:72]+"..."
        return string

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

    def slideshow(self):
        self.images = os.listdir("branding/slides")
        self.images.sort()
        self.slides = Gtk.Stack()
        self.slides.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.slides.set_transition_duration(250)
        self.builder.get_object("slidebox").add(self.slides)
        self.max_slide_page = len(self.images) - 1
        for i in self.images:
            im = Gtk.Image()
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                "branding/slides/" + i, 832, 468, False)
            im.set_from_pixbuf(pixbuf)
            self.gtkimages.append(im)
            self.gtkpixbufs.append(pixbuf)
            page_num = self.images.index(i)
            self.slides.add_titled(im, str(page_num), str(page_num))
        self.cur_slide_pos = 0
        #GLib.timeout_add(100, self.set_slide_page)

    def set_slide_page(self):
        self.slides.set_visible_child_name(str(self.cur_slide_pos))
        self.cur_slide_pos = self.cur_slide_pos + 1
        if(self.cur_slide_pos > self.max_slide_page):
            self.cur_slide_pos = 0
        print(self.cur_slide_pos)
        GLib.timeout_add(15000, self.set_slide_page)
