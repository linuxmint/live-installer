#!/bin/bash
# Remove live users for archlinux (archiso)
grep ":[0-9][0-9][0-9][0-9]:" /target/etc/passwd | while read line ; do
    user=$(echo $line | cut -f 1 -d ":")
    home=$(echo $line | cut -f 6 -d ":")
    if [[ "$user" != "" ]] ; then
        chroot /target deluser "$user"
        [[ -d /target/$home ]] && rm -rf /target/$home 
    fi
done
