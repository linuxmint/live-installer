fetch_deb(){
    mkdir -p /target/debs/ || true
    for pkg in $@ ; do
        cp -pvf $(find /run/live/medium/pool/ -iname ${pkg}_*.deb) /target/debs/
    done
    chroot /target sh -c "dpkg -i /debs/*"
    rm -rf /target/debs
}

fetch_deb "efibootmgr" "grub-common" "grub-efi-amd64" "grub-efi-amd64-bin" \
    "grub-efi-amd64-signed" "grub2-common" "libefiboot1" "libefivar1" \
    "mokutil" "os-prober" "shim-helpers-amd64-signed" "shim-signed" \
    "shim-signed-common" "shim-unsigned" "grub-pc-bin" "gettext-base"

