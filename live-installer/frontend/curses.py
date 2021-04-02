import sys
import os

def get_width():
    return os.get_terminal_size()[0]
    
def get_height():
    return os.get_terminal_size()[1]

def initscr():
    """Clear and create a window"""
    printw("\x1b[s\x1bc")


def endwin():
    """Clear and restore screen"""
    printw("\x1bc\x1b[u")


def move(x, y):
    """Move"""
    printw("\x1b[{};{}f".format(int(y), int(x)))


def printw(msg=''):
    """Print clone"""
    sys.stdout.write(msg)
    sys.stdout.flush()


def mvprintw(x, y, msg=''):
    """Move and print"""
    move(x, y)
    printw(msg)


def noecho(enabled=True):
    if enabled:
        printw("\x1b[?25l")
    else:
        printw("\x1b[?25h")


def attron(attribute):
    """Attribute enable"""
    if attribute == "A_NORMAL":
        sys.stdout.write("\x1b[;0m")
    elif attribute == "A_UNDERLINE":
        sys.stdout.write("\x1b[4m")
    elif attribute == "A_REVERSE":
        sys.stdout.write("\x1b[7m")
    elif attribute == "A_BLINK":
        sys.stdout.write("\x1b[5m")
    elif attribute == "A_DIM":
        sys.stdout.write("\x1b[2m")
    elif attribute == "A_BOLD":
        sys.stdout.write("\x1b[1m")
    elif attribute == "A_INVIS":
        sys.stdout.write("\x1b[8m")
    elif attribute == "C_BLACK":
        sys.stdout.write("\x1b[30m")
    elif attribute == "C_RED":
        sys.stdout.write("\x1b[31m")
    elif attribute == "C_GREEN":
        sys.stdout.write("\x1b[32m")
    elif attribute == "C_YELLOW":
        sys.stdout.write("\x1b[33m")
    elif attribute == "C_BLUE":
        sys.stdout.write("\x1b[34m")
    elif attribute == "C_MAGENTA":
        sys.stdout.write("\x1b[35m")
    elif attribute == "C_CYAN":
        sys.stdout.write("\x1b[36m")
    elif attribute == "C_WHITE":
        sys.stdout.write("\x1b374m")
    elif attribute == "B_BLACK":
        sys.stdout.write("\x1b[40m")
    elif attribute == "B_RED":
        sys.stdout.write("\x1b[41m")
    elif attribute == "B_GREEN":
        sys.stdout.write("\x1b[42m")
    elif attribute == "B_YELLOW":
        sys.stdout.write("\x1b[43m")
    elif attribute == "B_BLUE":
        sys.stdout.write("\x1b[44m")
    elif attribute == "B_MAGENTA":
        sys.stdout.write("\x1b[45m")
    elif attribute == "B_CYAN":
        sys.stdout.write("\x1b[46m")
    elif attribute == "B_WHITE":
        sys.stdout.write("\x1b[47m")
    sys.stdout.flush()


def drawbox(x1, y1, x2, y2,char="█"):
    """Draw box"""
    x1=int(x1)
    x2=int(x2)
    y1=int(y1)
    y2=int(y2)
    for i in range((x1), (x2)):
        mvprintw(i, y1, char)
        mvprintw(i, y2, char)
    for i in range((y1), (y2)):
        mvprintw(x1, i, char)
        mvprintw(x2, i, char)
        
def max_line_len(msg):
   i=0
   for line in msg.split("\n"):
       if len(line)>i:
           i=len(line)
   return [i,len(msg.split("\n"))]
