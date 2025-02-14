#!/usr/bin/python3


class OsRelease:
    def __init__(self):
        self.is_mint = True
        self.name = "Linux Mint"
        self.version = ""
        self.codename = ""

        with open("/etc/os-release") as f:
            for line in f:
                key, sep, data = line.strip("\n").partition("=")
                data = data.strip('"')
                if key == "ID":
                    self.is_mint = data == "linuxmint"
                if key == "NAME":
                    self.name = data
                elif key == "VERSION_ID":
                    self.version = data
                elif key == "VERSION_CODENAME":
                    self.codename = data.capitalize()
