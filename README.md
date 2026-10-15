# Linux Mint Live Installer

---

The installer for LMDE, and Releases of Linux Mint past version 27.

---

## Testing

Run `./test` in the terminal of an LMDE virtual machine.

---

## removing "remove installation media..." shutdown blurb if you ran it on metal.

go to `/usr/share/plymouth/themes/default.plymouth` and remove the line under [shutdown].

Run `sudo update-grub` and reboot
