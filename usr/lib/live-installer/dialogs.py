
import gtk

DIALOG_TYPES = {
    gtk.MESSAGE_INFO: 'MessageDialog',
    gtk.MESSAGE_ERROR: 'ErrorDialog',
    gtk.MESSAGE_WARNING: 'WarningDialog',
    gtk.MESSAGE_QUESTION: 'QuestionDialog',
}

class Dialog(gtk.MessageDialog):
    def __init__(self, style, buttons,
                 title, text, text2=None, parent=None):
        parent = parent or next((w for w in gtk.window_list_toplevels() if w.get_title()), None)
        gtk.MessageDialog.__init__(self, parent,
                                   gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                                   style, buttons, text)
        self.set_position(gtk.WIN_POS_CENTER)
        self.set_icon_from_file("/usr/share/icons/live-installer.png")
        self.set_title(title)
        self.set_markup(text)
        self.desc = text[:30] + ' ...' if len(text) > 30 else text
        self.dialog_type = DIALOG_TYPES[style]
        if text2: self.format_secondary_markup(text2)

    def show(self):
        """ Show the dialog.
            Returns True if user response was confirmatory.
        """
        print 'Showing {0.dialog_type} ({0.desc})'.format(self)
        try: return self.run() in (gtk.RESPONSE_YES, gtk.RESPONSE_APPLY,
                                   gtk.RESPONSE_OK, gtk.RESPONSE_ACCEPT)
        finally: self.destroy()

def MessageDialog(*args):
    return Dialog(gtk.MESSAGE_INFO, gtk.BUTTONS_OK, *args).show()

def QuestionDialog(*args):
    return Dialog(gtk.MESSAGE_QUESTION, gtk.BUTTONS_YES_NO, *args).show()

def WarningDialog(*args):
    return Dialog(gtk.MESSAGE_WARNING, gtk.BUTTONS_OK, *args).show()

def ErrorDialog(*args):
    return Dialog(gtk.MESSAGE_ERROR, gtk.BUTTONS_OK, *args).show()
