#!/bin/sh
set -eu
board=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
install -D -m 0644 "$board/grub.cfg" "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"
mkdir -p "$TARGET_DIR/usr/sbin"
for script in console-login zxs-https; do
    install -m 0755 "$board/scripts/$script" "$TARGET_DIR/usr/sbin/$script"
done
variant=busybox
if grep -qx 'BR2_INIT_SYSTEMD=y' "$BR2_CONFIG"; then
    variant=systemd
    # Buildroot enables IPv6 for systemd; keep the deployed endpoints IPv4-only.
    sed -i 's/console=ttyS0,115200n8/console=ttyS0,115200n8 ipv6.disable=1/' \
        "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"
    rm -f "$TARGET_DIR/etc/inittab" "$TARGET_DIR/etc/init.d/S11udev-coldplug" \
        "$TARGET_DIR/etc/init.d/S50stunnel"
fi
# The console wrapper initializes the locked root account locally on first boot.
sed -i 's|^root:[^:]*:|root:*:|' "$TARGET_DIR/etc/shadow"
rm -f "$TARGET_DIR/etc/zxs/initialized"
mkdir -p "$TARGET_DIR/srv" "$TARGET_DIR/etc/zxs"
printf '%s\n' 'buildroot=2026.08' 'linux=6.18.58' 'zxs=absent' "init=$variant" > "$TARGET_DIR/etc/zxs-release"
