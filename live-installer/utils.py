from glob import glob
import os
import yaml
from shlex import quote

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

# Package Manager
for package_manager in glob("resources/package_managers/*"):
        pm = open(package_manager, 'r')
        pm_contents = yaml.load(pm, Loader=yaml.FullLoader)
    
        if os.path.exists(pm_contents["check_this_dir"]):
            package_manager = pm_contents
            break

def PackageManager(process, packages=[]):
    if process == "name":
        exit("You can't use this parameter!")
    if process in package_manager:
        pkgs = " ".join(str(p) for p in packages)
        cmd = (package_manager[process] + " ").replace("{packages}", pkgs)

        return cmd
    else:
        exit("Process doesn't exists on package manager's config file!")
