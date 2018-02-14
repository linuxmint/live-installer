
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

class Dialog(Gtk.MessageDialog):
    def __init__(self, style, buttons, title, text, text2=None, parent=None):
        Gtk.MessageDialog.__init__(self, parent, 0, style, buttons)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_from_file("/usr/share/icons/live-installer.png")
        self.set_title(title)
        self.set_markup(text)
        self.desc = text[:30] + ' ...' if len(text) > 30 else text
        if text2: self.format_secondary_markup(text2)
        if parent:
            self.set_transient_for(parent)
            self.set_modal(True)

    def show(self):
        try:
            response = self.run()
            if response in (Gtk.ResponseType.YES, Gtk.ResponseType.APPLY, Gtk.ResponseType.OK, Gtk.ResponseType.ACCEPT):
                return True
            else:
                return False
        finally:
            self.destroy()

def MessageDialog(*args):
    return Dialog(Gtk.MessageType.INFO, Gtk.ButtonsType.OK, *args).show()

def QuestionDialog(*args):
    return Dialog(Gtk.MessageType.QUESTION, Gtk.ButtonsType.YES_NO, *args).show()

def WarningDialog(*args):
    return Dialog(Gtk.MessageType.WARNING, Gtk.ButtonsType.OK, *args).show()

def ErrorDialog(*args):
    return Dialog(Gtk.MessageType.ERROR, Gtk.ButtonsType.OK, *args).show()
