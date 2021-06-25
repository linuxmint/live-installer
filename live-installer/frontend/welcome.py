from frontend.gtk_interface import InstallerWindow
from frontend import *

gettext.install("live-installer", "/usr/share/locale")


class welcome:

    def __init__(self):
        self.builder = Gtk.Builder()
        self.resource_dir = './resources/'
        glade_file = os.path.join(self.resource_dir, 'welcome.ui')
        self.builder.add_from_file(glade_file)
        self.define_objects()
        self.connect_signal()
        self.i18n()
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", Gtk.main_quit)
        self.window.show_all()

    def connect_signal(self):
        self.trybut.connect("clicked", self.but_try)
        self.instbut.connect("clicked", self.but_install)

    def define_objects(self):
        self.window = self.builder.get_object("window")
        self.trybut = self.builder.get_object("try")
        self.instbut = self.builder.get_object("install")

    def i18n(self):
        self.builder.get_object("trylabel").set_text(
            _("Try %s") % config.get("distro_title", "17g"))
        self.builder.get_object("installabel").set_text(
            _("Install to Hard Drive"))
        self.builder.get_object("msglabel1").set_text(
            _("You are currently running %s from live media.") % config.get("distro_title", "17g"))
        self.builder.get_object("msglabel2").set_text(
            _("You can install %s now, or chose \"Install to Hard Drive\" in the Appication Menu later.") % config.get("distro_title", "17g"))
        self.builder.get_object("title").set_text(_("Welcome to %s") %
                                                  config.get("distro_title", "17g"))
        self.builder.get_object("copyright").set_text(
            config.get("copyright", "17g Developer Team"))

    def but_try(self, widget):
        exit(0)

    def but_install(self, widget):
        self.window.hide()
        InstallerWindow()
