fetch_deb(){
    mkdir -p /target/debs/ || true
    for pkg in $@ ; do
        f="$(find /run/live/medium/pool/ -type f  -iname ${pkg}_*.deb)"
        if [ "" !=  "$f" ] ; then
            cp -pvf "$f" /target/debs/
        fi
    done
    chroot /target sh -c "dpkg -i /debs/*"
    rm -rf /target/debs
}

fetch_deb "efibootmgr" "grub-common" "grub-efi-amd64" "grub-efi-amd64-bin" \
    "grub-efi-amd64-signed" "grub2-common" "libefiboot1" "libefivar1" \
    "mokutil" "os-prober" "shim-helpers-amd64-signed" "shim-signed" \
    "shim-signed-common" "shim-unsigned" "grub-pc-bin" "gettext-base"

