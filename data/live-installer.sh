#!/usr/bin/env bash
if ! ls -la /lib/live-installer/main.py | grep "^...x" ; then
    pkexec chmod 755 /lib/live-installer/main.py
fi
pkexec /lib/live-installer/main.py $@
