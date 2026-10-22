# ZXS Linux base

独立的 amd64／UEFI 精简 Linux。当前版本不包含 ZXS 或反向代理。

## 组成

- Buildroot **2026.08**、Linux **6.18.58**、Bootlin x86-64 glibc stable **2025.08-1**。
- BusyBox init、eudev、IPv4 DHCP／IPv4LL、curl／CA 证书、iproute2、ethtool、lspci、lsusb。
- Intel i915／Xe、AMD AMDGPU 的 KMS 与固件，DP 本地字符终端；没有桌面、音频、Wi-Fi 或蓝牙栈。
- 雷电／USB4 网络及 PCIe 热插拔；常见 Intel、Realtek、Aquantia、Broadcom PCIe 网卡，以及 Realtek、ASIX、CDC NCM、AQC111 USB 网卡驱动。
- 网口独立使用 IPv4 DHCP，无 DHCP 的点对点链路回退至 169.254/16 链路本地地址；不启用桥接、IP 转发、NAT 或 DHCP 服务端。

硬件型号未定，因此同时保留 Intel／AMD 核显固件。确定设备型号后，可在 `br2-external/configs/zxs_x86_64_defconfig` 与 `board/linux.fragment` 中裁剪。驱动被编译不代表所有设备型号已经实机验证。

## 构建

使用 x86_64 Debian 13，准备至少 **10 GiB 可用磁盘**（建议 20 GiB）、4 GiB 内存。脚本默认 `JOBS=4`。host 安装使用 Debian 签名索引和软件包摘要，解包到本项目 `.cache/host`，不需要 root。

```sh
./build.sh plan       # 查看版本、布局、并行度和目录
./build.sh setup      # 安装本地构建工具、QEMU、OVMF；核验 Buildroot 签名和 SHA256
./build.sh fetch      # 下载所有固定版本源码并校验 Buildroot 所列摘要
./build.sh build      # 编译并生成 GPT/UEFI 磁盘镜像
./build.sh verify     # 检查启动媒体、驱动、固件、工具及出厂账号状态
./build.sh test       # QEMU/OVMF 启动两次，验证初始化、登录、网络和持久化
./build.sh package    # 只打包通过当前镜像验证的产物
./build.sh legal-info # 汇集上游源码、补丁和许可证，补齐外部工具链组件源码

./build.sh all        # 完成上述设置、编译、验证和交付流程
```

日志保存在 `.work/logs/`；输出在 `.work/output/images/`；测试报告在 `.work/results/`。可用 `ZXS_CACHE_DIR`、`ZXS_WORK_DIR`、`JOBS` 覆盖缓存、输出根目录及并行度。重复构建保留有效下载和编译输出；修改工具链或基础库配置时应使用新的 `ZXS_WORK_DIR` 完整构建。

网络需要 HTTPS 访问 `deb.debian.org`、`security.debian.org`、`buildroot.org`、`sources.buildroot.net`、`toolchains.bootlin.com`、`cdn.kernel.org` 及各软件包在 Buildroot 中记录的源站。继承环境代理与 CA，不关闭 TLS、签名或摘要验证。发布公钥指纹为 `AB07 D806 D2CE 741F B886 EE50 B025 BA8B 59C3 6319`；源码包 SHA256 同时固定在构建入口。

## 镜像及首次启动

GPT 布局为 **64 MiB EFI 分区 + 1 GiB 可写 ext4 根分区**。GRUB 使用固定 PARTUUID 寻找根分区，不依赖 `/dev/sda`／`/dev/nvme0n1` 等设备名。此布局是硬件未定时的默认值，可在 `board/genimage.cfg` 和 defconfig 中调整。

通过 x86_64 UEFI 从磁盘镜像启动，启动要求 Secure Boot 关闭；本地屏幕使用 `tty1`，另有 `115200 8N1` 的 `ttyS0` 调试终端。首次启动必须在本地终端设置 root 密码，随后使用 `root` 和该密码登录；密码及配置保存在根分区。镜像没有 SSH、Telnet 或 Web 管理服务。

交付目录 `dist/zxs-linux-amd64/` 包含：

- `disk.img.xz`：压缩磁盘镜像，解压得到 `disk.img`；构建脚本不写入任何物理磁盘。
- `bzImage`、实际 Buildroot／Linux 配置及 `packages.json` 版本／许可清单。
- `validation.json`、`SHA256SUMS` 和本工程的 `build-sources.tar.gz`。

`./build.sh test` 对镜像副本执行 QEMU TCG／OVMF UEFI 冷启动、首次密码设置、密码登录、帧缓冲控制台绑定与终端写入、IPv4 DHCP、到宿主测试服务的 HTTP 请求、独立 e1000e 网卡的 IPv4LL 回退、关闭转发及第二次冷启动后的数据持久化检查。出厂镜像不包含测试密码或初始化状态。测试必须成功，且镜像摘要仍一致，才允许 `package`。

核显 DP 输出、真实雷电／USB4 链路、PCIe／USB 网卡吞吐及热插拔需在最终硬件上验收。雷电 PCIe 隧道的授权由硬件安全设置控制；系统不自动授权未知设备。

内核、BusyBox、工具和固件遵循各自上游许可证，`packages.json` 列出所选包。`./build.sh legal-info` 生成 `source-licenses.tar.gz`，包含 Buildroot 收集的包源码、应用补丁与许可证，以及固定摘要的 Buildroot 源码和 Bootlin SDK 全部组件的源码、应用补丁、构建配置及许可证。它同时更新交付目录中的 `SHA256SUMS`。

## 发布

已验证镜像的发布入口为 `scripts/publish-release.sh v0.1.0`，需要已认证的 GitHub CLI。先提交源码、创建指向当前提交的版本标签并推送，再执行 `./build.sh package` 和 `./build.sh legal-info`。发布脚本核验所有交付文件的 SHA256 与测试报告，上传全部文件到草稿，上传成功后才公开 Release；已有版本不会被覆盖。

GitHub Actions 的 `Build and publish release` 流程会在推送 `v*` 标签时自动执行完整构建、镜像检查、两次 UEFI 启动测试、打包和发布；失败时保留日志，公开 Release 前必须完成全部附件上传。流程在 Debian 13 容器中构建，构建任务只有读取仓库权限，独立发布任务使用 GitHub 内置令牌。也可在 Actions 页面手动运行，指定一个已存在且尚未发布的版本标签。

```sh
git tag -a v0.2.0 -m 'ZXS Linux v0.2.0'
git push origin main v0.2.0
```

`Supplement existing release toolchain sources` 手动流程可为早期发布附加完整 SDK 源码和许可证（含独立 SHA256）；新版本的常规 `legal-info` 已将这些组件一并收集。
