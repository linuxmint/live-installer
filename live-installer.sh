#!/usr/bin/env bash
xhost +
pkexec sh -c "DISPLAY=$DISPLAY /usr/lib/live-installer/main.py"
