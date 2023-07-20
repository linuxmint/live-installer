#!/bin/bash
# install grub packages for debian
fetch_deb(){
    missing=()
    mkdir -p /target/debs/ || true
    for pkg in $@ ; do
        f="$(find /run/live/medium/pool/ -type f  -iname ${pkg}_*.deb)"
        if [ "" !=  "$f" ] ; then
            cp -pvf "$f" /target/debs/
        else
            missing+=(${pkg})
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]  ; then
       chroot /target apt-get update
       chroot /target apt-get install ${missing[@]} -o Dpkg::Options::="--force-confnew" --yes
    fi
}

# fetch microcode packages
cp /run/live/medium/pool/contrib/i/iucode-tool/* /target/debs/
cp /run/live/medium/pool/non-free/i/intel-microcode/* /target/debs/
cp /run/live/medium/pool/non-free/a/amd64-microcode/* /target/debs/

# install microcode packages and clean
chroot /target sh -c 'dpkg -i /debs/*'
rm -rf /target/debs

# fetch common grub packages
fetch_deb "grub-common" "grub2-common" "os-prober" "gettext-base" \
           "libefiboot1" "libefivar1" "libfuse2"

# fetch platform spesific packages
if [ -d /sys/firmware/efi ] ; then
    fetch_deb "efibootmgr" 
    if [ "$(cat /sys/firmware/efi/fw_platform_size)" == "32" ] ; then
        fetch_deb "grub-efi-ia32" "grub-efi-ia32-bin"
    else
        fetch_deb "grub-efi-amd64" "grub-efi-amd64-bin"
    fi
else
    fetch_deb "grub-pc" "grub-pc-bin"
fi

# install packages and clean
chroot /target sh -c 'dpkg -i /debs/*'
rm -rf /target/debs
