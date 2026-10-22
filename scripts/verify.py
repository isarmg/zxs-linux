#!/usr/bin/env python3
"""Check the built boot media, kernel capabilities and initial root filesystem."""
import hashlib
import struct
import subprocess
import sys
import uuid
import zlib

from common import KERNEL_VERSION, OUTPUT, VARIANT, artifact_identity, config, report, sha256


def require(condition, description):
    if not condition:
        raise RuntimeError(description)
    checks.append(description)


checks = []
try:
    images = OUTPUT / 'images'
    for name in ('disk.img', 'bzImage', 'rootfs.ext2', 'efi-part.vfat'):
        require((images / name).stat().st_size > 0, f'Built artifact exists: {name}')
    kernel = config(OUTPUT / 'build' / ('linux-' + KERNEL_VERSION) / '.config')
    for flag in ('EFI', 'EFI_PARTITION', 'EXT4_FS', 'SATA_AHCI', 'BLK_DEV_NVME',
                 'VIRTIO_BLK', 'VIRTIO_NET', 'VT_CONSOLE', 'FRAMEBUFFER_CONSOLE', 'DRM_SIMPLEDRM'):
        require(kernel.get('CONFIG_' + flag) == 'y', f'Built-in boot capability: {flag}')
    for flag in ('USB4', 'USB4_NET', 'DRM_I915', 'DRM_XE', 'DRM_AMDGPU',
                 'E1000E', 'IGB', 'IGC', 'IXGBE', 'I40E', 'I40EVF', 'ICE', 'R8169', 'AQTION',
                 'USB_RTL8152', 'USB_NET_AX88179_178A', 'USB_NET_CDC_NCM', 'USB_NET_AQC111'):
        require(kernel.get('CONFIG_' + flag) in ('m', 'y'), f'Hardware driver enabled: {flag}')
    for flag in ('BRIDGE', 'NETFILTER', 'WLAN', 'BT', 'SOUND'):
        require(kernel.get('CONFIG_' + flag, 'n') == 'n', f'Excluded kernel capability: {flag}')
    if VARIANT == 'busybox':
        require(kernel.get('CONFIG_IPV6', 'n') == 'n', 'IPv6 is excluded from the BusyBox kernel')
    modules = OUTPUT / 'target' / 'lib/modules' / KERNEL_VERSION
    for name in ('thunderbolt', 'thunderbolt_net', 'i915', 'xe', 'amdgpu', 'igc', 'r8169', 'r8152'):
        require(any(modules.rglob(name + '.ko*')), f'Installed kernel module: {name}')
    target = OUTPUT / 'target'
    shadow_root = (target / 'etc/shadow').read_text().splitlines()[0].split(':')[1]
    require(shadow_root == '*', 'Factory root account is locked until local password setup')
    require(not (target / 'etc/zxs/initialized').exists(), 'Factory image has not been initialized')
    require((target / 'usr/sbin/console-login').stat().st_mode & 0o111 != 0,
            'First-boot console initialization is executable')
    build_config = config(OUTPUT / '.config')
    require(build_config.get('BR2_PACKAGE_BUSYBOX') == 'y', 'BusyBox supplies shell and basic commands')
    require(build_config.get('BR2_PACKAGE_COREUTILS', 'n') == 'n', 'GNU coreutils is not added')
    if VARIANT == 'systemd':
        require(build_config.get('BR2_INIT_SYSTEMD') == 'y', 'Selected init matches the systemd variant')
        for binary in ('usr/lib/systemd/systemd', 'usr/bin/systemctl', 'usr/bin/journalctl',
                       'usr/bin/udevadm', 'usr/sbin/e2fsck'):
            require((target / binary).exists(), f'Installed systemd capability: {binary}')
        for feature in ('BOOT', 'NETWORKD', 'RESOLVED', 'TIMESYNCD', 'LOGIND', 'HOMED', 'MACHINED',
                        'NSPAWN', 'COREDUMP', 'JOURNAL_REMOTE', 'HWDB', 'VCONSOLE', 'PSTORE',
                        'HOSTNAMED', 'TIMEDATED', 'SYSUPDATE', 'PORTABLED'):
            require(build_config.get('BR2_PACKAGE_SYSTEMD_' + feature, 'n') == 'n',
                    f'Excluded optional systemd feature: {feature}')
        require(not (target / 'etc/inittab').exists(), 'Systemd variant excludes the SysV inittab')
        require(not (target / 'etc/init.d/S11udev-coldplug').exists(), 'Systemd owns device coldplug')
        require(not (target / 'etc/init.d/S50stunnel').exists(), 'Systemd owns the HTTPS service')
        service = (target / 'etc/systemd/system/zxs-https.service').read_text()
        require('Restart=on-failure' in service and 'ConditionPathExists=' in service,
                'Native HTTPS service restarts on failure and requires device credentials')
        journal = (target / 'etc/systemd/journald.conf.d/zxs.conf').read_text()
        require('Storage=volatile' in journal and 'RuntimeMaxUse=8M' in journal,
                'Journal storage is volatile and capped at 8 MiB')
        require((target / 'etc/machine-id').read_text().strip() == '', 'Factory machine ID is unset')
    else:
        require(build_config.get('BR2_INIT_BUSYBOX') == 'y', 'Selected init matches the BusyBox variant')
        require((target / 'etc/init.d/S11udev-coldplug').stat().st_mode & 0o111 != 0,
                'Coldplug readiness synchronization is installed and executable')
    for binary in ('sbin/dhcpcd', 'sbin/ip', 'usr/bin/curl', 'usr/bin/openssl', 'usr/bin/stunnel',
                   'usr/sbin/zxs-https'):
        require((target / binary).exists(), f'Installed user-space tool: {binary}')
    if VARIANT == 'busybox':
        require((target / 'etc/init.d/S50stunnel').stat().st_mode & 0o111 != 0,
                'System HTTPS initialization is executable')
    tls_config = (target / 'etc/stunnel/stunnel.conf').read_text()
    require(all(line in tls_config.splitlines() for line in
                ('sslVersionMin = TLSv1.2', 'accept = 0.0.0.0:443', 'connect = 127.0.0.1:8080',
                 'cert = /etc/zxs/https/server.pem', 'setuid = nobody', 'setgid = nobody')),
            'HTTPS terminates TLS 1.2+ and forwards to a loopback backend with dropped privileges')
    require(not (target / 'etc/zxs/https/server.pem').exists(), 'Factory HTTPS credentials are absent')
    require(not any(b'PRIVATE KEY-----' in p.read_bytes() for p in (target / 'etc').rglob('*')
                    if p.is_file()), 'Factory /etc contains no shared private key')
    require(not (target / 'root/zxs-test-http').exists(), 'HTTP test fixture is excluded from factory image')
    for firmware in ('amdgpu', 'i915', 'xe', 'rtl_nic'):
        require((target / 'lib/firmware' / firmware).is_dir(), f'Installed firmware family: {firmware}')
    require('zxs=absent' in (target / 'etc/zxs-release').read_text(), 'ZXS is excluded')
    for daemon in ('zxs', 'nginx', 'caddy', 'dropbear', 'sshd'):
        require(not any(p.name == daemon for sub in ('usr/bin', 'usr/sbin', 'sbin')
                        for p in (target / sub).glob('*')), f'No installed daemon: {daemon}')
    with (images / 'disk.img').open('rb') as stream:
        stream.seek(512)
        header = bytearray(stream.read(512))
        require(header[:8] == b'EFI PART', 'Disk has a GPT header')
        length, crc = struct.unpack_from('<II', header, 12)
        struct.pack_into('<I', header, 16, 0)
        require(zlib.crc32(header[:length]) == crc, 'GPT header checksum is valid')
        lba, count, size, array_crc = struct.unpack_from('<QIII', header, 72)
        stream.seek(lba * 512)
        entries = stream.read(count * size)
        require(zlib.crc32(entries) == array_crc, 'GPT partition table checksum is valid')
        root_uuid = str(uuid.UUID(bytes_le=entries[size + 16:size + 32]))
        require(root_uuid == '4d6a788b-e38d-41ef-8f60-f7594f08c402', 'Root partition matches the GRUB PARTUUID')
        for index, filename in ((0, 'efi-part.vfat'), (1, 'rootfs.ext2')):
            first_lba = struct.unpack_from('<Q', entries, index * size + 32)[0]
            remaining = (images / filename).stat().st_size
            stream.seek(first_lba * 512)
            digest = hashlib.sha256()
            while remaining:
                block = stream.read(min(remaining, 1024 * 1024))
                if not block:
                    raise RuntimeError(f'Disk partition is truncated: {filename}')
                digest.update(block)
                remaining -= len(block)
            require(digest.hexdigest() == sha256(images / filename), f'Disk partition contains the current {filename}')
    raw_shadow = subprocess.run([str(OUTPUT / 'host/sbin/debugfs'), '-R', 'cat /etc/shadow',
                                 str(images / 'rootfs.ext2')], check=True, capture_output=True, text=True).stdout
    require(any(line.startswith('root:*:') for line in raw_shadow.splitlines()),
            'Built ext4 image contains the locked factory root account')
    subprocess.run(['mdir', '-i', str(images / 'efi-part.vfat'), '::/EFI/BOOT/BOOTX64.EFI'],
                   check=True, capture_output=True)
    checks.append('FAT image contains the removable-media amd64 UEFI loader')
    grub = subprocess.run(['mtype', '-i', str(images / 'efi-part.vfat'), '::/EFI/BOOT/grub.cfg'],
                          check=True, capture_output=True, text=True).stdout
    require(root_uuid in grub, 'EFI boot configuration points to the built root partition')
    if VARIANT == 'systemd':
        require('ipv6.disable=1' in grub, 'Systemd kernel boots with IPv6 disabled')
    identity = artifact_identity()
    path = report('verify', {'status': 'passed', 'checks': checks, 'artifacts': identity})
    print(f'Image verification passed: {len(checks)} checks; {path}')
except Exception as error:
    report('verify', {'status': 'failed', 'checks_passed': checks, 'error': str(error)})
    print(f'Image verification failed: {error}', file=sys.stderr)
    sys.exit(1)
