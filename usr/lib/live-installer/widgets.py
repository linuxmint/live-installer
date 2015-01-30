#!/usr/bin/python
# coding: utf-8
#
import gtk
import os
import gettext
import PIL.Image

gettext.install("live-installer", "/usr/share/linuxmint/locale")


class PictureChooserButton (gtk.Button):

    def __init__ (self, num_cols=4, button_picture_size=None, menu_pictures_size=None, has_button_label=False):        
        super(PictureChooserButton, self).__init__()
        self.num_cols = num_cols
        self.button_picture_size = button_picture_size
        self.menu_pictures_size = menu_pictures_size
        self.row = 0
        self.col = 0
        self.menu = gtk.Menu()
        self.button_box = gtk.VBox(spacing=2)
        self.button_image = gtk.Image()
        self.button_box.add(self.button_image)
        if has_button_label:
            self.button_label = gtk.Label()
            self.button_box.add(self.button_label)
        self.add(self.button_box)
        self.connect("button-release-event", self._on_button_clicked)
        self.progress = 0.0

        # context = self.get_style_context()
        # context.add_class("gtkstyle-fallback")

    #     self.connect_after("draw", self.on_draw) 

    # def on_draw(self, widget, cr, data=None):
    #     if self.progress == 0:
    #         return False
    #     box = widget.get_allocation()

    #     context = widget.get_style_context()
    #     c = context.get_background_color(gtk.STATE_SELECTED)

    #     max_length = box.width * .6
    #     start = (box.width - max_length) / 2
    #     y = box.height - 5

    #     cr.save()

    #     cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
    #     cr.set_line_width(3)
    #     cr.set_line_cap(1)
    #     cr.move_to(start, y)
    #     cr.line_to(start + (self.progress * max_length), y)
    #     cr.stroke()

    #     cr.restore()
    #     return False

    def increment_loading_progress(self, inc):
        progress = self.progress + inc
        self.progress = min(1.0, progress)
        self.queue_draw()

    def reset_loading_progress(self):
        self.progress = 0.0
        self.queue_draw()

    def set_picture_from_file (self, path):
        if os.path.exists(path):
            if self.button_picture_size is None:
                pixbuf = gtk.gdk.pixbuf_new_from_file(path)
            else:
                pixbuf = gtk.gdk.pixbuf_new_from_file_at_size(path, -1, self.button_picture_size)
            self.button_image.set_from_pixbuf(pixbuf)

    def set_button_label(self, label):
        self.button_label.set_markup(label)

    def popup_menu_below_button (self, menu, widget):  
        window = widget.get_window()
        screen = window.get_screen()
        monitor = screen.get_monitor_at_window(window)

        warea = screen.get_monitor_geometry(monitor)
        wrect = widget.get_allocation()
        mrect = menu.get_allocation()

        window_x, window_y = window.get_origin()

        # Position left edge of the menu with the right edge of the button
        x = window_x + wrect.x + wrect.width
        # Center the menu vertically with respect to the monitor
        y = warea.y + (warea.height / 2) - (mrect.height / 2)

        # Now, check if we're still touching the button - we want the right edge
        # of the button always 100% touching the menu

        if y > (window_y + wrect.y):
            y = y - (y - (window_y + wrect.y))
        elif (y + mrect.height) < (window_y + wrect.y + wrect.height):
            y = y + ((window_y + wrect.y + wrect.height) - (y + mrect.height))

        push_in = True # push_in is True so all menu is always inside screen
        return (x, y, push_in)

    def _on_button_clicked(self, widget, event):
        if event.button == 1:
            self.menu.show_all()
            self.menu.popup(None, None, self.popup_menu_below_button, event.button, event.time, self)

    def _on_picture_selected(self, menuitem, path, callback, id=None):
        if id is not None:
            result = callback(path, id)
        else:
            result = callback(path)
        
        if result:
            self.set_picture_from_file(path)            

    def clear_menu(self):
        menu = self.menu
        self.menu = gtk.Menu()
        self.row = 0
        self.col = 0
        menu.destroy()

    def add_picture(self, path, callback, title=None, id=None):
        if os.path.exists(path):          
            if self.menu_pictures_size is None:
                pixbuf = gtk.gdk.pixbuf_new_from_file(path)
            else:
                pixbuf = gtk.gdk.pixbuf_new_from_file_at_size(path, -1, self.menu_pictures_size)
            image = gtk.image_new_from_pixbuf(pixbuf)  
            menuitem = gtk.MenuItem()            
            if title is not None:
                vbox = gtk.VBox()
                vbox.pack_start(image, False, False, 2)
                label = gtk.Label()
                label.set_text(title)
                vbox.pack_start(label, False, False, 2)
                menuitem.add(vbox)
            else:
                menuitem.add(image)
            if id is not None:
                menuitem.connect('activate', self._on_picture_selected, path, callback, id)
            else:
                menuitem.connect('activate', self._on_picture_selected, path, callback)
            self.menu.attach(menuitem, self.col, self.col+1, self.row, self.row+1)
            self.col = (self.col+1) % self.num_cols
            if (self.col == 0):
                self.row = self.row + 1

    def add_separator(self):
        self.row = self.row + 1
        self.menu.attach(gtk.SeparatorMenuItem(), 0, self.num_cols, self.row, self.row+1)

    def add_menuitem(self, menuitem):
        self.row = self.row + 1
        self.menu.attach(menuitem, 0, self.num_cols, self.row, self.row+1)


