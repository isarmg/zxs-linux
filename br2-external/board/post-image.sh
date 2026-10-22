#!/bin/sh
set -eu
board=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
"$HOST_DIR/bin/genimage" --rootpath "$TARGET_DIR" --tmppath "$BUILD_DIR/genimage-zxs.tmp" \
    --inputpath "$BINARIES_DIR" --outputpath "$BINARIES_DIR" --config "$board/genimage.cfg"
