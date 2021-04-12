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
        self.page_welcome()
        self.page_language()
        self.page_timezone()

    def page_welcome(self):
        self.d.set_background_title(_("Welcome to the %s Installer.") % config.get("distro_title","17g"))
        self.d.msgbox(_("This program will ask you some questions and set up system on your computer."))

    def page_language(self):
        self.d.set_background_title(_("What language would you like to use?"))
        lang_menu = []
        for c in common.get_country_list():
            c = c.split(":")
            lang_menu.append((locale, "{} - {}".format(c[1],c[2])))
        c, self.setup.language = self.d.menu(_("Language"),choices=lang_menu)
        
    def page_timezone(self):
        tz_menu = []
        for tz in common.get_timezone_list():
            tz_menu.append((tz.split(" ")[1],tz.split(" ")[0]))
        tz, self.setup.timezone = self.d.menu(_("Timezone"),choices=tz_menu)
            
