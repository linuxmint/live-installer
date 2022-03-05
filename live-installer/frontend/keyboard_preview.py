import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

import subprocess


keys = {}
shifts = {}

class kbdpreview(Gtk.Box):

    def u2str(self,ucode):
        if ucode[0:2] == "U+":
            return chr(int(ucode[2:],16))
        elif ucode[0:2] == "+U":
            return chr(int(ucode[3:],16))
        return " "

    def update(self,keyboard="us",variant=""):
        global keys
        global shifts
        output = subprocess.getoutput("ckbcomp -model pc106 -layout {} {} -compact".format(keyboard,variant))
        for line in output.split("\n"):
            if "=" not in line or line[0] == "#":
                continue
            ucode = line.split("=")[1].strip().split(" ")[0]
            shift = line.split("=")[1].strip().split(" ")[1]
            num = line.split("=")[0].strip().split(" ")[1]
            keys[num] = self.u2str(ucode)
            shifts[num] = self.u2str(shift)
        for but in self.buttons:
            but.update()

    def __init__(self,keyboard="us",variant=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        rowA = self.getRow()
        rowB = self.getRow()
        rowC = self.getRow()
        rowD = self.getRow()
        self.buttons = []
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        self.add(rowA)
        rowA.add(self.getButton(41))
        for i in range(2,14):
            rowA.add(self.getButton(i))

        self.add(rowB)
        for i in range(16,28):
            rowB.add(self.getButton(i))


        self.add(rowC)
        for i in range(30,41):
            rowC.add(self.getButton(i))
        rowC.add(self.getButton(43))


        self.add(rowD)
        for i in range(44,54):
            rowD.add(self.getButton(i))

        self.update(keyboard,variant)

    def getRow(self):
        box = Gtk.Box()
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        return box

    def getButton(self,num):
        but = button(num)
        self.buttons.append(but)
        return but

class button(Gtk.Box):
    def __init__(self,num):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.but = Gtk.Label()
        self.but2 = Gtk.Label()

        self.set_margin_top(3)
        self.set_margin_bottom(3)
        self.set_margin_start(8)
        self.set_margin_end(8)

        self.add(self.but)
        self.add(self.but2)
        self.num = num

    def encode(self,char):
        char = char.replace("<","&lt;")
        char = char.replace(">","&gt;")
        char = char.replace("&","&amp;")
        return char

    def update(self):
        try:
            label = keys[str(self.num)]
            label2 = shifts[str(self.num)]
        except:
            label = ""
            label2 = ""

        self.but.set_markup("<span font_desc='Monospace 15'>{}</span>".format(self.encode(label)))
        self.but2.set_markup("<span font_desc='Monospace 7'>{}</span>".format(self.encode(label2)))

# Test & debug
if __name__ == "__main__":
    w = Gtk.Window()
    k = kbdpreview("tr","f")
    w.add(k)
    w.show_all()
    Gtk.main()
