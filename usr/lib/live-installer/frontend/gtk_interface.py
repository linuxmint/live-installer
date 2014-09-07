#!/usr/bin/env python

from installer import InstallerEngine, Setup
from slideshow import Slideshow
import partitioning

import pygtk; pygtk.require("2.0")
import gtk
import gtk.glade
import glib
import gettext
import os
import commands
import subprocess
import sys
import math
from PIL import Image
import threading
import gobject
import time
import webkit
import GeoIP
import urllib
import parted

gettext.install("live-installer", "/usr/share/linuxmint/locale")
gtk.gdk.threads_init()

LOADING_ANIMATION = '/usr/share/live-installer/loading.gif'

class ProgressDialog:
    
    def __init__(self):
        self.glade = '/usr/share/live-installer/interface.glade'
        self.dTree = gtk.glade.XML(self.glade, 'progress_window')
        self.window = self.dTree.get_widget('progress_window')
        self.progressbar = self.dTree.get_widget('progressbar_operation')
        self.label = self.dTree.get_widget('label_operation')
        self.should_pulse = False
        
    def show(self, label=None, title=None):
        def pbar_pulse():
            if(not self.should_pulse):
                return False
            self.progressbar.pulse()
            return self.should_pulse
        if(label is not None):
            self.label.set_markup(label)
        if(title is not None):
            self.window.set_title(title)
        self.should_pulse = True
        self.window.show_all()
        gobject.timeout_add(100, pbar_pulse)
        
    def hide(self):
        self.should_pulse = False
        self.window.hide()  

''' Handy. Makes message dialogs easy :D '''
class MessageDialog(object):

    def __init__(self, title, message, style, parent=None, secondary_message=None):
        self.title = title
        self.message = message
        self.style = style
        self.parent = parent
        self.secondary_message = secondary_message

    ''' Show me on screen '''
    def show(self):

        dialog = gtk.MessageDialog(self.parent, gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, self.style, gtk.BUTTONS_OK, self.message)
        dialog.set_title(self.title)
        dialog.set_position(gtk.WIN_POS_CENTER)
        dialog.set_icon_from_file("/usr/share/icons/live-installer.png")
        dialog.set_markup(self.message)
        if self.secondary_message is not None:
            dialog.format_secondary_markup(self.secondary_message)
        dialog.run()
        dialog.destroy()
        
class QuestionDialog(object):
    def __init__(self, title, message, parent=None):
        self.title = title
        self.message = message       
        self.parent = parent

    ''' Show me on screen '''
    def show(self):    
        dialog = gtk.MessageDialog(self.parent, gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_QUESTION, gtk.BUTTONS_YES_NO, self.message)
        dialog.set_title(self.title)
        dialog.set_position(gtk.WIN_POS_CENTER)
        dialog.set_icon_from_file("/usr/share/icons/live-installer.png")
        answer = dialog.run()
        if answer==gtk.RESPONSE_YES:
            return_value = True
        else:
            return_value = False
        dialog.destroy()
        return return_value    

class WizardPage:

    def __init__(self, help_text, icon):
        self.help_text = help_text    
        self.icon = icon    
        
class Timezone:
    def __init__(self, name, country_code, coordinates):
        self.height = 409 # Height of the map
        self.width = 800 # Width of the map
        self.name = name
        self.country_code = country_code
        self.coordinates = coordinates
        latlongsplit = coordinates.find('-', 1)
        if latlongsplit == -1:
            latlongsplit = coordinates.find('+', 1)
        if latlongsplit != -1:
            self.latitude = coordinates[:latlongsplit]
            self.longitude = coordinates[latlongsplit:]
        else:
            self.latitude = coordinates
            self.longitude = '+0'
        
        self.latitude = self.parse_position(self.latitude, 2)
        self.longitude = self.parse_position(self.longitude, 3)
        
        (self.x, self.y) = self.getPosition(self.latitude, self.longitude)            
    
    def parse_position(self, position, wholedigits):
        if position == '' or len(position) < 4 or wholedigits > 9:
            return 0.0
        wholestr = position[:wholedigits + 1]
        fractionstr = position[wholedigits + 1:]
        whole = float(wholestr)
        fraction = float(fractionstr)
        if whole >= 0.0:
            return whole + fraction / pow(10.0, len(fractionstr))
        else:
            return whole - fraction / pow(10.0, len(fractionstr))            
        
    # @return pixel coordinate of a latitude and longitude for self
    # map uses Miller Projection, but is also clipped
    def getPosition(self, la, lo):
        # need to add/sub magic numbers because the map doesn't actually go from -180...180, -90...90
        # thus the upper corner is not -180, -90 and we have to compensate
        # we need a better method of determining the actually range so we can better place citites (shtylman)
        xdeg_offset = -6
        # the 180 - 35) accounts for the fact that the map does not span the entire -90 to 90
        # the map does span the entire 360 though, just offset
        x = (self.width * (180.0 + lo) / 360.0) + (self.width * xdeg_offset/ 180.0)
        x = x % self.width

        #top and bottom clipping latitudes
        topLat = 81
        bottomLat = -59

        #percent of entire possible range
        topPer = topLat/180.0

        # get the y in rectangular coordinates
        y = 1.25 * math.log(math.tan(math.pi/4.0 + 0.4 * math.radians(la)))

        # calculate the map range (smaller than full range because the map is clipped on top and bottom
        fullRange = 4.6068250867599998
        # the amount of the full range devoted to the upper hemisphere
        topOffset = fullRange*topPer
        mapRange = abs(1.25 * math.log(math.tan(math.pi/4.0 + 0.4 * math.radians(bottomLat))) - topOffset)

        # Convert to a percentage of the map range
        y = abs(y - topOffset)
        y = y / mapRange

        # this then becomes the percentage of the height
        y = y * self.height

        return (int(x), int(y))        
        
