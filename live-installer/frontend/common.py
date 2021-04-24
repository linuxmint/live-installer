import os
import subprocess
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET
kbdxml = ET.parse('/usr/share/X11/xkb/rules/xorg.xml')


def get_country_list():
    countries = {}
    iso_standard = "3166"
    if os.path.exists("/usr/share/xml/iso-codes/iso_3166-1.xml"):
        iso_standard = "3166-1"
    for line in subprocess.getoutput("isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
        ccode, cname = line.split(None, 1)
        countries[ccode] = cname

    languages = {}
    iso_standard = "639"
    if os.path.exists("/usr/share/xml/iso-codes/iso_639-2.xml"):
        iso_standard = "639-2"
    for line in subprocess.getoutput("isoquery --iso %s | cut -f3,4-" % iso_standard).split('\n'):
        cols = line.split(None, 1)
        if len(cols) > 1:
            name = cols[1].replace(";", ",")
            languages[cols[0]] = name
    for line in subprocess.getoutput("isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
        cols = line.split(None, 1)
        if len(cols) > 1:
            if cols[0] not in list(languages.keys()):
                name = cols[1].replace(";", ",")
                languages[cols[0]] = name
    ccodes = []
    langlist = ""
    if os.path.isfile("/usr/share/i18n/SUPPORTED"):
        i18n = open("/usr/share/i18n/SUPPORTED","r").read().split('\n')
        for line in i18n:
            if " " in line and "_" in line and "@" not in line:
                if "UTF-8" in line:
                    if "." in line.split(" ")[0]:
                        langlist+=line.split(" ")[0].split(".")[0]+"\n"
                    else:
                        langlist+=line.split(" ")[0]+"\n"
    else:
        langlist = open("./resources/locales","r").read()

    for locale in langlist.split('\n'):
        if '_' in locale:
            lang, ccode = locale.split('_')
            language = lang
            country = ccode
            try:
                language = languages[lang]
            except:
                pass
            try:
                country = countries[ccode]
            except:
                pass
        else:
            lang = locale
            try:
                language = languages[lang]
            except:
                pass
            country = ''
        if country.isalnum() and language.isalnum() :
            ccodes.append(ccode+":"+language+":"+country+":"+locale)

    return ccodes


def get_timezone_list():
    return subprocess.getoutput("cat ./resources/timezones").split('\n')


def get_keyboard_model_list():
    models = []
    for node in kbdxml.iterfind('.//modelList/model/configItem'):
        name, desc = node.find('name').text, node.find('description').text
        models.append((desc, name))
    return models


def get_keyboard_layout_list():
    models = []
    for node in kbdxml.iterfind('.//layoutList/layout'):
        name, desc = node.find(
            'configItem/name').text, node.find('configItem/description').text
        models.append((desc, name, node))
    return models


def get_keyboard_variant_list(model):
    models = [("",_("No Variant"))]
    for variant in model[2].iterfind('variantList/variant/configItem'):
        var_name = variant.find('name').text
        var_desc = variant.find('description').text
        models.append((var_name, var_desc))
    return models
