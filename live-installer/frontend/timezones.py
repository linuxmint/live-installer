# coding: utf-8

from frontend import *
from subprocess import getoutput
from collections import defaultdict, namedtuple
from datetime import datetime, timedelta
from functools import reduce


# pixel center of where equatorial line and 0th meridian cross on our bg
# map; WARNING: cc.png relies on this exactly!
MAP_CENTER = (388, 272)
MAP_SIZE = (832, 425)


def pixel_position(lat, lon):
    """Transform latlong pair into map pixel coordinates"""
    dx = MAP_SIZE[0] / 2 / 180
    dy = MAP_SIZE[1] / 2 / 90
    # formulae from http://en.wikipedia.org/wiki/Miller_cylindrical_projection
    x = MAP_CENTER[0] + dx * lon
    y = MAP_CENTER[1] - dy * \
        math.degrees(
            5 / 4 * math.log(math.tan(math.pi / 4 + 2 / 5 * math.radians(lat))))
    return int(x), int(y)


TZ_SPLIT_COORDS = re.compile('([+-][0-9]+)([+-][0-9]+)')

timezones = []
region_menus = {}

Timezone = namedtuple('Timezone', 'name ccode x y'.split())


def build_timezones(_installer):
    global installer, time_label, time_label_box, timezone
    installer = _installer

    cssProvider = Gtk.CssProvider()
    cssProvider.load_from_path('./branding/style.css')
    screen = Gdk.Screen.get_default()
    styleContext = Gtk.StyleContext()
    styleContext.add_provider_for_screen(
        screen, cssProvider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    # Add the label displaying current time
    time_label = installer.builder.get_object("label_time")
    time_label_box = installer.builder.get_object("eventbox_time")

    time_label_box.set_name('TimezoneLabel')

    update_local_time_label()

    # Populate timezones model
    installer.builder.get_object("image_timezones").set_from_file('./resources/timezone.png')

    def autovivified():
        return defaultdict(autovivified)
    hierarchy = autovivified()

    for line in getoutput(
            "awk '/^[^#]/{ print $1,$2,$3 }' /usr/share/zoneinfo/zone.tab | sort -k3").split('\n'):
        ccode, coords, name = line.split()
        lat, lon = TZ_SPLIT_COORDS.search(coords).groups()
        x, y = pixel_position(to_float(lat, 2), to_float(lon, 3))
        if x < 0:
            x = MAP_SIZE[0] + x
        tup = Timezone(name, ccode, x, y)
        submenu = hierarchy
        parts = name.split('/')
        for i, part in enumerate(parts, 1):
            if i != len(parts):
                submenu = submenu[part]
            else:
                submenu[part] = tup
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

    installer.builder.get_object(
        'cont_button').connect('event', button_callback)
    installer.builder.get_object('cont_button').menu = cont_menu

    installer.builder.get_object('tz_button').connect('event', button_callback)

    installer.builder.get_object("event_timezones").connect(
        'button-release-event', map_clicked)


adjust_time = timedelta(0)


def button_callback(button, event):
    if event.type == Gdk.EventType.BUTTON_PRESS:
        button.menu.popup(None, None, None, None, 0, event.time)
        return True
    return False


def update_local_time_label():
    now = datetime.utcnow() + adjust_time
    time_label.set_label(now.strftime('%H:%M'))
    return True


def cont_menu_selected(widget, cont):
    installer.builder.get_object("cont_button").set_label(cont)

    installer.builder.get_object("tz_button").set_label(_('Select timezone'))
    installer.builder.get_object("tz_button").menu = region_menus[cont]


def tz_menu_selected(widget, tz):
    select_timezone(tz)


def map_clicked(widget, event, data=None):
    x, y = event.x, event.y
    if event.window != installer.builder.get_object(
            "event_timezones").get_window():
        dx, dy = event.window.get_position()
        x, y = x + dx, y + dy
    closest_timezone = min(timezones, key=lambda tz: math.sqrt(
        (x - tz.x)**2 + (y - tz.y)**2))
    select_timezone(closest_timezone)


ADJUST_HOURS_MINUTES = re.compile('([+-])([0-9][0-9])([0-9][0-9])')

IS_WINTER = datetime.now().timetuple().tm_yday not in list(
    range(80, 264))  # today is between Mar 20 and Sep 20


def select_timezone(tz):
    # Adjust time preview to current timezone (using `date` removes need for
    # pytz package)
    offset = getoutput('TZ={} date +%z'.format(tz.name))
    tzadj = ADJUST_HOURS_MINUTES.search(offset).groups()
    global adjust_time
    adjust_time = timedelta(hours=int(tzadj[0] + tzadj[1]),
                            minutes=int(tzadj[0] + tzadj[2]))

    installer.setup.timezone = tz.name
    cont, separator, tz_str = tz.name.partition("/")
    del(separator)

    installer.builder.get_object("cont_button").set_label(cont)
    installer.builder.get_object(
        "tz_button").set_label(tz_str.replace("_", " "))
    installer.builder.get_object("tz_button").menu = region_menus[cont]

    update_local_time_label()

    # Move the current time label to appropriate position
    a = time_label_box.get_allocation()
    width = a.width
    height = a.height

    x = tz.x - (width / 2)
    y = tz.y - (height / 2)

    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if (x + width) > MAP_SIZE[0]:
        x = MAP_SIZE[0] - width
    if (y + height) > MAP_SIZE[1]:
        y = MAP_SIZE[1] - height
    installer.builder.get_object("fixed_timezones").move(time_label_box, x, y)


def _get_x_offset():
    now = datetime.utcnow().timetuple()
    # night is centered at UTC noon (12)
    return - int((now.tm_hour * 60 + now.tm_min - 12 * 60) /
                 (24 * 60) * MAP_SIZE[0])
