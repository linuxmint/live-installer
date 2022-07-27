
# 17g installer

17g is fork of Linuxmint debian edition live-installer. (https://github.com/linuxmint/live-installer) Main purpose is creating flexible and configurable distro-independed live installation tools. 

17g is full featured calamares alternative. (https://github.com/calamares/calamares)

Better than calamares for gtk desktops because 17g never uses qt dependencies.

Features:

* Debian, Arch, Sulin based distribution relations. 
* Don't use bloat dependencies.
* Uses gtk and python3
* Configurable and expandable

## Installation

Dependencies:

```
    rsync (optional)
    python3-parted
    python3-gi
    gir1.2-gtk-3.0
    python3-yaml
    gettext-base
    isoquery (optional)
```
Getting source:

```
git clone https://gitlab.com/ggggggggggggggggg/17g.git 17g
```
Compile source:

```
make
```
Install source:

```
make install DESTDIR=/
```

Install service (Optional):

```
install 17g.initd /etc/init.d #(for openrc)
install 17g.service /etc/systemd/system #(for systemd)
```

# Building with unibuild

```shell
unibuild https://gitlab.com/ggggggggggggggggg/17g/-/raw/master/17g-installer.unibuild
```

# Installation for debian

```shell
git clone https://gitlab.com/ggggggggggggggggg/17g.git 17g
cd 17g
mk-build-deps --install
debuild -us -uc -b
dpkg -i ../17g*.deb
```

## Configuration

1. live-installer/branding/ directory is distribution images directory. Slides directory included slideshow images and images must be 16:9 format

1. configs/config.yaml file is main configuration file. You can disable or enable features.

1.  configs/live.yaml file is live service config. Some distributions not need this service. (for example: debian based) If you dont change anything 17g uses automatic mode and enable all features.

## Translation

https://www.transifex.com/17g/17g

## Community

https://t.me/iso_calismalari (Turkish telegram group)

## Mirrors:

* https://gitlab.com/ggggggggggggggggg/17g (Mainline)
* https://kod.pardus.org.tr/17g-installer/17g
* https://github.com/17g-installer/17g
* https://gitlab.gnome.org/sulincix/17g
* https://notabug.org/17g-installer

### Notes:

* Archlinux packagers must move /lib/live-installer to /usr/lib/live-instaler.
* Default hosts file source : https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
