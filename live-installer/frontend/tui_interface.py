import config
from dialog import Dialog
from installer import InstallerEngine, Setup
import frontend.common as common
import gettext
import locale

gettext.install("live-installer", "/usr/share/locale")


class InstallerWindow:
    def __init__(self):
        self.setup = Setup()
        self.installer = InstallerEngine(self.setup)
        self.d = Dialog(dialog="dialog", autowidgetsize=True)
        self.page_welcome()
        self.page_language()
        self.page_timezone()
        self.page_keyboard_model()

    def page_welcome(self):
        self.d.set_background_title(
            _("Welcome to the %s Installer.") % config.get("distro_title", "17g"))
        self.d.msgbox(
            _("This program will ask you some questions and set up system on your computer."))

    def page_language(self):
        self.d.set_background_title(_("What language would you like to use?"))
        lang_menu = []
        for c in common.get_country_list():
            c = c.split(":")
            lang_menu.append((c[3], "{} - {}".format(c[1], c[2])))
        c, self.setup.language = self.d.menu(_("Language"), choices=lang_menu)
        return c

    def page_timezone(self):
        self.d.set_background_title(_("Where are you?"))
        tz_menu = []
        for tz in common.get_timezone_list():
            tz_menu.append((tz.split(" ")[1], tz.split(" ")[0]))
        tz, self.setup.timezone = self.d.menu(_("Timezone"), choices=tz_menu)
        return tz

    def page_keyboard_model(self):
        self.d.set_background_title(_("What keyboard would you like to use?"))
        i = 0
        models = common.get_keyboard_model_list()
        kbd_model_menu = []
        for model in models:
            kbd_model_menu.append((str(i), model[0]))
            i += 1
        m, index = self.d.menu(_("Keyboard Model"), choices=kbd_model_menu)
        if m != self.d.OK:
            return m
        sel = models[int(index)]
        self.setup.keyboard_model_description = sel[0].strip()
        self.setup.keyboard_model = sel[1].strip()
        return m
