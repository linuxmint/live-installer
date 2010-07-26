import parted, commands
import gtk
import gtk.glade
import cairo
from math import pi

# Represents a disk partition in an easy format
class Partition(object):

    aggregatedPartitions = []

    def __init__(self, partition):
        self.partition = partition
        self.size = partition.getSize()
        self.start = partition.geometry.start
        self.end = partition.geometry.end
        self.description = ""
        self.used_space = ""
        self.free_space = ""
        self.real_type = None

        if partition.number != -1:
            self.name = partition.path
            self.real_type = partition.type
            if partition.fileSystem is None:
                # no filesystem, check flags
                if partition.type == parted.PARTITION_SWAP:
                    self.type = ("Linux swap")
                elif partition.type == parted.PARTITION_RAID:
                    self.type = ("RAID")
                elif partition.type == parted.PARTITION_LVM:
                    self.type = ("Linux LVM")
                elif partition.type == parted.PARTITION_HPSERVICE:
                    self.type = ("HP Service")
                elif partition.type == parted.PARTITION_PALO:
                    self.type = ("PALO")
                elif partition.type == parted.PARTITION_PREP:
                    self.type = ("PReP")
                elif partition.type == parted.PARTITION_MSFT_RESERVED:
                    self.type = ("MSFT Reserved")
                elif partition.type == parted.PARTITION_EXTENDED:
                    self.type = ("Extended Partition")
                elif partition.type == parted.PARTITION_LOGICAL:
                    self.type = ("Logical Partition")
                elif partition.type == parted.PARTITION_FREESPACE:
                    self.type = ("Free Space")
                else:
                    self.type =("Unknown")
            else:
                self.type = partition.fileSystem.type
        else:
            self.type = ""
            self.name = _("unallocated")

    def add_partition(self, partition):
        self.aggregatedPartitions.append(partition)
        self.size = self.size + partition.getSize()
        self.end = partition.geometry.end

# Create a GTK+ widget on which we will draw using Cairo
class Screen(gtk.DrawingArea):

    # Draw in response to an expose-event
    __gsignals__ = { "expose-event": "override" }

    def __init__(self, partitions):
        gtk.DrawingArea.__init__(self)
        ''' Init'd with a list of disks(partitions..) '''
        self.partitions = partitions
        self.total_size = 0
        for partition in self.partitions:
            if not partition.real_type == parted.PARTITION_EXTENDED:
                self.total_size += partition.size

        self.labels_added = False
        self.label_offset = 10
        self.y_offset = 110
        self.rows = 0
        # Generate our .. colors
        self.colors = [ (0.1, 0.5, 1.0),
                                         (0.0, 0.0, 0.6),
                                         (0.0, 0.6, 0.6),
                                         (0.0, 0.6, 0.0),
                                         (0.6, 0.6, 0.6),
                                         (0.6, 0.0, 0.0)]
        #import gobject
        #gobject.timeout_add(10, self.tick)

    def add_label(self, label, color):
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
        self.context.set_source_rgb(*color)
        self.rounded_rectangle(self.context, self.label_offset -1, self.y_offset - 11, 12, 12, r=0)
        self.context.fill()
        self.context.set_source_rgb(*color)
        self.rounded_rectangle(self.context, self.label_offset, self.y_offset - 10, 10, 10, r=0)
        self.context.fill()
        self.label_offset += 16
        self.context.move_to(self.label_offset, self.y_offset)
        self.context.set_source_rgb(*color)
        for cx, letter in enumerate(label):
            xbearing, ybearing, width, height, xadvance, yadvance = (self.context.text_extents(letter))
            self.label_offset += xadvance
            self.context.show_text(letter)
        self.y_offset = 110

    def rounded_rectangle(self, cr, x, y, w, h, r=0):
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

        self.draw(cr, event.area.width, event.area.height)
        
    def tick(self):
        #print "refreshing"
        self.alloc = self.get_allocation()
        rect = gtk.gdk.Rectangle(self.alloc.x, self.alloc.y, self.alloc.width, self.alloc.height)
        self.window.invalidate_rect(rect, True)
        #print "refreshed"
        return True		

    def draw(self, cr, width, height):
        #self.style.apply_default_background(self.window, True, gtk.STATE_NORMAL, None, 0, 0, width, height)
        cr.set_source_rgba(0, 0, 0, 0.0)
        self.width = width # for add_label
        self.height = height # for add_label
        height /= 2
        width -= 40
        cr.rectangle(0, 0, width, height)
        cr.fill()

        x_offset = 5
        y_offset = 5

        self.label_offset = 5
        self.rows = 0

        for partition in self.partitions:
            if partition.size > 0.5 and partition.real_type != parted.PARTITION_EXTENDED:
                if partition.partition.number == -1:
                    border_color = (0.6, 0.6, 0.6)
                    fill_color = (0.6, 0.6, 0.6)
                else:
                    border_color = self.colors[partition.partition.number % len(self.colors)]
                    fill_color = (0.9, 0.9, 0.9)

                ratio = float(partition.size) / float(self.total_size)
                pct = float(ratio * 100)
                pixels = ratio * width
                n_width = int(pixels)
                n_start = 10

                cr.set_source_rgb(*border_color)
                self.rounded_rectangle(cr, x_offset-1, n_start - 1, n_width+2, height+2)
                cr.fill()

                cr.set_source_rgb(*fill_color)
                self.rounded_rectangle(cr, x_offset, n_start, n_width, height)
                cr.fill()

                # partition usage
                if(partition.used_space != ""):
                    cr.set_source_rgb(0.8, 0.8, 0.8)
                    pct_used = float((partition.used_space.replace("%", "")))
                    ratio = pct_used / 100
                    pixels = n_width * ratio
                    #u_width = n_width - int(pixels)
                    u_width = int(pixels)

                    self.rounded_rectangle(cr, x_offset, n_start, u_width, height)
                    cr.fill()
                    cr.set_source_rgb(*fill_color)

                x_offset += n_width
                x_offset += 3

                if(partition.partition.number != -1):
                    if partition.description != "":
                        self.add_label("%s (%s)" % (partition.description, partition.name.replace('/dev/', '')), border_color)
                    else:
                        self.add_label(partition.name.replace('/dev/', ''), border_color)
        self.rows = 0
