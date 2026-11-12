#!/bin/bash
# shellcheck disable=SC2154
check() {
    require_binaries findmnt dmsetup swapon grep awk sed tr || return 1
    return 255
}
depends() {
    echo "systemd-initrd systemd-veritysetup dracut-systemd"
}
install() {
    inst_multiple findmnt dmsetup swapon grep awk sed tr
    inst_script "$moddir/check-root.sh" /usr/local/libexec/cvm-check-root
    inst_simple "$moddir/cvm-verity-root.service" "$systemdsystemunitdir/cvm-verity-root.service"
    mkdir -p "$initdir$systemdsystemunitdir/initrd-switch-root.service.d"
    inst_simple "$moddir/switch-root.conf" \
        "$systemdsystemunitdir/initrd-switch-root.service.d/cvm-verity.conf"
    $SYSTEMCTL -q --root "$initdir" add-requires initrd-switch-root.target cvm-verity-root.service
}
