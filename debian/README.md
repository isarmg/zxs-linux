# Debian 13：Intel Core Ultra 9 288V 配置

基于 Debian **13 stable（trixie）／amd64／UEFI**，面向 Intel Core Ultra 9 288V、Xe 核显和 NVMe 系统盘。不包含 ZXS 应用。保留官方 systemd、GNU coreutils、dash、APT／dpkg、Bash／Perl、initramfs 和 GRUB 更新工具，关闭自动更新。两套 Buildroot 镜像继续使用原配置。

## 硬件与系统配置

- 保留 Lunar Lake Xe 显示、GuC／HuC／GSC／DMC 固件及 Intel 06-bd CPU 微码，本地 tty1 字符终端和 USB 键盘；不提供串口终端。微码选择独立于构建机 CPU。
- 系统盘为 NVMe；保留 ext4／Btrfs、USB 存储救援、少量虚拟磁盘／显示驱动。移除 SATA、其他显卡、声卡、无线／蓝牙、GNSS 定位及无关网络驱动模块与固件；移除 PS/2、光驱／软驱／并口、物理串口以及 UHCI／OHCI／EHCI 等旧 USB 主控制器模块。现代 xHCI 继续支持 USB 2／3 键盘与存储设备。
- 网络仅保留 `thunderbolt-net`，用于两台设备通过雷电／USB4 线直连。开机加载协议，dhcpcd 为 `thunderbolt*` 接口配置 IPv4 DHCP 或 IPv4LL；没有 DHCP 服务端。两端均无 DHCP 时，使用各自 `169.254.*.*` 地址通信。
- 仅启用 IPv4：内核命令行 `ipv6.disable=1`；关闭转发，没有 Linux 软件网桥或 NAT。雷电扩展坞 RJ45 网口不在本配置支持范围内。
- 时区与硬件时钟采用 UTC。不安装 GeoClue／gpsd、桌面、SSH、NetworkManager、Wi-Fi／蓝牙用户态服务或 sudo。
- `zxs-https` 管理自定义证书，stunnel 监听 443 并转发至 `127.0.0.1:8080`；出厂没有私钥，未配置证书时不启动入口。用法见主 README。
- 保留 e2fsprogs、btrfs-progs、util-linux、fdisk，以及 nvme-cli、smartmontools、pciutils、usbutils、ethtool、procps、strace、tcpdump、curl 等诊断工具。没有常驻磁盘监控进程；每周 TRIM 不补跑错过的任务，每日检查证书到期、明显异常的 UTC 年份和 NVMe 健康，仅在日志中提示，不更新软件或替换证书。
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

验证检查出厂账号、分区内容和摘要、包管理链、驱动依赖、目标固件和微码、UTC、IPv4 及自动更新禁用配置。QEMU／OVMF 使用出厂磁盘副本和 NVMe 根盘启动，关闭物理串口，通过屏幕识别和 USB 键盘完成首次密码设置及登录。前两次冷启动验证网络、HTTPS、udev 恢复、持久日志、离线 APT 安装／卸载、真实官方内核包重装、固件裁剪及诊断包。普通包在未挂载 EFI 时安装且不改启动文件；启动包在此情形下须提前拒绝；内核重装须仅创建一次 initramfs。第三次通过 GRUB 一次性入口启动恢复副本，故意隐藏当前内核链接和模块，验证独立恢复、USB 登录和只读模块／固件挂载。屏幕识别工具仅安装在构建容器，不进入设备镜像。

QEMU 不模拟雷电网络。测试所需虚拟网卡和诊断字符通道模块放在独立只读测试介质中，经本地控制台登录后注入可丢弃的测试副本。诊断通道只用于自动执行检查，不配置为系统控制台或登录终端；交付镜像不含这些测试模块及服务。ext4／Btrfs 测试只操作新建的虚拟数据盘，覆盖 GPT、UUID 挂载、ext4 检查和扩容、Btrfs 快照／scrub／只读检查及跨启动数据持久化。实机 288V Xe DP 输出、NVMe、USB 设备、雷电链路／热插拔和启动速度仍须在最终设备上验收。

## 部署与手动更新

GPT 默认 **64 MiB EFI + 4 GiB 可写 ext4 根分区**；Btrfs 用于数据盘。空闲块保持可压缩，分区容量不等于下载体积。启动需要 x86_64 UEFI 并关闭 Secure Boot。首次在本地终端设置 root 密码，之后用 root 登录。不自动占满 NVMe 或改动其他分区；需要更大根分区时，在备份后从救援介质检查布局，扩展第 2 分区并运行 `resize2fs`，保持起始扇区和 UUID 不变。

保留完整包管理链，禁用 `apt-daily` 与 `apt-daily-upgrade` 的服务和定时器，不安装 unattended-upgrades。只有手动执行命令才更新：

```sh
apt-get update
apt-get upgrade
```

