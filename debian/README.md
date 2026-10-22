# Debian 13：Intel Core Ultra 9 288V 配置

基于 Debian **13 stable（trixie）／amd64／UEFI**，面向 Intel Core Ultra 9 288V、Xe 核显和 NVMe 系统盘。不包含 ZXS 应用。保留官方 systemd、GNU coreutils、dash、APT／dpkg、Bash／Perl、initramfs 和 GRUB 更新工具，关闭自动更新。两套 Buildroot 镜像继续使用原配置。

## 硬件与系统配置

- 保留 Lunar Lake Xe 显示、GuC／HuC／GSC／DMC 固件及 Intel 06-bd CPU 微码，本地 tty1 字符终端和 USB 键盘；不提供串口终端。微码选择独立于构建机 CPU。
- 系统盘为 NVMe；保留 ext4／Btrfs、USB 存储救援、少量虚拟磁盘／显示驱动。移除 SATA、其他显卡、声卡、无线／蓝牙、GNSS 定位及无关网络驱动模块与固件；移除 PS/2、光驱／软驱／并口、物理串口以及 UHCI／OHCI／EHCI 等旧 USB 主控制器模块。现代 xHCI 继续支持 USB 2／3 键盘与存储设备。
- 网络仅保留 `thunderbolt-net`，用于两台设备通过雷电／USB4 线直连。开机加载协议，dhcpcd 为 `thunderbolt*` 接口配置 IPv4 DHCP 或 IPv4LL；没有 DHCP 服务端。两端均无 DHCP 时，使用各自 `169.254.*.*` 地址通信。
- 仅启用 IPv4：内核命令行 `ipv6.disable=1`；关闭转发，没有 Linux 软件网桥或 NAT。雷电扩展坞 RJ45 网口不在本配置支持范围内。
- 时区与硬件时钟采用 UTC。不安装 GeoClue／gpsd、桌面、SSH、NetworkManager、Wi-Fi／蓝牙用户态服务或 sudo。
- `zxs-https` 管理自定义证书，stunnel 监听 443 并转发至 `127.0.0.1:8080`；出厂没有私钥，未配置证书时不启动入口。用法见主 README。
- 保留 e2fsprogs、btrfs-progs、util-linux、fdisk，以及 nvme-cli、smartmontools、pciutils、usbutils、ethtool、procps、strace、tcpdump、curl 等诊断工具。智能磁盘监控守护进程和自动维护定时器默认关闭。
- journald 日志持久保存，配置磁盘上限 64 MiB、保留空闲空间 128 MiB，单文件上限 8 MiB；上限回收按 journal 文件粒度执行。

使用 Debian 官方通用内核，不修改已签名的内核或模块二进制；仅按 `modules.keep`、`modules.deny` 和 `firmware.keep` 删除无关文件并保留依赖闭包。内核内建功能仍由 Debian 配置决定：IPv6 通过启动参数禁用；`8250.nr_uarts=0 i8042.nokbd i8042.noaux` 关闭内建 UART 端口及 PS/2 键鼠探测。GRUB 和 Linux 使用本地屏幕，`serial-getty@.service` 被屏蔽；不重编译官方内核来删除其内建代码。保留完整包数据库；`dpkg --verify` 可能报告按硬件策略主动删除的包文件，策略及保留模块清单见 `/etc/zxs/hardware/` 与 `/var/lib/zxs/hardware/`。

## 构建与验证

需要 x86_64 Linux、可用的本机 Docker、Python 3、至少 12 GiB 可用空间。bootstrap 容器需要 privileged 来建立 guest 的 proc／dev 挂载；磁盘操作只针对项目内普通镜像文件。

```sh
./debian-build.sh plan
./debian-build.sh setup
./debian-build.sh check
./debian-build.sh build
./debian-build.sh verify
./debian-build.sh test
./debian-build.sh package
./debian-build.sh legal-info
# 或执行全部阶段
./debian-build.sh all
```

