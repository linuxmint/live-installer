#!/usr/bin/python3
import config
import gettext
gettext.install("live-installer", "/usr/share/locale")

userlist = []

def username(username):
    isValid = True
    errorMessage = None
    if username == "" or username == None:
        return _("Please provide a username.")
    elif not (username.isascii() and username.replace("-","").isalnum() and username.islower()):
        errorMessage = _("Your username is invalid.")
    elif len(username) > 32:
        errorMessage = _("Your username is too long")
    elif username in userlist:
        errorMessage = _("Your username is unavailable")
    elif username[0] in "0123456789":
        errorMessage = _("Your username cannot start with numbers.")
    for char in username:
        if(char.isupper()):
            errorMessage = _("Your username must be lower case.")
            break
        elif(char.isspace()):
            errorMessage = _("Your username may not contain whitespace characters.")
            break
    return errorMessage

def hostname(hostname):
    errorMessage = None
    if hostname == "" or hostname == None:
        return _("Please provide a name for your computer.")
    elif not hostname.isascii():
        return _("The computer's name is invalid.")
    elif len(hostname) > 63:
        return _("The computer's name is too long.")
    elif hostname[0] == '-' or hostname[-1] == '-' :
        return _("The computer's name should not starts or ends with -")
    for char in hostname:
        if(char.isupper()) and not config.get("allow_uppercase_hostname", True):
            errorMessage = _("The computer's name must be lower case.")
            break
        elif(char.isspace()):
            errorMessage = _("The computer's name may not contain whitespace characters.")
            break
        elif char.lower() not in "abcdefghijklmnopqrstuvwxyz.1234567890-":
            errorMessage = _("The computer's name must consist of only a-z or A-Z or 0-9 or - characters")
            break
    return errorMessage

def password(password,username=""):
    # Strong password
    weeklevel = 0
    stronglevel = 0
    errorMessage = None
    weekMessage = ""
    if len(password) < config.get("min_password_length", 1):
       errorMessage = _("Your passwords is too short.")
    if password.isnumeric():
        isWeek = True
        weeklevel += 20
        weekMessage += _("Your password is numeric") + "\n"
    if password.lower() == password:
        isWeek = True
        weeklevel += 10
        weekMessage += _("Your password must have big letters") + "\n"
    if password.upper() == password:
        isWeek = True
        weeklevel += 10
        weekMessage += _("Your password must have small letters") + "\n"
    if password == username:
        isWeek = True
        stronglevel = 20
        weekMessage += _("Your password must not be the same as the user name") + "\n"
    if len(password) < 8:
        isWeek = True
        stronglevel = 20
        weekMessage += _("Your password length must be minimum 8 characters") + "\n"
    if len(password) == 0:
        isWeek = False
        stronglevel = 1

    has_char = False
    has_num = False
    characters = "\"!'^+%&/()=?_<>#${[]}\\|-*"
    numbers = "0123456789"
    for c in numbers:
        if c in password:
            has_num = True
            break
    for c in characters:
        if c in password:
            has_char = True
            break
    if not has_char:
        isWeek = True
        weeklevel += 10
        weekMessage += _("Your password must have exclusive characters") + "\n"
    if not has_num:
        isWeek = True
        weeklevel += 10
        weekMessage += _("Your password must have numbers") + "\n"
    if stronglevel != 0:
        weeklevel = 100-stronglevel
    if password == "":
        errorMessage = _("Please provide a password for your user account.")
        isWeek = False
    return errorMessage, weekMessage, weeklevel

