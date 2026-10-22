#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export ZXS_ROOT="$ROOT" ZXS_VARIANT=debian
export ZXS_WORK_DIR=${ZXS_WORK_DIR:-"$ROOT/.work/debian"}
export ZXS_OUTPUT_DIR="$ZXS_WORK_DIR/output"
export JOBS=${JOBS:-4}
builder=zxs-debian-builder
export BUILDX_CONFIG=${BUILDX_CONFIG:-"$ROOT/.cache/debian/buildx"}
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH
[[ $ZXS_WORK_DIR == "$ROOT/"* ]] || { echo 'ZXS_WORK_DIR must be within the repository.' >&2; exit 2; }
mkdir -p "$ZXS_WORK_DIR/logs"
export PATH="$ROOT/.cache/host/bin:$PATH"

container() {
    # Privileges are needed only to bootstrap the guest with proc/dev mounts.
    # No host block devices are used; image creation operates on regular files.
    local flags=()
    [[ $1 != build ]] || flags+=(--privileged)
    local script=scripts/debian-image.py
    [[ $1 != test ]] || script=scripts/boot-test.py
    [[ $1 != package ]] || script=scripts/debian-package.py
    docker run --rm "${flags[@]}" --volume "$ROOT:/src" \
        --mount type=bind,src=/etc/ssl/certs/ca-certificates.crt,dst=/run/zxs-build-ca.pem,readonly \
        --env ZXS_ROOT=/src --env ZXS_VARIANT=debian \
        --env "ZXS_WORK_DIR=/src/${ZXS_WORK_DIR#"$ROOT/"}" \
        --env "ZXS_OUTPUT_DIR=/src/${ZXS_OUTPUT_DIR#"$ROOT/"}" \
        --env "BUILD_UID=$(id -u)" --env "BUILD_GID=$(id -g)" \
        --env JOBS "$builder" bash -Eeuo pipefail -c \
        'trap '\''chown -R "$BUILD_UID:$BUILD_GID" "$ZXS_WORK_DIR/results" "$ZXS_WORK_DIR/boot-test" /src/dist/zxs-linux-amd64-debian 2>/dev/null || true'\'' EXIT; python3 "$@"' bash "$script" "$1"
}

case "${1:-plan}" in
    plan)
        echo 'Debian 13 stable / amd64 / UEFI; systemd, GNU tools and dash; no runtime package manager'
        echo '64 MiB EFI + 1 GiB ext4 root; ext4/Btrfs tools; wired DHCP/IPv4LL; LAN HTTPS'
        echo "Output: $ZXS_OUTPUT_DIR; delivery: dist/zxs-linux-amd64-debian"
        ;;
    setup)
        env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
            docker --host=unix:///var/run/docker.sock info --format '{{.ServerVersion}}'
        python3 "$ROOT/scripts/debian-bootstrap.py"
        docker build --build-arg DEBIAN_BASE=zxs-debian-bootstrap:13 \
            --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt \
            --tag "$builder" "$ROOT/debian"
        ;;
    check)
        docker image inspect "$builder" --format '{{.Id}}'
        docker run --rm "$builder" sh -c 'command -v mmdebstrap; command -v qemu-system-x86_64; test -f /usr/share/OVMF/OVMF_CODE_4M.fd'
        ;;
    build|verify|test|package|legal-info)
        stage=$1
        container "$stage" 2>&1 | tee "$ZXS_WORK_DIR/logs/$(date -u +%Y%m%dT%H%M%S)-$stage.log"
        ;;
    all)
        for stage in setup build verify test package legal-info; do "$0" "$stage"; done
        ;;
    *) echo 'Usage: ./debian-build.sh {plan|setup|check|build|verify|test|package|legal-info|all}' >&2; exit 2 ;;
esac
