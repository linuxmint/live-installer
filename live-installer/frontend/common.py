import os
import subprocess
from defusedxml import ElementTree
kbdxml = ElementTree.parse('/usr/share/X11/xkb/rules/base.xml')
kbdxml_extra = ElementTree.parse('/usr/share/X11/xkb/rules/base.extras.xml')
ignored_language = ["ku_TR", "el_CY","C"]

def get_user_list():
    passwd = open("/etc/passwd","r").read().strip().split("\n")
    users = []
    for line in passwd:
        user = line.split(":")[0]
        uid = line.split(":")[2]
        if uid != "1000":
            users.append(user)
    return users
    

def get_country_list():
    countries = {}
    iso_standard = "3166"
    if os.path.exists("/usr/share/xml/iso-codes/iso_3166-1.xml"):
        iso_standard = "3166-1"
    for line in subprocess.getoutput(
            "isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
        ccode, cname = line.split(None, 1)
        countries[ccode] = cname

    languages = {}
    iso_standard = "639"
    if os.path.exists("/usr/share/xml/iso-codes/iso_639-2.xml"):
        iso_standard = "639-2"
    for line in subprocess.getoutput(
            "isoquery --iso %s | cut -f3,4-" % iso_standard).split('\n'):
        cols = line.split(None, 1)
        if len(cols) > 1:
            name = cols[1].replace(";", ",")
            languages[cols[0]] = name
    for line in subprocess.getoutput(
            "isoquery --iso %s | cut -f1,4-" % iso_standard).split('\n'):
        cols = line.split(None, 1)
        if len(cols) > 1:
            if cols[0] not in list(languages.keys()):
                name = cols[1].replace(";", ",")
                languages[cols[0]] = name
    ccodes = []
    langlist = ""
    if os.path.isfile("/usr/share/i18n/SUPPORTED"):
        i18n = open("/usr/share/i18n/SUPPORTED", "r").read().split('\n')
        for line in i18n:
            l = line.split(" ")[0]
            if "." in l:
                l = l.split(".")[0]
            if l not in langlist and "@" not in l:
                if l not in ignored_language:
                    langlist += l + "\n"
    else:
        langlist = open("./resources/locales", "r").read()

    if "en_US" not in langlist:
        langlist += "en_US\n"
    langlist = langlist.split('\n')
    langlist.sort()
    for locale in langlist:
        if '_' in locale:
            lang, ccode = locale.split('_')
            language = lang
            country = ccode
            try:
                language = languages[lang]
            except BaseException:
                language = lang
            try:
                country = countries[ccode]
            except BaseException:
                country = ccode
            ccodes.append(
                ccode +
                ":" +
                language +
                ":" +
                country +
                ":" +
                locale)
    return ccodes


def get_timezone_list():
    l = subprocess.getoutput("cat ./resources/timezones").split('\n')
    l.sort()
    return l


def get_keyboard_model_list():
    models = []
    names = []
    for xml in [kbdxml]:
        for node in xml.iterfind('.//modelList/model/configItem'):
            name, desc = node.find('name').text, node.find('description').text
            if name not in names:
                models.append((desc, name))
                names.append(name)
    return models


def get_keyboard_layout_list():
    models = []
    for xml in [kbdxml]:
        names = []
        for node in xml.iterfind('.//layoutList/layout'):
            name, desc = node.find(
            'configItem/name').text, node.find('configItem/description').text
            if name not in names:
                models.append((desc, name, node))
                names.append(name)
    return models


def get_keyboard_variant_list(model):
    models = [("", model[0])]
    names = []
    for variant in model[2].iterfind('variantList/variant/configItem'):
        var_name = variant.find('name').text
        var_desc = variant.find('description').text
        if var_name not in names:
            models.append((var_name, var_desc))
            names.append(var_name)
    return models
