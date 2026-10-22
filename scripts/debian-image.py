#!/usr/bin/env python3
"""Build Debian from authenticated APT packages; all disks are regular files."""
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from common import DIST, OUTPUT, ROOT, WORK, artifact_identity, config, report, sha256

TARGET = OUTPUT / 'target'
IMAGES = OUTPUT / 'images'
ROOT_PARTUUID = '5a585300-0000-4000-8000-000000000002'
ROOT_FSUUID = '5a585300-0000-4000-8000-000000000003'
MIB = 1024**2
ROOT_MIB = 1024


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def write(relative, contents, mode=0o644):
    path = TARGET / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(mode)


def install(source, relative):
    path = TARGET / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / source, path)
    path.chmod(0o755)


def build():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(exist_ok=True)
    # A fresh root prevents stale packages after changing the selected package set.
    if TARGET.exists():
        shutil.rmtree(TARGET)
    packages = [line.strip() for line in (ROOT / 'debian/packages.list').read_text().splitlines()
                if line.strip() and not line.startswith('#')]
    run('mmdebstrap', '--mode=root', '--variant=minbase', '--architectures=amd64',
        '--keyring=/usr/share/keyrings/debian-archive-keyring.gpg',
        '--components=main,non-free-firmware', '--include=' + ','.join(packages),
        '--aptopt=Acquire::https::CaInfo "/run/zxs-build-ca.pem"',
        '--aptopt=Acquire::Retries "2"', '--aptopt=Acquire::https::Timeout "60"',
        '--setup-hook=mkdir -p "$1/run"; cp /run/zxs-build-ca.pem "$1/run/zxs-build-ca.pem"',
        '--dpkgopt=path-exclude=/usr/share/man/*',
        '--dpkgopt=path-exclude=/usr/share/info/*',
        '--dpkgopt=path-exclude=/usr/share/locale/*',
        'trixie', TARGET, ROOT / 'debian/sources.list')
    (TARGET / 'run/zxs-build-ca.pem').unlink(missing_ok=True)
    # The session CA is for downloads only, never a trust anchor in the guest.
    for path in (TARGET / 'etc/apt/apt.conf.d').glob('*'):
        if 'zxs-build-ca' in path.read_text():
            path.unlink()
    mounted = []
    try:
        for filesystem in ('proc', 'sysfs'):
            mountpoint = TARGET / ('proc' if filesystem == 'proc' else 'sys')
            mountpoint.mkdir(exist_ok=True)
            run('mount', '-t', filesystem, filesystem, mountpoint)
            mounted.append(mountpoint)
        configure()
    finally:
        for mountpoint in reversed(mounted):
            run('umount', mountpoint)
    trim_runtime()
    make_images()


