
from subprocess import Popen, PIPE

def shell_exec(command, **kwargs):
    print 'Executing:', command
    return Popen(command, shell=True, stdout=PIPE, **kwargs)

def getoutput(command, **kwargs):
    """Return stdout of command."""
    return shell_exec(command, **kwargs).communicate()[0].strip()

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
