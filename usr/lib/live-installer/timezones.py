# coding: utf-8

import subprocess
import gi
gi.require_version('TimezoneMap', '1.0')
from gi.repository import Gtk, Gdk, GLib, TimezoneMap
from collections import defaultdict, namedtuple
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TZDATA_FILE = '/usr/share/zoneinfo/tzdata.zi'
# The map stretches to whatever it's allocated, this keeps its ratio at the window's default width
MAP_HEIGHT = 385

timezones = []
region_menus = {}
zone_links = {}

Timezone = namedtuple('Timezone', 'name ccode'.split())

def build_timezones(_installer):
    global installer, time_label, date_label, tz_map
    installer = _installer

    cssProvider = Gtk.CssProvider()
    cssProvider.load_from_path('/usr/share/live-installer/style.css')
    screen = Gdk.Screen.get_default()
    styleContext = Gtk.StyleContext()
    styleContext.add_provider_for_screen(screen, cssProvider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    time_label = installer.builder.get_object("label_time")
    date_label = installer.builder.get_object("label_date")

    GLib.timeout_add_seconds(1, update_local_time_label)

    tz_map = TimezoneMap.TimezoneMap.new()
    tz_map.set_size_request(-1, MAP_HEIGHT)
    tz_map.connect('location-changed', map_location_changed)
    installer.builder.get_object("aspectframe_timezones").add(tz_map)
    tz_map.show()

    # The map's city data can use legacy zone names (Europe/Kiev...), tzdata.zi lists them as links
    try:
        with open(TZDATA_FILE) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 3 and parts[0] == 'L':
                    zone_links[parts[2]] = parts[1]
    except OSError as e:
        print("Could not read %s: %s" % (TZDATA_FILE, e))

    # Populate timezones model
    def autovivified():
        return defaultdict(autovivified)
    hierarchy = autovivified()

    for line in subprocess.getoutput("awk '/^[^#]/{ print $1,$3 }' /usr/share/zoneinfo/zone.tab | sort -k2").split('\n'):
        ccode, name = line.split()
        tup = Timezone(name, ccode)
        submenu = hierarchy
        parts = name.split('/')
        for i, part in enumerate(parts, 1):
            if i != len(parts): submenu = submenu[part]
            else: submenu[part] = tup
        timezones.append(tup)

    def _build_tz_menu(d):
        menu = Gtk.Menu()
        for k in sorted(d):
            v = d[k]
            item = Gtk.MenuItem(k.replace("_", " "))
            item.show()
            if isinstance(v, dict):
                item.set_submenu(_build_tz_menu(v))
            else:
                item.connect('activate', tz_menu_selected, v)
            menu.append(item)
        menu.show()
        return menu

    def _build_cont_menu(d):
        menu = Gtk.Menu()
        for k in sorted(d):
            v = d[k]
            item = Gtk.MenuItem(k.replace("_", " "))
            item.show()
            if isinstance(v, dict):
                region_menus[k] = _build_tz_menu(v)
                region_menus[k].show_all()

            item.connect('activate', cont_menu_selected, k)
            menu.append(item)
        menu.show()
        return menu

    cont_menu = _build_cont_menu(hierarchy)
    cont_menu.show_all()

    installer.builder.get_object('cont_button').connect('event', button_callback)
    installer.builder.get_object('cont_button').menu = cont_menu
    installer.builder.get_object('tz_button').connect('event', button_callback)

current_zone = None
current_country = None
syncing_map = False
map_updated = False

def button_callback(button, event):
    menu = button.menu

    if event.type == Gdk.EventType.BUTTON_PRESS:
        menu.popup(None, None, None, None, 0, event.time)
        return True
    return False

def update_local_time_label():
    if current_zone is None:
        return True
    try:
        now = datetime.now(ZoneInfo(current_zone))
    except ZoneInfoNotFoundError as e:
        print("Could not load timezone %s: %s" % (current_zone, e))
        return True
    time_label.set_markup(now.strftime('<span size="x-large" weight="bold">%H:%M</span> (%-I:%M %p)'))
    date_format = '%A, %B %-d, %Y' if current_country == 'US' else '%A, %-d %B %Y'
    date_label.set_label(now.strftime(date_format))
    return True

def cont_menu_selected(widget, cont):
    installer.builder.get_object("cont_button").set_label(cont)
    installer.builder.get_object("tz_button").set_label(_('Select timezone'))
    installer.builder.get_object("tz_button").menu = region_menus[cont]

def tz_menu_selected(widget, tz):
    select_timezone(tz)

def map_location_changed(widget, location):
    global map_updated
    map_updated = True
    if syncing_map or location is None:
        return
    name = zone_links.get(location.props.zone, location.props.zone)
    tz = next((tz for tz in timezones if tz.name == name), None)
    if tz is None:
        tz = Timezone(name, location.props.country)
    select_timezone(tz, from_map=True)

def show_on_map(name):
    global syncing_map, map_updated
    syncing_map = True
    map_updated = False
    tz_map.set_timezone(name)
    if not map_updated:
        # The map has no city for this zone, don't leave a stale pin on it
        tz_map.clear_location()
    syncing_map = False

def select_timezone(tz, from_map=False):
    global current_zone, current_country
    current_zone = tz.name
    current_country = tz.ccode

    installer.setup.timezone = tz.name
    cont, separator, tz_str = tz.name.partition("/")

    installer.builder.get_object("cont_button").set_label(cont)
    installer.builder.get_object("tz_button").set_label(tz_str.replace("_", " "))
    if cont in region_menus:
        installer.builder.get_object("tz_button").menu = region_menus[cont]

    update_local_time_label()

    if not from_map:
        show_on_map(tz.name)
