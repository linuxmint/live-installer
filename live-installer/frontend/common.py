import os
import subprocess


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
    ccodes=[]
    for locale in subprocess.getoutput("cat ./resources/locales").split('\n'):
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
        ccodes.append(ccode+":"+language+":"+country+":"+locale)
            
    return ccodes
