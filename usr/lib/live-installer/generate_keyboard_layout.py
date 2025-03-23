#!/usr/bin/python3

import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont
import math

FONT_NAME = "/usr/share/live-installer/GoNotoCurrent-Regular.ttf"

LARGE_FONT_SIZE = 14
REG_FONT_SIZE = 12
META_FONT_SIZE = 18

WIDTH = 640
HEIGHT = 178

key_color = (0x58, 0x58, 0x58)
meta_key_color = (0x50, 0x50, 0x50)
border_color = (0x99, 0x99, 0x99)
primary_text_color = (0xff, 0xff, 0xff)
secondary_text_color = (0x9e, 0xde, 0xff)

CAP_FONT_OFFSET_X = 6
CAP_FONT_OFFSET_Y = 3
REG_FONT_OFFSET_X = 6
REG_FONT_OFFSET_Y = 19
CTRL_FONT_OFFSET_X = 23
CTRL_FONT_OFFSET_Y = 19

UNICODE_RETURN = "\u21B5" # ⏎

# Dead key mappings
DEAD_KEYS = {
    "dead_circumflex": "\u005E",    # ^ (Circumflex Accent)
    "dead_acute": "\u00B4",         # ´ (Acute Accent)
    "dead_grave": "\u0060",         # ` (Grave Accent)
    "dead_tilde": "\u007E",         # ~ (Tilde)
    "dead_diaeresis": "\u00A8",     # ¨ (Diaeresis/Umlaut)
    "dead_cedilla": "\u00B8",       # ¸ (Cedilla)
    "dead_macron": "\u00AF",        # ¯ (Macron)
    "dead_breve": "\u02D8",         # ˘ (Breve)
    "dead_abovedot": "\u02D9",      # ˙ (Dot Above)
    "dead_ogonek": "\u02DB",        # ˛ (Ogonek)
    "dead_caron": "\u02C7",         # ˇ (Caron/Hacek)
    "dead_doubleacute": "\u02DD",   # ˝ (Double Acute)
    "dead_abovering": "\u02DA",     # ˚ (Ring Above)
    "dead_iota": "\u037A",          # ͺ (Greek Iota Subscript)
    "dead_greek": "\u037E",         # ; (Greek Question Mark, often for Greek tone)
    "dead_semivoiced_sound": "\u309A",  # ゚ (Katakana Semivoiced Sound)
    "dead_voiced_sound": "\u3099"   # ゛ (Katakana Voiced Sound)
}

# U+ , or +U+ ... to string
def fromUnicodeString(raw):
    if raw[0:2] == "U+":
        return chr(int(raw[2:], 16))
    elif raw[0:2] == "+U":
        return chr(int(raw[3:], 16))
    elif raw in DEAD_KEYS:
        return DEAD_KEYS[raw]
    elif raw.startswith("dead_"):
        return ""
    elif raw.startswith("KP_"):
        return ""
    elif raw in ["Delete", "Shift", "Return", "Caps_Lock", "Left", "Right", "Alt", "Control"]:
        return ""
    return raw

