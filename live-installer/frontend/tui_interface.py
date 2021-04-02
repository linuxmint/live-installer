from installer import InstallerEngine, Setup
from frontend.curses import *

class InstallerWindow:
    def __init__(self):
        self.setup = Setup()
        self.installer = InstallerEngine(self.setup)
        self.draw_page("Hello world\nOther line")
        
    def draw_page(self,msg):
        initscr()
        x=get_width()
        y=get_height()
        drawbox(1,1,x,y)
        i=0
        for line in msg.split("\n"):
            move((x-max_line_len(msg)[0])/2,((y-max_line_len(msg)[1])/2)+i)
            printw(line)
            i+=1
