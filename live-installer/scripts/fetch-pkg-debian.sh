fetch_deb(){
    if ping debian.org -c 1 &>/dev/null ; then
        chroot /target sh -c "apt-get update"
        chroot /target sh -c "apt-get install grub-pc-bin grub-efi grub-efi-ia32-bin -yq"
    else
        mkdir -p /target/debs/ || true
        cp /run/live/medium/pool/non-free/i/intel-microcode/* /target/debs/
        cp /run/live/medium/pool/non-free/a/amd64-microcode/* /target/debs/
        cp /run/live/medium/pool/contrib/i/iucode-tool/* /target/debs/
        for pkg in $@ ; do
            f="$(find /run/live/medium/pool/ -type f  -iname ${pkg}_*.deb)"
            if [ "" !=  "$f" ] ; then
                cp -pvf "$f" /target/debs/
            fi
        done
        chroot /target sh -c "dpkg -i /debs/*"
        rm -rf /target/debs
    fi
}

fetch_deb "efibootmgr" "grub-common" "grub-efi-amd64" "grub-efi-amd64-bin" \
    "grub2-common" "libefiboot1" "libefivar1" "os-prober" "grub-pc-bin" \
    "gettext-base" "grub-efi-ia32-bin"