雷电接口需要连接到可访问仓库的 IPv4 网络才能下载；没有网关的两机直连仅支持局域网通信。`/boot/efi` 按 UUID 挂载。APT 按事务中的包名预检启动相关更新，在 dpkg 改动前检查 EFI 并保存已经启动过的内核。普通包不要求 EFI 挂载，也不生成 initramfs 或 EFI。`aa-zxs-hardware` 在 Debian 自带的 initramfs 内核钩子之前裁剪模块；固件／硬件策略变化时重建 initramfs，只有 GRUB 包或引导配置变化时重建 EFI。内核预安装钩子也覆盖直接安装内核的情况。

直接使用 `dpkg -i` 安装固件前运行 `zxs-prepare-boot-update`，之后执行 `zxs-post-package-update`。手动更改硬件策略前也先运行准备命令；需要强制刷新时使用 `zxs-post-package-update --force`。修改驱动范围时同步修改硬件策略，否则更新钩子会再次移除策略外的驱动。升级前检查 `df -h / /boot/efi`，保留救援介质，另行备份配置、密码状态、证书和数据。

## 本地恢复与诊断

```sh
zxs-recovery status
zxs-recovery save       # 手动建立当前已启动内核的检查点
zxs-recovery boot-once  # 仅安排下一次启动，不自动重启
zxs-diagnose           # 输出权限为 0600 的诊断 tar.gz 路径
```

启动相关 APT 事务自动保存当前已启动内核、initrd 及其模块／固件。校验 `/run/zxs-booted.sha256`，避免把尚未启动的新文件覆盖到恢复副本；先同步完整新副本，再替换恢复链接。出厂镜像不重复存放内核，首次更新前创建副本；成功启动新内核后，下一次启动相关更新可将其保存为新检查点。

GRUB 保留 1 秒默认启动等待，可在菜单选择 `ZXS preserved kernel and drivers`；也可提前使用 `boot-once` 安排恢复启动。正常内核另有 `local rescue` 入口，使用本地 root 密码。恢复模式从 initramfs 只读挂载保存的模块和固件，不依赖当前内核包仍保留这些文件。恢复模式下禁止启动相关包事务，以保护副本；修复启动包请使用当前内核的 rescue 入口或独立救援介质。检查点仅覆盖内核及配套文件，不回滚整个用户态、配置或数据；根分区损坏仍需要救援介质，不提供 A/B 或自动重启回滚。

`zxs-diagnose` 汇总限量的本次／上次 journal、内核日志、失败服务、启动耗时及关键链、版本、磁盘空间、IPv4、USB／PCI／雷电和 NVMe 健康、证书公开信息。命令有超时，不收集密码数据库或私钥文件；日志仍可能包含应用消息、IP、设备序列号，分享前检查内容。诊断包默认写到 `/var/tmp`，导出后可删除。

排障可使用 `systemctl --failed`、`journalctl -b`、`journalctl -b -1`、`dmesg`、`lspci -nnk`、`lsusb`、`nvme list`、`smartctl` 和 `ip -4 addr`。ext4 修复前卸载对应分区，根分区离线修复需要独立启动介质。Btrfs 日常使用 `scrub`，离线诊断使用 `btrfs check --readonly`；`--repair` 不作为常规修复命令。

## 局域网配置和实机验收

两机固定地址可减少网络就绪等待，方便绑定自定义证书；它不改变系统启动是否等待网络。先用 `ip -br link` 确认接口名，在 `/etc/dhcpcd.conf` 末尾增加，例如：

```conf
interface thunderbolt0
static ip_address=192.168.88.1/30
```

另一台使用 `192.168.88.2/30`，选择与现有网络不冲突的地址。纯直连不配置默认网关或 DNS。本地执行 `systemctl restart dhcpcd` 应用。默认仍使用 DHCP／IPv4LL，没有新增网络管理进程。

系统仍使用 UTC，未启用联网校时。部署时从可信时钟设置 `date -u -s 'YYYY-MM-DD HH:MM:SS'`，用 `hwclock --systohc --utc` 写回 RTC；离线设备定期核对时钟。需要局域网 NTP 时再配置可信的局域网服务端和校时客户端。证书告警可用 `journalctl -t zxs-health` 查看；每日检查不会续期证书。

实机先保存 `systemd-analyze time`、`systemd-analyze critical-chain` 和诊断包，记录断电冷启动／重启到 tty1 以及网络可用的时间，检查 DP、USB 键盘／存储和雷电热插拔、断线重连、NVMe 温度／错误。当前保留 `MODULES=most`、gzip、fsck、CPU 微码及电源／散热管理；未获得 288V 实机数据前不改用构建机推导的模块列表或删除恢复工具。

交付文件统一使用 `debian-` 前缀，包括镜像、内核、initrd、配置、清单、验证报告、校验文件、本工程源码及对应 Debian 源码／补丁／许可证。main 分支运行构建验证并预热默认分支缓存，供后续标签构建读取；标签仍完整验证三套配置，全部通过后统一公开 Release，不直接复用旧版本验证报告。相同提交的分支／标签工作流串行运行，避免同时冷编译。刷机只需下载 `debian-disk.img.xz` 和 `debian-SHA256SUMS`。基础镜像、实际包版本和策略摘要均记录在清单中；APT 仍获取当前 stable 更新，不宣称按位可重复构建。
