#!/usr/bin/env python3
"""Check the built boot media, kernel capabilities and initial root filesystem."""
import hashlib
import struct
import subprocess
import sys
import uuid
import zlib

from common import KERNEL_VERSION, OUTPUT, artifact_identity, config, report, sha256


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
    for flag in ('IPV6', 'BRIDGE', 'NETFILTER', 'WLAN', 'BT', 'SOUND'):
        require(kernel.get('CONFIG_' + flag, 'n') == 'n', f'Excluded kernel capability: {flag}')
    modules = OUTPUT / 'target' / 'lib/modules' / KERNEL_VERSION
    for name in ('thunderbolt', 'thunderbolt_net', 'i915', 'xe', 'amdgpu', 'igc', 'r8169', 'r8152'):
        require(any(modules.rglob(name + '.ko*')), f'Installed kernel module: {name}')
    target = OUTPUT / 'target'
    shadow_root = (target / 'etc/shadow').read_text().splitlines()[0].split(':')[1]
    require(shadow_root == '*', 'Factory root account is locked until local password setup')
    require(not (target / 'etc/zxs/initialized').exists(), 'Factory image has not been initialized')
    require((target / 'usr/sbin/console-login').stat().st_mode & 0o111 != 0,
            'First-boot console initialization is executable')
    for binary in ('sbin/dhcpcd', 'sbin/ip', 'usr/bin/curl'):
        require((target / binary).exists(), f'Installed user-space tool: {binary}')
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
    identity = artifact_identity()
    path = report('verify', {'status': 'passed', 'checks': checks, 'artifacts': identity})
    print(f'Image verification passed: {len(checks)} checks; {path}')
except Exception as error:
    report('verify', {'status': 'failed', 'checks_passed': checks, 'error': str(error)})
    print(f'Image verification failed: {error}', file=sys.stderr)
    sys.exit(1)

