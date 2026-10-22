#!/usr/bin/env python3
"""Build Debian from authenticated APT packages; all disks are regular files."""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from common import DIST, OUTPUT, ROOT, WORK, artifact_identity, config, report, sha256
from debian_package import build_inputs, require_build_inputs

TARGET = OUTPUT / 'target'
IMAGES = OUTPUT / 'images'
ROOT_PARTUUID = '5a585300-0000-4000-8000-000000000002'
ROOT_FSUUID = '5a585300-0000-4000-8000-000000000003'
MIB = 1024**2
ROOT_MIB = 4096


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
    make_images()


def configure():
    write('etc/hostname', 'zxs-linux\n')
    write('etc/hosts', '127.0.0.1 localhost\n127.0.1.1 zxs-linux\n')
    write('etc/zxs-release', 'variant=debian\ninit=systemd\nzxs=absent\n')
    write('etc/machine-id', '')
    (TARGET / 'var/lib/dbus/machine-id').unlink(missing_ok=True)
    write('etc/fstab', f'UUID={ROOT_FSUUID} / ext4 defaults 0 1\nUUID=5A58-5301 /boot/efi vfat umask=0077 0 2\n')
    (TARGET / 'boot/efi').mkdir(exist_ok=True)
    (TARGET / 'etc/localtime').unlink(missing_ok=True)
    (TARGET / 'etc/localtime').symlink_to('/usr/share/zoneinfo/Etc/UTC')
    write('etc/timezone', 'Etc/UTC\n')
    write('etc/adjtime', '0.0 0 0.0\n0\nUTC\n')
    write('etc/default/intel-microcode', 'IUCODE_TOOL_INITRAMFS=early\nIUCODE_TOOL_SCANCPUS=no\n')
    # Use the target CPU policy, never hardware detection on the cloud builder.
    for name in ('modules.keep', 'modules.deny', 'firmware.keep'):
        write('etc/zxs/hardware/' + name, (ROOT / 'debian' / name).read_text())
    install('debian/zxs-hardware-trim', 'usr/sbin/zxs-hardware-trim')
    install('debian/zxs-update-bootloader', 'usr/sbin/zxs-update-bootloader')
    install('debian/apt-postinvoke', 'usr/sbin/zxs-post-package-update')
    install('debian/apt-preinstall', 'usr/sbin/zxs-apt-preinstall')
    for name in ('zxs-recovery', 'zxs-prepare-boot-update', 'zxs-diagnose', 'zxs-health-check'):
        install('debian/' + name, 'usr/sbin/' + name)
    install('debian/kernel-preinst', 'etc/kernel/preinst.d/aa-zxs-recovery')
    install('debian/kernel-postinst', 'etc/kernel/postinst.d/aa-zxs-hardware')
    install('debian/initramfs-recovery', 'etc/initramfs-tools/scripts/local-bottom/zxs-recovery')
    install('debian/initramfs-recovery-hook', 'etc/initramfs-tools/hooks/zxs-recovery')
    # Keep package databases and the official update toolchain intact.
    for source in (TARGET / 'etc/apt/sources.list.d').glob('*'):
        source.unlink()
    write('etc/apt/sources.list', (ROOT / 'debian/sources.list').read_text())
    write('etc/apt/apt.conf.d/90-zxs', '''APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Periodic::Enable "0";
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
DPkg::Post-Invoke { "/usr/sbin/zxs-post-package-update"; };
DPkg::Pre-Install-Pkgs { "/usr/sbin/zxs-apt-preinstall"; };
DPkg::Tools::Options::/usr/sbin/zxs-apt-preinstall::Version "2";
''')
    write('etc/dpkg/dpkg.cfg.d/01-zxs', '''path-exclude=/usr/share/man/*
path-exclude=/usr/share/info/*
path-exclude=/usr/share/locale/*
''')
    write('etc/modules-load.d/zxs.conf', 'thunderbolt_net\n')
    write('etc/systemd/network/10-zxs-thunderbolt.link', '''[Match]
OriginalName=thunderbolt*
[Link]
NamePolicy=
MACAddressPolicy=none
''')
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
    # Debian exposes local graphical consoles only; the Buildroot overlay is shared.
    shutil.rmtree(TARGET / 'etc/systemd/system/serial-getty@ttyS0.service.d', ignore_errors=True)
    write('etc/systemd/system-preset/00-zxs.preset', '''enable getty@tty1.service
enable dhcpcd.service
enable zxs-https.service
disable serial-getty@.service
''')
    for relative in ('getty@tty1.service.d/zxs-console.conf',):
        dropin = TARGET / 'etc/systemd/system' / relative
        dropin.write_text(dropin.read_text().replace('/usr/bin/agetty ', '/usr/sbin/agetty '))
    service = TARGET / 'etc/systemd/system/zxs-https.service'
    service.write_text(service.read_text().replace('nobody:nobody', 'nobody:nogroup')
                       .replace('/usr/bin/stunnel ', '/usr/bin/stunnel4 '))
    tls = (ROOT / 'br2-external/board/rootfs-overlay/etc/stunnel/stunnel.conf').read_text()
    write('etc/stunnel/stunnel.conf', tls.replace('setgid = nobody', 'setgid = nogroup'))
    write('etc/dhcpcd.conf', (ROOT / 'br2-external/board/rootfs-overlay/etc/dhcpcd.conf').read_text()
          + '\n# Host-to-host Thunderbolt/USB4 networking only.\nallowinterfaces thunderbolt*\n')
    write('etc/systemd/system/dhcpcd.service', '''[Unit]
Description=Thunderbolt IPv4 DHCP and IPv4LL
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
    write('etc/initramfs-tools/conf.d/zxs', 'MODULES=most\nCOMPRESS=gzip\nFSTYPE=ext4\n')
    write('etc/systemd/journald.conf.d/zxs.conf', '''[Journal]
Storage=persistent
SystemMaxUse=64M
SystemKeepFree=128M
SystemMaxFileSize=8M
RuntimeMaxUse=16M
''')
    write('etc/systemd/system/zxs-record-boot.service', '''[Unit]
Description=Record booted kernel files before local package maintenance
After=local-fs.target
Before=getty.target getty@tty1.service
[Service]
Type=oneshot
ExecStart=/usr/sbin/zxs-recovery record-boot
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
    write('etc/systemd/system/zxs-health.service', '''[Unit]
Description=Check local certificate, UTC clock and NVMe health
[Service]
Type=oneshot
ExecStart=/usr/sbin/zxs-health-check
''')
    write('etc/systemd/system/zxs-health.timer', '''[Unit]
Description=Daily local health check
[Timer]
OnBootSec=10min
OnUnitActiveSec=1d
RandomizedDelaySec=5min
[Install]
WantedBy=timers.target
''')
    write('etc/systemd/system/fstrim.timer.d/zxs.conf', '''[Timer]
OnCalendar=
OnCalendar=weekly
Persistent=false
RandomizedDelaySec=1h
''')
    for unit in ('stunnel4.service', 'systemd-networkd.service', 'systemd-networkd-wait-online.service',
                 'systemd-resolved.service', 'systemd-timesyncd.service',
                 'apt-daily.timer', 'apt-daily-upgrade.timer', 'apt-daily.service', 'apt-daily-upgrade.service',
                 'e2scrub_all.timer', 'e2scrub_reap.service', 'smartmontools.service',
                 'nvmefc-boot-connections.service', 'nvmf-autoconnect.service', 'nvmf-connect.target',
                 'serial-getty@.service'):
        run('chroot', TARGET, 'systemctl', '--root=/', 'mask', unit)
    run('chroot', TARGET, 'systemctl', '--root=/', 'enable', 'dhcpcd.service', 'zxs-https.service',
        'getty@tty1.service', 'zxs-record-boot.service', 'zxs-health.timer', 'fstrim.timer')
    run('chroot', TARGET, 'systemctl', '--root=/', 'set-default', 'multi-user.target')
    kernel = next((TARGET / 'lib/modules').iterdir()).name
    save_test_modules(kernel)
    run('chroot', TARGET, 'zxs-hardware-trim', 'all')
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
    additional = sorted(set(re.findall(r'([a-z0-9][a-z0-9+.-]*)\s*\(=\s*([^\s)]+)\)', built_using)))
    (OUTPUT / 'additional-sources.json').write_text(json.dumps(additional, indent=2) + '\n')
    (OUTPUT / 'build.json').write_text(json.dumps({
        'distribution': 'Debian 13 (trixie)', 'kernel': kernel, 'variant': 'debian',
        'root_partuuid': ROOT_PARTUUID, 'root_fsuuid': ROOT_FSUUID,
        'efi_mib': 64, 'root_mib': ROOT_MIB, 'updates': 'manual apt/dpkg; automatic updates disabled',
        'hardware': 'Intel Core Ultra 9 288V, Xe, NVMe, Thunderbolt/USB4 host-to-host IPv4',
        'timezone': 'Etc/UTC', 'journal': 'persistent, 64 MiB maximum',
        'recovery': 'booted kernel, initrd, modules and firmware checkpoint before boot-package updates',
        'maintenance': 'weekly TRIM; daily local health checks; no automatic package updates',
        'console': 'local tty1 with USB keyboard; physical serial disabled',
        'legacy_hardware': 'no PS/2, optical/floppy/parallel, legacy USB hosts or physical serial',
        'package_manifest_scope': 'installed Debian packages; hardware files filtered by /etc/zxs/hardware',
        'hardware_policy_sha256': {name: sha256(ROOT / 'debian' / name)
                                   for name in ('modules.keep', 'modules.deny', 'firmware.keep')},
        'package_selection_sha256': sha256(ROOT / 'debian/packages.list'),
        'package_manifest_sha256': sha256(OUTPUT / 'packages.json'),
        'build_inputs_sha256': build_inputs(),
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
    journal = TARGET / 'var/log/journal'
    journal.mkdir(mode=0o2755)
    gid = next(int(line.split(':')[2]) for line in (TARGET / 'etc/group').read_text().splitlines()
               if line.startswith('systemd-journal:'))
    os.chown(journal, 0, gid)
    journal.chmod(0o2755)
    licenses = WORK / 'licenses'
    if licenses.exists():
        shutil.rmtree(licenses)
    licenses.mkdir()
    for name in ('doc', 'common-licenses'):
        shutil.copytree(TARGET / 'usr/share' / name, licenses / name, symlinks=True)


def save_test_modules(kernel):
    """QEMU NICs belong on test media, never on the shipping appliance disk."""
    source = TARGET / 'lib/modules' / kernel
    fixture = OUTPUT / 'test-modules'
    shutil.rmtree(fixture, ignore_errors=True)
    dependencies = dict(line.split(':', 1) for line in (source / 'modules.dep').read_text().splitlines())
    selected = set()
    def retain(name):
        if name in selected:
            return
        selected.add(name)
        for dependency in dependencies[name].split():
            retain(dependency)
    for name in dependencies:
        if re.search(r'/(virtio_net|e1000e|virtio_console)\.ko', name):
            retain(name)
    for name in selected:
        destination = fixture / 'lib/modules' / kernel / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)


def make_images():
    kernel = (OUTPUT / 'kernel-version').read_text().strip()
    grub_cfg = ((ROOT / 'debian/grub.cfg').read_text().replace('@ROOT_FSUUID@', ROOT_FSUUID)
                .replace('@ROOT_PARTUUID@', ROOT_PARTUUID))
    (IMAGES / 'grub.cfg').write_text(grub_cfg)
    write('etc/zxs/grub.cfg', grub_cfg)
    loader = IMAGES / 'BOOTX64.EFI'
    run('grub-mkstandalone', '-O', 'x86_64-efi', '-o', loader,
        '--modules=part_gpt ext2 fat search search_fs_uuid linux normal all_video loadenv',
        'boot/grub/grub.cfg=' + str(IMAGES / 'grub.cfg'))
    run('chroot', TARGET, 'zxs-post-package-update', '--record-state')
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
        require_build_inputs()
        checks.append('Image and package manifest bound to the current source inputs')
        packages = {p['name'].split(':')[0] for p in json.loads((OUTPUT / 'packages.json').read_text())}
        for name in ('systemd-sysv', 'coreutils', 'e2fsprogs', 'btrfs-progs', 'util-linux', 'fdisk',
                     'grub-efi-amd64-bin', 'stunnel4'):
            require(name in packages, 'Official Debian bootstrap input: ' + name)
        for name in ('openssh-server', 'network-manager', 'wpasupplicant', 'bluez', 'xserver-xorg', 'sudo'):
            require(name not in packages, 'Excluded optional package: ' + name)
        require((TARGET / 'etc/debian_version').read_text().startswith('13'), 'Debian 13 stable rootfs')
        require((TARGET / 'var/lib/dpkg/status').is_file(), 'Complete installed-package database retained')
        require((TARGET / 'etc/apt/sources.list').read_text() == (ROOT / 'debian/sources.list').read_text(),
                'Official stable, updates and security APT repositories retained')
        for binary in ('apt', 'apt-get', 'apt-cache', 'dpkg', 'dpkg-query', 'perl', 'bash',
                       'grub-mkstandalone'):
            require((TARGET / 'usr/bin' / binary).is_file(), 'Supported package/update tool retained: ' + binary)
        for name in ('geoclue-2.0', 'gpsd', 'firmware-amd-graphics', 'firmware-realtek'):
            require(name not in packages, 'Excluded hardware/location package: ' + name)
        require((TARGET / 'etc/localtime').readlink() == Path('/usr/share/zoneinfo/Etc/UTC'), 'UTC system timezone')
        require((TARGET / 'usr/share/zoneinfo/Etc/UTC').is_file(), 'Official UTC timezone data retained')
        require('allowinterfaces thunderbolt*' in (TARGET / 'etc/dhcpcd.conf').read_text(), 'Only Thunderbolt network interfaces configured')
        require((TARGET / 'etc/modules-load.d/zxs.conf').read_text().strip() == 'thunderbolt_net',
                'Host-to-host Thunderbolt protocol loaded at boot')
        require('Storage=persistent' in (TARGET / 'etc/systemd/journald.conf.d/zxs.conf').read_text(),
                'Bounded persistent journal configured')
        for unit in ('apt-daily.timer', 'apt-daily-upgrade.timer', 'apt-daily.service', 'apt-daily-upgrade.service'):
            require((TARGET / 'etc/systemd/system' / unit).readlink() == Path('/dev/null'), 'Automatic updates disabled: ' + unit)
        kernel_version = (OUTPUT / 'kernel-version').read_text().strip()
        module_root = TARGET / 'lib/modules' / kernel_version
        modules = [str(p.relative_to(module_root)) for p in module_root.rglob('*.ko*')]
        for name in ('nvme', 'xe', 'thunderbolt', 'thunderbolt_net', 'usbhid', 'hid-generic', 'evdev',
                     'xhci-hcd', 'xhci-pci', 'usb-storage', 'uas', 'ext4', 'btrfs'):
            require(any(re.search('/' + re.escape(name) + r'\.ko', p) for p in modules), 'Required hardware module: ' + name)
        denied = [line for line in (ROOT / 'debian/modules.deny').read_text().splitlines() if line and not line.startswith('#')]
        require(not any(re.search(pattern, p) for pattern in denied for p in modules), 'Sound, legacy network, SATA and location modules excluded')
        require(all('/net/thunderbolt/' in p for p in modules if p.startswith('kernel/drivers/net/')),
                'Thunderbolt is the only shipped network driver')
        require(not any('/virtio_console.ko' in p for p in modules), 'Test diagnostic character channel excluded from production')
        dependency_lines = (module_root / 'modules.dep').read_text().splitlines()
        require(all((module_root / p).is_file() for line in dependency_lines for p in line.replace(':', '').split()),
                'Retained module dependency closure is complete')
        for name in ('xe/lnl_guc_70.bin', 'xe/lnl_huc.bin', 'xe/lnl_gsc_1.bin', 'i915/xe2lpd_dmc.bin', 'intel-ucode/06-bd-01'):
            require((TARGET / 'usr/lib/firmware' / name).is_file(), 'Lunar Lake firmware: ' + name)
        require(sha256(TARGET / 'boot' / ('initrd.img-' + kernel_version)) == sha256(IMAGES / 'initrd.img'),
                'Published initramfs matches the installed kernel')
        initrd_listing = run('chroot', TARGET, 'lsinitramfs', '/boot/initrd.img-' + kernel_version,
                             capture_output=True, text=True).stdout
        require('kernel/x86/microcode/GenuineIntel.bin' in initrd_listing, 'Intel early microcode included in initramfs')
        require(re.search(r'(^|/)sbin/fsck\n', initrd_listing) is not None, 'Early root filesystem check tool included')
        require(re.search(r'(^|/)sbin/e2fsck\n', initrd_listing) is not None, 'Early ext4 checker included independently of build host')
        require('IUCODE_TOOL_SCANCPUS=no' in (TARGET / 'etc/default/intel-microcode').read_text(),
                'Microcode selection is independent of build-host CPU')
        require('ipv6.disable=1' in (TARGET / 'etc/zxs/grub.cfg').read_text(), 'IPv6 disabled by kernel command line')
        grub = (TARGET / 'etc/zxs/grub.cfg').read_text()
        require('ttyS' not in grub and 'serial' not in grub, 'Production boot has no serial console')
        require(all(option in grub for option in ('8250.nr_uarts=0', 'i8042.nokbd', 'i8042.noaux')),
                'Built-in legacy UART and PS/2 discovery disabled')
        require((TARGET / 'etc/systemd/system/serial-getty@.service').readlink() == Path('/dev/null'),
                'Physical serial login service masked')
        require(not (TARGET / 'etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service').is_symlink(),
                'No serial login enabled')
        require('UUID=5A58-5301 /boot/efi' in (TARGET / 'etc/fstab').read_text(), 'EFI partition mounted for supported bootloader updates')
        require((TARGET / 'etc/kernel/postinst.d/aa-zxs-hardware').is_file(), 'Hardware policy runs before the official initramfs kernel hook')
        require(not (TARGET / 'etc/kernel/postinst.d/zz-zxs-hardware').exists(), 'No duplicate late initramfs generation hook')
        require('zxs-apt-preinstall' in (TARGET / 'etc/apt/apt.conf.d/90-zxs').read_text(), 'Boot package updates have preflight and checkpoint hooks')
        require('zxs-post-package-update' in (TARGET / 'etc/apt/apt.conf.d/90-zxs').read_text(), 'APT refreshes changed boot inputs only')
        require('scripts/local-bottom/zxs-recovery' in initrd_listing, 'Recovery mounts preserved modules and firmware from the initramfs')
        require('usr/bin/zxs-mount' in initrd_listing, 'Official mount utility supports read-only recovery bind mounts')
        require('zxs-recovery' in grub and 'systemd.unit=rescue.target' in grub, 'GRUB offers preserved-kernel and password-protected local rescue entries')
        require(not (TARGET / 'boot/zxs-recovery/current').exists(), 'No duplicate recovery kernel on the factory image')
        for tool in ('zxs-recovery', 'zxs-diagnose', 'zxs-health-check'):
            require((TARGET / 'usr/sbin' / tool).is_file(), 'Local maintenance helper: ' + tool)
        for source, destination in (
                ('apt-postinvoke', 'usr/sbin/zxs-post-package-update'),
                ('apt-preinstall', 'usr/sbin/zxs-apt-preinstall'),
                ('kernel-preinst', 'etc/kernel/preinst.d/aa-zxs-recovery'),
                ('kernel-postinst', 'etc/kernel/postinst.d/aa-zxs-hardware'),
                ('initramfs-recovery', 'etc/initramfs-tools/scripts/local-bottom/zxs-recovery'),
                ('initramfs-recovery-hook', 'etc/initramfs-tools/hooks/zxs-recovery'),
                *((name, 'usr/sbin/' + name) for name in
                  ('zxs-recovery', 'zxs-prepare-boot-update', 'zxs-diagnose', 'zxs-health-check',
                   'zxs-hardware-trim', 'zxs-update-bootloader'))):
            require(sha256(ROOT / 'debian' / source) == sha256(TARGET / destination),
                    'Installed helper matches current source: ' + source)
        require((TARGET / 'var/lib/zxs/update/firmware').is_file(), 'Factory boot input state recorded')
        require((TARGET / 'etc/systemd/system/timers.target.wants/fstrim.timer').is_symlink(), 'Weekly NVMe TRIM enabled without boot catch-up')
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
        for flag in ('EFI', 'EFI_PARTITION', 'EXT4_FS', 'BTRFS_FS', 'BLK_DEV_NVME',
                     'VIRTIO_BLK', 'USB4', 'USB4_NET', 'DRM_XE', 'USB_HID', 'FRAMEBUFFER_CONSOLE'):
            require(kernel.get('CONFIG_' + flag) in ('y', 'm'), 'Official kernel capability: ' + flag)
        table = json.loads(run('sfdisk', '--json', IMAGES / 'disk.img', capture_output=True, text=True).stdout)
        entries = table['partitiontable']['partitions']
        require(entries[1]['uuid'].lower() == ROOT_PARTUUID, 'GPT root PARTUUID matches boot configuration')
        require(entries[0]['size'] == 131072 and entries[1]['size'] == ROOT_MIB * 2048,
                '64 MiB EFI and 4 GiB ext4 root')
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
    require_build_inputs()
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