def configure():
    write('etc/hostname', 'zxs-linux\n')
    write('etc/hosts', '127.0.0.1 localhost\n127.0.1.1 zxs-linux\n')
    write('etc/zxs-release', 'variant=debian\ninit=systemd\nzxs=absent\n')
    write('etc/machine-id', '')
    (TARGET / 'var/lib/dbus/machine-id').unlink(missing_ok=True)
    write('etc/fstab', f'UUID={ROOT_FSUUID} / ext4 defaults 0 1\n')
    (TARGET / 'etc/resolv.conf').unlink(missing_ok=True)
    write('etc/resolv.conf', '# Managed by dhcpcd on the device.\n')
    run('chroot', TARGET, 'passwd', '--lock', 'root')
    run('chroot', TARGET, 'usermod', '--shell', '/bin/sh', 'root')
    install('br2-external/board/scripts/console-login', 'usr/sbin/console-login')
    install('br2-external/board/scripts/zxs-https', 'usr/sbin/zxs-https')
    systemd_overlay = ROOT / 'br2-external/board/systemd/rootfs-overlay/etc/systemd'
    shutil.copytree(systemd_overlay, TARGET / 'etc/systemd', dirs_exist_ok=True)
    for source in systemd_overlay.rglob('*'):
        destination = TARGET / 'etc/systemd' / source.relative_to(systemd_overlay)
        destination.chmod(0o755 if source.is_dir() else 0o644)
    for relative in ('getty@tty1.service.d/zxs-console.conf', 'serial-getty@ttyS0.service.d/zxs-console.conf'):
        dropin = TARGET / 'etc/systemd/system' / relative
        dropin.write_text(dropin.read_text().replace('/usr/bin/agetty ', '/usr/sbin/agetty '))
    service = TARGET / 'etc/systemd/system/zxs-https.service'
    service.write_text(service.read_text().replace('nobody:nobody', 'nobody:nogroup')
                       .replace('/usr/bin/stunnel ', '/usr/bin/stunnel4 '))
    tls = (ROOT / 'br2-external/board/rootfs-overlay/etc/stunnel/stunnel.conf').read_text()
    write('etc/stunnel/stunnel.conf', tls.replace('setgid = nobody', 'setgid = nogroup'))
    shutil.copyfile(ROOT / 'br2-external/board/rootfs-overlay/etc/dhcpcd.conf', TARGET / 'etc/dhcpcd.conf')
    write('etc/systemd/system/dhcpcd.service', '''[Unit]
Description=Wired IPv4 DHCP and IPv4LL
Wants=systemd-udev-trigger.service
After=systemd-udev-trigger.service
Before=network.target
[Service]
Type=forking
PIDFile=/run/dhcpcd/pid
ExecStart=/usr/sbin/dhcpcd --background --ipv4only
ExecStop=/usr/sbin/dhcpcd --exit
Restart=on-failure
[Install]
WantedBy=multi-user.target
''')
    write('etc/sysctl.d/90-zxs.conf', 'net.ipv4.ip_forward=0\n')
    write('etc/initramfs-tools/conf.d/zxs', 'MODULES=most\nCOMPRESS=gzip\n')
    # Official binaries are used to assemble a fixed-function, image-updated system.
    for unit in ('stunnel4.service', 'systemd-networkd.service', 'systemd-networkd-wait-online.service',
                 'systemd-resolved.service', 'systemd-timesyncd.service', 'systemd-logind.service',
                 'apt-daily.timer', 'apt-daily-upgrade.timer', 'e2scrub_all.timer', 'e2scrub_reap.service', 'fstrim.timer'):
        run('chroot', TARGET, 'systemctl', '--root=/', 'mask', unit)
    run('chroot', TARGET, 'systemctl', '--root=/', 'enable', 'dhcpcd.service', 'zxs-https.service',
        'getty@tty1.service', 'serial-getty@ttyS0.service')
    run('chroot', TARGET, 'systemctl', '--root=/', 'set-default', 'multi-user.target')
    kernel = next((TARGET / 'lib/modules').iterdir()).name
    run('chroot', TARGET, 'update-initramfs', '-u', '-k', kernel)
    for backup in (TARGET / 'boot').glob('*.dpkg-bak'):
        backup.unlink()
    (OUTPUT / 'kernel-version').write_text(kernel + '\n')
    shutil.copyfile(TARGET / 'boot' / ('vmlinuz-' + kernel), IMAGES / 'bzImage')
    shutil.copyfile(TARGET / 'boot' / ('initrd.img-' + kernel), IMAGES / 'initrd.img')
    shutil.copyfile(TARGET / 'boot' / ('config-' + kernel), OUTPUT / 'linux.config')
    query = run('chroot', TARGET, 'dpkg-query', '-W',
                '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\t${Architecture}\n',
                capture_output=True, text=True).stdout
    packages = [dict(zip(('name', 'version', 'source', 'source_version', 'architecture'), line.split('\t')))
                for line in query.splitlines()]
    (OUTPUT / 'packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    built_using = run('chroot', TARGET, 'dpkg-query', '-W',
                      '-f=${Built-Using}\n${Static-Built-Using}\n', capture_output=True, text=True).stdout
    import re
    additional = sorted(set(re.findall(r'([a-z0-9][a-z0-9+.-]*)\s*\(=\s*([^\s)]+)\)', built_using)))
    (OUTPUT / 'additional-sources.json').write_text(json.dumps(additional, indent=2) + '\n')
    (OUTPUT / 'build.json').write_text(json.dumps({
        'distribution': 'Debian 13 (trixie)', 'kernel': kernel, 'variant': 'debian',
        'root_partuuid': ROOT_PARTUUID, 'root_fsuuid': ROOT_FSUUID,
        'efi_mib': 64, 'root_mib': ROOT_MIB, 'updates': 'rebuild and replace image',
        'package_manifest_scope': 'authenticated bootstrap inputs before runtime trimming',
        'package_selection_sha256': sha256(ROOT / 'debian/packages.list'),
        'sources_sha256': sha256(ROOT / 'debian/sources.list'),
    }, indent=2) + '\n')
    for relative in ('var/cache/apt/archives', 'var/lib/apt/lists', 'var/log', 'tmp', 'var/tmp'):
        directory = TARGET / relative
        for path in directory.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    (TARGET / 'var/lib/systemd/random-seed').unlink(missing_ok=True)


