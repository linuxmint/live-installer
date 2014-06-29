#!/usr/bin/env python
import sys
sys.path.append('/usr/lib/live-installer')
from installer import InstallerEngine, Setup, PartitionSetup

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
    import math    
    sys.path.append('/usr/lib/live-installer')
    from PIL import Image
    import pango
    import threading
    import xml.dom.minidom
    from xml.dom.minidom import parse
    import gobject
    import time
    import webkit
    import GeoIP
    import urllib
    import string
    import parted    
except Exception, detail:
    print detail

from slideshow import Slideshow

gettext.install("live-installer", "/usr/share/linuxmint/locale")
gtk.gdk.threads_init()

INDEX_PARTITION_PATH=0
INDEX_PARTITION_TYPE=1
INDEX_PARTITION_DESCRIPTION=2
INDEX_PARTITION_FORMAT_AS=3
INDEX_PARTITION_MOUNT_AS=4
INDEX_PARTITION_SIZE=5
INDEX_PARTITION_FREE_SPACE=6
INDEX_PARTITION_OBJECT=7

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
        if "--debug" in sys.argv:
            self.window.set_title((_("%s Installer") % DISTRIBUTION_NAME) + " (debug)")
        else:
            self.window.set_title(_("%s Installer") % DISTRIBUTION_NAME)
        self.window.connect("destroy", self.quit_cb)

        # Wizard pages
        [self.PAGE_LANGUAGE, self.PAGE_PARTITIONS, self.PAGE_USER, self.PAGE_ADVANCED, self.PAGE_KEYBOARD, self.PAGE_OVERVIEW, self.PAGE_INSTALL, self.PAGE_TIMEZONE, self.PAGE_HDD, self.PAGE_CUSTOMWARNING, self.PAGE_CUSTOMPAUSED] = range(11)
        self.wizard_pages = range(11)
        self.wizard_pages[self.PAGE_LANGUAGE] = WizardPage(_("Language"), "locales.png")
        self.wizard_pages[self.PAGE_TIMEZONE] = WizardPage(_("Timezone"), "time.png")
        self.wizard_pages[self.PAGE_KEYBOARD] = WizardPage(_("Keyboard layout"), "keyboard.png")
        self.wizard_pages[self.PAGE_USER] = WizardPage(_("User info"), "user.png")
        self.wizard_pages[self.PAGE_HDD] = WizardPage(_("Hard drive"), "hdd.svg")
        self.wizard_pages[self.PAGE_PARTITIONS] = WizardPage(_("Partitioning"), "hdd.svg")
        self.wizard_pages[self.PAGE_CUSTOMWARNING] = WizardPage(_("Please make sure you wish to manually manage partitions"), "hdd.svg")
        self.wizard_pages[self.PAGE_ADVANCED] = WizardPage(_("Advanced options"), "advanced.png")
        self.wizard_pages[self.PAGE_OVERVIEW] = WizardPage(_("Summary"), "summary.png")
        self.wizard_pages[self.PAGE_INSTALL] = WizardPage(_("Installing Linux Mint..."), "install.png")
        self.wizard_pages[self.PAGE_CUSTOMPAUSED] = WizardPage(_("Installation is paused: please finish the custom installation"), "install.png")
        
        # set the button events (wizard_cb)
        self.wTree.get_widget("button_next").connect("clicked", self.wizard_cb, False)
        self.wTree.get_widget("button_back").connect("clicked", self.wizard_cb, True)
        self.wTree.get_widget("button_quit").connect("clicked", self.quit_cb)

        ren = gtk.CellRendererPixbuf()
        column = gtk.TreeViewColumn("Flags", ren)
        column.add_attribute(ren, "pixbuf", 2)
        self.wTree.get_widget("treeview_language_list").append_column(column)

        ren = gtk.CellRendererText()
        column = gtk.TreeViewColumn("Languages", ren)
        column.add_attribute(ren, "text", 0)
        self.wTree.get_widget("treeview_language_list").append_column(column)
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
            self.wTree.get_widget("face_take_picture_button").set_tooltip_text(_("Click this button to take a new picture of yourself with the webcam"))
        else:
            self.wTree.get_widget("face_take_picture_button").set_sensitive(False)
            self.wTree.get_widget("face_take_picture_button").set_tooltip_text(_("The installer did not detect any webcam"))
        
        # build the language list
        self.build_lang_list()        

        self.build_timezones()

        # disk view
        ren = gtk.CellRendererText()
        self.column1 = gtk.TreeViewColumn("Hard drive", ren)
        self.column1.add_attribute(ren, "text", 0)
        self.wTree.get_widget("treeview_hdds").append_column(self.column1)
        self.column2 = gtk.TreeViewColumn("Description", ren)
        self.column2.add_attribute(ren, "text", 1)
        self.wTree.get_widget("treeview_hdds").append_column(self.column2)
        self.wTree.get_widget("treeview_hdds").connect("cursor-changed", self.assign_hdd)
        self.build_hdds()
        #self.build_grub_partitions()

        self.wTree.get_widget("radio_hdd").set_group(self.wTree.get_widget("radio_custom"))
        self.wTree.get_widget("radio_hdd").connect("toggled", self.hdd_pane_toggled)
        self.wTree.get_widget("radio_hdd").set_active(True)
        
        self.wTree.get_widget("button_edit").connect("clicked", self.edit_partitions)        
        self.wTree.get_widget("button_refresh").connect("clicked", self.refresh_partitions)
        self.wTree.get_widget("treeview_disks").connect("row_activated", self.assign_partition)
        self.wTree.get_widget("treeview_disks").connect( "button-release-event", self.partitions_popup_menu)
        
        # device
        ren = gtk.CellRendererText()
        self.column3 = gtk.TreeViewColumn(_("Device"), ren)
        self.column3.add_attribute(ren, "markup", INDEX_PARTITION_PATH)
        self.wTree.get_widget("treeview_disks").append_column(self.column3)
        # Type
        ren = gtk.CellRendererText()
        self.column4 = gtk.TreeViewColumn(_("Type"), ren)
        self.column4.add_attribute(ren, "markup", INDEX_PARTITION_TYPE)
        self.wTree.get_widget("treeview_disks").append_column(self.column4)
        # description
        ren = gtk.CellRendererText()
        self.column5 = gtk.TreeViewColumn(_("Operating system"), ren)
        self.column5.add_attribute(ren, "markup", INDEX_PARTITION_DESCRIPTION)
        self.wTree.get_widget("treeview_disks").append_column(self.column5)        
        # mount point
        ren = gtk.CellRendererText()
        self.column6 = gtk.TreeViewColumn(_("Mount point"), ren)
        self.column6.add_attribute(ren, "markup", INDEX_PARTITION_MOUNT_AS)
        self.wTree.get_widget("treeview_disks").append_column(self.column6)
        # format
        ren = gtk.CellRendererText()
        self.column7 = gtk.TreeViewColumn(_("Format?"), ren)
        self.column7.add_attribute(ren, "markup", INDEX_PARTITION_FORMAT_AS)        
        self.wTree.get_widget("treeview_disks").append_column(self.column7)
        # size
        ren = gtk.CellRendererText()
        self.column8 = gtk.TreeViewColumn(_("Size"), ren)
        self.column8.add_attribute(ren, "markup", INDEX_PARTITION_SIZE)
        self.wTree.get_widget("treeview_disks").append_column(self.column8)
        # Used space
        ren = gtk.CellRendererText()
        self.column9 = gtk.TreeViewColumn(_("Free space"), ren)
        self.column9.add_attribute(ren, "markup", INDEX_PARTITION_FREE_SPACE)
        self.wTree.get_widget("treeview_disks").append_column(self.column9)

        self.wTree.get_widget("entry_your_name").connect("notify::text", self.assign_realname)        
        self.wTree.get_widget("entry_username").connect("notify::text", self.assign_username)    
        self.wTree.get_widget("entry_hostname").connect("notify::text", self.assign_hostname)    

        # events for detecting password mismatch..        
        self.wTree.get_widget("entry_userpass1").connect("changed", self.assign_password)
        self.wTree.get_widget("entry_userpass2").connect("changed", self.assign_password)

        # link the checkbutton to the combobox
        grub_check = self.wTree.get_widget("checkbutton_grub")
        grub_box = self.wTree.get_widget("combobox_grub")
        grub_check.connect("clicked", self.assign_grub_install, grub_box)        
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
        self.wTree.get_widget("label_install_progress").set_markup("<i>%s</i>" % _("Calculating file indexes..."))
    
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
        
        self.browser = webkit.WebView()
        s = self.browser.get_settings()
        s.set_property('enable-file-access-from-file-uris', True)
        s.set_property('enable-default-context-menu', False)     
        self.wTree.get_widget("scrolled_partitions").add(self.browser)   
        
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
        self.wTree.get_widget("label_your_name_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This will be shown in the About Me application"))
        self.wTree.get_widget("label_username").set_markup("<b>%s</b>" % _("Your username"))
        self.wTree.get_widget("label_username_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This is the name you will use to log in to your computer"))
        self.wTree.get_widget("label_choose_pass").set_markup("<b>%s</b>" % _("Your password"))
        self.wTree.get_widget("label_pass_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("Please enter your password twice to ensure it is correct"))
        self.wTree.get_widget("label_hostname").set_markup("<b>%s</b>" % _("Hostname"))
        self.wTree.get_widget("label_hostname_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This hostname will be the computers name on the network"))
                
        self.wTree.get_widget("face_label").set_markup("<b>%s</b>" % _("Your picture"))
        self.wTree.get_widget("face_description").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This picture represents your user account. It is used in the login screen and a few other places."))
        self.wTree.get_widget("face_take_picture_button").set_label(_("Take a photo"))        
        
        self.wTree.get_widget("face_select_picture_button").set_label(_("Select a picture"))
        self.wTree.get_widget("face_select_picture_button").set_tooltip_text(_("Click this button to choose a picture for your account"))
                
        # timezones
        self.wTree.get_widget("label_timezones").set_label(_("Selected timezone:"))
        
        # grub
        self.wTree.get_widget("label_grub").set_markup("<b>%s</b>" % _("Bootloader"))
        self.wTree.get_widget("checkbutton_grub").set_label(_("Install GRUB"))
        self.wTree.get_widget("label_grub_help").set_label(_("GRUB is a bootloader used to load the Linux kernel"))
        
        # keyboard page
        self.wTree.get_widget("label_test_kb").set_label(_("Use this box to test your keyboard layout"))
        self.wTree.get_widget("label_kb_model").set_label(_("Model"))        
        
        # custom install warning
        self.wTree.get_widget("label_custom_install_directions_1").set_label(_("You have selected to manage your partitions manually, this feature is for ADVANCED USERS ONLY."))
        self.wTree.get_widget("label_custom_install_directions_2").set_label(_("Before continuing, please mount your target filesystem(s) at /target."))
        self.wTree.get_widget("label_custom_install_directions_3").set_label(_("Do NOT mount virtual devices such as /dev, /proc, /sys, etc on /target/."))
        self.wTree.get_widget("label_custom_install_directions_4").set_label(_("During the install, you will be given time to chroot into /target and install any pacakges that will be needed to boot your new system."))
        self.wTree.get_widget("label_custom_install_directions_5").set_label(_("During the install, you will be required to write your own /etc/fstab."))
        self.wTree.get_widget("label_custom_install_directions_6").set_label(_("If you aren't sure what any of this means, please go back and deselect manual partition management."))

        # custom install installation paused directions
        self.wTree.get_widget("label_custom_install_paused_1").set_label(_("Please do the following and then click Forward to finish Installation:"))
        self.wTree.get_widget("label_custom_install_paused_2").set_label(_("Create /target/etc/fstab for the filesystems as they will be mounted in your new system, matching those currently mounted at /target (without using the /target prefix in the mount paths themselves)."))
        self.wTree.get_widget("label_custom_install_paused_3").set_label(_("Install any packages that may be needed for first boot (mdadm, cryptsetup, dmraid, etc) by calling \"sudo chroot /target\" followed by the relevant apt-get/aptitude installations."))
        self.wTree.get_widget("label_custom_install_paused_4").set_label(_("Note that in order for update-initramfs to work properly in some cases (such as dm-crypt), you may need to have drives currently mounted using the same block device name as they appear in /target/etc/fstab."))
        self.wTree.get_widget("label_custom_install_paused_5").set_label(_("Double-check that your /target/etc/fstab is correct, matches what your new system will have at first boot, and matches what is currently mounted at /target."))

        # hdd page
        self.wTree.get_widget("label_radio_hdd").set_label(_("Install Linux Mint on the selected drive:"))
        self.wTree.get_widget("label_radio_custom").set_label(_("Manually mount partitions (ADVANCED USERS ONLY)."))
        
        #Columns
        self.column1.set_title(_("Hard drive")) 
        self.column2.set_title(_("Description")) 
        self.column3.set_title(_("Device")) 
        self.column4.set_title(_("Type")) 
        self.column5.set_title(_("Operating system")) 
        self.column6.set_title(_("Mount point")) 
        self.column7.set_title(_("Format?")) 
        self.column8.set_title(_("Size")) 
        self.column9.set_title(_("Free space")) 
        self.column10.set_title(_("Layout")) 
        self.column11.set_title(_("Variant")) 
        self.column12.set_title(_("Overview")) 
        
        #Partitions
        self.wTree.get_widget("label_edit_partitions").set_label(_("Edit partitions"))

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

    def assign_partition(self, widget, data=None, data2=None):
        ''' assign the partition ... '''
        model, iter = self.wTree.get_widget("treeview_disks").get_selection().get_selected()
        if iter is not None:
            row = model[iter]
            partition = row[INDEX_PARTITION_OBJECT]            
            if not partition.partition.type == parted.PARTITION_EXTENDED and not partition.partition.number == -1:            
                dlg = PartitionDialog(row[INDEX_PARTITION_PATH], row[INDEX_PARTITION_MOUNT_AS], row[INDEX_PARTITION_FORMAT_AS], row[INDEX_PARTITION_TYPE])
                (mount_as, format_as) = dlg.show()                
                self.assign_mount_point(partition, mount_as, format_as)
                
    def partitions_popup_menu( self, widget, event ):
        if event.button == 3:
            model, iter = self.wTree.get_widget("treeview_disks").get_selection().get_selected()
            if iter is not None:
                partition = model.get_value(iter, INDEX_PARTITION_OBJECT)
                partition_type = model.get_value(iter, INDEX_PARTITION_TYPE)
                if not partition.partition.type == parted.PARTITION_EXTENDED and not partition.partition.number == -1 and "swap" not in partition_type:
                    menu = gtk.Menu()
                    menuItem = gtk.MenuItem(_("Edit"))
                    menuItem.connect( "activate", self.assign_partition, partition)
                    menu.append(menuItem)
                    menuItem = gtk.SeparatorMenuItem()
                    menu.append(menuItem)
                    menuItem = gtk.MenuItem(_("Assign to /"))
                    menuItem.connect( "activate", self.assign_mount_point_context_menu_wrapper, partition, "/", "ext4")
                    menu.append(menuItem)
                    menuItem = gtk.MenuItem(_("Assign to /home"))
                    menuItem.connect( "activate", self.assign_mount_point_context_menu_wrapper, partition, "/home", "")
                    menu.append(menuItem)

                    if self.setup.gptonefi:
                        menuItem = gtk.SeparatorMenuItem()
                        menu.append(menuItem)
                        menuItem = gtk.MenuItem(_("Assign to /boot/efi"))
                        menuItem.connect( "activate", self.assign_mount_point_context_menu_wrapper, partition, "/boot/efi", "")
                        menu.append(menuItem)

                    menu.show_all()
                    menu.popup( None, None, None, event.button, event.time )

    def assign_mount_point_context_menu_wrapper(self, menu, partition, mount_point, filesystem):
        self.assign_mount_point(partition, mount_point, filesystem)

    def assign_mount_point(self, partition, mount_point, filesystem):
        
        #Assign it in the treeview
        model = self.wTree.get_widget("treeview_disks").get_model()
        iter = model.get_iter_first()
        while iter is not None:
            iter_partition = model.get_value(iter, INDEX_PARTITION_OBJECT)
            if iter_partition == partition:
                model.set_value(iter, INDEX_PARTITION_MOUNT_AS, mount_point)         
                model.set_value(iter, INDEX_PARTITION_FORMAT_AS, filesystem)
            else:
                mountpoint = model.get_value(iter, INDEX_PARTITION_MOUNT_AS)
                if mountpoint == mount_point:
                    model.set_value(iter, INDEX_PARTITION_MOUNT_AS, "")
                    model.set_value(iter, INDEX_PARTITION_FORMAT_AS, "")
            iter = model.iter_next(iter)
        #Assign it in our setup
        for apartition in self.setup.partitions:
            if (apartition.partition.path == partition.partition.path):
                apartition.mount_as = mount_point
                apartition.format_as = filesystem
            else:                
                if apartition.mount_as == mount_point:
                    apartition.mount_as = None
                    apartition.format_as = None
        self.setup.print_setup()
                

    def refresh_partitions(self, widget, data=None):
        ''' refresh the partitions ... '''
        self.build_partitions()

    def edit_partitions(self, widget, data=None):
        ''' edit the partitions ... '''
        os.popen("gparted &")

    def build_lang_list(self):

        #Try to find out where we're located...
        cur_country_code = None
        try:
            whatismyip = 'http://www.linuxmint.com/installer/show_my_ip.php'
            ip = urllib.urlopen(whatismyip).readlines()[0]
            gi = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)
            cur_country_code = gi.country_code_by_addr(ip)
        except:
            pass #best effort, we get here if we're not connected to the Internet            

        #Plan B... find out what locale we're in (i.e. USA on the live session)
        cur_lang = os.environ['LANG']
        if("." in cur_lang):
            cur_lang = cur_lang.split(".")[0]

        model = gtk.ListStore(str,str,gtk.gdk.Pixbuf)
        model.set_sort_column_id(0, gtk.SORT_ASCENDING)

        #Load countries into memory
        countries = {}
        file = open(os.path.join(self.resource_dir, 'countries'), "r")
        for line in file:
            line = line.strip()
            split = line.split("=")
            if len(split) == 2:
                countries[split[0]] = split[1]
        file.close()

        #Load languages into memory
        languages = {}
        file = open(os.path.join(self.resource_dir, 'languages'), "r")
        for line in file:
            line = line.strip()
            split = line.split("=")
            if len(split) == 2:
                languages[split[0]] = split[1]
        file.close()

        path = os.path.join(self.resource_dir, 'locales')
        locales = open(path, "r")
        cur_index = -1 # find the locale :P
        set_index = None
        for line in locales:
            cur_index += 1
            if "UTF-8" in line:
                locale_code = line.replace("UTF-8", "")
                locale_code = locale_code.replace(".", "")
                locale_code = locale_code.strip()
                if "_" in locale_code:
                    split = locale_code.split("_")
                    if len(split) == 2:
                        language_code = split[0]
                        if language_code in languages:
                            language = languages[language_code]
                        else:
                            language = language_code

                        country_code = split[1].lower()
                        if country_code in countries:
                            country = countries[country_code]
                        else:
                            country = country_code

                        language_label = "%s (%s)" % (language, country)
                        #language_label = "%s - %s" % (country, language)

                        iter = model.append()
                        model.set_value(iter, 0, language_label)
                        model.set_value(iter, 1, locale_code)
                        flag_path = self.resource_dir + '/flags/16/' + country_code + '.png'
                        if os.path.exists(flag_path):
                            model.set_value(iter, 2, gtk.gdk.pixbuf_new_from_file(flag_path))
                        else:
                            flag_path = self.resource_dir + '/flags/16/generic.png'
                            model.set_value(iter, 2, gtk.gdk.pixbuf_new_from_file(flag_path))
                        # If it's matching our country code, that's our language right there.. 
                        if ((cur_country_code is not None) and (cur_country_code.lower() == country_code)):                            
                            if (set_index is None):
                                set_index = iter                                
                            else:
                                # If we find more than one language for a particular country, one of them being English, go for English by default.
                                if (language_code == "en"):
                                    set_index = iter                 
                                # Guesswork... handy for countries which have their own language (fr_FR, de_DE, es_ES.. etc. )
                                elif (country_code == language_code):
                                    set_index = iter
                                    
                        # as a plan B... use the locale (USA)
                        if((set_index is None) and (locale_code == cur_lang)):
                            set_index = iter
                            #print "Set via locale: " + cur_lang

        treeview = self.wTree.get_widget("treeview_language_list")
        treeview.set_model(model)
        if(set_index is not None):
            column = treeview.get_column(0)
            path = model.get_path(set_index)
            treeview.set_cursor(path, focus_column=column)
            treeview.scroll_to_cell(path, column=column)
        treeview.set_search_column(0)

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
                                
    def build_hdds(self):
        self.setup.disks = []
        model = gtk.ListStore(str, str)            
        output = subprocess.Popen("lsblk -nrdo TYPE,NAME,SIZE,RM | grep ^disk", shell=True, stdout=subprocess.PIPE)
        for line in output.stdout:
            line = line.rstrip("\r\n")
            sections = line.split(" ")
            if len(sections) == 4:
                dev_name = sections[1]
                dev_size = sections[2]
                dev_removable = sections[3]
                dev_path = "/dev/%s" % dev_name
                dev_model = commands.getoutput("lsblk -nrdo MODEL %s" % dev_path)
                self.setup.disks.append(dev_path)
                description = "%s (%s)" % (dev_model, dev_size)
                if dev_removable == "1":
                    description = _("Removable device:") + " " + description
                iter = model.append([dev_path, description]);
            else:
                print "WARNING, erroneous info collected for disks. Please show this to the development team: %s" % line
                
        self.wTree.get_widget("treeview_hdds").set_model(model)
        
        if(len(self.setup.disks) > 0):
            # select the first HDD
            treeview = self.wTree.get_widget("treeview_hdds")            
            column = treeview.get_column(0)
            path = model.get_path(model.get_iter_first())
            treeview.set_cursor(path, focus_column=column)
            treeview.scroll_to_cell(path, column=column)
            self.setup.target_disk = model.get_value(model.get_iter_first(), 0) 
    
    def build_grub_partitions(self):
        grub_model = gtk.ListStore(str)
        # Add disks
        for disk in self.setup.disks:
            grub_model.append([disk])
        # Add partitions
        output = commands.getoutput("lsblk -nrbo TYPE,NAME | grep ^part").split("\n")
        for line in output:
            line = line.strip()
            sections = line.split(" ")
            if len(sections) == 2:
                partition = sections[1]
                grub_model.append(["/dev/%s" % partition])
            else:
                print "WARNING, erroneous info collected for partitions. Please show this to the development team: %s" % line            
        self.wTree.get_widget("combobox_grub").set_model(grub_model)
        self.wTree.get_widget("combobox_grub").set_active(0)
  
    def build_partitions(self):        
        self.window.set_sensitive(False)
        # "busy" cursor.
        cursor = gtk.gdk.Cursor(gtk.gdk.WATCH)
        self.window.window.set_cursor(cursor)        
        
        os.popen('mkdir -p /tmp/live-installer/tmpmount')
        
        try:                                                                                            
            #grub_model = gtk.ListStore(str)
            self.setup.partitions = []
            
            html_partitions = ""        
            model = gtk.ListStore(str,str,str,str,str,str,str, object, bool, str, str, bool)
            model2 = gtk.ListStore(str)
            
            swap_found = False
            os.system("modprobe efivars >/dev/null 2>&1")
            # Are we running under with efi ?
            if os.path.exists("/proc/efi") or os.path.exists("/sys/firmware/efi"):
                self.setup.gptonefi = True
            if self.setup.target_disk is not None:
                path =  self.setup.target_disk # i.e. /dev/sda
                #grub_model.append([path])
                device = parted.getDevice(path)                
                try:
                    disk = parted.Disk(device)
                except Exception:
                    dialog = QuestionDialog(_("Installation Tool"), _("No partition table was found on the hard drive. Do you want the installer to create a set of partitions for you? Note: This will erase any data present on the disk."), self.window)
                    if (dialog.show()):
                        # Create a default partition set up
                        # try to load efivars
                        os.system("modprobe efivars >/dev/null 2>&1")
                        # Are we running under with efi ?
                        if (self.setup.gptonefi):
                            disk = parted.freshDisk(device, 'gpt')
                        else:
                            disk = parted.freshDisk(device, 'msdos')
                        disk.commit()
                        post_partition_gap = parted.sizeToSectors(512, "KiB", device.sectorSize)
                        post_mbr_gap = parted.sizeToSectors(1, "MiB", device.sectorSize) # Grub2 requires a post-MBR gap
                        start = post_mbr_gap  
                        #efi                        
                        regions = disk.getFreeSpaceRegions()
                        if ((len(regions) > 0) and (self.setup.gptonefi)):
                                region = regions[-1]                                                                                                                                  
                                root_size = 419430
                                num_sectors = parted.sizeToSectors(root_size, "KiB", device.sectorSize)
                                end = start + num_sectors                                
                                cylinder = device.endSectorToCylinder(end)
                                end = device.endCylinderToSector(cylinder)
                                geometry = parted.Geometry(device=device, start=start, end=end)
                                if end < region.length:
                                    partition = parted.Partition(disk=disk, type=parted.PARTITION_NORMAL, geometry=geometry)
                                    constraint = parted.Constraint(exactGeom=geometry)
                                    disk.addPartition(partition=partition, constraint=constraint)
                                    partition.setFlag(parted.PARTITION_BOOT)
                                    
                                    disk.commit()
                                    os.system("mkfs.vfat %s -F 32 " % partition.path)
                                    start = end + post_partition_gap
                        #Swap
                        regions = disk.getFreeSpaceRegions()
                        if len(regions) > 0:
                            region = regions[-1]    
                            ram_size = int(commands.getoutput("cat /proc/meminfo | grep MemTotal | awk {'print $2'}")) # in KiB
                            ram_size = ram_size * 1.5 # Give 1.5 times the amount of RAM                            
                            if ram_size > 2097152:
                                ram_size = 2097152 # But no more than 2GB
                            num_sectors = parted.sizeToSectors(ram_size, "KiB", device.sectorSize)
                            end = start + num_sectors
                            cylinder = device.endSectorToCylinder(end)
                            end = device.endCylinderToSector(cylinder)
                            geometry = parted.Geometry(device=device, start=start, end=end)
                            if end < region.length:
                                partition = parted.Partition(disk=disk, type=parted.PARTITION_NORMAL, geometry=geometry)
                                constraint = parted.Constraint(exactGeom=geometry)
                                disk.addPartition(partition=partition, constraint=constraint)
                                disk.commit()
                                os.system("mkswap %s" % partition.path)                     
                        
                        #Root
                        regions = disk.getFreeSpaceRegions()
                        if len(regions) > 0:
                            region = regions[-1]
                            start = end + post_partition_gap
                            end = start + region.length - parted.sizeToSectors(1, "MiB", device.sectorSize)
                            geometry = parted.Geometry(device=device, start=start, end=end)
                            partition = parted.Partition(disk=disk, type=parted.PARTITION_NORMAL, geometry=geometry)
                            constraint = parted.Constraint(exactGeom=geometry)
                            disk.addPartition(partition=partition, constraint=constraint)
                            disk.commit()                            
                            os.system("mkfs.ext4 %s" % partition.path)
                            
                        self.build_partitions()
                        return
                    else:
                        # Do nothing... just get out of here..
                        raise
                partition = disk.getFirstPartition()
                last_added_partition = PartitionSetup(partition)
                #self.setup.partitions.append(last_added_partition)
                partition = partition.nextPartition()
                html_partitions = html_partitions + "<table width='100%'><tr>"
                while (partition is not None):
                    if last_added_partition.partition.number == -1 and partition.number == -1:
                        last_added_partition.add_partition(partition)
                    else:                        
                        last_added_partition = PartitionSetup(partition)                       
                        
                        if "swap" in last_added_partition.type:
                            last_added_partition.type = "swap"
                            last_added_partition.description = "swap"

                        if partition.number != -1 and "swap" not in last_added_partition.type and partition.type != parted.PARTITION_EXTENDED:
                            
                            #grub_model.append([partition.path])

                            #Umount temp folder
                            if ('/tmp/live-installer/tmpmount' in commands.getoutput('mount')):
                                os.popen('umount /tmp/live-installer/tmpmount')

                            #Mount partition if not mounted
                            if (partition.path not in commands.getoutput('mount')):                                
                                os.system("mount %s /tmp/live-installer/tmpmount" % partition.path)

                            #Identify partition's description and used space
                            if (partition.path in commands.getoutput('mount')):
                                df_lines = commands.getoutput("grep %s /proc/mounts 2>/dev/null" % partition.path).split('\n')
                                for df_line in df_lines:
                                    df_elements = df_line.split()
                                    if df_elements[0] == partition.path:
                                        last_added_partition.used_space = df_elements[4]  
                                        mount_point = df_elements[5]                              
                                        if "%" in last_added_partition.used_space:
                                            used_space_pct = int(last_added_partition.used_space.replace("%", "").strip())
                                            last_added_partition.free_space = int(float(last_added_partition.size) * (float(100) - float(used_space_pct)) / float(100))                                            
                                                                            
                                        if os.path.exists(os.path.join(mount_point, 'etc/lsb-release')):
                                            last_added_partition.description = commands.getoutput("cat " + os.path.join(mount_point, 'etc/lsb-release') + " | grep DISTRIB_DESCRIPTION").replace('DISTRIB_DESCRIPTION', '').replace('=', '').replace('"', '').strip()
                                        if os.path.exists(os.path.join(mount_point, 'etc/issue')):
                                            last_added_partition.description = commands.getoutput("cat " + os.path.join(mount_point, 'etc/issue')).replace('\\n', '').replace('\l', '').strip()
                                        if os.path.exists(os.path.join(mount_point, 'etc/linuxmint/info')):
                                            last_added_partition.description = commands.getoutput("cat " + os.path.join(mount_point, 'etc/linuxmint/info') + " | grep GRUB_TITLE").replace('GRUB_TITLE', '').replace('=', '').replace('"', '').strip()
                                        if commands.getoutput("/sbin/gdisk -l %s | grep EF00 | awk {'print $1;'}" % self.setup.target_disk) == str(partition.number):                                            
                                            last_added_partition.description = "EFI System Partition"
                                        if os.path.exists(os.path.join(mount_point, 'Windows/servicing/Version')):
                                            version = commands.getoutput("ls %s" % os.path.join(mount_point, 'Windows/servicing/Version'))                                    
                                            if version.startswith("6.1"):
                                                last_added_partition.description = "Windows 7"
                                            elif version.startswith("6.0"):
                                                last_added_partition.description = "Windows Vista"
                                            elif version.startswith("5.1") or version.startswith("5.2"):
                                                last_added_partition.description = "Windows XP"
                                            elif version.startswith("5.0"):
                                                last_added_partition.description = "Windows 2000"
                                            elif version.startswith("4.90"):
                                                last_added_partition.description = "Windows Me"
                                            elif version.startswith("4.1"):
                                                last_added_partition.description = "Windows 98"
                                            elif version.startswith("4.0.1381"):
                                                last_added_partition.description = "Windows NT"
                                            elif version.startswith("4.0.950"):
                                                last_added_partition.description = "Windows 95"
                                        elif os.path.exists(os.path.join(mount_point, 'Boot/BCD')):
                                            if os.system("grep -qs \"V.i.s.t.a\" " + os.path.join(mount_point, 'Boot/BCD')) == 0:
                                                last_added_partition.description = "Windows Vista bootloader"
                                            elif os.system("grep -qs \"W.i.n.d.o.w.s. .7\" " + os.path.join(mount_point, 'Boot/BCD')) == 0:
                                                last_added_partition.description = "Windows 7 bootloader"
                                            elif os.system("grep -qs \"W.i.n.d.o.w.s. .R.e.c.o.v.e.r.y. .E.n.v.i.r.o.n.m.e.n.t\" " + os.path.join(mount_point, 'Boot/BCD')) == 0:
                                                last_added_partition.description = "Windows recovery"
                                            elif os.system("grep -qs \"W.i.n.d.o.w.s. .S.e.r.v.e.r. .2.0.0.8\" " + os.path.join(mount_point, 'Boot/BCD')) == 0:
                                                last_added_partition.description = "Windows Server 2008 bootloader"
                                            else:
                                                last_added_partition.description = "Windows bootloader"
                                        elif os.path.exists(os.path.join(mount_point, 'Windows/System32')):
                                            last_added_partition.description = "Windows"
                                        break
                            else:
                                print "Failed to mount %s" % partition.path

                            
                            #Umount temp folder
                            if ('/tmp/live-installer/tmpmount' in commands.getoutput('mount')):
                                os.popen('umount /tmp/live-installer/tmpmount')
                                
                    if last_added_partition.size > 1.0:
                        if last_added_partition.partition.type == parted.PARTITION_LOGICAL:
                            display_name = "  " + last_added_partition.name
                        else:
                            display_name = last_added_partition.name

                        iter = model.append([display_name, last_added_partition.type, last_added_partition.description, "", "", '%.0f' % round(last_added_partition.size, 0), last_added_partition.free_space, last_added_partition, False, last_added_partition.start, last_added_partition.end, False]);
                        if last_added_partition.partition.number == -1:                     
                            model.set_value(iter, INDEX_PARTITION_TYPE, "<span foreground='#a9a9a9'>%s</span>" % last_added_partition.type)                                    
                        elif last_added_partition.partition.type == parted.PARTITION_EXTENDED:                    
                            model.set_value(iter, INDEX_PARTITION_TYPE, "<span foreground='#a9a9a9'>%s</span>" % _("Extended"))  
                        else:                                        
                            if last_added_partition.type == "ntfs":
                                color = "#66a6a8"
                                style = "ntfs"
                            elif last_added_partition.type == "fat32" or last_added_partition.type == "fat16":
                                color = "#47872a"
                                style = "fat"
                                if last_added_partition.description == "EFI System Partition":
                                    last_added_partition.mount_as = "/boot/efi"
                                    model.set_value(iter, INDEX_PARTITION_MOUNT_AS, "/boot/efi")
                            elif last_added_partition.type == "ext4":
                                color = "#21619e"
                                style = "ext4"
                            elif last_added_partition.type == "ext3":
                                color = "#2582a0"
                                style = "ext"
                            elif last_added_partition.type == "ext2":
                                color = "#2582a0"
                                style = "ext"
                            elif last_added_partition.type in ["linux-swap", "swap"]:
                                color = "#be3a37"
                                style = "swap"
                                last_added_partition.mount_as = "swap"
                                model.set_value(iter, INDEX_PARTITION_MOUNT_AS, "swap")
                            else:
                                color = "#636363"
                                style = "unknown"
                            model.set_value(iter, INDEX_PARTITION_TYPE, "<span foreground='%s'>%s</span>" % (color, last_added_partition.type))                                            
                            html_partition = "<td class='partition-cell' title='$title' width='$space%'><div class='partition " + style + "'>\n  <div class='shine' style='width: $usage; height: 50px'></div>\n <div class='partition-text'>$path</div><div class='partition-os'>$OS</div>\n</div>\n</td>"
                            deviceSize = float(device.getSize()) * float(0.9) # Hack.. reducing the real size to 90% of what it is, to make sure our partitions fit..
                            space = int((float(partition.getSize()) / deviceSize) * float(80))                            
                            subs = {}
                            subs['OS'] = last_added_partition.description
                            subs['path'] = display_name.replace("/dev/", "")
                            if (space < 10 and len(last_added_partition.description) > 5):
                                subs['OS'] = "%s..." % last_added_partition.description[0:5]
                            if (space < 5):
                                #Not enough space, don't write the name
                                subs['path'] = ""                          
                                subs['OS'] = ""

                            subs['color'] = color                            
                            if (space == 0):
                                space = 1
                            subs['space'] = space
                            subs['title'] = display_name + "\n" + last_added_partition.description
                            if "%" in last_added_partition.used_space:               
                                subs['usage'] = last_added_partition.used_space.strip()
                            else:
                                subs['usage'] = "0"
                            html_partition = string.Template(html_partition).safe_substitute(subs)                     
                            html_partitions = html_partitions + html_partition
                            self.setup.partitions.append(last_added_partition)
                            
                    partition = partition.nextPartition()
                html_partitions = html_partitions + "</tr></table>"
            #self.wTree.get_widget("combobox_grub").set_model(grub_model)
            #self.wTree.get_widget("combobox_grub").set_active(0)
                        
            import tempfile            
            html_header = "<html><head><style> \
body{background-color:#d6d6d6;} \
.partition{position:relative;width:100%;float: left;background: white;border-radius: 3px;} \
.partition-cell{position:relative;margin: 2px 5px 2px 0;padding: 1px;float: left;background: #9c9c9c;border-radius: 3px;} \
.partition-text{position:absolute;top:10;text-align: center;width=100px;left: 0;right: 0;margin: 0 auto;font-size:12px;font-weight: bold;color:#ffffff;text-shadow: 1px 1px 1px #000;} \
.partition-os{position:absolute;top:30;text-align: center;width=100px;left: 0;right: 0;margin: 0 auto;font-size:10px;color:#ffffff;text-shadow: 1px 1px 1px #000;} \
.fat {background-color: #b4d59b;background-image: -webkit-gradient(linear, left top, left bottom, from(#b4d59b), to(#47872a));background-image: -webkit-linear-gradient(top, #b4d59b, #47872a);background-image: -moz-linear-gradient(top, #b4d59b, #47872a);} \
.ntfs {background-color: #c9e3e4;background-image: -webkit-gradient(linear, left top, left bottom, from(#c9e3e4), to(#66a6a8));background-image: -webkit-linear-gradient(top,#c9e3e4, #66a6a8);background-image: -moz-linear-gradient(top, #c9e3e4, #66a6a8);} \
.ext {background-color: #98d4e0;background-image: -webkit-gradient(linear, left top, left bottom, from(#98d4e0), to(#2582a0));background-image: -webkit-linear-gradient(top, #98d4e0, #2582a0);background-image: -moz-linear-gradient(top, #98d4e0, #2582a0);} \
.ext4 {background-color: #95c4de;background-image: -webkit-gradient(linear, left top, left bottom, from(#95c4de), to(#21619e));background-image: -webkit-linear-gradient(top, #95c4de, #21619e);background-image: -moz-linear-gradient(top, #95c4de, #21619e);} \
.swap {background-color: #eaaca9;background-image: -webkit-gradient(linear, left top, left bottom, from(#eaaca9), to(#be3a37));background-image: -webkit-linear-gradient(top, #eaaca9, #be3a37);background-image: -moz-linear-gradient(top, #eaaca9, #be3a37);} \
.unknown {background-color: #c8c8c8;background-image: -webkit-gradient(linear, left top, left bottom, from(#c8c8c8), to(#636363));background-image: -webkit-linear-gradient(top, #c8c8c8, #636363);background-image: -moz-linear-gradient(top, #c8c8c8, #636363);} \
.shine {position: relative;opacity: .2; top: 0;right: 0;bottom: 0;left: 0;background: #fff;-moz-border-radius: 0px;-webkit-border-radius: 0px;border-radius: 0px;margin:1px;} \
</style></head><body>"
            html_footer = "</body></html>"
            html = html_header + html_partitions + html_footer
           
            # create temporary file
            f = tempfile.NamedTemporaryFile(delete=False)
            f.write(html)
            f.close()  
       
            self.browser.open(f.name)            
            #browser.load_html_string(html, "file://")                 
            self.wTree.get_widget("scrolled_partitions").show_all()                                                                        
            self.wTree.get_widget("treeview_disks").set_model(model)                                
            
            
        except Exception, detail:
            print detail  
                
        self.window.set_sensitive(True)
        self.window.window.set_cursor(None)        
        

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
                self.setup.keyboard_layout = section.split("+")[1]
            if("xkb_geometry" in line):
                first_bracket = line.index("(") +1
                substr = line[first_bracket:]
                last_bracket = substr.index(")")
                substr = substr[0:last_bracket]
                keyboard_geom = substr
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
        set_keyboard_variant = None

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
            if(item == keyboard_geom):
                set_keyboard_model = iter_model
        root_layouts = root.getElementsByTagName('layoutList')[0]
        for element in root_layouts.getElementsByTagName('layout'):
            conf = element.getElementsByTagName('configItem')[0]
            name = conf.getElementsByTagName('name')[0]
            desc = conf.getElementsByTagName('description')[0]
            iter_layout = model_layouts.append([self.getText(desc.childNodes), self.getText(name.childNodes)])
            item = self.getText(name.childNodes)
            if(item == self.setup.keyboard_layout):
                set_keyboard_layout = iter_layout
        # now set the model        
        self.wTree.get_widget("combobox_kb_model").set_model(model_models)
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
             # show it in the combo
            combo = self.wTree.get_widget("combobox_kb_model")
            model = combo.get_model()                    
            combo.set_active_iter(set_keyboard_model)            
            
    def build_kb_variant_lists(self):
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
                self.setup.keyboard_layout = section.split("+")[1]
        p.poll()

        xml_file = '/usr/share/X11/xkb/rules/xorg.xml'      
        model_variants = gtk.ListStore(str,str)
        model_variants.set_sort_column_id(0, gtk.SORT_ASCENDING)        
        dom = parse(xml_file)
        
        # grab the root element
        root = dom.getElementsByTagName('xkbConfigRegistry')[0]
        # build the list of variants       
        root_layouts = root.getElementsByTagName('layoutList')[0]
        for layout in root_layouts.getElementsByTagName('layout'):
            conf = layout.getElementsByTagName('configItem')[0]
            layout_name = self.getText(conf.getElementsByTagName('name')[0].childNodes)            
            layout_description = self.getText(conf.getElementsByTagName('description')[0].childNodes)            
            if (layout_name == self.setup.keyboard_layout):
                iter_variant = model_variants.append([layout_description, None])  
                variants_list = layout.getElementsByTagName('variantList')
                if len(variants_list) > 0:
                    root_variants = layout.getElementsByTagName('variantList')[0]   
                    for variant in root_variants.getElementsByTagName('variant'):                    
                        variant_conf = variant.getElementsByTagName('configItem')[0]
                        variant_name = self.getText(variant_conf.getElementsByTagName('name')[0].childNodes)
                        variant_description = "%s - %s" % (layout_description, self.getText(variant_conf.getElementsByTagName('description')[0].childNodes))
                        iter_variant = model_variants.append([variant_description, variant_name])                                                    
                break
                                                                                
        # now set the model        
        self.wTree.get_widget("treeview_variants").set_model(model_variants)
        
        # select the first item (standard variant layout)
        treeview = self.wTree.get_widget("treeview_variants")
        model = treeview.get_model()
        column = treeview.get_column(0)
        path = model.get_path(model.get_iter_first())
        treeview.set_cursor(path, focus_column=column)

    def getText(self, nodelist):
        rc = []
        for node in nodelist:
            if node.nodeType == node.TEXT_NODE:
                rc.append(node.data)
        return ''.join(rc)
    

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
        self.setup.language = row[1]
        self.setup.print_setup()
        try:            
            self.translation = gettext.translation('live-installer', "/usr/share/linuxmint/locale", languages=[self.setup.language])
            self.translation.install()
        except Exception, detail:
            print "No translation found, switching back to English"
            self.translation = gettext.translation('live-installer', "/usr/share/linuxmint/locale", languages=['en'])
            self.translation.install()        
        try:
            self.i18n()
        except:
            pass # Best effort. Fails the first time as self.column1 doesn't exist yet.

    def assign_hdd(self, treeview, data=None):
        ''' Called whenever someone updates the HDD '''
        model = treeview.get_model()
        active = treeview.get_selection().get_selected_rows()
        if(len(active) < 1):
            return
        active = active[1][0]
        if(active is None):
            return
        row = model[active]
        self.setup.target_disk = row[0] 

    def hdd_pane_toggled(self, hdd_button):
        ''' Called whenever the radio buttons on the hdd page toggle ''' 
        if(hdd_button.get_active()):
            self.wTree.get_widget("treeview_hdds").set_sensitive(True)
            self.setup.skip_mount = False
        else:
            self.wTree.get_widget("treeview_hdds").set_sensitive(False)
            self.setup.skip_mount = True
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
       
    def assign_keyboard_model(self, combobox, data=None):
        ''' Called whenever someone updates the keyboard model '''
        model = combobox.get_model()
        active = combobox.get_active()
        if(active > -1):
            row = model[active]
            os.system("setxkbmap -model %s" % row[1])
            self.setup.keyboard_model = row[1]
            self.setup.keyboard_model_description = row[0]
        self.setup.print_setup()

    def assign_keyboard_layout(self, treeview, data=None):
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
        self.setup.keyboard_layout = row[1]
        self.setup.keyboard_layout_description = row[0]
        self.build_kb_variant_lists()
        self.setup.print_setup()

    def assign_keyboard_variant(self, treeview, data=None):
        ''' Called whenever someone updates the keyboard layout '''
        model = treeview.get_model()
        active = treeview.get_selection().get_selected_rows()
        if(len(active) < 1):
            return
        active = active[1][0]
        if(active is None):
            return
        row = model[active]
        if (row[1] is None):
            os.system("setxkbmap -layout %s" % self.setup.keyboard_layout)
        else:
            os.system("setxkbmap -variant %s" % row[1])
        self.setup.keyboard_variant = row[1]
        self.setup.keyboard_variant_description = row[0]
        self.setup.print_setup()
        
        filename = "/tmp/live-install-keyboard-layout.png"
        os.system("python /usr/lib/live-installer/frontend/generate_keyboard_layout.py %s %s %s" % (self.setup.keyboard_layout, self.setup.keyboard_variant, filename))
        self.wTree.get_widget("image_keyboard").set_from_file(filename)        

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
            self.wTree.get_widget("label_mismatch").set_label(_("Passwords do not match"))            
        else:
            self.wTree.get_widget("image_mismatch").set_from_stock(gtk.STOCK_OK, gtk.ICON_SIZE_BUTTON)            
            self.wTree.get_widget("label_mismatch").set_label(_("Passwords match"))                    
        self.setup.print_setup()
        
    def activate_page(self, index):
        help_text = _(self.wizard_pages[index].help_text)        
        self.wTree.get_widget("help_label").set_markup("<big><b>%s</b></big>" % help_text)
        self.wTree.get_widget("help_icon").set_from_file("/usr/share/live-installer/icons/%s" % self.wizard_pages[index].icon)
        self.wTree.get_widget("notebook1").set_current_page(index)

    def wizard_cb(self, widget, goback, data=None):
        ''' wizard buttons '''
        sel = self.wTree.get_widget("notebook1").get_current_page()
        self.wTree.get_widget("button_next").set_label(_("Forward"))
        self.wTree.get_widget("button_back").set_sensitive(True)
        
        # check each page for errors
        if(not goback):
            if(sel == self.PAGE_LANGUAGE):
                if ("_" in self.setup.language):
                    country_code = self.setup.language.split("_")[1]                    
                else:
                    country_code = self.setup.language
                combo = self.wTree.get_widget("combo_timezones")
                model = combo.get_model()
                iter = model.get_iter_first()
                while iter is not None:
                    iter_country_code = model.get_value(iter, 1).country_code
                    if iter_country_code == country_code:
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
                    errorMessage = _("Please provide your full name")
                elif(self.setup.username is None or self.setup.username == ""):
                    errorFound = True
                    errorMessage = _("Please provide a username")                
                elif(self.setup.password1 is None or self.setup.password1 == ""):
                    errorFound = True
                    errorMessage = _("Please provide a password for your user account")
                elif(self.setup.password1 != self.setup.password2):
                    errorFound = True
                    errorMessage = _("Your passwords do not match")
                elif(self.setup.hostname is None or self.setup.hostname == ""):
                    errorFound = True
                    errorMessage = _("Please provide a hostname")
                else:
                    for char in self.setup.username:
                        if(char.isupper()):
                            errorFound = True
                            errorMessage = _("Your username must be lower case")
                            break
                        elif(char.isspace()):
                            errorFound = True
                            errorMessage = _("Your username may not contain whitespace")
                    
                    for char in self.setup.hostname:
                        if(char.isupper()):
                            errorFound = True
                            errorMessage = _("Your hostname must be lower case")
                            break
                        elif(char.isspace()):
                            errorFound = True
                            errorMessage = _("Your hostname may not contain whitespace")
                    
                if (errorFound):
                    MessageDialog(_("Installation Tool"), errorMessage, gtk.MESSAGE_WARNING, self.window).show()
                else:
                    self.activate_page(self.PAGE_HDD)                
            elif(sel == self.PAGE_HDD):
                if (self.setup.skip_mount):
                    self.activate_page(self.PAGE_CUSTOMWARNING)
                else:
                    self.activate_page(self.PAGE_PARTITIONS)
                    self.build_partitions()
            elif(sel == self.PAGE_PARTITIONS):                
                model = self.wTree.get_widget("treeview_disks").get_model()

                # Check for root partition
                found_root_partition = False
                for partition in self.setup.partitions:
                    if(partition.mount_as == "/"):
                        found_root_partition = True
                        if partition.format_as is None or partition.format_as == "":                            
                            MessageDialog(_("Installation Tool"), _("Please indicate a filesystem to format the root (/) partition before proceeding"), gtk.MESSAGE_ERROR, self.window).show()
                            return
                if not found_root_partition:
                    MessageDialog(_("Installation Tool"), _("<b>Please select a root partition</b>"), gtk.MESSAGE_ERROR, self.window, _("A root partition is needed to install Linux Mint.\n\n - Mount point: /\n - Recommended size: Larger than 10GB\n - Recommended format: ext4\n ")).show()
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
                        MessageDialog(_("Installation Tool"), _("<b>Please select an EFI partition</b>"), gtk.MESSAGE_ERROR, self.window, _("An EFI system partition is needed with the following requirements:\n\n - Mount point: /boot/efi\n - Partition flags: Bootable\n - Size: Larger than 100MB\n - Format: vfat or fat32\n\nTo ensure compatibility with Windows we recommend you use the first partition of the disk as the EFI system partition.\n ")).show()
                        return

                self.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)

            elif(sel == self.PAGE_CUSTOMWARNING):
                self.build_grub_partitions()
                self.activate_page(self.PAGE_ADVANCED)
            elif(sel == self.PAGE_ADVANCED):
                self.activate_page(self.PAGE_OVERVIEW)
                self.show_overview()
                self.wTree.get_widget("treeview_overview").expand_all()
                self.wTree.get_widget("button_next").set_label(_("Install"))
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
                self.activate_page(self.PAGE_HDD)
            elif(sel == self.PAGE_PARTITIONS):
                self.activate_page(self.PAGE_HDD)
            elif(sel == self.PAGE_HDD):
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
        top = model.append(None)
        model.set(top, 0, _("System settings"))
        iter = model.append(top)
        model.set(iter, 0, _("Hostname: ") + "<b>%s</b>" % self.setup.hostname)       
        iter = model.append(top)
        if(self.setup.grub_device is not None):
            model.set(iter, 0, _("Install bootloader in %s") % ("<b>%s</b>" % self.setup.grub_device))
        else:
            model.set(iter, 0, _("Do not install bootloader"))
        top = model.append(None)
        model.set(top, 0, _("Filesystem operations"))  
        if(self.setup.skip_mount):
            iter = model.append(top)
            model.set(iter, 0, "<b>Use already-mounted /target.</b>")
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
        try:        
            print " ## INSTALLATION "
            ''' Actually perform the installation .. '''
            inst = self.installer            

            if "--debug" in sys.argv:
                print " ## DEBUG MODE - INSTALLATION PROCESS NOT LAUNCHED"            
                sys.exit(0)
                                   
            inst.set_progress_hook(self.update_progress)
            inst.set_error_hook(self.error_message)

            # do we dare? ..
            self.critical_error_happened = False

            # Now it's time to load the slide show
            if os.path.exists(self.slideshow_path):                        
                slideThr = Slideshow(self.slideshow_browser, self.slideshow_path, self.setup.language)
                # Let the slide-thread die when the parent thread dies
                slideThr.daemon = True
                slideThr.start()

            # Start installing
            do_try_finish_install = True
            
            try:
                inst.init_install(self.setup)
            except Exception, detail1:
                print detail1
                do_try_finish_install = False
                try:
                    gtk.gdk.threads_enter()
                    MessageDialog(_("Installation error"), str(detail1), gtk.MESSAGE_ERROR, self.window).show()
                    gtk.gdk.threads_leave()
                except Exception, detail2:
                    print detail2

            if self.critical_error_happened:
                gtk.gdk.threads_enter()
                MessageDialog(_("Installation error"), self.critical_error_message, gtk.MESSAGE_ERROR, self.window).show()
                gtk.gdk.threads_leave()
                do_try_finish_install = False

            if do_try_finish_install:
                if(self.setup.skip_mount):
                    gtk.gdk.threads_enter()
                    self.paused = True
                    self.activate_page(self.PAGE_CUSTOMPAUSED)
                    self.wTree.get_widget("button_next").show()
                    MessageDialog(_("Installation Paused"), _("Installation is now paused. Please read the instructions on the page carefully and click Forward to finish installation."), gtk.MESSAGE_INFO, self.window).show()
                    gtk.gdk.threads_leave()
                    self.wTree.get_widget("button_next").set_sensitive(True)

                    while(self.paused):
                        time.sleep(0.1)

                try:
                    inst.finish_install(self.setup)
                except Exception, detail1:
                    print detail1
                    try:
                        gtk.gdk.threads_enter()
                        MessageDialog(_("Installation error"), str(detail1), gtk.MESSAGE_ERROR, self.window).show()
                        gtk.gdk.threads_leave()
                    except Exception, detail2:
                        print detail2

                # show a message dialog thingum
                while(not self.done):
                    time.sleep(0.1)
            
                if self.critical_error_happened:
                    gtk.gdk.threads_enter()
                    MessageDialog(_("Installation error"), self.critical_error_message, gtk.MESSAGE_ERROR, self.window).show()
                    gtk.gdk.threads_leave()                
                else:
                    gtk.gdk.threads_enter()                    
                    dialog = QuestionDialog(_("Installation finished"), _("Installation is now complete. Do you want to restart your computer to use the new system?"), self.window)
                    if (dialog.show()):
                        # Reboot now
                        os.system('reboot')
                    gtk.gdk.threads_leave()
                
                print " ## INSTALLATION COMPLETE "
            
        except Exception, detail:
            print "!!!! General exception"
            print detail
            
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

