# ZXS Linux base

独立的 amd64／UEFI 精简 Linux。当前版本不包含 ZXS；系统通过 stunnel 提供局域网 HTTPS。

额外提供 [Debian 13 最小化版本](debian/README.md)：面向 Intel Core Ultra 9 288V／Xe、NVMe、雷电／USB4 两机直连；使用官方 systemd 和 GNU 工具，保留完整 apt／dpkg、关闭自动更新，采用 UTC、仅 IPv4，包含 ext4／Btrfs 和排障工具及持久日志。通过 `./debian-build.sh all` 构建，交付文件使用 `debian-` 前缀。下面的 Buildroot 配置与两套原有镜像继续保留。

## 组成

- Buildroot **2026.08**、Linux **6.18.58**、Bootlin x86-64 glibc stable **2025.08-1**。
- 默认版本：BusyBox init + eudev；额外提供裁剪 **systemd 258.7 + BusyBox 工具**版本，两版均使用 BusyBox Shell 和基础命令。
- IPv4 DHCP／IPv4LL、curl／CA 证书、OpenSSL／stunnel、iproute2、ethtool、lspci、lsusb。
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

ZXS_VARIANT=systemd ./build.sh all
ZXS_VARIANT=systemd ./build.sh legal-info
```

日志保存在 `.work/logs/`；输出在 `.work/output/images/`；测试报告在 `.work/results/`。可用 `ZXS_CACHE_DIR`、`ZXS_WORK_DIR`、`JOBS` 覆盖缓存、输出根目录及并行度。重复构建保留有效下载和编译输出；修改工具链或基础库配置时应使用新的 `ZXS_WORK_DIR` 完整构建。

`ZXS_VARIANT=busybox` 是默认值。systemd 版的输出、日志、报告位于 `.work/systemd/`，交付目录为 `dist/zxs-linux-amd64-systemd/`，文件名统一使用 `systemd-` 前缀。两版共享下载缓存，编译输出相互独立；不能把两种 init 的产物混在一个输出目录中。覆盖 `ZXS_WORK_DIR` 时必须为不同版本选择不同目录。两版并存建议准备至少 30 GiB 可用磁盘。

systemd 版保留 PID 1、udev、journald、tmpfiles 和随机种子服务；使用原生 getty、dhcpcd 和 HTTPS 服务。关闭 networkd、resolved、timesyncd、登录会话管理、容器、虚拟机、休眠、远程日志、coredump、hwdb 等可选功能。journald 使用最多 8 MiB 的内存日志。必要依赖包含 D-Bus、kmod、部分 util-linux、libcap、libxcrypt 和 ext4 检查工具；未添加 GNU coreutils。Buildroot 为 systemd 内核启用 IPv6 支持，部署时通过 `ipv6.disable=1` 关闭，两个版本均只提供 IPv4 端点。

两版完成 `build → verify → test → package` 后，可运行 `python3 scripts/compare-variants.py` 比较压缩镜像大小及启动观测值，结果保存为 `dist/variant-comparison.json`。测试采用相同 QEMU TCG／q35、2 vCPU、2 GiB 内存设置，分别记录首次出厂控制台提示和已初始化后的登录提示时间。应在同一空闲宿主上依次测试；这两次观测不能替代实机启动基准。`login_mem_available_kib` 是登录后采样的 guest MemAvailable，不是服务的 RSS。

网络需要 HTTPS 访问 `deb.debian.org`、`security.debian.org`、`buildroot.org`、`sources.buildroot.net`、`toolchains.bootlin.com`、`cdn.kernel.org` 及各软件包在 Buildroot 中记录的源站。继承环境代理与 CA，不关闭 TLS、签名或摘要验证。发布公钥指纹为 `AB07 D806 D2CE 741F B886 EE50 B025 BA8B 59C3 6319`；源码包 SHA256 同时固定在构建入口。

## 镜像及首次启动

GPT 布局为 **64 MiB EFI 分区 + 1 GiB 可写 ext4 根分区**。GRUB 使用固定 PARTUUID 寻找根分区，不依赖 `/dev/sda`／`/dev/nvme0n1` 等设备名。此布局是硬件未定时的默认值，可在 `board/genimage.cfg` 和 defconfig 中调整。

通过 x86_64 UEFI 从磁盘镜像启动，启动要求 Secure Boot 关闭；本地屏幕使用 `tty1`，另有 `115200 8N1` 的 `ttyS0` 调试终端。首次启动必须在本地终端设置 root 密码，随后使用 `root` 和该密码登录；密码及配置保存在根分区。镜像没有 SSH、Telnet 或 Web 管理应用；配置证书后可启用 HTTPS 转发入口。

## 局域网 HTTPS

系统负责 TLS，链路为 `客户端 → HTTPS :443 → stunnel → HTTP 127.0.0.1:8080 → ZXS`。ZXS 只需监听本机 HTTP 端口；当前基础镜像不包含 ZXS，必须另行安装并启动应用。stunnel 使用 TLS 1.2／1.3，读取证书和绑定端口后以 nobody 用户运行。无需公网域名、ACME 或互联网证书服务。

在设备本地以 root 登录，导入自定义 PEM 证书链和无密码私钥：

```sh
zxs-https install /path/server-chain.pem /path/server-key.pem
```

也可以在设备上生成有效期 365 天的独立自签名证书。以下名称和地址须替换为实际值，至少提供一个 `DNS:` 或 `IP:` 参数：

```sh
zxs-https generate DNS:zxs.lan IP:192.168.1.50
zxs-https certificate > /path/server.crt  # 仅导出公有证书
```

命令会检查证书是否过期、私钥是否有效且匹配，再原子替换凭据并重启服务。凭据保存在 `/etc/zxs/https/server.pem`（目录 0700、文件 0600），冷启动后自动恢复 HTTPS。每台设备本地生成自己的私钥，出厂镜像不含证书或私钥；未配置凭据时不会启动 HTTPS。到期前重新执行生成或导入命令完成更新。

客户端需要信任该自签名证书，或者信任签发自定义证书的 CA。证书 SAN 必须包含访问时使用的域名／IP；建议在 DHCP 中保留设备地址。使用域名时需配置局域网 DNS 或客户端 hosts，系统不自动发布该域名。签发、检查及访问证书前应确保系统时间正确。

默认端口可在 `/etc/stunnel/stunnel.conf` 调整。BusyBox init 版修改后执行 `/etc/init.d/S50stunnel restart`；systemd 版执行 `systemctl restart zxs-https.service`，使用 `journalctl -u zxs-https.service` 查看日志。证书管理命令会自动选择对应的服务管理器。保持后端使用 `127.0.0.1`。stunnel 透传 TCP，不添加 HTTP 转发头；ZXS 如需生成绝对链接或重定向，应将应用的外部访问地址配置为实际 HTTPS 地址。

交付目录 `dist/zxs-linux-amd64/` 包含：

- `disk.img.xz`：压缩磁盘镜像，解压得到 `disk.img`；构建脚本不写入任何物理磁盘。
- `bzImage`、实际 Buildroot／Linux 配置及 `packages.json` 版本／许可清单。
- `validation.json`、`SHA256SUMS` 和本工程的 `build-sources.tar.gz`。

`./build.sh test` 对镜像副本执行 QEMU TCG／OVMF UEFI 冷启动、首次密码设置、密码登录、帧缓冲控制台绑定与终端写入、IPv4 DHCP、到宿主测试服务的 HTTP 请求、独立 e1000e 网卡的 IPv4LL 回退、关闭转发、延迟 udev 启动后的设备重枚举与网络恢复，以及第二次冷启动后的数据持久化检查。它还验证无证书时跳过 HTTPS、证书生成与导入、拒绝不匹配私钥、TLS 1.2／1.3 到本机 HTTP 的实际请求、DNS／IP SAN、拒绝未知证书和错误域名、服务降权、权限及证书跨重启持久化。HTTP 测试后端只下载到测试镜像副本。出厂镜像不包含测试密码、私钥或初始化状态。测试必须成功，且镜像摘要仍一致，才允许 `package`。

systemd 版在设备服务停止期间触发设备事件，再重启原生 coldplug 单元来测试设备重枚举；另检查无失败单元、D-Bus 实际调用、machine-id 持久化，以及 HTTPS 主进程被 SIGKILL 终止后的自动恢复。

核显 DP 输出、真实雷电／USB4 链路、PCIe／USB 网卡吞吐及热插拔需在最终硬件上验收。雷电 PCIe 隧道的授权由硬件安全设置控制；系统不自动授权未知设备。

内核、BusyBox、工具和固件遵循各自上游许可证，`packages.json` 列出所选包。`./build.sh legal-info` 生成 `source-licenses.tar.gz`，包含 Buildroot 收集的包源码、应用补丁与许可证，以及固定摘要的 Buildroot 源码和 Bootlin SDK 全部组件的源码、应用补丁、构建配置及许可证。它同时更新交付目录中的 `SHA256SUMS`。

## 发布

已验证镜像的发布入口为 `scripts/publish-release.sh v0.5.0`，需要已认证的 GitHub CLI。先提交源码、创建指向当前提交的版本标签并推送，分别完成两套 Buildroot 的 `package`／`legal-info` 和 Debian 的对应阶段。发布脚本核验三个交付目录的 SHA256 与独立测试报告，上传全部文件到草稿，上传成功后才公开 Release；已有版本不会被覆盖。

GitHub Actions 的 `Build and publish release` 流程会在推送 `v*` 标签时，分别构建 BusyBox init、裁剪 systemd 和 Debian 三版，执行各自的镜像检查、两次 UEFI 启动测试和打包。三版均成功后才发布同一个 Release；失败时保留各自日志，公开 Release 前必须完成全部附件上传。每版都附带自己的源码／许可证档案；刷机只需下载所选版本的 `disk.img.xz` 和对应 `SHA256SUMS`，注意 systemd／Debian 的文件名前缀。流程在 Debian 13 容器中构建，构建任务只有读取仓库权限，独立发布任务使用 GitHub 内置令牌。也可在 Actions 页面手动运行，指定一个已存在且尚未发布的版本标签。

```sh
git tag -a v0.5.0 -m 'ZXS Linux v0.5.0: Debian 288V, NVMe, Thunderbolt IPv4 and manual APT updates'
git push origin main v0.5.0
```

`Supplement existing release toolchain sources` 手动流程可为早期发布附加完整 SDK 源码和许可证（含独立 SHA256）；新版本的常规 `legal-info` 已将这些组件一并收集。

BusyBox init 版在网络服务前等待 udev 控制应答，再重放冷插拔事件并等待队列完成，避免后台启动竞态导致网卡未初始化。systemd 版由原生设备单元和依赖关系处理冷插拔。CI 分开显示设置、编译、镜像检查、启动测试和打包阶段；按版本缓存下载与兼容的编译输出，配置或工具链变化会切换缓存。
