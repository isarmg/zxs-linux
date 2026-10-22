#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export ZXS_ROOT="$ROOT"
export ZXS_CACHE_DIR=${ZXS_CACHE_DIR:-"$ROOT/.cache"}
export ZXS_WORK_DIR=${ZXS_WORK_DIR:-"$ROOT/.work"}
export ZXS_OUTPUT_DIR="$ZXS_WORK_DIR/output"
BR_VERSION=2026.08
BR_SHA256=87aaca4164ea9d5c8085854953018263f7963f07c22e73a2a2185cc98c581c34
BR_DIR="$ZXS_CACHE_DIR/buildroot-$BR_VERSION"
export JOBS=${JOBS:-4}
[[ $JOBS =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be a positive integer' >&2; exit 2; }

activate_host() {
    local prefix="$ZXS_CACHE_DIR/host"
    if [[ -d $prefix/bin ]]; then
        export PATH="$prefix/bin:$PATH"
    fi
    export LC_ALL=C
}

source_setup() {
    local archive="$ZXS_CACHE_DIR/sources/buildroot-$BR_VERSION.tar.xz"
    mkdir -p "$ZXS_CACHE_DIR/sources"
    if [[ ! -f $archive ]]; then
        curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
            --output "$archive.part" "https://buildroot.org/downloads/buildroot-$BR_VERSION.tar.xz" || return
        mv -- "$archive.part" "$archive" || return
    fi
    printf '%s  %s\n' "$BR_SHA256" "$archive" | sha256sum --check --status || return
    if [[ ! -f $archive.sign ]]; then
        curl --fail --location --retry 2 --connect-timeout 15 --max-time 60 \
            --output "$archive.sign" "https://buildroot.org/downloads/buildroot-$BR_VERSION.tar.xz.sign" || return
    fi
    gpg --batch --yes --dearmor --output "$ZXS_CACHE_DIR/buildroot-key.gpg" "$ROOT/keys/buildroot-release.asc" || return
    gpgv --keyring "$ZXS_CACHE_DIR/buildroot-key.gpg" --output - - < "$archive.sign" > "$ZXS_CACHE_DIR/release.txt" || return
    grep --fixed-strings --line-regexp \
        "SHA256: $BR_SHA256  buildroot-$BR_VERSION.tar.xz" "$ZXS_CACHE_DIR/release.txt" || return
    if [[ ! -f $BR_DIR/.zxs-release-sha256 ]] || [[ $(cat "$BR_DIR/.zxs-release-sha256") != "$BR_SHA256" ]]; then
        local staging backup
        staging=$(mktemp -d "$ZXS_CACHE_DIR/extract.XXXXXX") || return
        tar --extract --xz --no-same-owner --file "$archive" --directory "$staging" || return
        printf '%s\n' "$BR_SHA256" > "$staging/buildroot-$BR_VERSION/.zxs-release-sha256"
        if [[ -e $BR_DIR ]]; then
            backup=$(mktemp -d "$ZXS_CACHE_DIR/previous-source.XXXXXX") || return
            mv -- "$BR_DIR" "$backup/" || return
        fi
        mv -- "$staging/buildroot-$BR_VERSION" "$BR_DIR" || return
        rmdir -- "$staging"
    fi
}

check() {
    local missing=0 tool
    for tool in gcc g++ make perl python3 tar xz unzip patch wget curl git rsync bc cpio bison flex file gpg gpgv; do
        if ! command -v "$tool" >/dev/null; then echo "Missing tool: $tool" >&2; missing=1; fi
    done
    (( missing == 0 )) || { echo 'Run ./build.sh setup first.' >&2; return 1; }
    python3 - "$ROOT" "$JOBS" <<'PY'
import os, shutil, sys
from pathlib import Path
root, jobs = Path(sys.argv[1]), int(sys.argv[2])
if os.uname().machine != 'x86_64':
    raise SystemExit('The host must be x86_64 Linux for the pinned Bootlin toolchain.')
free = shutil.disk_usage(root).free
if free < 10 * 1024**3:
    raise SystemExit('At least 10 GiB of free disk space is required for this build.')
print(f'Host checks passed; jobs={jobs}, free={free / 1024**3:.1f} GiB')
PY
}

brmake() {
    make -C "$BR_DIR" O="$ZXS_OUTPUT_DIR" BR2_EXTERNAL="$ROOT/br2-external" \
        BR2_DL_DIR="$ZXS_CACHE_DIR/downloads" BR2_JLEVEL="$JOBS" -j"$JOBS" "$@"
}

configure() {
    check || return
    source_setup || return
    mkdir -p "$ZXS_OUTPUT_DIR"
    # Buildroot reapplies overlays on each build; configured package outputs are retained.
    brmake zxs_x86_64_defconfig
}

run_stage() {
    local stage=$1
    shift
    mkdir -p "$ZXS_WORK_DIR/logs"
    local logfile
    logfile="$ZXS_WORK_DIR/logs/$(date -u +%Y%m%dT%H%M%S)-$stage.log"
    printf 'Stage: %s; log: %s\n' "$stage" "$logfile"
    "$@" 2>&1 | tee "$logfile"
}

activate_host
case "${1:-plan}" in
    plan)
        printf '%s\n' 'amd64 / UEFI, Linux 6.18.58, Buildroot 2026.08, Bootlin glibc 2025.08-1' \
            'BusyBox + eudev + IPv4 DHCP/IPv4LL; Intel/AMD iGPU KMS; USB4/Thunderbolt and wired NIC drivers' \
            'No ZXS, reverse proxy, bridge, NAT, Wi-Fi or graphical desktop' \
            'GPT: 64 MiB EFI + 1 GiB writable ext4 root; first console login sets the root password' \
            "Cache: $ZXS_CACHE_DIR" "Output: $ZXS_OUTPUT_DIR" "Parallel jobs: $JOBS"
        ;;
    setup)
        run_stage host-setup "$ROOT/scripts/setup-host.sh"
        activate_host
        run_stage source-setup source_setup
        check
        ;;
    check) check ;;
    configure) run_stage configure configure ;;
    fetch) configure; run_stage fetch brmake source ;;
    build) configure; run_stage build brmake all ;;
    verify) run_stage verify python3 "$ROOT/scripts/verify.py" ;;
    test) run_stage boot-test python3 "$ROOT/scripts/boot-test.py" ;;
    package) run_stage package python3 "$ROOT/scripts/package.py" ;;
    legal-info)
        run_stage legal-info brmake legal-info
        run_stage source-licenses python3 "$ROOT/scripts/legal-info.py"
        ;;
    all)
        "$0" setup
        "$0" build
        "$0" verify
        "$0" test
        "$0" package
        ;;
    *) echo 'Usage: ./build.sh {plan|setup|check|configure|fetch|build|verify|test|package|legal-info|all}' >&2; exit 2 ;;
esac

