import parted, commands
import gtk
import gtk.glade
import cairo
from math import pi

# Represents a disk partition in an easy format
class PyDisk(object):
	def __init__(self, number, name, size, type):
		self.number = number
		self.name = name
		self.size = size
		self.type = type

# Create a GTK+ widget on which we will draw using Cairo
class Screen(gtk.DrawingArea):

    # Draw in response to an expose-event
    __gsignals__ = { "expose-event": "override" }

    def __init__(self, disks):
		gtk.DrawingArea.__init__(self)
		''' Init'd with a list of disks(partitions..) '''
		self.disks = disks
		self.total_size = 0
		for item in self.disks:
			self.total_size += item.size
		self.labels_added = False
		self.label_offset = 10
		self.y_offset = 110
		self.rows = 0
		# Generate our .. colours
		self.colours = [ (0.1, 0.5, 1.0),
						 (0.2, 0.6, 0.9),
						 (0.3, 0.7, 0.8),
						 (0.4, 0.8, 0.7),
						 (0.5, 0.9, 0.6),
						 (0.6, 1.0, 0.5),
						 (0.7, 0.1, 0.4),
						 (0.8, 0.2, 0.3),
						 (0.9, 0.3, 0.2),
						 (1.0, 0.4, 0.1) ]

    def add_label(self, label):
		''' Add a label to the whatchacallit '''
		# see if the line extends onto a new line
		xbearing, ybearing, width, height, xadvance, yadvance = (self.context.text_extents(label))
		STARTY = (self.height / 2) + 30
		if(xadvance + (self.label_offset + 26) >= self.width):
			self.rows += 1
			self.y_offset = STARTY + yadvance + (self.rows * 15) # spacing, new line
			if(self.label_offset != 5):
				self.label_offset += 10
			self.label_offset = 5
		else:
			self.y_offset = STARTY + yadvance + (self.rows * 15)
			if(self.label_offset >= self.width):
				self.label_offset = 5
			else:
				if(self.label_offset > 5):
					self.label_offset += 16
		# outline
		self.context.set_source_rgb(0, 0, 0)
		self.rounded_rectangle(self.context, self.label_offset -1, self.y_offset - 11, 12, 12, r=0)
		self.context.fill()
		self.context.set_source_rgb(*self.old_colour)
		self.rounded_rectangle(self.context, self.label_offset, self.y_offset - 10, 10, 10, r=0)
		self.context.fill()
		self.label_offset += 16
		self.context.move_to(self.label_offset, self.y_offset)
		self.context.set_source_rgb(0, 0, 0)
		for cx, letter in enumerate(label):
			xbearing, ybearing, width, height, xadvance, yadvance = (self.context.text_extents(letter))
			self.label_offset += xadvance
			self.context.show_text(letter)
		self.y_offset = 110
		
    def rounded_rectangle(self, cr, x, y, w, h, r=12):
		cr.move_to(x+r,y)                      # Move to A
		cr.line_to(x+w-r,y)                    # Straight line to B
		cr.curve_to(x+w,y,x+w,y,x+w,y+r)       # Curve to C, Control points are both at Q
		cr.line_to(x+w,y+h-r)                  # Move to D
		cr.curve_to(x+w,y+h,x+w,y+h,x+w-r,y+h) # Curve to E
		cr.line_to(x+r,y+h)                    # Line to F
		cr.curve_to(x,y+h,x,y+h,x,y+h-r)       # Curve to G
		cr.line_to(x,y+r)                      # Line to H
		cr.curve_to(x,y,x,y,x+r,y)             # Curve to A

    # Handle the expose-event by drawing
    def do_expose_event(self, event):

        # Create the cairo context
        cr = self.window.cairo_create()
        self.context = cr
        # Restrict Cairo to the exposed area; avoid extra work
        #cr.set_source_rgba(0, 0, 0, 0)
        #cr.rectangle(event.area.x, event.area.y,
        #        event.area.width, event.area.height)
        #cr.clip()

        self.draw(cr, *self.window.get_size())

    def draw(self, cr, width, height):
        self.style.apply_default_background(self.window, True, gtk.STATE_NORMAL, None, 0, 0, width, height)
        cr.set_source_rgba(0, 0, 0, 0.0)
        self.width = width # for add_label
        self.height = height # for add_label
        height /= 2
        width -= 40
        cr.rectangle(0, 0, width, height)
        cr.fill()

        x_offset = 5
        y_offset = 5
        COLOUR = 0.1
        COLOUR2 = 1.0
        self.label_offset = 5
        self.rows = 0
        for item in self.disks:
			ratio = float(item.size) / float(self.total_size)
			pct = float(ratio * 100)
			pixels = ratio * width
			n_width = int(pixels)
			cr.set_source_rgb(0, 0, 0)
			self.rounded_rectangle(cr, x_offset-1, 9, n_width+2, height+2)
			cr.fill()
			x = 0.0
			colour = self.colours[item.number % len(self.colours)]
			cr.set_source_rgb(*colour)
			T_COLOUR = colour[0]
			T_COLOUR2 = colour[1]
			T_COLOUR3 = colour[2]
			self.rounded_rectangle(cr, x_offset, 10, n_width, height)
			cr.fill()
			x_offset += n_width
			x_offset += 3
			COLOUR += 0.3
			COLOUR2 -= 0.3
			self.old_colour = (T_COLOUR, T_COLOUR2, T_COLOUR3)
			self.add_label(item.name + " - " + item.type) 
        self.rows = 0