默认 `.work/debian/` 保存输出、日志、测试副本和报告，`dist/zxs-linux-amd64-debian/` 保存交付文件。构建重新创建 rootfs，避免旧包残留。基础构建层来自 Debian 官方 debuerreotype 发布仓库，固定提交、大小与 SHA256 记录在 `bootstrap.json`。APT 验证官方 stable、安全及稳定更新仓库的签名和包摘要；会话 CA 仅用于构建，不进入设备信任库。包版本及硬件策略摘要记录在交付清单，重新构建会获取当前稳定版更新。

验证检查出厂账号、分区内容和摘要、包管理链、驱动依赖、目标固件和微码、UTC、IPv4 及自动更新禁用配置。QEMU／OVMF 使用未经修改的出厂磁盘副本和 NVMe 根盘实际冷启动两次，关闭 QEMU 物理串口，通过屏幕识别和 USB 键盘完成首次密码设置及登录，验证本地控制台、实际 USB HID 按键、网络、HTTPS、udev 恢复、持久日志，以及离线 APT 安装／重启／卸载。屏幕识别工具仅安装在构建容器，不进入设备镜像。APT 测试会实际重建 initramfs 和 EFI 启动器。

QEMU 不模拟雷电网络。测试所需虚拟网卡和诊断字符通道模块放在独立只读测试介质中，经本地控制台登录后注入可丢弃的测试副本。诊断通道只用于自动执行检查，不配置为系统控制台或登录终端；交付镜像不含这些测试模块及服务。ext4／Btrfs 测试只操作新建的虚拟数据盘，覆盖 GPT、UUID 挂载、ext4 检查和扩容、Btrfs 快照／scrub／只读检查及跨启动数据持久化。实机 288V Xe DP 输出、NVMe、USB 设备、雷电链路／热插拔和启动速度仍须在最终设备上验收。

## 部署与手动更新

GPT 默认 **64 MiB EFI + 1 GiB 可写 ext4 根分区**；Btrfs 用于数据盘。启动需要 x86_64 UEFI 并关闭 Secure Boot。首次在本地终端设置 root 密码，之后用 root 登录。根分区可按 NVMe 容量手动扩容。

保留完整包管理链，禁用 `apt-daily` 与 `apt-daily-upgrade` 的服务和定时器，不安装 unattended-upgrades。只有手动执行命令才更新：

```sh
apt-get update
apt-get upgrade
```

雷电接口需要连接到可访问仓库的 IPv4 网络才能下载；没有网关的两机直连仅支持局域网通信。`/boot/efi` 按 UUID 挂载。APT 事务结束后自动重新应用硬件裁剪策略、生成 initramfs 和 EFI 启动器；内核 postinst 也会重新裁剪该内核模块。直接使用 `dpkg -i` 安装固件等包后，执行 `zxs-post-package-update` 应用策略和更新启动器。修改驱动范围前同时修改硬件策略；否则更新钩子会再次移除策略外的驱动。

1 GiB 根分区只为精简系统预留空间；大量日志、额外软件或保留多个内核前应扩容。升级前检查 `df -h / /boot/efi`，保留救援介质并备份配置、密码状态、证书和数据。本工程不提供 A/B 回滚或自动数据迁移。

排障可使用 `systemctl --failed`、`journalctl -b`、`journalctl -b -1`、`dmesg`、`lspci -nnk`、`lsusb`、`nvme list`、`smartctl` 和 `ip -4 addr`。ext4 修复前卸载对应分区，根分区离线修复需要独立启动介质。Btrfs 日常使用 `scrub`，离线诊断使用 `btrfs check --readonly`；`--repair` 不作为常规修复命令。

交付文件统一使用 `debian-` 前缀，包括镜像、内核、initrd、配置、清单、验证报告、校验文件、本工程源码及对应 Debian 源码／补丁／许可证。GitHub 标签发布会构建三套配置，全部验证通过后统一公开 Release。刷机只需下载 `debian-disk.img.xz` 和 `debian-SHA256SUMS`。