class PartitionDialog:

    def __init__(self, path, mount_as, format_as, type):
        self.resource_dir = '/usr/share/live-installer/'
        self.glade = os.path.join(self.resource_dir, 'interface.glade')
        self.dTree = gtk.glade.XML(self.glade, 'dialog')
        self.window = self.dTree.get_widget("dialog")
        self.window.set_title(_("Edit partition"))
               
        ''' Build supported filesystems list '''
        model = gtk.ListStore(str)        
        model.append([""])
        if "swap" in type:        
            model.append(["swap"])
        else:
            try:
                for item in os.listdir("/sbin"):
                    if(item.startswith("mkfs.")):
                        fstype = item.split(".")[1]
                        model.append([fstype])
            except Exception, detail:
                print detail
                print _("Could not build supported filesystems list!")
        self.dTree.get_widget("combobox_use_as").set_model(model)
        
        if "swap" in type:
            mounts = ["", "swap"]
        else:
            mounts = ["", "/", "/boot", "/boot/efi", "/tmp", "/home", "/srv"]
        model = gtk.ListStore(str)
        for mount in mounts:
            model.append([mount])
        self.dTree.get_widget("comboboxentry_mount_point").set_model(model)  
                
        # i18n
        self.dTree.get_widget("label_partition").set_markup("<b>%s</b>" % _("Device:"))
        self.dTree.get_widget("label_partition_value").set_label(path)        
        
        self.dTree.get_widget("label_use_as").set_markup(_("Format as:"))        
        cur = -1
        model = self.dTree.get_widget("combobox_use_as").get_model()
        for item in model:
            cur += 1
            if(item[0] == format_as):
                self.dTree.get_widget("combobox_use_as").set_active(cur)
                break
                
        self.dTree.get_widget("label_mount_point").set_markup(_("Mount point:"))
        self.dTree.get_widget("comboboxentry_mount_point").child.set_text(mount_as)         

    def show(self):
        self.window.run()
        self.window.hide()
        w = self.dTree.get_widget("comboboxentry_mount_point")
        w.child.get_text().replace(" ","")
        mount_as = w.child.get_text()
        w = self.dTree.get_widget("combobox_use_as")
        # find filesystem ..
        active = w.get_active()
        model = w.get_model()[active]
        format_as = model[0]
        return (mount_as, format_as)
