import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

import subprocess


keys = {}
shifts = {}
altgrs = {}
altsfts = {}

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
            try:
                num    = line.split("=")[0].strip().split(" ")[1]
                ucode  = line.split("=")[1].strip().split(" ")[0]
                shift  = line.split("=")[1].strip().split(" ")[1]
                altgr  = line.split("=")[1].strip().split(" ")[2]
                altsft = line.split("=")[1].strip().split(" ")[3]
            except:
                num    = ""
                ucode  = ""
                shift  = ""
                altgr  = ""
                altsft = ""
            keys[num] = self.u2str(ucode)
            shifts[num] = self.u2str(shift)
            altgrs[num] = " "
            altsfts[num] = " "
            if self.u2str(shift) != self.u2str(altgr) and self.u2str(ucode) != self.u2str(altgr):
                altgrs[num] = self.u2str(altgr)
            if self.u2str(shift) != self.u2str(altsft) and self.u2str(altgr) != self.u2str(altsft):
                altsfts[num] = self.u2str(altsft)
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

        cssProvider = Gtk.CssProvider()
        cssProvider.load_from_path('./branding/style.css')
        screen = Gdk.Screen.get_default()
        styleContext = Gtk.StyleContext()
        styleContext.add_provider_for_screen(
        screen, cssProvider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        self.add(rowA)
        rowA.set_name("key_row_A")
        rowA.add(self.getButton(41))
        for i in range(2,14):
            rowA.add(self.getButton(i))

        self.add(rowB)
        rowB.set_name("key_row_A")
        for i in range(16,28):
            rowB.add(self.getButton(i))


        self.add(rowC)
        rowC.set_name("key_row_A")
        for i in range(30,41):
            rowC.add(self.getButton(i))
        rowC.add(self.getButton(43))


        self.add(rowD)
        rowD.set_name("key_row_A")
        for i in range(44,54):
            rowD.add(self.getButton(i))

        self.update(keyboard,variant)
        self.set_name("key_layout")

    def getRow(self):
        box = Gtk.Box()
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_spacing(3)
        return box

    def getButton(self,num):
        but = button(num)
        but.set_name("key_button")
        self.buttons.append(but)
        return but

class button(Gtk.Box):
    def __init__(self,num):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.but = Gtk.Label()
        self.but2 = Gtk.Label()
        self.but3 = Gtk.Label()
        self.but4 = Gtk.Label()

        self.box1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.box2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        self.but.set_name("key_label")
        self.but2.set_name("key_label_shift")
        self.but3.set_name("key_label_altgr")
        self.but4.set_name("key_label_altsft")
        self.set_name("key_box")

        self.set_margin_top(3)
        self.set_margin_bottom(3)
        self.set_size_request(25,25)

        self.box1.add(self.but2)
        self.box1.add(self.but4)
        self.box2.add(self.but)
        self.box2.add(self.but3)

        self.add(self.box1)
        self.add(self.box2)
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
            label3 = altgrs[str(self.num)]
            label4 = altsfts[str(self.num)]
        except:
            label = " "
            label2 = " "
            label3 = " "
            label4 = " "

        self.but.set_text(label)
        self.but2.set_text(label2)
        self.but3.set_text(label3)
        self.but4.set_text(label4)

# Test & debug
if __name__ == "__main__":
    w = Gtk.Window()
    k = kbdpreview("tr","f")
    w.add(k)
    w.show_all()
    Gtk.main()
