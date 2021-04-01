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
        self.window.show_all()

    def connect_signal(self):
        self.trybut.connect("clicked", self.but_try)
        self.instbut.connect("clicked", self.but_install)

    def define_objects(self):
        self.window = self.builder.get_object("window")
        self.wel = self.builder.get_object("welcome")
        self.trybut = self.builder.get_object("try")
        self.instbut = self.builder.get_object("install")

    def i18n(self):
        self.wel.set_text(_("Welcome"))
        self.builder.get_object("trylabel").set_text(_("Try"))
        self.builder.get_object("installabel").set_text(_("Install"))
        self.builder.get_object("distro").set_text(config.get("distro_title","17g"))
        self.builder.get_object("copyright").set_text(config.get("copyright","17g Developer Team"))

    def but_try(self, widget):
        exit(0)

    def but_install(self, widget):
        self.window.hide()
        InstallerWindow()