class KeyboardRenderer:
    # Keyboard layouts
    kb_104 = {
        "extended_return": False,
        "keys": [
        (0x29, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xa, 0xb, 0xc, 0xd),
        (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b, 0x2b),
        (0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28),
        (0x2c, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35),
        ()]
    }

    kb_105 = {
        "extended_return": True,
        "keys": [
        (0x29, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xa, 0xb, 0xc, 0xd),
        (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b),
        (0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2b),
        (0x55, 0x2c, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35),
        ()]
    }

    kb_106 = {
        "extended_return": True,
        "keys": [
        (0x29, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xa, 0xb, 0xc, 0xd, 0xe),
        (0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b),
        (0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29),
        (0x2c, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36),
        ()]
    }

    def __init__(self, layout, variant, scale, filename):
        self.codes = []
        self.layout = layout
        self.variant = variant
        self.scale = scale
        self.kb = None
        self.width = WIDTH
        self.height = HEIGHT
        self.filename = filename
        self.space = 6
        self.usable_width = self.width - 6
        self.key_w = round((self.usable_width - 14 * self.space) / 15)

        try:
            self.large_font = ImageFont.truetype(FONT_NAME, LARGE_FONT_SIZE * self.scale)
            self.regular_font = ImageFont.truetype(FONT_NAME, REG_FONT_SIZE * self.scale)
            self.meta_font = ImageFont.truetype(FONT_NAME, META_FONT_SIZE * self.scale)
        except IOError:
            print("Error: Could not load fonts!")
            sys.exit(1)

        self.loadCodes()
        self.loadInfo()
        self.render()

    def loadInfo(self):
        kbl_104 = ["us", "th"]
        kbl_106 = ["jp"]

        # most keyboards are 105 key so default to that
        if self.layout in kbl_104:
            self.kb = self.kb_104
        elif self.layout in kbl_106:
            self.kb = self.kb_106
        elif self.kb != self.kb_105:
            self.kb = self.kb_105

    def regular_text(self, index):
        return self.codes[index - 1][0]

    def shift_text(self, index):
        return self.codes[index - 1][1]

    def ctrl_text(self, index):
        return self.codes[index - 1][2]

    def alt_text(self, index):
        return self.codes[index - 1][3]

    def loadCodes(self):
        if self.layout is None:
            return

        variantParam = ""
        if self.variant is not None and self.variant != "None":
            variantParam = "-variant %s" % self.variant

        cmd = "ckbcomp -model pc106 -layout %s %s -compact" % (self.layout, variantParam)

        pipe = subprocess.Popen(cmd, shell=True, encoding='utf-8', errors='ignore', stdout=subprocess.PIPE, stderr=None)
        cfile = pipe.communicate()[0]

        # clear the current codes
        del self.codes[:]

        for l in cfile.split('\n'):
            if l[:7] != "keycode":
                continue

            codes = l.split('=')[1].strip().split(' ')

            plain = fromUnicodeString(codes[0])
            shift = fromUnicodeString(codes[1])
            ctrl = fromUnicodeString(codes[2])
            alt = fromUnicodeString(codes[3])

            if ctrl == plain:
                ctrl = ""

            if alt == plain:
                alt = ""

            self.codes.append((plain, shift, ctrl, alt))

    def rounded_rectangle(self, draw, xy, radius, fill, outline=None, width=1):
        x1, y1, x2, y2 = xy
        r = radius
        draw.ellipse((x1, y1, x1 + r*2, y1 + r*2), fill=fill, outline=outline, width=width)
        draw.ellipse((x2 - r*2, y1, x2, y1 + r*2), fill=fill, outline=outline, width=width)
        draw.ellipse((x1, y2 - r*2, x1 + r*2, y2), fill=fill, outline=outline, width=width)
        draw.ellipse((x2 - r*2, y2 - r*2, x2, y2), fill=fill, outline=outline, width=width)
        draw.rectangle((x1 + r, y1, x2 - r, y2), fill=fill, outline=outline, width=0)
        draw.rectangle((x1, y1 + r, x2, y2 - r), fill=fill, outline=outline, width=0)
        if outline:
            draw.line((x1 + r, y1, x2 - r, y1), fill=outline, width=width)
            draw.line((x1 + r, y2, x2 - r, y2), fill=outline, width=width)
            draw.line((x1, y1 + r, x1, y2 - r), fill=outline, width=width)
            draw.line((x2, y1 + r, x2, y2 - r), fill=outline, width=width)

    def render(self):
        width = self.width * self.scale
        height = self.height * self.scale
        key_w = self.key_w * self.scale
        space = self.space * self.scale

        # Create a transparent image
        img = Image.new('RGBA', (width, height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)

        rx = 3 * self.scale  # Corner radius

        def draw_row(row_index, row, sx, sy, last_end=False):
            x = sx
            y = sy
            keys = row
            rw = self.usable_width * self.scale - sx
            i = 0

            for k in keys:
                rect_width = key_w

                if i == len(keys) - 1 and last_end:
                    rect_width = rw

                # Draw key
                self.rounded_rectangle(draw, (x, y, x + rect_width, y + key_w), rx,
                                      fill=key_color, outline=border_color, width=1)


                shift_text = self.shift_text(k)
                regular_text = self.regular_text(k)
                ctrl_text = self.ctrl_text(k)
                if shift_text == regular_text or shift_text == regular_text.upper():
                    # Draw shift text (center)
                    bbox = draw.textbbox((0, 0), shift_text, font=self.large_font)
                    text_x = x + key_w // 2 - bbox[2] // 2
                    text_y = y + key_w // 2 - bbox[3] // 2
                    draw.text((text_x, text_y), shift_text, font=self.large_font, fill=primary_text_color)
                else:
                    # Draw shift text (top)
                    text_x = x + CAP_FONT_OFFSET_X * self.scale
                    text_y = y + CAP_FONT_OFFSET_Y * self.scale
                    draw.text((text_x, text_y), shift_text, font=self.regular_font, fill=primary_text_color)

                    # Draw regular text (bottom)
                    text_x = x + REG_FONT_OFFSET_X * self.scale
                    text_y = y + REG_FONT_OFFSET_Y * self.scale
                    draw.text((text_x, text_y), regular_text, font=self.regular_font, fill=primary_text_color)

                    # Control text
                    ctrl_text = self.ctrl_text(k)
                    if ctrl_text and row_index == 0:
                        ctrl_bbox = draw.textbbox((0, 0), ctrl_text, font=self.regular_font)
                        text_x = x + CTRL_FONT_OFFSET_X * self.scale
                        text_y = y + CTRL_FONT_OFFSET_Y * self.scale
                        draw.text((text_x, text_y), ctrl_text, font=self.regular_font, fill=secondary_text_color)

                rw = rw - space - key_w
                x = x + space + key_w
                i = i + 1

            return (x, rw)

        x = 6 * self.scale
        y = 6 * self.scale

        keys = self.kb["keys"]
        ext_return = self.kb["extended_return"]

        first_key_w = 0

        rows = 4
        remaining_x = [0, 0, 0, 0]
        remaining_widths = [0, 0, 0, 0]

        left_keys = []
        left_keys.append(0) # not used..
        left_keys.append("\u21E5") # ⇥
        left_keys.append("\u21EA") # ⇪
        left_keys.append("\u21E7") # ⇧

        right_keys = []
        right_keys.append("←") # backspace
        right_keys.append(0) # not used..
        right_keys.append(0) # not used..
        right_keys.append("\u21E7") # ⇧

        for i in range(0, rows):
            if first_key_w > 0:
                first_key_w = first_key_w * 1.375

                if self.kb == self.kb_105 and i == 3:
                    first_key_w = key_w * 1.275

                # Draw first key
                self.rounded_rectangle(draw, (6 * self.scale, y, 6 * self.scale + first_key_w, y + key_w),
                                      rx, fill=meta_key_color, outline=border_color, width=1)

                # Draw regular text (bottom)
                regular_text = left_keys[i]
                bbox = draw.textbbox((0, 0), regular_text, font=self.meta_font)
                text_x = x + REG_FONT_OFFSET_X * self.scale
                text_y = y + key_w // 2 - bbox[3] // 2
                draw.text((text_x, text_y), regular_text, font=self.meta_font, fill=secondary_text_color)

                x = 6 * self.scale + first_key_w + space
            else:
                first_key_w = key_w

            x, rw = draw_row(i, keys[i], x, y, i == 1 and not ext_return)

            remaining_x[i] = x
            remaining_widths[i] = rw

            if i != 1 and i != 2:
                # Draw remaining keys
                self.rounded_rectangle(draw, (x, y, x + rw, y + key_w),
                                      rx, fill=meta_key_color, outline=border_color, width=1)

                # Draw regular text (bottom)
                regular_text = right_keys[i]
                bbox = draw.textbbox((0, 0), regular_text, font=self.meta_font)
                text_x = x + REG_FONT_OFFSET_X * self.scale
                text_y = y + key_w // 2 - bbox[3] // 2
                draw.text((text_x, text_y), regular_text, font=self.meta_font, fill=secondary_text_color)

            x = 6 * self.scale
            y = y + space + key_w

        if ext_return:
            # Draw extended return key
            x1 = remaining_x[1]
            y1 = 6 * self.scale + key_w * 1 + space * 1
            w1 = remaining_widths[1]
            x2 = remaining_x[2]
            y2 = 6 * self.scale + key_w * 2 + space * 2

            # Create a polygon for the extended return key
            # This is a simplified approach that doesn't perfectly match the rounded corners
            # of the original, but it's close enough
            poly = [
                (x1, y1 + rx),                # Start from left side
                (x1 + rx, y1),                # Top left corner
                (x1 + w1 - rx, y1),           # Top right corner (almost)
                (x1 + w1, y1 + rx),           # Top right corner
                (x1 + w1, y2 + key_w - rx),   # Bottom right corner (almost)
                (x1 + w1 - rx, y2 + key_w),   # Bottom right corner
                (x2 + rx, y2 + key_w),        # Bottom left of the second part
                (x2, y2 + key_w - rx),        # Bottom left corner
                (x2, y1 + key_w),             # Middle left edge
                (x1 + rx, y1 + key_w),        # Bottom of top part
                (x1, y1 + key_w - rx)         # Bottom left corner of top part
            ]

            draw.polygon(poly, fill=meta_key_color, outline=border_color)
            # Draw regular text (bottom)
            regular_text = UNICODE_RETURN
            bbox = draw.textbbox((0, 0), regular_text, font=self.meta_font)
            text_x = x1 + REG_FONT_OFFSET_X * self.scale
            text_y = y1 + 12 * self.scale
            draw.text((text_x, text_y), regular_text, font=self.meta_font, fill=secondary_text_color)

        else:
            # Draw regular return key
            x = remaining_x[2]
            y = 6 * self.scale + key_w * 2 + space * 2
            self.rounded_rectangle(draw, (x, y, x + remaining_widths[2], y + key_w),
                                  rx, fill=key_color, outline=border_color, width=1)

            # Draw regular text (bottom)
            regular_text = UNICODE_RETURN
            bbox = draw.textbbox((0, 0), regular_text, font=self.meta_font)
            text_x = x + REG_FONT_OFFSET_X * self.scale
            text_y = y + key_w // 2 - bbox[3] // 2
            draw.text((text_x, text_y), regular_text, font=self.meta_font, fill=secondary_text_color)


        # Save to file
        img.save(filename, "PNG")

# Command line usage
if __name__ == "__main__":
    layout = sys.argv[1]
    variant = sys.argv[2]
    filename = sys.argv[3]
    scale = 1
    if len(sys.argv) > 4 and sys.argv[4] == "hidpi":
        scale = 2
        print("Keyboard layout being generated for hidpi")

    kb = KeyboardRenderer(layout, variant, scale, filename)