class InstallerWindow:
    # Cancelable timeout for keyboard preview generation, which is
    # quite expensive, so avoid drawing it if only scrolling through
    # the keyboard layout list
    kbd_preview_generation = -1

    def __init__(self, fullscreen=False):
        
        #Disable the screensaver to prevent a segfault situation in GTK2
        os.system("sudo -u mint gsettings set org.cinnamon.desktop.screensaver lock-enabled false 2> /dev/null")
        os.system("sudo -u mint gsettings set org.mate.screensaver lock-enabled false 2> /dev/null")
        
        #Build the Setup object (where we put all our choices)
        self.setup = Setup()
        
        self.resource_dir = '/usr/share/live-installer/'
        #self.glade = 'interface.glade'
        self.glade = os.path.join(self.resource_dir, 'interface.glade')
        self.wTree = gtk.glade.XML(self.glade, 'main_window')

        # should be set early
        self.done = False
        self.fail = False
        self.paused = False

        # here comes the installer engine
        self.installer = InstallerEngine()
        # the distribution name
        DISTRIBUTION_NAME = self.installer.get_distribution_name()
        # load the window object
        self.window = self.wTree.get_widget("main_window")
        if __debug__:
            self.window.set_title((_("%s Installer") % DISTRIBUTION_NAME) + ' (debug)')
        else:
            self.window.set_title((_("%s Installer") % DISTRIBUTION_NAME))
        self.window.connect("destroy", self.quit_cb)

        # Wizard pages
        (self.PAGE_LANGUAGE,
         self.PAGE_PARTITIONS,
         self.PAGE_USER,
         self.PAGE_ADVANCED,
         self.PAGE_KEYBOARD,
         self.PAGE_OVERVIEW,
         self.PAGE_INSTALL,
         self.PAGE_TIMEZONE,
         self.PAGE_CUSTOMWARNING,
         self.PAGE_CUSTOMPAUSED) = range(10)
        self.wizard_pages = range(10)
        self.wizard_pages[self.PAGE_LANGUAGE] = WizardPage(_("Language"), "locales.png")
        self.wizard_pages[self.PAGE_TIMEZONE] = WizardPage(_("Timezone"), "time.png")
        self.wizard_pages[self.PAGE_KEYBOARD] = WizardPage(_("Keyboard layout"), "keyboard.png")
        self.wizard_pages[self.PAGE_USER] = WizardPage(_("User info"), "user.png")
        self.wizard_pages[self.PAGE_PARTITIONS] = WizardPage(_("Partitioning"), "hdd.svg")
        self.wizard_pages[self.PAGE_CUSTOMWARNING] = WizardPage(_("Please make sure you wish to manage partitions manually"), "hdd.svg")
        self.wizard_pages[self.PAGE_ADVANCED] = WizardPage(_("Advanced options"), "advanced.png")
        self.wizard_pages[self.PAGE_OVERVIEW] = WizardPage(_("Summary"), "summary.png")
        self.wizard_pages[self.PAGE_INSTALL] = WizardPage(_("Installing Linux Mint..."), "install.png")
        self.wizard_pages[self.PAGE_CUSTOMPAUSED] = WizardPage(_("Installation is paused: please finish the custom installation"), "install.png")
        
        # set the button events (wizard_cb)
        self.wTree.get_widget("button_next").connect("clicked", self.wizard_cb, False)
        self.wTree.get_widget("button_back").connect("clicked", self.wizard_cb, True)
        self.wTree.get_widget("button_quit").connect("clicked", self.quit_cb)

        col = gtk.TreeViewColumn("", gtk.CellRendererPixbuf(), pixbuf=2)
        self.wTree.get_widget("treeview_language_list").append_column(col)
        ren = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Language"), ren, text=0)
        col.set_sort_column_id(0)
        self.wTree.get_widget("treeview_language_list").append_column(col)
        col = gtk.TreeViewColumn(_("Country"), ren, text=1)
        col.set_sort_column_id(1)
        self.wTree.get_widget("treeview_language_list").append_column(col)

        self.wTree.get_widget("treeview_language_list").connect("cursor-changed", self.assign_language)

        # build user info page
        self.wTree.get_widget("face_select_picture_button").connect( "button-release-event", self.face_select_picture_button_clicked)        
        self.wTree.get_widget("face_take_picture_button").connect( "button-release-event", self.face_take_picture_button_clicked)           
        os.system("convert /usr/share/pixmaps/faces/t9penguino_trans.png -resize x96 /tmp/live-installer-face.png")
        self.wTree.get_widget("face_image").set_from_file("/tmp/live-installer-face.png")   
        
        webcam_detected = False
        try:
            import cv
            capture = cv.CaptureFromCAM(-1) 
            for i in range(10):
                img = cv.QueryFrame(capture)        
                if img != None:                    
                    webcam_detected = True   
        except Exception, detail:
            print detail

        if webcam_detected:
            self.wTree.get_widget("face_take_picture_button").set_tooltip_text(_("Click this button to take a new picture of yourself with the webcam."))
        else:
            self.wTree.get_widget("face_take_picture_button").set_sensitive(False)
            self.wTree.get_widget("face_take_picture_button").set_tooltip_text(_("The installer did not detect any webcams."))
        
        # build the language list
        self.build_lang_list()        

        self.build_timezones()

        # partitions
        self.wTree.get_widget("label_edit_partitions").set_label(_("_Edit partitions"))
        self.wTree.get_widget("label_custommount").set_label(_("_Expert mode"))
        self.wTree.get_widget("button_custommount").connect("clicked", self.show_customwarning)
        self.wTree.get_widget("button_edit").connect("clicked", partitioning.manually_edit_partitions)
        self.wTree.get_widget("button_refresh").connect("clicked", lambda _: partitioning.build_partitions(self))
        self.wTree.get_widget("treeview_disks").get_selection().connect("changed", partitioning.update_html_preview)
        self.wTree.get_widget("treeview_disks").connect("row_activated", partitioning.edit_partition_dialog)
        self.wTree.get_widget("treeview_disks").connect("button-release-event", partitioning.partitions_popup_menu)
        text = gtk.CellRendererText()
        for i in (partitioning.IDX_PART_PATH,
                  partitioning.IDX_PART_TYPE,
                  partitioning.IDX_PART_DESCRIPTION,
                  partitioning.IDX_PART_MOUNT_AS,
                  partitioning.IDX_PART_FORMAT_AS,
                  partitioning.IDX_PART_SIZE,
                  partitioning.IDX_PART_FREE_SPACE):
            col = gtk.TreeViewColumn("", text, markup=i)  # real title is set in i18n()
            self.wTree.get_widget("treeview_disks").append_column(col)

        self.wTree.get_widget("entry_your_name").connect("notify::text", self.assign_realname)        
        self.wTree.get_widget("entry_username").connect("notify::text", self.assign_username)    
        self.wTree.get_widget("entry_hostname").connect("notify::text", self.assign_hostname)    

        # events for detecting password mismatch..        
        self.wTree.get_widget("entry_userpass1").connect("changed", self.assign_password)
        self.wTree.get_widget("entry_userpass2").connect("changed", self.assign_password)

        # link the checkbutton to the combobox
        grub_check = self.wTree.get_widget("checkbutton_grub")
        grub_box = self.wTree.get_widget("combobox_grub")
        grub_check.connect("toggled", self.assign_grub_install, grub_box)
        grub_box.connect("changed", self.assign_grub_device)

        # Install Grub by default
        grub_check.set_active(True)
        grub_box.set_sensitive(True)
        
        # kb models
        cell = gtk.CellRendererText()
        self.wTree.get_widget("combobox_kb_model").pack_start(cell, True)
        self.wTree.get_widget("combobox_kb_model").add_attribute(cell, 'text', 0)        
        self.wTree.get_widget("combobox_kb_model").connect("changed", self.assign_keyboard_model)

        # kb layouts
        ren = gtk.CellRendererText()
        self.column10 = gtk.TreeViewColumn(_("Layout"), ren)
        self.column10.add_attribute(ren, "text", 0)
        self.wTree.get_widget("treeview_layouts").append_column(self.column10)
        self.wTree.get_widget("treeview_layouts").connect("cursor-changed", self.assign_keyboard_layout)
        
        ren = gtk.CellRendererText()
        self.column11 = gtk.TreeViewColumn(_("Variant"), ren)
        self.column11.add_attribute(ren, "text", 0)
        self.wTree.get_widget("treeview_variants").append_column(self.column11)
        self.wTree.get_widget("treeview_variants").connect("cursor-changed", self.assign_keyboard_variant)
        
        self.build_kb_lists()

        # 'about to install' aka overview
        ren = gtk.CellRendererText()
        self.column12 = gtk.TreeViewColumn(_("Overview"), ren)
        self.column12.add_attribute(ren, "markup", 0)
        self.wTree.get_widget("treeview_overview").append_column(self.column12)
        # install page
        self.wTree.get_widget("label_install_progress").set_markup("<i>%s</i>" % _("Calculating file indexes ..."))
    
        #i18n
        self.i18n()

        # build partition list
        self.should_pulse = False

        # make sure we're on the right page (no pun.)
        self.activate_page(0)

        # this is a hack atm to steal the menubar's background color
        self.wTree.get_widget("menubar").realize()
        style = self.wTree.get_widget("menubar").style.copy()
        self.wTree.get_widget("menubar").hide()
        # apply to the header       
        self.title_box = self.wTree.get_widget("title_eventbox")
        self.title_box.set_border_width(6);
        bgColor = gtk.gdk.color_parse('#585858')
        self.title_box.modify_bg(gtk.STATE_NORMAL, bgColor)
        fgColor = gtk.gdk.color_parse('#FFFFFF')
        self.help_label = self.wTree.get_widget("help_label")
        self.help_label.modify_fg(gtk.STATE_NORMAL, fgColor)            
        if(fullscreen):
            # dedicated installer mode thingum
            self.window.maximize()
            self.window.fullscreen()        
        
        #''' Launch the Slideshow '''
        #if ("_" in self.setup.language):
        #    locale_code = self.setup.language.split("_")[0]
        #else:
        #     locale_code = self.setup.language
        
        #slideshow_path = "/usr/share/live-installer-slideshow/slides/index.html"
        #if os.path.exists(slideshow_path):            
        #    browser = webkit.WebView()
        #    s = browser.get_settings()
        #    s.set_property('enable-file-access-from-file-uris', True)
        #    s.set_property('enable-default-context-menu', False)
        #    browser.open("file://" + slideshow_path  + "#?locale=" + locale_code)
        #    self.wTree.get_widget("vbox_install").add(browser)
        #    self.wTree.get_widget("vbox_install").show_all()         
        # Initiate the slide show
        self.slideshow_path = "/usr/share/live-installer/slideshow"
        if os.path.exists(self.slideshow_path):
            self.slideshow_browser = webkit.WebView()
            s = self.slideshow_browser.get_settings()
            s.set_property('enable-file-access-from-file-uris', True)
            s.set_property('enable-default-context-menu', False)            
            self.slideshow_browser.open("file://" + os.path.join(self.slideshow_path, 'template.html'))
            self.wTree.get_widget("vbox_install").add(self.slideshow_browser)
            self.wTree.get_widget("vbox_install").show_all()                                                            
        
        self.partitions_browser = webkit.WebView()
        s = self.partitions_browser.get_settings()
        s.set_property('enable-file-access-from-file-uris', True)
        s.set_property('enable-default-context-menu', False)
        self.partitions_browser.set_transparent(True)
        self.wTree.get_widget("scrolled_partitions").add(self.partitions_browser)
        
        self.window.show_all()

        # fix text wrap
        self.fix_text_wrap()
        
    def face_select_picture_button_clicked(self, widget, event):
        image = gtk.Image()
        preview = gtk.ScrolledWindow()
        preview.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        preview.set_size_request(150, 150)
        preview.add_with_viewport(image)
        image.show()
        preview.show()
        chooser = gtk.FileChooserDialog(title=None, parent=self.window,
                                        action=gtk.FILE_CHOOSER_ACTION_OPEN,
                                        buttons=(gtk.STOCK_CANCEL,
                                                 gtk.RESPONSE_CANCEL,
                                                 gtk.STOCK_OPEN,
                                                 gtk.RESPONSE_OK),
                                        backend=None)
        chooser.set_default_response(gtk.RESPONSE_OK)
        chooser.set_current_folder("/usr/share/pixmaps/faces")

        filter = gtk.FileFilter()
        filter.set_name(_('Images'))
        filter.add_mime_type('image/png')
        filter.add_mime_type('image/jpeg')
        filter.add_mime_type('image/gif')
        filter.add_mime_type('bitmap/bmp')        
        chooser.add_filter(filter)        
        chooser.set_preview_widget(preview)
        chooser.connect("update-preview", self.update_preview_cb, preview)
        response = chooser.run()
        if response == gtk.RESPONSE_OK:
            filename = chooser.get_filename()
            os.system("convert '%s' -resize x96 /tmp/live-installer-face.png" % filename)
            self.wTree.get_widget("face_image").set_from_file("/tmp/live-installer-face.png")
        chooser.destroy()
    
    def update_preview_cb(self, file_chooser, preview):
        filename = file_chooser.get_preview_filename()
        try:
            if filename:
                pixbuf = gtk.gdk.pixbuf_new_from_file(filename)
                preview.child.child.set_from_pixbuf(pixbuf)
                have_preview = True
            else:
                have_preview = False
        except Exception, e:
            #print e
            have_preview = False
        file_chooser.set_preview_widget_active(have_preview)
        return  
            
    def face_take_picture_button_clicked(self, widget, event):
        try:
            import cv
            capture = cv.CaptureFromCAM(-1) 
            for i in range(10):
                img = cv.QueryFrame(capture)        
                if img != None:
                    cv.SaveImage("/tmp/live-installer-webcam.png", img)
                    os.system("convert /tmp/live-installer-webcam.png -resize x96 /tmp/live-installer-face.png")
                    self.wTree.get_widget("face_image").set_from_file("/tmp/live-installer-face.png")
        except Exception, detail:
            print detail

    def fix_text_wrap(self):
        while gtk.events_pending():
            gtk.main_iteration_do(False)

        # this looks bad on resize, but to handle it on resize gracefully requires quite a bit of code (to keep from lagging)
        width = self.window.get_size()[0] - 75

        # custom install warning
        self.wTree.get_widget("label_custom_install_directions_1").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_directions_1").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_directions_2").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_directions_3").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_directions_4").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_directions_5").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_directions_6").set_size_request(width, -1)

        # custom install installation paused directions
        self.wTree.get_widget("label_custom_install_paused_1").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_paused_2").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_paused_3").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_paused_4").set_size_request(width, -1)
        self.wTree.get_widget("label_custom_install_paused_5").set_size_request(width, -1)
        
    def i18n(self):
        # about you
        self.wTree.get_widget("label_your_name").set_markup("<b>%s</b>" % _("Your full name"))
        self.wTree.get_widget("label_your_name_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This will be shown in the About Me application."))
        self.wTree.get_widget("label_username").set_markup("<b>%s</b>" % _("Your username"))
        self.wTree.get_widget("label_username_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This is the name you will use to log in to your computer."))
        self.wTree.get_widget("label_choose_pass").set_markup("<b>%s</b>" % _("Your password"))
        self.wTree.get_widget("label_pass_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("Please enter your password twice to ensure it is correct."))
        self.wTree.get_widget("label_hostname").set_markup("<b>%s</b>" % _("Hostname"))
        self.wTree.get_widget("label_hostname_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This hostname will be the computer's name on the network."))
        self.wTree.get_widget("label_autologin").set_markup("<b>%s</b>" % _("Automatic login"))
        self.wTree.get_widget("label_autologin_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("If enabled, the login screen is skipped when the system starts, and you are signed into your desktop session automatically."))
        self.wTree.get_widget("checkbutton_autologin").set_label(_("Log in automatically on system boot"))
        self.wTree.get_widget("checkbutton_autologin").connect("toggled", self.assign_autologin)
                
        self.wTree.get_widget("face_label").set_markup("<b>%s</b>" % _("Your picture"))
        self.wTree.get_widget("face_description").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This picture represents your user account. It is used in the login screen and a few other places."))
        self.wTree.get_widget("face_take_picture_button").set_label(_("Take a photo"))        
        
        self.wTree.get_widget("face_select_picture_button").set_label(_("Select a picture"))
        self.wTree.get_widget("face_select_picture_button").set_tooltip_text(_("Click this button to choose a picture from the hard disk."))
                
        # timezones
        self.wTree.get_widget("label_timezones").set_label(_("Selected timezone:"))
        
        # grub
        self.wTree.get_widget("label_grub").set_markup("<b>%s</b>" % _("Bootloader"))
        self.wTree.get_widget("checkbutton_grub").set_label(_("Install GRUB"))
        self.wTree.get_widget("label_grub_help").set_label(_("GRUB is a bootloader used to load the Linux kernel."))
        
        # keyboard page
        self.wTree.get_widget("label_test_kb").set_label(_("Use this box to test your keyboard layout."))
        self.wTree.get_widget("label_kb_model").set_label(_("Model"))        
        
        # custom install warning
        self.wTree.get_widget("label_custom_install_directions_1").set_label(_("You have selected to manage your partitions manually, this feature is for ADVANCED USERS ONLY."))
        self.wTree.get_widget("label_custom_install_directions_2").set_label(_("Before continuing, please mount your target filesystem(s) at /target."))
        self.wTree.get_widget("label_custom_install_directions_3").set_label(_("Do NOT mount virtual devices such as /dev, /proc, /sys, etc on /target/."))
        self.wTree.get_widget("label_custom_install_directions_4").set_label(_("During the install, you will be given time to chroot into /target and install any packages that will be needed to boot your new system."))
        self.wTree.get_widget("label_custom_install_directions_5").set_label(_("During the install, you will be required to write your own /etc/fstab."))
        self.wTree.get_widget("label_custom_install_directions_6").set_label(_("If you aren't sure what any of this means, please go back and deselect manual partition management."))

        # custom install installation paused directions
        self.wTree.get_widget("label_custom_install_paused_1").set_label(_("Please do the following and then click Forward to finish installation:"))
        self.wTree.get_widget("label_custom_install_paused_2").set_label(_("Create /target/etc/fstab for the filesystems as they will be mounted in your new system, matching those currently mounted at /target (without using the /target prefix in the mount paths themselves)."))
        self.wTree.get_widget("label_custom_install_paused_3").set_label(_("Install any packages that may be needed for first boot (mdadm, cryptsetup, dmraid, etc) by calling \"sudo chroot /target\" followed by the relevant apt-get/aptitude installations."))
        self.wTree.get_widget("label_custom_install_paused_4").set_label(_("Note that in order for update-initramfs to work properly in some cases (such as dm-crypt), you may need to have drives currently mounted using the same block device name as they appear in /target/etc/fstab."))
        self.wTree.get_widget("label_custom_install_paused_5").set_label(_("Double-check that your /target/etc/fstab is correct, matches what your new system will have at first boot, and matches what is currently mounted at /target."))

        # Columns
        for col, title in zip(self.wTree.get_widget("treeview_disks").get_columns(),
                              (_("Device"),
                               _("Type"),
                               _("Operating system"),
                               _("Mount point"),
                               _("Format as"),
                               _("Size"),
                               _("Free space"))):
            col.set_title(title)

        self.column10.set_title(_("Layout")) 
        self.column11.set_title(_("Variant")) 
        self.column12.set_title(_("Overview")) 

    def assign_realname(self, entry, prop):
        self.setup.real_name = entry.props.text
        text = entry.props.text.strip().lower()
        if " " in entry.props.text:
            elements = text.split()
            text = elements[0]
        self.setup.username = text
        self.wTree.get_widget("entry_username").set_text(text)   
        self.setup.print_setup()    

    def assign_username(self, entry, prop):
        self.setup.username = entry.props.text
        self.setup.print_setup()       

    def assign_hostname(self, entry, prop):
        self.setup.hostname = entry.props.text
        self.setup.print_setup()
        
    def quit_cb(self, widget, data=None):
        ''' ask whether we should quit. because touchpads do happen '''
        gtk.main_quit()

    def show_customwarning(self, widget):
        self.activate_page(self.PAGE_CUSTOMWARNING)

    def build_lang_list(self):

        #Try to find out where we're located...
        cur_country_code, cur_timezone = None, None
        try:
            whatismyip = 'http://www.linuxmint.com/installer/show_my_ip.php'
            ip = urllib.urlopen(whatismyip).readlines()[0]
            gi = GeoIP.open('/usr/share/GeoIP/GeoIPCity.dat', GeoIP.GEOIP_STANDARD)
            gir = gi.record_by_addr(ip)
            cur_country_code, cur_timezone = gir['country_code'], gir['time_zone']
        except:
            pass #best effort, we get here if we're not connected to the Internet            

        self.cur_country_code = cur_country_code or os.environ.get('LANG', 'US').split('.')[0].split('_')[-1]  # fallback to LANG location or 'US'
        self.cur_timezone = cur_timezone

        #Load countries into memory
        countries = {}
        for line in commands.getoutput("isoquery --iso 3166 | cut -f1,4-").split('\n'):
            ccode, cname = line.split(None, 1)
            countries[ccode] = cname

        #Load languages into memory
        languages = {}
        for line in commands.getoutput("isoquery --iso 639").split('\n'):
            _, code3, code2, language = line.split('\t')
            languages[code2 or code3] = language

        # Construct language selection model
        model = gtk.ListStore(str, str, gtk.gdk.Pixbuf, str)
        set_iter = None
        flag_path = lambda ccode: self.resource_dir + '/flags/16/' + ccode.lower() + '.png'
        from utils import memoize
        flag = memoize(lambda ccode: gtk.gdk.pixbuf_new_from_file(flag_path(ccode)))
        for locale in commands.getoutput("awk -F'[@ \.]' '/UTF-8/{ print $1 }' /usr/share/i18n/SUPPORTED | uniq").split('\n'):
            try:
                if '_' in locale:
                    lang, ccode = locale.split('_')
                    language, country = languages[lang], countries[ccode]
                else:
                    lang = locale
                    language = languages[lang]
                    country = ''
            except:
                print "Error adding locale '%s'" % locale
                continue
            pixbuf = flag(ccode) if not lang in 'eo ia' else flag('_' + lang)
            iter = model.append((language, country, pixbuf, locale))
            if (ccode == cur_country_code and
                (not set_iter or
                 set_iter and lang == 'en' or  # prefer English, or
                 set_iter and lang == ccode.lower())):  # fuzzy: lang matching ccode (fr_FR, de_DE, es_ES, ...)
                set_iter = iter

        # Sort by Country, then by Language
        model.set_sort_column_id(0, gtk.SORT_ASCENDING)
        model.set_sort_column_id(1, gtk.SORT_ASCENDING)
        # Set the model and pre-select the correct language
        treeview = self.wTree.get_widget("treeview_language_list")
        treeview.set_model(model)
        if set_iter:
            path = model.get_path(set_iter)
            treeview.set_cursor(path)
            treeview.scroll_to_cell(path)

    def build_timezones(self):
        
        self.combo_timezones = self.wTree.get_widget("combo_timezones")
        self.combo_timezones.connect('changed', self.timezone_combo_selected)
        
        self.timezone_colors = {}
        self.timezone_colors["2b0000"] = "-11.0"
        self.timezone_colors["550000"] = "-10.0"
        self.timezone_colors["66ff00"] = "-9.5"
        self.timezone_colors["800000"] = "-9.0"
        self.timezone_colors["aa0000"] = "-8.0"
        self.timezone_colors["d40000"] = "-7.0"
        self.timezone_colors["ff0001"] = "-6.0"
        self.timezone_colors["66ff00"] = "-5.5"
        self.timezone_colors["ff2a2a"] = "-5.0"
        self.timezone_colors["c0ff00"] = "-4.5"
        self.timezone_colors["ff5555"] = "-4.0"
        self.timezone_colors["00ff00"] = "-3.5"
        self.timezone_colors["ff8080"] = "-3.0"
        self.timezone_colors["ffaaaa"] = "-2.0"
        self.timezone_colors["ffd5d5"] = "-1.0"
        self.timezone_colors["2b1100"] = "0.0"
        self.timezone_colors["552200"] = "1.0"
        self.timezone_colors["803300"] = "2.0"
        self.timezone_colors["aa4400"] = "3.0"
        self.timezone_colors["00ff66"] = "3.5"
        self.timezone_colors["d45500"] = "4.0"
        self.timezone_colors["00ccff"] = "4.5"
        self.timezone_colors["ff6600"] = "5.0"
        self.timezone_colors["0066ff"] = "5.5"        
        self.timezone_colors["00ffcc"] = "5.75"
        self.timezone_colors["ff7f2a"] = "6.0"
        self.timezone_colors["cc00ff"] = "6.5"
        self.timezone_colors["ff9955"] = "7.0"
        self.timezone_colors["ffb380"] = "8.0"
        self.timezone_colors["ffccaa"] = "9.0"
        self.timezone_colors["a90345"] = "9.5"
        self.timezone_colors["ffe6d5"] = "10.0"
        self.timezone_colors["d10255"] = "10.5"
        self.timezone_colors["d4aa00"] = "11.0"
        self.timezone_colors["fc0266"] = "11.5"
        self.timezone_colors["ffcc00"] = "12.0"
        self.timezone_colors["fd2c80"] = "12.75"
        self.timezone_colors["fc5598"] = "13.0"
        
        #Add some timezones for cities which are located on borders (for which the color doesn't match the color of the rest of the timezone)
        self.timezone_colors["6771a9"] = "5.5" # Calcutta, India
        self.timezone_colors["ff7b7b"] = "-3.0" # Buenos Aires, Argentina
        self.timezone_colors["ff7f7f"] = "-3.0" # Rio Gallegos, Argentina
        self.timezone_colors["d45c27"] = "11.0" # Lord Howe, Australia
        self.timezone_colors["b71f54"] = "10.5" # Adelaide, Australia        
        self.timezone_colors["d29130"] = "-4.0" # Aruba
        self.timezone_colors["ee5f00"] = "4.0" # Baku, Azerbaidjan
        self.timezone_colors["6a2a00"] = "2.0" # Sofia, Bulgaria
        self.timezone_colors["3c1800"] = "" # Porto Novo
        self.timezone_colors["3c1800"] = "1.0" # Benin
        self.timezone_colors["ff9898"] = "-3.0" # Maceio, Brazil
        self.timezone_colors["ff3f3f"] = "-4.0" # Rio Branco, Brazil
        self.timezone_colors["ff802c"] = "6.0" # Thimphu, Bhutan
        self.timezone_colors["ff0000"] = "-6.0" # Belize
        self.timezone_colors["11f709"] = "-3.5" # St Johns, Canada
        self.timezone_colors["e56347"] = "-4.0" # Curacao
        self.timezone_colors["cd5200"] = "4.0" # Tbilisi, Georgia
        self.timezone_colors["2f1300"] = "0.0" # Guernsey. UK
        self.timezone_colors["cea7a3"] = "0.0" # Danmarkshavn, Greenland
        self.timezone_colors["ff2b2b"] = "-4.0" # Thule, Greenland
        self.timezone_colors["79594e"] = "0.0" # Banjul, Gambia
        self.timezone_colors["c7a19d"] = "0.0" # Conakry, Guinea
        self.timezone_colors["5b3e31"] = "0.0" # Bissau, Guinea-Bissau
        self.timezone_colors["3f2314"] = "0.0" # Monrovia, Liberia
        self.timezone_colors["d515db"] = "6.5" # Rangoon, Myanmar
        self.timezone_colors["fd0000"] = "-7.0" # Bahia_Banderas, Mexico
        self.timezone_colors["ffb37f"] = "8.0" # Kuching, Malaysia
        self.timezone_colors["ff0066"] = "11.5" # Norfolk
        self.timezone_colors["351500"] = "1.0" # Lagos, Nigeria
        self.timezone_colors["ff8935"] = "12.75" # Chatham, New Zealand
        self.timezone_colors["913a00"] = "2.0" # Kigali, Rwanda
        self.timezone_colors["ffb17d"] = "8.0" # Singapore
        self.timezone_colors["ddb6b3"] = "0.0" # Freetown, Sierra Leone
        self.timezone_colors["ffb482"] = "9.0" # Dili, East Timor
        self.timezone_colors["ff5599"] = "13.0" # Tongatapu, Tonga
        self.timezone_colors["ff2020"] = "-5.0" # Monticello, USA        
        self.timezone_colors["ff2525"] = "-5.0" # Marengo, USA
        self.timezone_colors["9d0000"] = "-9.0" # Metlakatla, Alaska/USA
        
        self.timezones = []        
        model = gtk.ListStore(str, object)
        model.set_sort_column_id(0, gtk.SORT_ASCENDING)                   
        timezones = open("/usr/share/zoneinfo/zone.tab", "r")
        for line in timezones:
            if not line.strip().startswith("#"):                
                content = line.strip().split("\t")
                if len(content) >= 2:                    
                    country_code = content[0]
                    coordinates = content[1]
                    timezone = content[2]
                    tz = Timezone(timezone, country_code, coordinates)
                    self.timezones.append(tz)
                    iter = model.append()
                    model.set_value(iter, 0, timezone)
                    model.set_value(iter, 1, tz)  

                    # Uncomment the code below to check that each timezone has a corresponding color code (the code is here for debugging only)
                    #print "Timezone: %s, X: %s, Y: %s" % (tz.name, tz.x, tz.y)
                    #if (tz.x <= 800 and tz.y <= 409):
                    #    im = Image.open('/usr/share/live-installer/timezone/cc.png')
                    #    rgb_im = im.convert('RGB')                    
                    #    hexcolor = '%02x%02x%02x' % rgb_im.getpixel((tz.x, tz.y))
                    #    print " Color: #%s" % (hexcolor)
                    #    image = "/usr/share/live-installer/timezone/timezone_%s.png" % self.timezone_colors[hexcolor]
                    #    print "Image: %s" % image
            
        cell = gtk.CellRendererText()
        self.combo_timezones.pack_start(cell, True)
        self.combo_timezones.add_attribute(cell, 'text', 0)
        
        self.combo_timezones.set_model(model)
        self.timezone_map = self.wTree.get_widget("image_timezones")
        timezone_event = self.wTree.get_widget("event_timezones")
        self.timezone_map.set_from_file("/usr/share/live-installer/timezone/bg.png")
        timezone_event.connect("button-release-event", self.timezone_map_clicked)   
                     
    def timezone_combo_selected(self, combobox):
        model = combobox.get_model()
        index = combobox.get_active()
        if index:
            timezone = model[index][1]
            self.timezone_select(timezone)            
    
    def timezone_map_clicked(self, widget, event):
        x = event.x
        y = event.y
        print "Coords: %s %s" % (x, y)
        
        min_distance = 1000 # Looking for min, starting with a large number
        closest_timezone = None
        for timezone in self.timezones:
            distance = abs(x - timezone.x) + abs(y - timezone.y)
            if distance < min_distance:
                min_distance = distance
                closest_timezone = timezone
        
        print "Closest timezone %s" % closest_timezone.name
        self.timezone_select(closest_timezone)
                
        model = self.combo_timezones.get_model()
        iter = model.get_iter_first()
        while iter is not None:            
            if closest_timezone.name == model.get_value(iter, 1).name:
                self.combo_timezones.set_active_iter(iter)
                break
            iter = model.iter_next(iter)
        
    def timezone_select(self, timezone):        
        im = Image.open('/usr/share/live-installer/timezone/cc.png')
        rgb_im = im.convert('RGB')
        hexcolor = '%02x%02x%02x' % rgb_im.getpixel((timezone.x, timezone.y))
        print "Color: #%s" % (hexcolor)
        
        overlay_path = "/usr/share/live-installer/timezone/timezone_%s.png" % self.timezone_colors[hexcolor]
        print "Image: %s" % overlay_path
        
        # Superpose the picture of the timezone on the map
        background = Image.open("/usr/share/live-installer/timezone/bg.png")
        dot = Image.open("/usr/share/live-installer/timezone/dot.png")
        overlay = Image.open(overlay_path)
        background = background.convert("RGBA")
        overlay = overlay.convert("RGBA")
        dot = dot.convert("RGBA")
        background.paste(overlay, (0,0), overlay)
        background.paste(dot, (timezone.x-3, timezone.y-3), dot)
        background.save("/tmp/live-installer-map.png","PNG")
        self.timezone_map.set_from_file("/tmp/live-installer-map.png")
        
        # Save the selection
        self.setup.timezone = timezone.name
        self.setup.timezone_code = timezone.name

    def build_kb_lists(self):
        ''' Do some xml kung-fu and load the keyboard stuffs '''
        # Determine the layouts in use
        (keyboard_geom,
         self.setup.keyboard_layout) = commands.getoutput("setxkbmap -query | awk '/^(model|layout)/{print $2}'").split()
        # Build the models
        from collections import defaultdict
        def _ListStore_factory():
            model = gtk.ListStore(str, str)
            model.set_sort_column_id(0, gtk.SORT_ASCENDING)
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
            variants[name].append((desc, None))
            for variant in node.iterfind('variantList/variant/configItem'):
                var_name, var_desc = variant.find('name').text, variant.find('description').text
                var_desc = var_desc if var_desc.startswith(desc) else '{} - {}'.format(desc, var_desc)
                variants[name].append((var_desc, var_name))
            iterator = layouts.append((desc, name))
            if name == self.setup.keyboard_layout:
                set_keyboard_layout = iterator
        # Set the models
        self.wTree.get_widget("combobox_kb_model").set_model(models)
        self.wTree.get_widget("treeview_layouts").set_model(layouts)
        self.layout_variants = variants
        # Preselect currently active keyboard info
        try:
            self.wTree.get_widget("combobox_kb_model").set_active_iter(set_keyboard_model)
        except NameError: pass  # set_keyboard_model not set
        try:
            treeview = self.wTree.get_widget("treeview_layouts")
            path = layouts.get_path(set_keyboard_layout)
            treeview.set_cursor(path)
            treeview.scroll_to_cell(path)
        except NameError: pass  # set_keyboard_layout not set

    def assign_language(self, treeview, data=None):
        ''' Called whenever someone updates the language '''
        model = treeview.get_model()
        active = treeview.get_selection().get_selected_rows()
        if(len(active) < 1):
            return
        active = active[1][0]
        if(active is None):
            return
        row = model[active]
        self.setup.language = row[-1]
        self.setup.print_setup()
        gettext.translation('live-installer', "/usr/share/linuxmint/locale",
                            languages=[self.setup.language, self.setup.language.split('_')[0]],
                            fallback=True).install()  # Try e.g. zh_CN, zh, or fallback to hardcoded English
        try:
            self.i18n()
        except:
            pass # Best effort. Fails the first time as self.column1 doesn't exist yet.

    def assign_autologin(self, checkbox, data=None):
        self.setup.autologin = checkbox.get_active()
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
        (self.setup.keyboard_model_description,
         self.setup.keyboard_model) = model[active]
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
        self.wTree.get_widget("treeview_variants").set_model(model)
        # ... and select the first variant (standard)
        self.wTree.get_widget("treeview_variants").set_cursor(0)

    def assign_keyboard_variant(self, treeview):
        ''' Called whenever someone updates the keyboard layout or variant '''
        glib.source_remove(self.kbd_preview_generation)  # stop previous preview generation, if any
        model, active = treeview.get_selection().get_selected_rows()
        if not active: return
        (self.setup.keyboard_variant_description,
         self.setup.keyboard_variant) = model[active[0]]
        if self.setup.keyboard_variant:
            os.system('setxkbmap -variant ' + self.setup.keyboard_variant)
        else:
            os.system('setxkbmap -layout ' + self.setup.keyboard_layout)
        self.setup.print_setup()
        # Set preview image
        self.wTree.get_widget("image_keyboard").set_from_file(LOADING_ANIMATION)
        self.kbd_preview_generation = glib.timeout_add(500, self._generate_keyboard_layout_preview)

    def _generate_keyboard_layout_preview(self):
        filename = "/tmp/live-install-keyboard-layout.png"
        os.system("python /usr/lib/live-installer/frontend/generate_keyboard_layout.py %s %s %s" % (self.setup.keyboard_layout, self.setup.keyboard_variant, filename))
        self.wTree.get_widget("image_keyboard").set_from_file(filename)
        return False

    def assign_password(self, widget):
        ''' Someone typed into the entry '''
        self.setup.password1 = self.wTree.get_widget("entry_userpass1").get_text()
        self.setup.password2 = self.wTree.get_widget("entry_userpass2").get_text()        
        if(self.setup.password1 == "" and self.setup.password2 == ""):
            self.wTree.get_widget("image_mismatch").hide()
            self.wTree.get_widget("label_mismatch").hide()
        else:
            self.wTree.get_widget("image_mismatch").show()
            self.wTree.get_widget("label_mismatch").show()
        if(self.setup.password1 != self.setup.password2):
            self.wTree.get_widget("image_mismatch").set_from_stock(gtk.STOCK_NO, gtk.ICON_SIZE_BUTTON)            
            self.wTree.get_widget("label_mismatch").set_label(_("Passwords do not match."))
        else:
            self.wTree.get_widget("image_mismatch").set_from_stock(gtk.STOCK_OK, gtk.ICON_SIZE_BUTTON)            
            self.wTree.get_widget("label_mismatch").set_label(_("Passwords match."))
        self.setup.print_setup()
        
    def activate_page(self, index):
        help_text = _(self.wizard_pages[index].help_text)        
        self.wTree.get_widget("help_label").set_markup("<big><b>%s</b></big>" % help_text)
        self.wTree.get_widget("help_icon").set_from_file("/usr/share/live-installer/icons/%s" % self.wizard_pages[index].icon)
        self.wTree.get_widget("notebook1").set_current_page(index)
        # TODO: move other page-depended actions from the wizard_cb into here below
        if index == self.PAGE_PARTITIONS:
            self.setup.skip_mount = False
        if index == self.PAGE_CUSTOMWARNING:
            self.setup.skip_mount = True

    def wizard_cb(self, widget, goback, data=None):
        ''' wizard buttons '''
        sel = self.wTree.get_widget("notebook1").get_current_page()
        self.wTree.get_widget("button_next").set_label(gtk.STOCK_GO_FORWARD)
        self.wTree.get_widget("button_next").set_use_stock(True)
        self.wTree.get_widget("button_back").set_sensitive(True)
        
        # check each page for errors
        if(not goback):
            if(sel == self.PAGE_LANGUAGE):
                lang_country_code = self.setup.language.split('_')[-1]
                combo = self.wTree.get_widget("combo_timezones")
                model = combo.get_model()
                if self.cur_timezone:  # timezone guessed from IP
                    want_selected = lambda iter: self.cur_timezone == model.get_value(iter, 0)
                elif self.cur_country_code:  # otherwise pick country from IP
                    want_selected = lambda iter: self.cur_country_code == model.get_value(iter, 1).country_code
                else:  # otherwise just use country from language selection
                    want_selected = lambda iter: lang_country_code == model.get_value(iter, 1).country_code
                iter = model.get_iter_first()
                while iter:
                    if want_selected(iter):
                        combo.set_active_iter(iter)
                        break
                    iter = model.iter_next(iter)
                self.activate_page(self.PAGE_TIMEZONE)
            elif (sel == self.PAGE_TIMEZONE):
                if ("_" in self.setup.language):
                    country_code = self.setup.language.split("_")[1]
                else:
                    country_code = self.setup.language
                treeview = self.wTree.get_widget("treeview_layouts")
                model = treeview.get_model()
                iter = model.get_iter_first()                
                while iter is not None:
                    iter_country_code = model.get_value(iter, 1)
                    if iter_country_code.lower() == country_code.lower():
                        column = treeview.get_column(0)
                        path = model.get_path(iter)
                        treeview.set_cursor(path, focus_column=column)
                        treeview.scroll_to_cell(path, column=column)
                        break
                    iter = model.iter_next(iter)
                self.activate_page(self.PAGE_KEYBOARD)
            elif(sel == self.PAGE_KEYBOARD):
                self.activate_page(self.PAGE_USER)
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
                    
                    for char in self.setup.hostname:
                        if(char.isupper()):
                            errorFound = True
                            errorMessage = _("The hostname must be lower case.")
                            break
                        elif(char.isspace()):
                            errorFound = True
                            errorMessage = _("The hostname may not contain whitespace characters.")
                    
                if (errorFound):
                    MessageDialog(_("Installation Tool"), errorMessage, gtk.MESSAGE_WARNING, self.window).show()
                else:
                    self.activate_page(self.PAGE_PARTITIONS)
                    partitioning.build_partitions(self)
            elif(sel == self.PAGE_PARTITIONS):                
                model = self.wTree.get_widget("treeview_disks").get_model()

                # Check for root partition
                found_root_partition = False
                for partition in self.setup.partitions:
                    if(partition.mount_as == "/"):
                        found_root_partition = True
                        if partition.format_as is None or partition.format_as == "":                            
                            MessageDialog(_("Installation Tool"), _("Please indicate a filesystem to format the root (/) partition with before proceeding."), gtk.MESSAGE_ERROR, self.window).show()
                            return
                if not found_root_partition:
                    MessageDialog(_("Installation Tool"), _("<b>Please select a root (/) partition.</b>"), gtk.MESSAGE_ERROR, self.window, _("A root partition is needed to install Linux Mint on.\n\n - Mount point: /\n - Recommended size: 30GB\n - Recommended filesystem format: ext4\n ")).show()
                    return

                if self.setup.gptonefi:
                    # Check for an EFI partition
                    found_efi_partition = False
                    for partition in self.setup.partitions:
                        if(partition.mount_as == "/boot/efi"):
                            found_efi_partition = True
                            if not partition.partition.getFlag(parted.PARTITION_BOOT):
                                MessageDialog(_("Installation Tool"), _("The EFI partition is not bootable. Please edit the partition flags."), gtk.MESSAGE_ERROR, self.window).show()
                                return
                            if int(float(partition.size)) < 100:
                                MessageDialog(_("Installation Tool"), _("The EFI partition is too small. It must be at least 100MB."), gtk.MESSAGE_ERROR, self.window).show()
                                return
                            if partition.format_as == None or partition.format_as == "":
                                # No partitioning
                                if partition.type != "vfat" and partition.type != "fat32" and partition.type != "fat16":
                                    MessageDialog(_("Installation Tool"), _("The EFI partition must be formatted as vfat."), gtk.MESSAGE_ERROR, self.window).show()
                                    return
                            else:
                                if partition.format_as != "vfat":
                                    MessageDialog(_("Installation Tool"), _("The EFI partition must be formatted as vfat."), gtk.MESSAGE_ERROR, self.window).show()
                                    return
                            
                    if not found_efi_partition:
                        MessageDialog(_("Installation Tool"), _("<b>Please select an EFI partition.</b>"), gtk.MESSAGE_ERROR, self.window, _("An EFI system partition is needed with the following requirements:\n\n - Mount point: /boot/efi\n - Partition flags: Bootable\n - Size: Larger than 100MB\n - Format: vfat or fat32\n\nTo ensure compatibility with Windows we recommend you use the first partition of the disk as the EFI system partition.\n ")).show()
                        return

                partitioning.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)

            elif(sel == self.PAGE_CUSTOMWARNING):
                partitioning.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)
            elif(sel == self.PAGE_ADVANCED):
                self.activate_page(self.PAGE_OVERVIEW)
                self.show_overview()
                self.wTree.get_widget("treeview_overview").expand_all()
                self.wTree.get_widget("button_next").set_label(gtk.STOCK_APPLY)
            elif(sel == self.PAGE_OVERVIEW):
                self.activate_page(self.PAGE_INSTALL)
                # do install
                self.wTree.get_widget("button_next").set_sensitive(False)
                self.wTree.get_widget("button_back").set_sensitive(False)
                self.wTree.get_widget("button_quit").set_sensitive(False)
                thr = threading.Thread(name="live-install", group=None, args=(), kwargs={}, target=self.do_install)
                thr.start()
            elif(sel == self.PAGE_CUSTOMPAUSED):
                self.activate_page(self.PAGE_INSTALL)
                self.wTree.get_widget("button_next").hide()
                self.paused = False            
        else:
            self.wTree.get_widget("button_back").set_sensitive(True)
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

    def show_overview(self):
        ''' build the summary page '''
        model = gtk.TreeStore(str)        
        top = model.append(None)
        model.set(top, 0, _("Localization"))
        iter = model.append(top)
        model.set(iter, 0, _("Language: ") + "<b>%s</b>" % self.setup.language)        
        iter = model.append(top)
        model.set(iter, 0, _("Timezone: ") + "<b>%s</b>" % self.setup.timezone)        
        iter = model.append(top)
        if (self.setup.keyboard_variant_description is None):
            model.set(iter, 0, _("Keyboard layout: ") + "<b>%s - %s</b>" % (self.setup.keyboard_model_description, self.setup.keyboard_layout_description))       
        else:
            model.set(iter, 0, _("Keyboard layout: ") + "<b>%s - %s (%s)</b>" % (self.setup.keyboard_model_description, self.setup.keyboard_layout_description, self.setup.keyboard_variant_description))
        top = model.append(None)
        model.set(top, 0, _("User settings"))       
        iter = model.append(top)
        model.set(iter, 0, _("Real name: ") + "<b>%s</b>" % self.setup.real_name)        
        iter = model.append(top)
        model.set(iter, 0, _("Username: ") + "<b>%s</b>" % self.setup.username)
        iter = model.append(top)
        model.set(iter, 0, _("Automatic login: ") + "<b>%s</b>" % (_("enabled") if self.setup.autologin else _("disabled")))
        top = model.append(None)
        model.set(top, 0, _("System settings"))
        iter = model.append(top)
        model.set(iter, 0, _("Hostname: ") + "<b>%s</b>" % self.setup.hostname)       
        iter = model.append(top)
        if(self.setup.grub_device is not None):
            model.set(iter, 0, _("Install bootloader on %s") % ("<b>%s</b>" % self.setup.grub_device))
        else:
            model.set(iter, 0, _("Do not install bootloader"))
        top = model.append(None)
        model.set(top, 0, _("Filesystem operations"))  
        if(self.setup.skip_mount):
            iter = model.append(top)
            model.set(iter, 0, "<b>%s</b>" % _("Use already-mounted /target."))
        else:      
            for partition in self.setup.partitions:
                if(partition.format_as is not None and partition.format_as != ""):
                    # format it
                    iter = model.append(top)
                    model.set(iter, 0, "<b>%s</b>" % (_("Format %(partition)s as %(format)s") % {'partition':partition.partition.path, 'format':partition.format_as}))
            for partition in self.setup.partitions:
                if(partition.mount_as is not None and partition.mount_as != ""):
                    # mount point
                    iter = model.append(top)
                    model.set(iter, 0, "<b>%s</b>" % (_("Mount %(partition)s as %(mountpoint)s") % {'partition':partition.partition.path, 'mountpoint':partition.mount_as}))
        self.wTree.get_widget("treeview_overview").set_model(model)

    def do_install(self):        
        print " ## INSTALLATION "
        ''' Actually perform the installation .. '''
        inst = self.installer

        if __debug__:
            print " ## DEBUG MODE - INSTALLATION PROCESS NOT LAUNCHED"
            with gtk.gdk.lock:
                return gtk.main_quit()

        inst.set_progress_hook(self.update_progress)
        inst.set_error_hook(self.error_message)

        # do we dare? ..
        self.critical_error_happened = False

        # Now it's time to load the slide show
        slideThr = Slideshow(self.slideshow_browser, self.setup.language)
        slideThr.daemon = True  # let the slide-thread die with the parent
        slideThr.start()

        # Start installing
        do_try_finish_install = True

        try:
            inst.init_install(self.setup)
        except Exception, detail1:
            print detail1
            do_try_finish_install = False
            with gtk.gdk.lock:
                MessageDialog(_("Installation error"), str(detail1), gtk.MESSAGE_ERROR, self.window).show()

        if self.critical_error_happened:
            with gtk.gdk.lock:
                MessageDialog(_("Installation error"), self.critical_error_message, gtk.MESSAGE_ERROR, self.window).show()
            do_try_finish_install = False

        if do_try_finish_install:
            if(self.setup.skip_mount):
                with gtk.gdk.lock:
                    self.paused = True
                    self.activate_page(self.PAGE_CUSTOMPAUSED)
                    self.wTree.get_widget("button_next").show()
                    MessageDialog(_("Installation paused"), _("Installation is now paused. Please read the instructions on the page carefully before clicking Forward to finish the installation."), gtk.MESSAGE_INFO, self.window).show()
                    self.wTree.get_widget("button_next").set_sensitive(True)

                while(self.paused):
                    time.sleep(0.1)

            try:
                inst.finish_install(self.setup)
            except Exception, detail1:
                print detail1
                with gtk.gdk.lock:
                    MessageDialog(_("Installation error"), str(detail1), gtk.MESSAGE_ERROR, self.window).show()

            # show a message dialog thingum
            while(not self.done):
                time.sleep(0.1)

            with gtk.gdk.lock:
                if self.critical_error_happened:
                    MessageDialog(_("Installation error"), self.critical_error_message, gtk.MESSAGE_ERROR, self.window).show()
                else:
                    dialog = QuestionDialog(_("Installation finished"), _("Installation is now complete. Do you want to restart your computer to use the new system?"), self.window)
                    if (dialog.show()):
                        # Reboot now
                        os.system('reboot')

            print " ## INSTALLATION COMPLETE "

        # safe??
        gtk.main_quit()
        # you are now..
        sys.exit(0)

    def error_message(self, message=""):
        self.critical_error_happened = True
        self.critical_error_message = message

    def update_progress(self, fail=False, done=False, pulse=False, total=0,current=0,message=""):
        
        #print "%d/%d: %s" % (current, total, message)
        
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
            gtk.gdk.threads_enter()
            self.wTree.get_widget("progressbar").pulse()
            gtk.gdk.threads_leave()
            return self.should_pulse
        if(not self.should_pulse):
            self.should_pulse = True
            gobject.timeout_add(100, pbar_pulse)
        else:
            # asssume we're "pulsing" already
            self.should_pulse = True
            pbar_pulse()
