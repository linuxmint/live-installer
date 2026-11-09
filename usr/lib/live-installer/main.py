#!/usr/bin/python3

from installer import InstallerEngine, Setup
from dialogs import MessageDialog, QuestionDialog, ErrorDialog, WarningDialog, ConfirmDialog
import distro
import timezones
import partitioning
import gettext
import os
import re
import subprocess
import sys
import threading
import time
import requests

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, Gdk, GdkPixbuf, WebKit2, Pango, GLib

gettext.install("live-installer", "/usr/share/locale")

USERNAME_REGEX = r"[a-z_]{1}[a-z0-9_-]*[$]?"
USERNAME_LENGTH = (1, 32)

# "[[:ascii:]]+" gives nested set warning. https://www.regular-expressions.info/posixbrackets.html
PASSWORD_REGEX = r"[\x00-\x7F]+"
PASSWORD_LENGTHS = (1, 2147483647)

HOSTNAME_REGEX = r"^(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])$"
HOSTNAME_MIN_LENGTH = 1
HOSTNAME_SEG_LENGTH = 63
HOSTNAME_TOTAL_LENGTH = 253

WHITESPACE_REGEX = r"[\s]+"
HAS_LOWER_REGEX = r"[A-Z]+"

DISTRIBUTION = distro.name()
VERSION = distro.version()
IS_MINT = os.path.exists("/usr/share/doc/ubuntu-system-adjustments/copyright") or "ubuntu" in distro.like()

NON_LATIN_KB_LAYOUTS = ['am', 'af', 'ara', 'ben', 'bd', 'bg', 'bn', 'bt', 'by', 'deva', 'et', 'ge', 'gh', 'gn', 'gr', 'guj', 'guru', 'id', 'il', 'iku', 'in', 'iq', 'ir', 'kan', 'kg', 'kh', 'kz', 'la', 'lao', 'lk', 'ma', 'mk', 'mm', 'mn', 'mv', 'mal', 'my', 'np', 'ori', 'pk', 'ru', 'rs', 'scc', 'sy', 'syr', 'tel', 'th', 'tj', 'tam', 'tz', 'ua', 'uz']

# Used as a decorator to run things in the background
def asynchronous(func):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper

# Used as a decorator to run things in the main loop, from another thread
def idle(func):
    def wrapper(*args, **kwargs):
        GLib.idle_add(func, *args, **kwargs)
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

    def __init__(self, expert_mode=False, oem_mode=False, oem_config=False):

        self.expert_mode = expert_mode
        self.oem_mode = oem_mode
        self.oem_config = oem_config

        # build the setup object (where we put all our choices)
        self.setup = Setup()
        self.setup.oem_mode = oem_mode
        self.setup.oem_config = oem_config
        self.setup.is_mint = IS_MINT
        self.installer = InstallerEngine(self.setup)

        self.resource_dir = '/usr/share/live-installer/'
        glade_file = os.path.join(self.resource_dir, 'interface.ui')
        self.builder = Gtk.Builder()
        self.builder.add_from_file(glade_file)

        # should be set early
        self.done = False
        self.fail = False
        self.paused = False
        self.showing_last_dialog = False

        # load the window object
        self.window = self.builder.get_object("main_window")
        # The icon is a scalable SVG, so GTK would only hand the window manager a 48px raster
        # which looks blurry when enlarged (e.g. in alt-tab). Provide several sizes.
        try:
            icon_theme = Gtk.IconTheme.get_default()
            self.window.set_icon_list([icon_theme.load_icon("live-installer", size, 0) for size in (16, 32, 48, 64, 128, 256)])
        except GLib.Error as e:
            print("could not load window icon: %s" % e.message)
        self.window.connect("delete-event", self.quit_cb)
        self.scale = self.window.get_scale_factor()

        if self.oem_config:
            self.window.connect('key-press-event', self.on_key_press)
            self.builder.get_object("event_box").get_style_context().add_class("live-installer-welcome-oem-config")
            self.builder.get_object("welcome_release_label").hide()
            self.builder.get_object("headerbar").set_show_close_button(False) # Hide all window controls
        else:
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
            self.builder.get_object("event_box").get_style_context().add_class("live-installer-welcome")

        # wizard pages
        (self.PAGE_LANGUAGE,
         self.PAGE_TIMEZONE,
         self.PAGE_KEYBOARD,
         self.PAGE_USER,
         self.PAGE_TYPE,
         self.PAGE_PARTITIONS,
         self.PAGE_ADVANCED,
         self.PAGE_OVERVIEW,
         self.PAGE_CUSTOMWARNING,
         self.PAGE_CUSTOMPAUSED,
         self.PAGE_INSTALL) = range(11)

        # set the button events (wizard_cb)
        self.builder.get_object("button_next").connect("clicked", self.wizard_cb, False)
        self.builder.get_object("button_back").connect("clicked", self.wizard_cb, True)
        self.builder.get_object("button_quit").connect("clicked", self.quit_cb)

        ren = Gtk.CellRendererPixbuf()
        ren.set_property("ypad", 6)
        ren.set_property("xpad", 6)
        col = Gtk.TreeViewColumn("", ren)
        col.add_attribute(ren, "pixbuf", 2)
        col.set_cell_data_func(ren, self.data_func_surface)
        self.builder.get_object("treeview_language_list").append_column(col)
        ren = Gtk.CellRendererText()
        ren.set_property("ypad", 6)
        self.language_column = Gtk.TreeViewColumn(_("Language"), ren, text=0)
        self.language_column.set_sort_column_id(0)
        self.language_column.set_expand(True)
        self.language_column.set_resizable(True)
        ren.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object("treeview_language_list").append_column(self.language_column)

        ren = Gtk.CellRendererText()
        ren.set_property("ypad", 6)
        self.country_column = Gtk.TreeViewColumn(_("Location"), ren, text=1)
        self.country_column.set_sort_column_id(1)
        self.country_column.set_expand(True)
        self.country_column.set_resizable(True)
        ren.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        self.builder.get_object("treeview_language_list").append_column(self.country_column)

        self.builder.get_object("treeview_language_list").connect("cursor-changed", self.assign_language)

        self.detect_geoip()

        # build timezones
        timezones.build_timezones(self)

        # type page
        model = Gtk.ListStore(str, str, str)
        model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        for disk_path, disk_description in partitioning.get_disks():
            iterator = model.append(("%s (%s)" % (disk_description, disk_path), disk_path,
                                     "%s <span alpha='60%%'>%s</span>" % (GLib.markup_escape_text(disk_description), GLib.markup_escape_text(disk_path))))
        self.builder.get_object("combo_disk").set_model(model)
        renderer_text = Gtk.CellRendererText()
        self.builder.get_object("combo_disk").pack_start(renderer_text, True)
        self.builder.get_object("combo_disk").add_attribute(renderer_text, "markup", 2)

        self.builder.get_object("entry_passphrase").connect("changed", self.assign_passphrase)
        self.builder.get_object("entry_passphrase2").connect("changed", self.assign_passphrase)
        self.builder.get_object("radio_automated").connect("toggled", self.assign_type_options)
        self.builder.get_object("radio_manual").connect("toggled", self.assign_type_options)
        for name in ("check_badblocks", "check_encrypt", "check_lvm"):
            self.builder.get_object(name).connect("notify::active", self.assign_type_options)
        for name, switch in (("event_badblocks", "check_badblocks"), ("event_encrypt", "check_encrypt"), ("event_lvm", "check_lvm")):
            self.builder.get_object(name).connect("button-release-event", self.toggle_switch, self.builder.get_object(switch))
        self.builder.get_object("combo_disk").connect("changed", self.assign_type_options)
        if len(model) == 1:
            self.builder.get_object("combo_disk").set_active(0)

        # partitions
        self.builder.get_object("button_expert").connect("clicked", self.show_customwarning)
        self.builder.get_object("button_gparted").connect("clicked", partitioning.manually_edit_partitions)
        self.builder.get_object("button_edit").connect("clicked", partitioning.edit_partition_dialog)
        self.builder.get_object("button_refresh").connect("clicked", lambda _: partitioning.build_partitions(self))
        self.builder.get_object("treeview_disks").get_selection().connect("changed", partitioning.selection_changed)
        self.builder.get_object("treeview_disks").connect("row_activated", partitioning.edit_partition_dialog)
        self.builder.get_object("treeview_disks").connect("button-release-event", partitioning.partitions_popup_menu)
        text = Gtk.CellRendererText()
        text.set_property("ypad", 4)
        for i in (partitioning.IDX_PART_PATH,
                  partitioning.IDX_PART_TYPE,
                  partitioning.IDX_PART_MOUNT_AS,
                  partitioning.IDX_PART_FORMAT_AS,
                  partitioning.IDX_PART_DESCRIPTION,
                  partitioning.IDX_PART_SIZE,
                  partitioning.IDX_PART_FREE_SPACE):
            col = Gtk.TreeViewColumn("", text, markup=i)  # real title is set in i18n()
            self.builder.get_object("treeview_disks").append_column(col)

        self.builder.get_object("entry_name").connect("notify::text", self.assign_realname)
        self.builder.get_object("entry_username").connect("notify::text", self.assign_username)
        self.hostname_handler_id = self.builder.get_object("entry_hostname").connect("notify::text", self.assign_hostname)

        # events for detecting password mismatch..
        self.builder.get_object("entry_password").connect("changed", self.assign_password)
        self.builder.get_object("entry_confirm").connect("changed", self.assign_password)

        for name in ("switch_password_login", "switch_encrypt_home"):
            self.builder.get_object(name).connect("notify::active", self.assign_login_options)
        for name, switch in (("event_password_login", "switch_password_login"), ("event_encrypt_home", "switch_encrypt_home")):
            self.builder.get_object(name).connect("button-release-event", self.toggle_switch, self.builder.get_object(switch))

        # link the checkbutton to the combobox
        grub_check = self.builder.get_object("checkbutton_grub")
        grub_box = self.builder.get_object("combobox_grub")
        grub_check.connect("notify::active", self.assign_grub_install, grub_box)
        self.builder.get_object("event_grub").connect("button-release-event", self.toggle_switch, grub_check)
        grub_box.connect("changed", self.assign_grub_device)

        # install Grub by default
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
        ren.set_property("ypad", 6)
        self.column10 = Gtk.TreeViewColumn(_("Layout"), ren)
        self.column10.add_attribute(ren, "text", 0)
        self.builder.get_object("treeview_layouts").append_column(self.column10)
        self.builder.get_object("treeview_layouts").connect("cursor-changed", self.assign_keyboard_layout)

        ren = Gtk.CellRendererText()
        ren.set_property("ypad", 6)
        self.column11 = Gtk.TreeViewColumn(_("Variant"), ren)
        self.column11.add_attribute(ren, "text", 0)
        self.builder.get_object("treeview_variants").append_column(self.column11)
        self.builder.get_object("treeview_variants").connect("cursor-changed", self.assign_keyboard_variant)

        self.build_kb_lists()

        # i18n
        self.i18n()

        # pre-fill user details in debug mode
        if __debug__:
            self.builder.get_object("entry_name").set_text("John Boone")
            self.builder.get_object("entry_username").set_text("john")
            self.builder.get_object("entry_hostname").set_text("mars")
            self.builder.get_object("entry_password").set_text("dummy_password")
            self.builder.get_object("entry_confirm").set_text("dummy_password")

        if self.oem_mode:
            self.builder.get_object("entry_name").set_text("Temporary OEM User")
            self.builder.get_object("entry_name").set_sensitive(False)
            self.builder.get_object("entry_username").set_text("oem")
            self.builder.get_object("entry_username").set_sensitive(False)
            self.builder.get_object("entry_hostname").set_text("oem")
            self.builder.get_object("entry_hostname").set_sensitive(False)
            self.builder.get_object("entry_password").set_text("oem")
            self.builder.get_object("entry_password").set_visibility(True)
            self.builder.get_object("entry_password").set_sensitive(False)
            self.builder.get_object("entry_confirm").set_text("oem")
            self.builder.get_object("entry_confirm").set_visibility(True)
            self.builder.get_object("entry_confirm").set_sensitive(False)
            self.builder.get_object("switch_password_login").set_active(False)
            self.builder.get_object("switch_password_login").set_sensitive(False)
            self.builder.get_object("check_lvm").set_sensitive(False)
            self.builder.get_object("check_badblocks").set_sensitive(False)
            self.builder.get_object("check_encrypt").set_sensitive(False)
            self.builder.get_object("entry_passphrase").set_sensitive(False)
            self.builder.get_object("entry_passphrase2").set_sensitive(False)

        # build partition list
        self.should_pulse = False

        # initiate the slide show
        # We have no significant browsing interface, so there isn't much point
        # in WebKit creating a memory-hungry cache.
        context = WebKit2.WebContext.get_default()
        context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

        self.partitions_browser = WebKit2.WebView()
        # The ancestors' colors are only resolved once the window is mapped (and change with the theme)
        self.partitions_browser.connect('map', self.update_partitions_background)
        self.partitions_browser.connect('style-updated', self.update_partitions_background)
        s = self.partitions_browser.get_settings()
        s.set_allow_file_access_from_file_urls(True)
        self.partitions_browser.connect('context-menu', self.on_context_menu)
        self.partitions_browser.show()
        self.partitions_browser.set_size_request(-1, 80)
        self.builder.get_object("scrolled_partitions").add(self.partitions_browser)

        self.builder.get_object("button_lets_go").connect("clicked", self.on_lets_go_clicked)

        self.window.show_all()

    def update_partitions_background(self, widget=None):
        # A transparent webview shows the desktop through the window, so paint it with the
        # color of the nearest ancestor which has a background (the page's, in the current theme).
        parent = self.builder.get_object("scrolled_partitions")
        while parent is not None:
            color = parent.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
            if color.alpha > 0:
                self.partitions_browser.set_background_color(color)
                return
            parent = parent.get_parent()

    def on_key_press(self, widget, event):
        # CTRL+T key
        if (event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval == Gdk.KEY_t):
            subprocess.Popen(['x-terminal-emulator'])
            return True
        return False

    def on_lets_go_clicked(self, button):
        self.build_lang_list()
        self.activate_page(self.PAGE_LANGUAGE)
        self.builder.get_object("stack").set_visible_child_name("page_notebook")
        GLib.timeout_add(400, self.remove_dark_mode)

    def remove_dark_mode(self):
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", False)

    def on_context_menu(self, unused_web_view, unused_context_menu,
                    unused_event, unused_hit_test_result):
        # True will not show the menu
        return True

    def i18n(self):
        title = _("Installer")
        if self.oem_mode:
            title = "OEM Installation"
        if self.oem_config:
            title = _("Initial Setup")
        self.builder.get_object("button_expert").set_no_show_all(True)
        if self.expert_mode:
            self.builder.get_object("button_expert").show()
        else:
            self.builder.get_object("button_expert").hide()

        if __debug__:
            self.builder.get_object("debug_label").set_text("Debug mode")

        self.builder.get_object("headerbar").set_title(title)

        # Header
        self.wizard_pages = list(range(11))
        self.wizard_pages[self.PAGE_LANGUAGE] = WizardPage(_("Language"), "preferences-desktop-locale-symbolic", _("What language would you like to use?"))
        self.wizard_pages[self.PAGE_TIMEZONE] = WizardPage(_("Timezone"), "mark-location-symbolic", _("Where are you?"))
        self.wizard_pages[self.PAGE_KEYBOARD] = WizardPage(_("Keyboard layout"), "preferences-desktop-keyboard-symbolic", _("What is your keyboard layout?"))
        self.wizard_pages[self.PAGE_USER] = WizardPage(_("User account"), "avatar-default-symbolic", _("Who are you?"))
        self.wizard_pages[self.PAGE_TYPE] = WizardPage(_("Installation Type"), "drive-harddisk-system-symbolic", _("Where do you want to install %s?") % DISTRIBUTION)
        self.wizard_pages[self.PAGE_PARTITIONS] = WizardPage(_("Partitioning"), "drive-harddisk-system-symbolic", _("Where do you want to install %s?") % DISTRIBUTION)
        self.wizard_pages[self.PAGE_ADVANCED] = WizardPage(_("Advanced options"), "preferences-system-symbolic", _("Configure the boot menu"))
        self.wizard_pages[self.PAGE_OVERVIEW] = WizardPage(_("Summary"), "object-select-symbolic", _("Check that everything is correct"))
        self.wizard_pages[self.PAGE_INSTALL] = WizardPage(_("Installing"), "system-run-symbolic", "Please wait...")
        self.wizard_pages[self.PAGE_CUSTOMWARNING] = WizardPage(_("Expert mode"), "drive-harddisk-system-symbolic", "")
        self.wizard_pages[self.PAGE_CUSTOMPAUSED] = WizardPage(_("Installation paused"), "system-run-symbolic", "")

        # Buttons
        self.builder.get_object("button_quit").set_label(_("Quit"))
        self.builder.get_object("button_back").set_label(_("Back"))
        self.builder.get_object("button_next").set_label(_("Next"))

        # Welcome page
        image_widget = self.builder.get_object("welcome_image")
        if self.oem_config:
            # Localized to the language chosen by the manufacturer
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale("/usr/share/live-installer/welcome.svg", -1, 400 * self.scale, True)
            self.builder.get_object("welcome_label").set_markup("<b><span size='large'>%s</span></b>" % _("Welcome to your new computer!"))
            self.builder.get_object("welcome_button_label").set_markup("<b><span size='large'>%s</span></b>" % _("Start"))
        elif self.oem_mode:
            # Always in English
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale("/usr/share/live-installer/logo.svg", -1, 150 * self.scale, True)
            self.builder.get_object("welcome_release_label").set_text(f"Welcome to {DISTRIBUTION} {VERSION}!")
            self.builder.get_object("welcome_label").set_text(f"This program will pre-install {DISTRIBUTION} on this computer.")
            self.builder.get_object("welcome_button_label").set_label("Let's go!")
        else:
            # Always in English
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale("/usr/share/live-installer/logo.svg", -1, 150 * self.scale, True)
            self.builder.get_object("welcome_release_label").set_text(f"Welcome to {DISTRIBUTION} {VERSION}!")
            self.builder.get_object("welcome_label").set_text(f"This program will ask you some questions and install {DISTRIBUTION} on your computer.")
            self.builder.get_object("welcome_button_label").set_label("Let's go!")
        surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, self.scale, image_widget.get_window())
        image_widget.set_from_surface(surface)

        # Language page
        self.language_column.set_title(_("Language"))
        self.country_column.set_title(_("Location"))

        # Keyboard page
        self.builder.get_object("label_kb_model").set_label(_("Keyboard Model:"))
        self.column10.set_title(_("Layout"))
        self.column11.set_title(_("Variant"))
        self.builder.get_object("entry_test_kb").set_placeholder_text(_("Type here to test your keyboard layout"))
        self.builder.get_object("entry_test_kb").set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "input-keyboard-symbolic")
        self.builder.get_object("label_non_latin").set_text(_("* Your username, your computer's name and your password should only contain Latin characters. In addition to your selected layout, English (US) is set as the default. You can switch layouts by pressing Super+Space."))

        # User page
        self.builder.get_object("label_name").set_text(_("Your name:"))
        self.builder.get_object("label_hostname").set_text(_("Your computer's name:"))
        self.builder.get_object("label_hostname_help").set_text(_("The name it uses when it talks to other computers."))
        self.builder.get_object("label_username").set_text(_("Pick a username:"))
        self.builder.get_object("label_password").set_text(_("Choose a password:"))
        self.builder.get_object("label_confirm").set_text(_("Confirm your password:"))

        self.builder.get_object("label_password_login").set_label(_("Require my password to log in"))
        self.builder.get_object("label_encrypt_home").set_label(_("Encrypt my home folder"))

        # Type page
        self.builder.get_object("label_automated").set_text(_("Automated Installation"))
        self.builder.get_object("label_automated2").set_text(_("Erase a disk and install %s on it.") % DISTRIBUTION)
        self.builder.get_object("label_disk").set_text(_("Disk:"))
        self.builder.get_object("label_erase_warning").set_text(_("All data on the selected disk will be erased."))
        self.builder.get_object("label_encrypt").set_text(_("Encrypt the operating system"))
        self.builder.get_object("entry_passphrase").set_placeholder_text(_("Passphrase"))
        self.builder.get_object("entry_passphrase2").set_placeholder_text(_("Confirm passphrase"))
        self.builder.get_object("label_lvm").set_text(_("Use LVM (Logical Volume Management)"))
        self.builder.get_object("label_manual").set_text(_("Manual Partitioning"))
        self.builder.get_object("label_manual2").set_text(_("Manually create, resize or choose partitions for %s.") % DISTRIBUTION)
        self.builder.get_object("label_badblocks").set_text(_("Fill the disk with random data"))
        self.builder.get_object("check_badblocks").set_tooltip_text(_("This provides extra security but it can take hours."))

        # Partitions page
        self.builder.get_object("button_edit").set_label(_("Edit"))
        self.builder.get_object("button_gparted").set_label(_("Launch GParted"))
        self.builder.get_object("button_refresh").set_label(_("Refresh"))
        self.builder.get_object("button_expert").set_label(_("Expert mode"))
        for col, title in zip(self.builder.get_object("treeview_disks").get_columns(),
                              (_("Device"),
                               _("Type"),
                               _("Mount point"),
                               _("Format as"),
                               _("Operating system"),
                               _("Size"),
                               _("Free space"))):
            col.set_title(title)

        # Advanced page
        self.builder.get_object("label_grub").set_label(_("Install the GRUB boot menu"))
        self.builder.get_object("label_grub_device").set_label(_("Install on:"))
        self.builder.get_object("label_grub_hint").set_label(_("The default settings work for most computers. If you are unsure, leave them as they are. Change them only if you know your computer starts from another disk, or if you use a different boot manager."))

        # Custom install warning
        self.builder.get_object("label_custom_install_directions_1").set_label(_("You selected to manage your partitions manually, this feature is for ADVANCED USERS ONLY."))
        self.builder.get_object("label_custom_install_directions_2").set_label(_("Before continuing, mount your target filesystem(s) on /target."))
        self.builder.get_object("label_custom_install_directions_3").set_label(_("Do NOT mount virtual devices such as /dev, /proc, /sys, etc on /target/."))
        self.builder.get_object("label_custom_install_directions_4").set_label(_("During the install, you will be given time to chroot into /target and install any packages that will be needed to boot your new system."))
        self.builder.get_object("label_custom_install_directions_5").set_label(_("During the install, you will be required to write your own /etc/fstab."))

        # Custom install installation paused directions
        self.builder.get_object("label_custom_install_paused_1").set_label(_("Do the following and then click Next to finish installation:"))
        self.builder.get_object("label_custom_install_paused_2").set_label(_("Create /target/etc/fstab for the filesystems as they will be mounted in your new system, matching those currently mounted at /target (without using the /target prefix in the mount paths themselves)."))
        self.builder.get_object("label_custom_install_paused_3").set_label(_("Install any packages that may be needed for first boot (mdadm, cryptsetup, dmraid, etc) by calling \"sudo chroot /target\" followed by the relevant apt-get/aptitude installations."))
        self.builder.get_object("label_custom_install_paused_4").set_label(_("Note that in order for update-initramfs to work properly in some cases (such as dm-crypt), you may need to have drives currently mounted using the same block device name as they appear in /target/etc/fstab."))
        self.builder.get_object("label_custom_install_paused_5").set_label(_("Double-check that your /target/etc/fstab is correct, matches what your new system will have at first boot, and matches what is currently mounted at /target."))

        self.builder.get_object("label_install_progress").set_text(_("Calculating file indexes ..."))

        # Refresh the current title and help question in the page header
        self.activate_page(self.PAGE_LANGUAGE)

    def validate_entry(self, entry, validate_regex, len_range, check_lower=False, check_space=False, check_hyphens=False, check_match=None, also_true=True):
        error = None
        value = entry.props.text

        while True:
            if check_match != None and value != check_match:
                error = _("The values do not match.")
                break
            if len(value) == 0:
                error = _("This field cannot be empty.")
                break
            if check_space and re.search(WHITESPACE_REGEX, value):
                error = _("This field may not contain space characters.")
                break
            if check_lower and re.search(HAS_LOWER_REGEX, value):
                error = _("This field must be lower case.")
                break
            if check_hyphens and value[0] in ("-", ".") or value[-1] in ("-", "."):
                error = _("This field cannot start or end with a hyphen or period.")
                break
            if len_range:
                if len(value) < len_range[0]:
                    error = _("The entry is too short.")
                elif len(value) > len_range[1]:
                    error = _("The entry is too long.")
                    break

            r = re.fullmatch(validate_regex, value)
            if validate_regex != None and (not re.fullmatch(validate_regex, value)):
                error = _("This field contains invalid characters.")
                break
            break

        if error and also_true:
            entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "dialog-error")
            entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, error)
        else:
            entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
            entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "")

        return error

    def validate_user_page(self, focus_widget=False):
        for name in ["entry_name", "entry_hostname", "entry_username", "entry_password", "entry_confirm"]:
            entry = self.builder.get_object(name)
            if entry.get_text() == "" or entry.get_icon_name(Gtk.EntryIconPosition.SECONDARY) == "dialog-error":
                if focus_widget:
                    entry.grab_focus()
                if entry.is_visible():
                    self.builder.get_object("button_next").set_sensitive(False)
                return
        self.builder.get_object("button_next").set_sensitive(True)

    def hostname_too_long(self, hostname):
        segments = hostname.split(".")

        for segment in segments:
            if len(segment) > HOSTNAME_SEG_LENGTH:
                return True

        if len(hostname) > HOSTNAME_TOTAL_LENGTH:
            return True

        return False

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

        self.validate_entry(self.builder.get_object("entry_username"), USERNAME_REGEX, USERNAME_LENGTH, check_lower=True, check_space=True)
        self.validate_user_page()
        self.setup.print_setup()

    def assign_username(self, entry, prop):
        self.setup.username = entry.props.text

        self.validate_entry(entry, USERNAME_REGEX, USERNAME_LENGTH, check_lower=True, check_space=True)
        self.validate_user_page()
        self.setup.print_setup()

    def assign_hostname(self, entry, prop):
        self.setup.hostname = entry.props.text

        # Hostname is too complicated for validate_entry, as it can have multiple domains separated by a '.',
        # and there are length rules for each segment and the total length. Do the check here, then pass along
        # such length requirements as to trigger too short/long warnings.
        if self.hostname_too_long(self.setup.hostname):
            self.validate_entry(entry, HOSTNAME_REGEX, (0, 0), check_space=True, check_hyphens=True)
        else:
            self.validate_entry(entry, HOSTNAME_REGEX, (HOSTNAME_MIN_LENGTH, 9999), check_space=True, check_hyphens=True)

        self.validate_user_page()
        self.setup.print_setup()

    def assign_password(self, widget):
        self.setup.password1 = self.builder.get_object("entry_password").get_text()
        self.setup.password2 = self.builder.get_object("entry_confirm").get_text()

        self.validate_entry(self.builder.get_object("entry_password"), PASSWORD_REGEX, PASSWORD_LENGTHS)
        self.validate_entry(self.builder.get_object("entry_confirm"), PASSWORD_REGEX, PASSWORD_LENGTHS, check_match=self.setup.password1)
        self.validate_user_page()
        self.setup.print_setup()

    def assign_type_options(self, widget=None, data=None):
        self.setup.automated = self.builder.get_object("radio_automated").get_active()
        self.builder.get_object("combo_disk").set_sensitive(self.setup.automated)
        if not self.oem_mode:
            self.builder.get_object("check_badblocks").set_sensitive(self.setup.automated)
            self.builder.get_object("check_encrypt").set_sensitive(self.setup.automated)
            self.builder.get_object("check_lvm").set_sensitive(self.setup.automated)
            self.builder.get_object("entry_passphrase").set_sensitive(self.setup.automated)
            self.builder.get_object("entry_passphrase2").set_sensitive(self.setup.automated)
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

        self.setup.passphrase1 = self.builder.get_object("entry_passphrase").get_text()
        self.setup.passphrase2 = self.builder.get_object("entry_passphrase2").get_text()
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

        self.setup.badblocks = self.builder.get_object("check_badblocks").get_active()

        self.builder.get_object("revealer_automated").set_reveal_child(self.setup.automated)
        self.builder.get_object("revealer_passphrase").set_reveal_child(self.builder.get_object("check_encrypt").get_active())
        for name, selected in (("card_automated", self.setup.automated), ("card_manual", not self.setup.automated)):
            context = self.builder.get_object(name).get_style_context()
            if selected:
                context.add_class("selected")
            else:
                context.remove_class("selected")

        block_next = False

        if self.setup.automated:
            block_next = block_next or self.setup.disk == None
            if self.builder.get_object("check_encrypt").get_active():
                block_next = block_next or self.setup.passphrase1 in (None, "") or self.setup.passphrase1 != self.setup.passphrase2

        self.builder.get_object("button_next").set_sensitive(not block_next)
        self.setup.print_setup()

    def assign_passphrase(self, widget):
        self.setup.passphrase1 = self.builder.get_object("entry_passphrase").get_text()
        self.setup.passphrase2 = self.builder.get_object("entry_passphrase2").get_text()

        self.validate_entry(self.builder.get_object("entry_passphrase"), PASSWORD_REGEX, PASSWORD_LENGTHS, also_true=self.setup.luks)
        self.validate_entry(self.builder.get_object("entry_passphrase2"), PASSWORD_REGEX, PASSWORD_LENGTHS, check_match=self.setup.passphrase1, also_true=self.setup.luks)

        self.update_disk_selection_next()
        self.setup.print_setup()

    def update_disk_selection_next(self):
        error = self.setup.disk == None
        error = error or self.setup.luks and (self.setup.passphrase1 in (None, "") or self.setup.passphrase1 != self.setup.passphrase2)

        self.builder.get_object("button_next").set_sensitive(not error)

    def quit_cb(self, widget, data=None):
        if QuestionDialog(_("Quit?"), _("Are you sure you want to quit?")):
            Gtk.main_quit()
            return False
        else:
            return True

    def show_customwarning(self, widget):
        self.activate_page(self.PAGE_CUSTOMWARNING)

    def data_func_surface(self, column, cell, model, iter_, *args):
        pixbuf = model.get_value(iter_, 2)
        surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, self.scale)
        cell.set_property("surface", surface)

    def flag(self, lang, language, ccode):
        for filename in [language.lower(), ccode.lower(), "united_nations"]:
            path = f"/usr/share/circle-flags-svg/{filename}.svg"
            if os.path.exists(path):
                break
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, -1, 18 * self.scale)
        return pixbuf

    def detect_geoip(self):
        # Try to find out where we're located...
        try:
            lookup = requests.get('http://geoip.ubuntu.com/lookup', timeout=5).content.decode("UTF-8")
            self.cur_country_code = re.search('<CountryCode>(.*)</CountryCode>', lookup).group(1)
            self.cur_timezone = re.search('<TimeZone>(.*)</TimeZone>', lookup).group(1)
            if self.cur_country_code == 'None': self.cur_country_code = "US"
            if self.cur_timezone == 'None': self.cur_timezone = "America/New_York"
        except:
            self.cur_country_code, self.cur_timezone = "US", "America/New_York"  # no internet connection

    def build_lang_list(self):
        #Load countries into memory
        countries = {}
        iso_standard = "3166"
        if os.path.exists("/usr/share/xml/iso-codes/iso_3166-1.xml"):
            iso_standard = "3166-1"
        for line in subprocess.getoutput("isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
            ccode, cname = line.split(None, 1)
            countries[ccode] = cname

        #Load languages into memory
        languages = {}
        iso_standard = "639"
        if os.path.exists("/usr/share/xml/iso-codes/iso_639-2.xml"):
            iso_standard = "639-2"
        for line in subprocess.getoutput("isoquery --iso %s | cut -f3,4-" % iso_standard).split('\n'):
            cols = line.split(None, 1)
            if len(cols) > 1:
                name = cols[1].replace(";", ",")
                languages[cols[0]] = name
        for line in subprocess.getoutput("isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
            cols = line.split(None, 1)
            if len(cols) > 1:
                if cols[0] not in languages:
                    name = cols[1].replace(";", ",")
                    languages[cols[0]] = name

        # Construct language selection model
        model = Gtk.ListStore(str, str, GdkPixbuf.Pixbuf, str)
        set_iter = None
        for locale in subprocess.getoutput("awk -F'[@ .]' '/UTF-8/{ print $1 }' /usr/share/i18n/SUPPORTED | uniq").split('\n'):
            if '_' in locale:
                lang, ccode = locale.split('_')
                language = lang
                country = ccode
                try:
                    language = languages[lang]
                except:
                    print(f"Skipping unknown language code '{lang}'")
                    continue
                try:
                    country = countries[ccode]
                except:
                    pass
            else:
                lang = locale
                try:
                    language = languages[lang]
                except:
                    print(f"Skipping unknown language code '{lang}'")
                    continue
                country = ''
            language = language.split(",")[0].split("(")[0]
            country = country.split(",")[0].split("(")[0]
            if country == '':
                continue
            pixbuf = self.flag(lang, language, ccode)
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
            treeview.scroll_to_cell(path, None, True, 0.0, 0.0)

    def build_kb_lists(self):
        ''' Do some xml kung-fu and load the keyboard stuffs '''
        # Determine the layouts in use
        (keyboard_geom,
         self.setup.keyboard_layout) = subprocess.check_output("setxkbmap -query | awk '/^(model|layout)/{print $2}'", shell=True, encoding='utf-8').split()
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
        if selection.count_selected_rows() > 0:
            (model, iter) = selection.get_selected()
            if iter is not None:
                self.setup.language = model.get_value(iter, 3)
                self.setup.print_setup()
                gettext.translation('live-installer', "/usr/share/locale",
                                languages=[self.setup.language, self.setup.language.split('_')[0]],
                                fallback=True).install()  # Try e.g. zh_CN, zh, or fallback to hardcoded English
                try:
                    self.i18n()
                except:
                    pass # Best effort. Fails the first time as self.column1 doesn't exist yet.

    def toggle_switch(self, widget, event, switch):
        if switch.get_sensitive():
            switch.set_active(not switch.get_active())
        return True

    def assign_login_options(self, switch, data=None):
        password_login = self.builder.get_object("switch_password_login").get_active()
        encrypt_home = self.builder.get_object("switch_encrypt_home")
        if password_login:
            encrypt_home.set_sensitive(True)
        else:
            encrypt_home.set_active(False)
            encrypt_home.set_sensitive(False)

        self.setup.ecryptfs = encrypt_home.get_active()
        self.setup.autologin = not password_login
        self.setup.print_setup()

    def assign_grub_install(self, checkbox, pspec, grub_box):
        grub_box.set_sensitive(checkbox.get_active())
        self.builder.get_object("revealer_grub").set_reveal_child(checkbox.get_active())
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

        command = "setxkbmap -layout '%s' -variant '%s' -option grp:win_space_toggle" % (self.setup.keyboard_layout, self.setup.keyboard_variant)
        if not __debug__:
            os.system(command)
            self.setup.print_setup()

        # Set preview image
        self._generate_keyboard_layout_preview()

    @idle
    def _generate_keyboard_layout_preview(self, *args, **kwargs):
        filename = "/tmp/live-install-keyboard-layout.png"
        layout = self.setup.keyboard_layout.split(",")[-1]
        variant = self.setup.keyboard_variant.split(",")[-1]
        if variant == "":
            variant = None

        if self.builder.get_object("image_keyboard").get_scale_factor() > 1:
            hidpi = "hidpi"
        else:
            hidpi = "normal"

        os.system("/usr/lib/live-installer/generate_keyboard_layout.py %s %s %s %s" % (layout, variant, filename, hidpi))
        self._on_layout_generated()

    @idle
    def _on_layout_generated(self):
        filename = "/tmp/live-install-keyboard-layout.png"
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
        for i in range(8):
            dot = self.builder.get_object("progress_%d" % i)
            dot.set_valign(Gtk.Align.CENTER)
            context = dot.get_style_context()
            context.add_class("live-installer-progress-dot")
            if i <= index:
                context.add_class("done")
            else:
                context.remove_class("done")
            if i == index:
                context.add_class("current")
            else:
                context.remove_class("current")
        help_text = _(self.wizard_pages[index].help_text)
        self.builder.get_object("help_label").set_markup("<big><b>%s</b></big>" % help_text)
        self.builder.get_object("help_icon").set_from_icon_name(self.wizard_pages[index].icon, Gtk.IconSize.LARGE_TOOLBAR)
        self.builder.get_object("help_question").set_text(self.wizard_pages[index].question)
        self.builder.get_object("notebook1").set_current_page(index)
        # TODO: move other page-depended actions from the wizard_cb into here below
        if index == self.PAGE_LANGUAGE:
            self.builder.get_object("button_back").hide()
        else:
            self.builder.get_object("button_back").show()
        if index == self.PAGE_PARTITIONS:
            self.setup.skip_mount = False
        if index == self.PAGE_CUSTOMWARNING:
            self.setup.skip_mount = True

    def ensure_paths_unmounted(self, mounted):
        # Offer to unmount any mounted partitions that would cause format to fail.
        if not mounted:
            return True
        paths = ""
        for p in mounted:
            paths += f"{GLib.markup_escape_text(p)}\n"
        title = _("Unmount required")
        text = "<b>%s</b>" % _("The following partitions must be unmounted before proceeding:")

        if not ConfirmDialog(title, text, paths, _("Unmount"), _("Cancel")):
            return False

        failed = partitioning.unmount_partitions(mounted)

        if failed:
            details = "\n".join(failed)
            ErrorDialog(_("Installer"), _("Failed to unmount one or more partitions."), details)
            return False
        return True

    def wizard_cb(self, widget, goback, data=None):
        ''' wizard buttons '''
        sel = self.builder.get_object("notebook1").get_current_page()
        self.builder.get_object("button_back").set_sensitive(True)

        # check each page for errors
        if(not goback):
            if(sel == self.PAGE_LANGUAGE):
                if self.setup.language is None:
                    WarningDialog(_("Installer"), _("Please choose a language"))
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
                self.validate_user_page(focus_widget=True)
            elif(sel == self.PAGE_USER):
                if self.oem_config:
                    # OEM config, skip straight from user page to overview. HDD, GRUB is already set up.
                    self.activate_page(self.PAGE_OVERVIEW)
                    self.show_overview()
                    self.builder.get_object("button_next").set_label(_("Install"))
                else:
                    self.activate_page(self.PAGE_TYPE)
                    self.assign_type_options()
            elif(sel == self.PAGE_TYPE):
                if self.setup.automated:
                    if QuestionDialog(_("Warning"), _("This will delete all the data on %s. Are you sure?") % self.setup.diskname):
                        if not self.ensure_paths_unmounted(partitioning.get_mounted_partitions_on_target_disk(self.setup.disk)):
                            return
                        partitioning.build_partitions(self)
                        partitioning.build_grub_partitions()
                        self.activate_page(self.PAGE_ADVANCED)
                else:
                    self.activate_page(self.PAGE_PARTITIONS)
                    partitioning.build_partitions(self)
            elif(sel == self.PAGE_PARTITIONS):
                model = self.builder.get_object("treeview_disks").get_model()

                if not self.ensure_paths_unmounted(partitioning.get_mounted_target_partitions(self.setup.partitions)):
                    return

                # Check for root partition
                found_root_partition = False
                for partition in self.setup.partitions:
                    if(partition.mount_as == "/"):
                        found_root_partition = True
                        if partition.format_as is None or partition.format_as == "":
                            ErrorDialog(_("Installer"), _("Please indicate a filesystem to format the root (/) partition with before proceeding."))
                            return

                if not found_root_partition:
                    ErrorDialog(_("Installer"), "<b>%s</b>" % _("Please select a root (/) partition."), _(
                        "A root partition is needed to install Linux Mint on.\n\n"
                        " - Mount point: /\n - Recommended size: 30GB\n"
                        " - Recommended filesystem format: ext4\n"))
                    return

                if self.setup.gptonefi:
                    # Several ESPs may have been pre-assigned during detection,
                    # keep the one on the same disk as the root partition.
                    efi_partitions = [p for p in self.setup.partitions if p.mount_as == partitioning.EFI_MOUNT_POINT]
                    if len(efi_partitions) > 1:
                        root_disk = [p for p in self.setup.partitions if p.mount_as == "/"][0].partition.disk.device.path
                        same_disk = [p for p in efi_partitions if p.partition.disk.device.path == root_disk]
                        preferred = same_disk[0] if same_disk else efi_partitions[0]
                        partitioning.assign_mount_point(preferred, partitioning.EFI_MOUNT_POINT, preferred.format_as)
                        efi_partitions = [preferred]

                    for partition in efi_partitions:
                        if not partition.is_esp:
                            ErrorDialog(_("Installer"), _("%s is not an EFI System Partition. Please edit the partition type or flags, or select another partition.") % partition.path)
                            return
                        if int(float(partition.partition.getLength('MB'))) < 35:
                            ErrorDialog(_("Installer"), _("The EFI partition (%s) is too small. It must be at least 35MB.") % partition.path)
                            return
                        if partition.format_as:
                            fs = partition.format_as
                        else:
                            fs = partition.type
                        if fs not in ("vfat", "fat32", "fat16"):
                            ErrorDialog(_("Installer"), _("The EFI partition (%s) must be formatted as vfat.") % partition.path)
                            return

                    if not efi_partitions:
                        ErrorDialog(_("Installer"), "<b>%s</b>" % _("Please select an EFI partition."),_("An EFI system partition is needed with the following requirements:\n\n - Mount point: /boot/efi\n - Partition type: EFI System Partition (the 'esp' flag)\n - Size: at least 35MB (100MB or more recommended)\n - Format: vfat or fat32\n\nTo ensure compatibility with Windows we recommend you use the first partition of the disk as the EFI system partition.\n "))
                        return

                partitioning.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)

            elif(sel == self.PAGE_CUSTOMWARNING):
                if not os.path.exists("/target"):
                    ErrorDialog(_("Installer"), _("Before continuing, mount your target filesystem(s) on /target."))
                    return
                partitioning.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)
            elif(sel == self.PAGE_ADVANCED):
                self.activate_page(self.PAGE_OVERVIEW)
                self.show_overview()
                self.builder.get_object("button_next").set_label(_("Install"))
            elif(sel == self.PAGE_OVERVIEW):
                self.activate_page(self.PAGE_INSTALL)
                self.builder.get_object("button_next").set_sensitive(False)
                self.builder.get_object("button_back").set_sensitive(False)
                self.builder.get_object("button_quit").set_sensitive(False)
                if self.oem_config:
                    self.do_oem_config()
                else:
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
                self.builder.get_object("button_next").set_sensitive(False)
                self.builder.get_object("button_back").set_sensitive(False)
                self.builder.get_object("button_quit").set_sensitive(False)
                self.builder.get_object("title_eventbox").hide()
                self.builder.get_object("button_eventbox").hide()
                self.window.resize(100, 100)
                self.paused = False
        else:
            self.builder.get_object("button_back").set_sensitive(True)
            self.builder.get_object("button_next").set_sensitive(True)
            self.builder.get_object("button_next").set_label(_("Next"))
            if(sel == self.PAGE_OVERVIEW):
                if self.oem_config:
                    self.activate_page(self.PAGE_USER)
                else:
                    self.activate_page(self.PAGE_ADVANCED)
            elif(sel == self.PAGE_ADVANCED):
                if (self.setup.skip_mount):
                    self.activate_page(self.PAGE_CUSTOMWARNING)
                elif self.setup.automated:
                    self.activate_page(self.PAGE_TYPE)
                else:
                    self.activate_page(self.PAGE_PARTITIONS)
            elif(sel == self.PAGE_CUSTOMWARNING):
                self.activate_page(self.PAGE_PARTITIONS)
            elif(sel == self.PAGE_PARTITIONS):
                self.activate_page(self.PAGE_TYPE)
            elif(sel == self.PAGE_TYPE):
                self.activate_page(self.PAGE_USER)
            elif(sel == self.PAGE_USER):
                self.activate_page(self.PAGE_KEYBOARD)
            elif(sel == self.PAGE_KEYBOARD):
                self.activate_page(self.PAGE_TIMEZONE)
            elif(sel == self.PAGE_TIMEZONE):
                self.activate_page(self.PAGE_LANGUAGE)

    def show_overview(self):
        box = self.builder.get_object("box_overview")
        for child in box.get_children():
            box.remove(child)
        keys = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        escape = GLib.markup_escape_text
        dim = lambda text: "<span alpha='60%%'>%s</span>" % escape(text)
        on_off = lambda value: _("On") if value else _("Off")
        key_text = lambda msgid: msgid.strip().rstrip(":\uff1a").strip()  # reuse the "Language: " strings

        def section(title):
            heading = Gtk.Label(label=title, xalign=0)
            heading.get_style_context().add_class("live-installer-overview-heading")
            grid = Gtk.Grid(row_spacing=6, column_spacing=16, margin_start=12)
            wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            wrapper.pack_start(heading, False, False, 0)
            wrapper.pack_start(grid, False, False, 0)
            box.pack_start(wrapper, False, False, 0)
            rows = [0]

            def add(key, value):
                value_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
                value_label.set_markup(value)
                if key is None:
                    grid.attach(value_label, 0, rows[0], 2, 1)
                else:
                    key_label = Gtk.Label(label=key_text(key), xalign=0, yalign=0)
                    key_label.get_style_context().add_class("live-installer-form-hint")
                    keys.add_widget(key_label)
                    grid.attach(key_label, 0, rows[0], 1, 1)
                    grid.attach(value_label, 1, rows[0], 1, 1)
                rows[0] += 1
            return add

        if self.setup.automated and not self.oem_config and not self.setup.skip_mount:
            notice = Gtk.Box(spacing=12)
            notice.get_style_context().add_class("live-installer-notice")
            notice.pack_start(Gtk.Image.new_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.DIALOG), False, False, 0)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
            title = Gtk.Label(xalign=0, wrap=True)
            title.set_markup("<b>%s</b>" % escape(_("All data on the selected disk will be erased.")))
            detail = Gtk.Label(xalign=0, wrap=True)
            detail.set_markup(escape(self.setup.diskname or ""))
            text.pack_start(title, False, False, 0)
            text.pack_start(detail, False, False, 0)
            notice.pack_start(text, True, True, 0)
            box.pack_start(notice, False, False, 0)

        language = self.setup.language
        for row in self.builder.get_object("treeview_language_list").get_model() or []:
            if row[3] == self.setup.language:
                language = "%s (%s)" % (row[0], row[1])
                break
        timezone = self.setup.timezone or ""
        if "/" in timezone:
            timezone = "%s, %s" % (timezone.rpartition("/")[2].replace("_", " "), timezone.partition("/")[0].replace("_", " "))
        keyboard = escape(self.setup.keyboard_layout_description or "")
        variant = self.setup.keyboard_variant_description
        if variant and variant != self.setup.keyboard_layout_description:
            keyboard += " (%s)" % escape(variant)
        if self.setup.keyboard_model_description:
            keyboard += "  " + dim(self.setup.keyboard_model_description)

        add = section(_("Localization"))
        add(_("Language: "), escape(language or ""))
        add(_("Timezone: "), escape(timezone))
        add(_("Keyboard layout: "), keyboard)

        add = section(_("User settings"))
        add(_("Real name: "), escape(self.setup.real_name or ""))
        add(_("Username: "), escape(self.setup.username or ""))
        add(_("Automatic login: "), on_off(self.setup.autologin))
        add(_("Home encryption: "), on_off(self.setup.ecryptfs))

        add = section(_("System settings"))
        add(_("Computer's name: "), escape(self.setup.hostname or ""))

        if not self.oem_config:
            add = section(_("Filesystem operations"))
            add(None, escape(_("Install bootloader on %s") % self.setup.grub_device) if self.setup.grub_device else escape(_("Do not install bootloader")))
            if self.setup.skip_mount:
                add(None, escape(_("Use already-mounted /target.")))
            elif self.setup.automated:
                add(None, escape(_("Automated installation on %s") % self.setup.diskname))
                add(_("LVM: "), on_off(self.setup.lvm))
                add(_("Disk Encryption: "), on_off(self.setup.luks))
            else:
                for p in self.setup.partitions:
                    if p.format_as:
                        add(None, escape(_("Format %(path)s as %(filesystem)s") % {'path': p.path, 'filesystem': p.format_as}))
                for p in self.setup.partitions:
                    if p.mount_as:
                        add(None, escape(_("Mount %(path)s as %(mount)s") % {'path': p.path, 'mount': p.mount_as}))
        box.show_all()

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
        self.builder.get_object("button_next").set_sensitive(True)
        self.builder.get_object("button_next").set_label(_("Next"))
        self.builder.get_object("button_back").hide()
        self.builder.get_object("button_quit").hide()
        self.builder.get_object("title_eventbox").show()
        self.builder.get_object("button_eventbox").show()
        MessageDialog(_("Installation paused"), _("The installation is now paused. Please read the instructions on the page carefully before clicking Next to finish the installation."))

    @asynchronous
    def do_oem_config(self):
        print(" ## OEM CONFIG ")
        print(' Actually perform the OEM config .. ')

        if __debug__:
            print(" ## DEBUG MODE - OEM CONFIG NOT LAUNCHED")
            time.sleep(200)
            Gtk.main_quit()
            sys.exit(0)

        self.installer.set_progress_hook(self.update_progress)
        self.installer.set_error_hook(self.set_critical_error)
        self.critical_error_happened = False

        try:
            self.installer.perform_oem_config()
        except Exception as detail:
            self.set_critical_error(str(detail))

        if self.critical_error_happened:
            self.showing_last_dialog = True
            self.show_error_dialog(_("OEM config error"), self.critical_error_message)
            while(self.showing_last_dialog):
                time.sleep(0.1)

        print(" ## OEM CONFIG COMPLETE ")

        os.system("reboot")
        Gtk.main_quit()
        sys.exit(0)

    @asynchronous
    def do_install(self):
        print(" ## INSTALLATION ")
        print(' Actually perform the installation .. ')

        if __debug__:
            print(" ## DEBUG MODE - INSTALLATION PROCESS NOT LAUNCHED")
            for partition in self.setup.partitions:
                if(partition.format_as is not None and partition.format_as != ""):
                    partition.type = partition.format_as
            self.installer.write_fstab("dummy_fstab")
            self.installer.write_mtab("dummy_fstab", "dummy_mtab")
            self.installer.write_crypttab("dummy_crypttab")
            time.sleep(200)
            Gtk.main_quit()
            sys.exit(0)

        self.installer.set_progress_hook(self.update_progress)
        self.installer.set_error_hook(self.set_critical_error)
        self.critical_error_happened = False

        try:
            self.installer.start_installation()
        except Exception as detail1:
            self.set_critical_error(str(detail1))

        if not self.critical_error_happened:
            if(self.setup.skip_mount):
                self.paused = True
                self.pause_installation()
                while(self.paused):
                    time.sleep(0.1)

            try:
                self.installer.finish_installation()
            except Exception as detail1:
                self.set_critical_error(str(detail1))

        # Wait for the end of the installation (or a critical error)
        while(not self.done):
            time.sleep(0.1)

        # Either show a critical error, or a reboot dialog
        # In both cases, wait for the user to acknowledge the dialog before quitting the installer
        self.showing_last_dialog = True
        if self.critical_error_happened:
            self.show_error_dialog(_("Installation error"), self.critical_error_message)
        else:
            self.show_reboot_dialog()
        while(self.showing_last_dialog):
            time.sleep(0.1)

        Gtk.main_quit()
        sys.exit(0)

    def set_critical_error(self, message=""):
        print(" ## CRITICAL ERROR: %s" % message)
        self.critical_error_happened = True
        self.critical_error_message = message
        self.done = True

    @idle
    def update_progress(self, percentage, pulse, done, message):
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
        fraction = float(percentage) / float(100)
        self.builder.get_object("progressbar").set_fraction(fraction)
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
            GLib.timeout_add(100, pbar_pulse)
        else:
            # asssume we're "pulsing" already
            self.should_pulse = True
            pbar_pulse()

# main entry
if __name__ == "__main__":
    expert_mode = "--expert-mode" in sys.argv
    oem_mode = "--oem-mode" in sys.argv
    oem_config = "--oem-config" in sys.argv
    if "oem-mode" in subprocess.getoutput("cat /proc/cmdline"):
        oem_mode = True
    win = InstallerWindow(expert_mode, oem_mode, oem_config)
    Gtk.main()