def trim_runtime():
    """Remove installation/build tools by package ownership, retaining runtime libraries."""
    packages = json.loads((OUTPUT / 'packages.json').read_text())
    removable = {'apt', 'dpkg', 'sqv', 'gpgv', 'debian-archive-keyring', 'libapt-pkg7.0',
                 'debconf', 'debconf-i18n', 'perl-base', 'perl', 'bash', 'bash-completion',
                 'initramfs-tools', 'initramfs-tools-core', 'grub-common', 'grub2-common',
                 'grub-efi-amd64-bin', 'dosfstools'}
    selected = [p['name'] for p in packages if p['name'].split(':')[0] in removable
                or p['name'].startswith(('libperl', 'libtext-', 'liblocale-gettext-perl'))]
    # Save copyright information outside the runtime filesystem before deleting docs.
    licenses = WORK / 'licenses'
    if licenses.exists():
        shutil.rmtree(licenses)
    licenses.mkdir()
    for name in ('doc', 'common-licenses'):
        shutil.copytree(TARGET / 'usr/share' / name, licenses / name, symlinks=True)
    owned = run('chroot', TARGET, 'dpkg-query', '-L', *selected, capture_output=True, text=True).stdout
    for filename in owned.splitlines():
        if filename.startswith('/'):
            path = TARGET / filename.lstrip('/')
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
    for relative in ('etc/apt', 'etc/dpkg', 'etc/initramfs-tools', 'var/lib/apt', 'var/lib/dpkg',
                     'var/cache/apt', 'var/cache/debconf', 'usr/share/doc', 'usr/share/man',
                     'usr/share/info', 'usr/share/locale', 'usr/share/lintian', 'usr/share/bug',
                     'usr/share/initramfs-tools'):
        shutil.rmtree(TARGET / relative, ignore_errors=True)
    # These optional ext4 maintenance scripts need LVM/Perl and are unused here.
    for name in ('e2scrub', 'e2scrub_all', 'e2scrub_reap'):
        (TARGET / 'usr/sbin' / name).unlink(missing_ok=True)
    metadata = json.loads((OUTPUT / 'build.json').read_text())
    metadata['removed_runtime_packages'] = selected
    (OUTPUT / 'build.json').write_text(json.dumps(metadata, indent=2) + '\n')


def make_images():
    kernel = (OUTPUT / 'kernel-version').read_text().strip()
    grub_cfg = f'''set timeout=1
set default=0
serial --unit=0 --speed=115200
terminal_input console serial
terminal_output console serial
menuentry 'ZXS Debian minimal' {{
    search --no-floppy --fs-uuid --set=root {ROOT_FSUUID}
    linux /vmlinuz root=PARTUUID={ROOT_PARTUUID} rootfstype=ext4 rootwait rw console=tty1 console=ttyS0,115200n8 ipv6.disable=1
    initrd /initrd.img
}}
'''
    (IMAGES / 'grub.cfg').write_text(grub_cfg)
    loader = IMAGES / 'BOOTX64.EFI'
    run('grub-mkstandalone', '-O', 'x86_64-efi', '-o', loader,
        '--modules=part_gpt ext2 fat search search_fs_uuid linux normal serial all_video',
        'boot/grub/grub.cfg=' + str(IMAGES / 'grub.cfg'))
    efi = IMAGES / 'efi-part.vfat'
    with efi.open('wb') as stream:
        stream.truncate(64 * MIB)
    run('mkfs.vfat', '-F', '32', '-i', '5A585301', '-n', 'ZXS_EFI', efi)
    run('mmd', '-i', efi, '::/EFI', '::/EFI/BOOT')
    run('mcopy', '-i', efi, loader, '::/EFI/BOOT/BOOTX64.EFI')
    rootfs = IMAGES / 'rootfs.ext4'
    with rootfs.open('wb') as stream:
        stream.truncate(ROOT_MIB * MIB)
    run('mke2fs', '-t', 'ext4', '-F', '-m', '1', '-U', ROOT_FSUUID, '-L', 'zxs-root',
        '-E', 'lazy_itable_init=0,lazy_journal_init=0', '-d', TARGET, rootfs)
    run('e2fsck', '-fn', rootfs)
    disk = IMAGES / 'disk.img'
    with disk.open('wb') as stream:
        stream.truncate((1 + 64 + ROOT_MIB + 1) * MIB)
    run('sfdisk', disk, input=(f'label: gpt\nunit: sectors\n'
        f'start=2048,size=131072,type=U\n'
        f'start=133120,size={ROOT_MIB * 2048},type=44479540-f297-41b2-9af7-d131d5f0458a,uuid={ROOT_PARTUUID}\n'), text=True)
    for source, offset in ((efi, 1), (rootfs, 65)):
        run('dd', 'if=' + str(source), 'of=' + str(disk), 'bs=1M', 'seek=' + str(offset),
            'conv=notrunc,sparse', 'status=none')


