import config
from dialog import Dialog
from installer import InstallerEngine, Setup
import frontend.common as common
import gettext, locale

gettext.install("live-installer", "/usr/share/locale")

class InstallerWindow:
    def __init__(self):
        self.setup = Setup()
        self.installer = InstallerEngine(self.setup)
        self.d = Dialog(dialog="dialog",autowidgetsize=True)
        for c in common.get_country_list():
            c = c.split(":")
            print(c)
        #exit(0)
        self.page_welcome()
        self.page_language()

    def page_welcome(self):
        self.d.set_background_title(_("Welcome to the %s Installer.") % config.get("distro_title","17g"))
        self.d.msgbox(_("This program will ask you some questions and set up system on your computer."))

    def page_language(self):
        self.d.set_background_title(_("What language would you like to use?"))
        lang_menu = []
        for c in common.get_country_list():
            c = c.split(":")
            ccode = c[0]
            language = c[1]
            country = c[2]
            locale = c[3]
            lang_menu.append((locale, "{} - {}".format(language,country)))
        c, self.setup.language = self.d.menu(_("Language"),choices=lang_menu)

InstallerWindow()
