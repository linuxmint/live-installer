#!/usr/bin/python3
import sys
import gi
import os
from slideshow import Slideshow

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.0")

from gi.repository import Gtk, Gdk, WebKit2

def on_context_menu(unused_web_view, unused_context_menu,
                    unused_event, unused_hit_test_result):
    # True will not show the menu
    return True

resource_dir = 'usr/share/live-installer/'
glade_file = os.path.join(resource_dir, 'interface.ui')
builder = Gtk.Builder()
builder.add_from_file(glade_file)

# We have no significant browsing interface, so there isn't much point
# in WebKit creating a memory-hungry cache.
context = WebKit2.WebContext.get_default()
context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

slideshow_path = "file:///usr/share/live-installer/slideshow/index.html"
webview = WebKit2.WebView()
s = webview.get_settings()
s.set_allow_file_access_from_file_urls(True)

webview.connect('context-menu', on_context_menu)
s.set_property('enable-caret-browsing', False)

webview.load_uri(slideshow_path)
webview.show()

builder.get_object("scrolled_slideshow").add(webview)

window = builder.get_object("main_window")
builder.get_object("notebook1").set_current_page(8)
window.show_all()
window.connect("destroy", Gtk.main_quit)
Gtk.main()