def verify():
    checks = []
    def require(condition, description):
        if not condition:
            raise RuntimeError(description)
        checks.append(description)
    try:
        packages = {p['name'].split(':')[0] for p in json.loads((OUTPUT / 'packages.json').read_text())}
        for name in ('systemd-sysv', 'coreutils', 'e2fsprogs', 'btrfs-progs', 'util-linux', 'fdisk',
                     'grub-efi-amd64-bin', 'stunnel4'):
            require(name in packages, 'Official Debian bootstrap input: ' + name)
        for name in ('openssh-server', 'network-manager', 'wpasupplicant', 'bluez', 'xserver-xorg', 'sudo'):
            require(name not in packages, 'Excluded optional package: ' + name)
        require((TARGET / 'etc/debian_version').read_text().startswith('13'), 'Debian 13 stable rootfs')
        require(not (TARGET / 'var/lib/dpkg').exists(), 'Runtime package database removed')
        require(not (TARGET / 'etc/apt').exists(), 'Runtime APT configuration removed')
        for binary in ('apt', 'apt-get', 'apt-cache', 'dpkg', 'dpkg-query', 'perl', 'bash',
                       'grub-mkstandalone'):
            require(not (TARGET / 'usr/bin' / binary).exists(), 'Runtime installation/build tool removed: ' + binary)
        require((TARGET / 'etc/machine-id').read_text().strip() == '', 'Factory machine ID is empty')
        require(not (TARGET / 'etc/zxs/initialized').exists(), 'Factory console is not initialized')
        require(not (TARGET / 'etc/zxs/https/server.pem').exists(), 'No factory TLS key or certificate')
        require(not (TARGET / 'run/zxs-build-ca.pem').exists(), 'Build session CA is excluded')
        require(not list((TARGET / 'boot').glob('*.dpkg-bak')), 'Initramfs build backups are excluded')
        require((TARGET / 'vmlinuz').is_file() and (TARGET / 'initrd.img').is_file(),
                'Debian kernel update links are valid')
        for binary in ('usr/sbin/agetty', 'usr/sbin/dhcpcd', 'usr/bin/stunnel4', 'usr/sbin/fdisk',
                       'usr/sbin/e2fsck', 'usr/sbin/mkfs.btrfs', 'usr/bin/btrfs'):
            require((TARGET / binary).is_file(), 'Debian tool executable exists: ' + binary)
        inputs = json.loads((OUTPUT / 'packages.json').read_text())
        target_grub = next(p['version'] for p in inputs if p['name'] == 'grub-efi-amd64-bin')
        builder_grub = run('dpkg-query', '-W', '-f=${Version}', 'grub-efi-amd64-bin',
                           capture_output=True, text=True).stdout
        require(target_grub == builder_grub, 'EFI loader matches the archived Debian GRUB package version')
        shadow = run('debugfs', '-R', 'cat /etc/shadow', IMAGES / 'rootfs.ext4',
                     capture_output=True, text=True).stdout
        require(next(line for line in shadow.splitlines() if line.startswith('root:')).split(':')[1].startswith(('!', '*')),
                'Built root account is locked before local password setup')
        kernel = config(OUTPUT / 'linux.config')
        for flag in ('EFI', 'EFI_PARTITION', 'EXT4_FS', 'BTRFS_FS', 'BLK_DEV_NVME', 'SATA_AHCI',
                     'VIRTIO_BLK', 'VIRTIO_NET', 'USB4', 'USB4_NET', 'DRM_I915', 'DRM_AMDGPU',
                     'E1000E', 'IGC', 'R8169', 'USB_RTL8152', 'FRAMEBUFFER_CONSOLE'):
            require(kernel.get('CONFIG_' + flag) in ('y', 'm'), 'Official kernel capability: ' + flag)
        table = json.loads(run('sfdisk', '--json', IMAGES / 'disk.img', capture_output=True, text=True).stdout)
        entries = table['partitiontable']['partitions']
        require(entries[1]['uuid'].lower() == ROOT_PARTUUID, 'GPT root PARTUUID matches boot configuration')
        require(entries[0]['size'] == 131072 and entries[1]['size'] == ROOT_MIB * 2048,
                '64 MiB EFI and 1 GiB ext4 root')
        with (IMAGES / 'disk.img').open('rb') as disk:
            import hashlib
            for entry, name in zip(entries, ('efi-part.vfat', 'rootfs.ext4')):
                disk.seek(entry['start'] * 512)
                digest = hashlib.sha256()
                remaining = entry['size'] * 512
                while remaining:
                    block = disk.read(min(remaining, MIB))
                    if not block:
                        raise RuntimeError('Truncated partition')
                    digest.update(block)
                    remaining -= len(block)
                require(digest.hexdigest() == sha256(IMAGES / name), 'Disk contains current partition: ' + name)
        run('mdir', '-i', IMAGES / 'efi-part.vfat', '::/EFI/BOOT/BOOTX64.EFI', capture_output=True)
        checks.append('Removable-media amd64 UEFI loader installed')
        path = report('verify', {'status': 'passed', 'checks': checks, 'artifacts': artifact_identity()})
        print(f'Debian image verification passed: {len(checks)} checks; {path}')
    except Exception as error:
        report('verify', {'status': 'failed', 'checks_passed': checks, 'error': str(error)})
        raise


