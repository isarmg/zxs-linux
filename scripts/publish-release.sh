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
destination="$ROOT/dist/zxs-linux-amd64"
names=(README.md build-sources.tar.gz source-licenses.tar.gz buildroot.config bzImage
       disk.img.xz linux.config packages.json validation.json SHA256SUMS)
assets=()
for name in "${names[@]}"; do
    [[ -s $destination/$name ]] || { echo "Missing release asset: $name" >&2; exit 1; }
    assets+=("$destination/$name")
done
(cd "$destination" && sha256sum --check --strict SHA256SUMS)
python3 - "$destination/validation.json" <<'PY'
import json,sys
report=json.load(open(sys.argv[1]))
if not (report['image']['status']==report['boot']['status']=='passed'
        and report['image']['artifacts']==report['boot']['artifacts']):
    raise SystemExit('Both image verification and boot tests must pass for the same image.')
PY
notes=$(mktemp)
trap 'rm -f -- "$notes"' EXIT
cat > "$notes" <<'NOTES'
amd64／UEFI 精简 Linux 基础系统，不包含 ZXS 或反向代理。

- Buildroot 2026.08、Linux 6.18.58；保留 Intel／AMD 核显、USB4／雷电及有线网卡驱动。
- 下载 `disk.img.xz` 及 `SHA256SUMS`，校验后解压。默认磁盘布局为 64 MiB EFI + 1 GiB 可写 ext4 根分区。
- 启动需要关闭 Secure Boot；首次在本地终端设置 root 密码。
- 已通过镜像检查及两次 QEMU／OVMF UEFI 冷启动、密码登录、DHCP、IPv4LL、HTTP、延迟 udev 后的冷插拔与网络恢复，以及数据持久化测试，报告见 `validation.json`。
- 实际核显 DP 输出、USB4 链路和网卡吞吐／热插拔仍需在最终硬件上验收。

附内核、实际构建配置、包清单、构建脚本源码、对应上游源码／补丁／许可证及 SHA256 校验文件。完整使用说明见 README.md。
NOTES
# Failed uploads leave an unpublished draft; an existing release is never overwritten.
gh release create "$version" "${assets[@]}" --verify-tag --draft \
    --title "ZXS Linux $version" --notes-file "$notes"
gh release edit "$version" --draft=false --latest
gh release view "$version" --json url --jq .url
