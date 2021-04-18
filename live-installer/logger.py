import os
import sys

logfile = None
file = "/var/log/17g-installer"
if os.getuid() != 0:
    file = "/tmp/17g-installer.log"
if os.path.isfile(file):
    os.unlink(file)
logfile = open(file, "a")


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
