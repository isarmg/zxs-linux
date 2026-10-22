#!/usr/bin/env bash
# Publish the already-tested artifacts of an exact, pushed version tag.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
version=${1:-}
[[ $version =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || {
    echo 'Usage: scripts/publish-release.sh vMAJOR.MINOR.PATCH' >&2; exit 2;
}
command -v gh >/dev/null || { echo 'Install and authenticate GitHub CLI first.' >&2; exit 1; }
[[ -z $(git status --porcelain) ]] || { echo 'Commit all source changes before publishing.' >&2; exit 1; }
[[ $(git rev-parse "refs/tags/$version^{commit}") == "$(git rev-parse HEAD)" ]] || {
    echo 'The version tag must refer to the checked-out source commit.' >&2; exit 1;
}
destination=${ZXS_RELEASE_DIR:-"$ROOT/dist/release-assets"}
names=(README.md build-sources.tar.gz source-licenses.tar.gz buildroot.config bzImage
       disk.img.xz linux.config packages.json validation.json SHA256SUMS)
assets=()
for variant in busybox systemd; do
    prefix=
    folder=$destination
    if [[ $variant == systemd ]]; then prefix=systemd-; fi
    if [[ -z ${ZXS_RELEASE_DIR:-} ]]; then
        folder="$ROOT/dist/zxs-linux-amd64${prefix:+-systemd}"
    fi
    for name in "${names[@]}"; do
        [[ -s $folder/$prefix$name ]] || { echo "Missing release asset: $prefix$name" >&2; exit 1; }
        assets+=("$folder/$prefix$name")
    done
    (cd "$folder" && sha256sum --check --strict "${prefix}SHA256SUMS")
    python3 - "$folder/${prefix}validation.json" "$variant" <<'PY'
import json,sys
report=json.load(open(sys.argv[1]))
if not (report.get('variant')==sys.argv[2]
        and report['image']['status']==report['boot']['status']=='passed'
        and report['image']['artifacts']==report['boot']['artifacts']
        and report['image']['variant']==report['boot']['variant']==sys.argv[2]):
    raise SystemExit('Both variants require matching passed image and boot reports.')
PY
done
notes=$(mktemp)
trap 'rm -f -- "$notes"' EXIT
cat > "$notes" <<'NOTES'
amd64／UEFI 精简 Linux 基础系统，不包含 ZXS；系统通过 stunnel 提供局域网 HTTPS。

- Buildroot 2026.08、Linux 6.18.58；保留 Intel／AMD 核显、USB4／雷电及有线网卡驱动。
- 原版使用 BusyBox init + eudev，下载 `disk.img.xz` 和 `SHA256SUMS`。
- 新增裁剪 systemd 258.7 + BusyBox 工具版本，下载 `systemd-disk.img.xz` 和 `systemd-SHA256SUMS`。保留原版，未加入 GNU coreutils。
- 两版均为 64 MiB EFI + 1 GiB 可写 ext4 根分区，镜像只能选择其一写入同一设备。
- 启动需要关闭 Secure Boot；首次在本地终端设置 root 密码。
- HTTPS 默认转发至 `127.0.0.1:8080`；支持自定义证书或设备本地生成自签名证书，无需公网服务。配置命令见 README；出厂镜像不含私钥，未配置证书时入口不启动。
- 两版分别通过镜像检查、两次 QEMU／OVMF UEFI 冷启动、密码登录、DHCP／IPv4LL／HTTP、设备重枚举恢复、TLS 1.2／1.3、证书校验及持久化测试。systemd 版额外验证原生服务及 HTTPS 进程被终止后的自动恢复。
- systemd 版关闭容器、休眠、远程日志及其他可选服务，journald 使用最多 8 MiB 的内存日志。启动计时记录于各自的 validation.json，仅代表 QEMU TCG 测试环境。
- 实际核显 DP 输出、USB4 链路和网卡吞吐／热插拔仍需在最终硬件上验收。

附内核、实际构建配置、包清单、构建脚本源码、对应上游源码／补丁／许可证及 SHA256 校验文件。完整使用说明见 README.md。
NOTES
# Failed uploads leave an unpublished draft; an existing release is never overwritten.
gh release create "$version" "${assets[@]}" --verify-tag --draft \
    --title "ZXS Linux $version" --notes-file "$notes"
gh release edit "$version" --draft=false --latest
gh release view "$version" --json url --jq .url
