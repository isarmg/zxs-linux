#!/bin/sh
set -eu
board=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
install -D -m 0644 "$board/grub.cfg" "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"
# The console wrapper initializes the locked root account locally on first boot.
sed -i 's|^root:[^:]*:|root:*:|' "$TARGET_DIR/etc/shadow"
rm -f "$TARGET_DIR/etc/zxs/initialized"
mkdir -p "$TARGET_DIR/srv" "$TARGET_DIR/etc/zxs"
printf '%s\n' 'buildroot=2026.08' 'linux=6.18.58' 'zxs=absent' > "$TARGET_DIR/etc/zxs-release"

