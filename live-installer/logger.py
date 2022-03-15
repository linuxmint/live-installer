import os
import sys

logfile = None
_file = "/var/log/17g-installer"

def set_logfile(path):
    if os.getuid() != 0:
        return
    global logfile
    if logfile:
        logfile.flush()
        logfile.close()
    if os.path.isfile(path):
        os.unlink(path)
    logfile = open(path,"a")

if os.getuid() != 0:
    _file = "/tmp/17g-installer.log"
    logfile = open(_file,"a")

set_logfile(_file)


def log(output, err=False):
    output = str(output)
    output += "\n"
    logfile.write(output)
    logfile.flush()
    if err:
        sys.stderr.write(output)
    else:
        sys.stdout.write(output)


def err(output):
    sys.stderr.write("\x1b[31;1m")
    log(output, True)
    sys.stderr.write("\x1b[;0m")


def inf(output):
    sys.stdout.write("\x1b[32;1m")
    log(output, False)
    sys.stdout.write("\x1b[;0m")
