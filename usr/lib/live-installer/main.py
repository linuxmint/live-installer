#!/usr/bin/python -OO

import sys
import commands

def uncaught_excepthook(*args):
    sys.__excepthook__(*args)
    if __debug__:
        from pprint import pprint
        from types import BuiltinFunctionType, ClassType, ModuleType, TypeType
        tb = sys.last_traceback
        while tb.tb_next: tb = tb.tb_next
        print('\nDumping locals() ...')
        pprint({k:v for k,v in tb.tb_frame.f_locals.items()
                    if not k.startswith('_') and
                       not isinstance(v, (BuiltinFunctionType,
                                          ClassType, ModuleType, TypeType))})
        if sys.stdin.isatty() and (sys.stdout.isatty() or sys.stderr.isatty()):
            try:
                import ipdb as pdb  # try to import the IPython debugger
            except ImportError:
                import pdb as pdb
            print '\nStarting interactive debug prompt ...'
            pdb.pm()
    else:
        import traceback
        from .dialogs import ErrorDialog
        ErrorDialog(_('Unexpected error'),
                    _('<b>The installer has failed with the following unexpected error. Please submit a bug report!</b>'),
                    '<tt>' + '\n'.join(traceback.format_exception(*args)) + '</tt>')
    sys.exit(1)

sys.excepthook = uncaught_excepthook

from .gtk_interface import InstallerWindow

import pygtk
pygtk.require("2.0")
import gtk

# main entry
if __name__ == "__main__":
	if("install" in commands.getoutput("cat /proc/cmdline")):
		win = InstallerWindow(fullscreen=True)
	else:
		win = InstallerWindow(fullscreen=False)
	gtk.main()
