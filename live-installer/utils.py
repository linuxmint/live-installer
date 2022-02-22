
import os
import subprocess
import sys
import threading
import config
from gi.repository import GObject
from logger import log, err, inf

def memoize(func):
    """ Caches expensive function calls.

    Use as:

        c = Cache(lambda arg: function_to_call_if_yet_uncached(arg))
        c('some_arg')  # returns evaluated result
        c('some_arg')  # returns *same* (non-evaluated) result

    or as a decorator:

        @memoize
        def some_expensive_function(args [, ...]):
            [...]

    See also: http://en.wikipedia.org/wiki/Memoization
    """
    class memodict(dict):
        def __call__(self, *args):
            return self[args]

        def __missing__(self, key):
            ret = self[key] = func(*key)
            return ret
    return memodict()


def debug(func):
    '''Decorator to print function call details - parameters names and effective values'''
    def wrapper(*func_args, **func_kwargs):
        print('func_args =', func_args)
        print('func_kwargs =', func_kwargs)
        params = []
        for argNo in range(func.__code__.co_argcount):
            argName = func.__code__.co_varnames[argNo]
            argValue = func_args[argNo] if argNo < len(func_args) else func.__defaults__[
                argNo - func.__code__.co_argcount]
            params.append((argName, argValue))
        for argName, argValue in list(func_kwargs.items()):
            params.append((argName, argValue))
        params = [argName + ' = ' + repr(argValue)
                  for argName, argValue in params]
        print(func.__name__ + '(' + ', '.join(params) + ')')
        return func(*func_args, **func_kwargs)
    return wrapper

# Used as a decorator to run things in the background


def asynchronous(func):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper


# Used as a decorator to run things in the main loop, from another thread
def idle(func):
    def wrapper(*args, **kwargs):
        GObject.idle_add(func, *args, **kwargs)
    return wrapper


def to_float(position, wholedigits):
    assert position and len(position) > 4 and wholedigits < 9
    return float(position[:wholedigits + 1] + '.' + position[wholedigits + 1:])


def is_root():
    return os.getuid() == 0

def is_cmd(cmd):
    return os.system("which "+cmd) == 0

def run(cmd, vital=True):
    inf("Running: " + cmd)
    if "||" in cmd:
        mode = cmd.split("||")[0].strip()
        cmd = cmd.split("||")[1].strip()
        if mode == "chroot":
            i = do_run_in_chroot(cmd)
        else:
            i = os.system(cmd)
    else:
        i = int(os.system(cmd)/256)
    if vital and i != 0:
        err("Failed to run command (Exited with {}): {}".format(
            str(i), cmd))
    return i


efivar_loaded = False
def is_efi_supported():
    global efivar_loaded
    # Are we running under with efi ?
    if not efivar_loaded:
        run("modprobe efivars")
        efivar_loaded = True
    return os.path.exists("/proc/efi") or os.path.exists("/sys/firmware/efi")


def path_exists(*args):
    return os.path.exists(os.path.join(*args))


def shell_exec(command):
    return subprocess.Popen(command, shell=True, stdout=subprocess.PIPE)


def getoutput(command):
    return shell_exec(command).stdout.read().strip()


def do_run_in_chroot(command=None, vital=False):
    command = str(command).replace('"', "'").strip()
    return os.system("chroot /target/ /bin/bash -c \"%s\"" % command)


def set_governor(governor):
    i = 0
    path = "/sys/devices/system/cpu/"
    node = "/cpufreq/scaling_governor"
    while os.path.exists("{}cpu{}{}".format(path, str(i), node)):
        os.system("echo {} > {}cpu{}{}".format(governor, path, str(i), node))
        i += 1