def legal_info():
    from common import require_current_report
    identity = artifact_identity()
    require_current_report('verify', identity)
    require_current_report('boot-test', identity)
    sources = WORK / 'sources'
    sources.mkdir(exist_ok=True)
    options = ['-o', 'Dir::Etc::sourcelist=' + str(ROOT / 'debian/sources.list'),
               '-o', 'Dir::Etc::sourceparts=-', '-o', 'Acquire::https::CaInfo=/run/zxs-build-ca.pem',
               '-o', 'Acquire::Retries=2']
    run('apt-get', *options, 'update')
    packages = json.loads((OUTPUT / 'packages.json').read_text())
    additional = json.loads((OUTPUT / 'additional-sources.json').read_text())
    source_versions = {(p['source'], p['source_version']) for p in packages}
    source_versions.update(map(tuple, additional))
    for name, version in sorted(source_versions):
        run('apt-get', *options, '--download-only', '--only-source', 'source', f'{name}={version}', cwd=sources)
    DIST.mkdir(parents=True, exist_ok=True)
    with tarfile.open(DIST / 'debian-source-licenses.tar.gz', 'w:gz', compresslevel=1) as archive:
        archive.add(sources, arcname='sources')
        archive.add(WORK / 'licenses/doc', arcname='licenses/doc')
        archive.add(WORK / 'licenses/common-licenses', arcname='licenses/common-licenses')
        archive.add(OUTPUT / 'packages.json', arcname='packages.json')
        archive.add(OUTPUT / 'additional-sources.json', arcname='additional-sources.json')
        archive.add(ROOT / 'debian/sources.list', arcname='sources.list')
    from debian_package import checksums
    checksums()
    print('Archived corresponding Debian sources, patches, and licenses')


def main():
    try:
        {'build': build, 'verify': verify, 'legal-info': legal_info}[sys.argv[1]]()
    finally:
        # Guest file ownership remains root; make build artifacts accessible to the caller.
        uid, gid = int(os.environ['BUILD_UID']), int(os.environ['BUILD_GID'])
        for directory in (IMAGES, WORK / 'results', WORK / 'sources', DIST):
            if directory.exists():
                for path in (directory, *directory.rglob('*')):
                    os.chown(path, uid, gid, follow_symlinks=False)


if __name__ == '__main__':
    main()
