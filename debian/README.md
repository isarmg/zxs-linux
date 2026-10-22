# Debian 最小化版本

独立的 Debian **13 stable（trixie）／amd64／UEFI** 固定功能镜像，不包含 ZXS 应用。使用官方 systemd、GNU coreutils、dash、glibc、通用内核和预生成 initramfs。设备端不含 apt、dpkg 或其他软件包安装工具；更新通过重新构建并交付镜像完成。两套 Buildroot 镜像仍使用原配置。

## 功能与裁剪

- Intel／AMD 核显固件、通用 PCIe／USB 有线网卡、雷电／USB4 内核驱动；本地 tty1 字符终端和 115200 8N1 串口。
- dhcpcd 为各有线网口独立配置 IPv4 DHCP／IPv4LL；关闭 IPv6 和 IP 转发，没有桥接、NAT 或 DHCP 服务端。
- e2fsprogs、btrfs-progs、util-linux：ext4 创建、检查、修复和扩容，Btrfs 创建、子卷、快照、scrub、send／receive，GPT 分区和 UUID 挂载。
- 沿用 `zxs-https`，stunnel 监听 443，转发至 `127.0.0.1:8080`。证书在设备上生成或导入，出厂没有私钥；用法见仓库主 README。
- 不安装桌面、SSH 服务、NetworkManager、Wi-Fi／蓝牙用户态服务、sudo；关闭不使用的网络管理服务和自动磁盘维护定时器。
- 不安装 Recommends／Suggests；构建完成后移除 apt、dpkg、包数据库、安装配置、Bash、Perl、GRUB 和 initramfs 生成工具、文档、翻译及缓存。预生成的启动器和 initramfs 保留，根用户使用 dash。
- 构建包清单和完整许可证随交付文件单独归档。官方通用内核与 Intel／AMD 固件用于硬件型号未定的设备；驱动按需加载。
- journald 使用内存日志，上限 8 MiB；没有额外常驻磁盘管理服务。

## 构建与验证

需要 x86_64 Linux、可用的本机 Docker、Python 3、至少 12 GiB 可用空间。构建容器中的 bootstrap 阶段需要 privileged 来建立 guest 的 proc／dev 挂载；所有磁盘操作只针对项目内普通镜像文件。

```sh
./debian-build.sh plan
./debian-build.sh setup
./debian-build.sh check
./debian-build.sh build
./debian-build.sh verify
./debian-build.sh test
./debian-build.sh package
./debian-build.sh legal-info
# 或依次执行全部阶段
./debian-build.sh all
```

默认 `.work/debian/` 保存输出、日志、测试副本和报告，`dist/zxs-linux-amd64-debian/` 保存交付文件。`ZXS_WORK_DIR` 可覆盖为仓库内的独立目录。构建重新创建 rootfs，避免已删除的软件包残留；不会改动两套 Buildroot 输出。

基础构建层来自 Debian 官方 debuerreotype 发布仓库，固定提交、大小与 SHA256 记录在 `bootstrap.json`。构建机使用 APT 从 `sources.list` 的 stable、安全和稳定更新仓库取包，验证发行签名及包摘要。构建继承代理及 CA；会话 CA 仅用于构建，不进入设备信任库。设备端不保留 APT。构建输入版本记录于 `debian-packages.json`，裁掉的组件列于 `debian-build.json`；该清单包含引导装配时使用的工具，不等同于设备端可用命令。重新构建会获取当前安全更新，未使用冻结的软件仓库快照。

验证包括镜像分区内容与摘要、UEFI 启动器、内核驱动、官方包与出厂账号；QEMU／OVMF 实际冷启动两次，检查首次密码设置、密码登录、字符控制台、DHCP／IPv4LL、HTTP、TLS 1.2／1.3、自定义证书校验及持久化、HTTPS 进程自动恢复和 udev 设备恢复。

磁盘测试仅操作 QEMU 新建的两块虚拟数据盘：GPT 分区、ext4 格式化、离线检查、扩容、UUID 挂载，Btrfs 子卷、只读快照、scrub、离线只读检查；第二次启动验证数据持久化。还验证 apt、dpkg、Bash、Perl 和 initramfs 生成工具均不存在。根分区或物理磁盘不会用于数据盘测试。实机 DP、USB4 链路、具体网卡和启动速度仍需在最终设备验收。

## 部署与更新

GPT 默认 **64 MiB EFI + 1 GiB 可写 ext4 根分区**。Btrfs 用于数据盘；本版没有把系统根分区改为 Btrfs。启动需要 x86_64 UEFI 并关闭 Secure Boot。首次在本地终端设置 root 密码，之后用 root 登录。

升级时在构建机重新执行完整构建和验证，交付新镜像。设备端不安装软件包、不重新生成内核或启动器。GRUB 读取镜像内 `/vmlinuz`、`/initrd.img`，EFI 分区无需在日常运行时挂载。覆盖系统镜像前另行备份设备配置、密码状态、证书及数据；当前工程不提供自动数据迁移或在线升级器。

ext4 修复前卸载对应分区；根分区离线修复需要独立启动介质。Btrfs 日常检查使用 `scrub`，离线诊断使用 `btrfs check --readonly`；`--repair` 不作为常规修复命令。损坏数据的修复依赖可用的冗余副本。

交付文件统一使用 `debian-` 前缀：压缩磁盘镜像、内核、initrd、内核配置、包版本清单、构建信息、验证报告、校验清单、本工程源码及对应 Debian 源码／补丁／许可证。源码收集覆盖所安装软件包和 Built-Using／Static-Built-Using 声明，包含签名内核对应的 Linux 源码；固件遵循各自许可证。

GitHub 标签发布会并行构建三套配置，全部验证通过后统一公开 Release。Debian 刷机下载 `debian-disk.img.xz` 和 `debian-SHA256SUMS`；源码／许可证档案用于审计和再分发，无需写入设备。
