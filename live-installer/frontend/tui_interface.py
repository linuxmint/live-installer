import config
from dialog import Dialog
from installer import InstallerEngine, Setup
import gettext, locale
gettext.install("live-installer", "/usr/share/locale")

class InstallerWindow:
    def __init__(self):
        self.setup = Setup()
        self.installer = InstallerEngine(self.setup)
        self.d = Dialog(dialog="dialog")
        self.page_welcome()
        self.page_language()

    def page_welcome(self):
        self.d.set_background_title(_("Welcome to the %s Installer.") % config.get("distro_title","17g"))
        self.d.msgbox(_("This program will ask you some questions and set up system on your computer."))

    def page_language(self):
        self.d.set_background_title(_("What language would you like to use?"))
        c, self.setup.language = self.d.menu(_("Language"),choices=[
                                 ("tr_TR","Turkish"),
                                 ("en_US", "English")
                                 ])

InstallerWindow()
