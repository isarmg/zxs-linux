#!/usr/bin/env python3
"""Boot the actual GPT/UEFI disk twice, exercising login, DHCP, HTTP and persistence."""
import http.server
import json
import os
import re
import secrets
import select
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time

from common import CACHE, OUTPUT, ROOT, VARIANT, WORK, artifact_identity, report, require_current_report

SYSTEMD = VARIANT in ('systemd', 'debian')


class Handler(http.server.BaseHTTPRequestHandler):
    backend_payload = b''

    def do_GET(self):
        if self.path == '/test-backend':
            payload = self.backend_payload
        elif self.path == '/readiness':
            payload = b'zxs-linux-network-ok\n'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class Console:
    def __init__(self, process, logfile, password):
        self.process, self.logfile, self.pending = process, logfile, ''
        self.reader, self.writer = process.stdout, process.stdin
        self.password = password.encode()
        self.log_pending = b''

    def log(self, data, final=False):
        self.log_pending += data
        if final:
            complete, self.log_pending = self.log_pending, b''
        elif b'\n' in self.log_pending:
            complete, self.log_pending = self.log_pending.rsplit(b'\n', 1)
            complete += b'\n'
        else:
            return
        complete = complete.replace(self.password, b'[redacted]')
        complete = re.sub(rb'((?:New password|Retype(?: new)? password|Password):)[^\r\n]*', rb'\1 [redacted]', complete)
        self.logfile.write(complete)
        self.logfile.flush()

    def expect(self, pattern, timeout=180):
        end = time.monotonic() + timeout
        while True:
            found = re.search(pattern, self.pending.replace('\r', ''), re.MULTILINE)
            if found:
                # Discard already consumed output, retaining only later terminal text.
                normalized = self.pending.replace('\r', '')
                self.pending = normalized[found.end():]
                return found
            if time.monotonic() >= end:
                raise RuntimeError(f'Console timeout waiting for {pattern!r}; inspect {self.logfile.name}')
            ready, _, _ = select.select([self.reader], [], [], 1)
            if ready:
                data = os.read(self.reader.fileno(), 65536)
                if not data:
                    raise RuntimeError(f'QEMU exited before {pattern!r}; inspect {self.logfile.name}')
                self.log(data)
                self.pending += data.decode(errors='replace')
                if len(self.pending) > 1024 * 1024:
                    self.pending = self.pending[-512 * 1024:]

    def send(self, line):
        self.writer.write((line + '\n').encode())
        self.writer.flush()


def metric_pattern(marker):
    # '$' also matches an incomplete read buffer; wait for the actual newline.
    return '^' + re.escape(marker) + r':([0-9.]+):([0-9]+)\n'


def boot(stage, folder, password, nonce, port):
    prefix = CACHE / 'host/usr/share'
    if VARIANT == 'debian' and not (prefix / 'OVMF/OVMF_CODE_4M.fd').exists():
        from pathlib import Path
        prefix = Path('/usr/share')
    firmware_dir = prefix / 'OVMF'
    variables = folder / 'OVMF_VARS.fd'
    if not variables.exists():
        shutil.copyfile(firmware_dir / 'OVMF_VARS_4M.fd', variables)
    command = [
        'qemu-system-x86_64', '-machine', 'q35,accel=tcg', '-cpu', 'max', '-m', '2048', '-smp', '2',
        '-nodefaults', '-display', 'none', '-serial', 'stdio', '-monitor', 'none', '-no-reboot',
        '-L', str(prefix / 'qemu'),
        '-drive', f'if=pflash,format=raw,unit=0,readonly=on,file={firmware_dir}/OVMF_CODE_4M.fd',
        '-drive', f'if=pflash,format=raw,unit=1,file={variables}',
        '-drive', f'if=none,format=raw,id=system,file={folder}/disk.img',
        '-device', 'virtio-blk-pci,drive=system',
        '-device', f'VGA,romfile={prefix}/seabios/vgabios-stdvga.bin',
        '-netdev', 'user,id=n0', '-device', 'virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56,romfile=',
        '-netdev', 'hubport,id=isolated,hubid=1',
        '-device', 'e1000e,netdev=isolated,mac=52:54:00:12:34:57,romfile=',
    ]
    if VARIANT == 'debian':
        command[command.index('-serial') + 1] = 'none'
        command[command.index('virtio-blk-pci,drive=system')] = 'nvme,drive=system,serial=zxs-test-nvme'
        for index, name in enumerate(('ext4-test.img', 'btrfs-test.img')):
            command += ['-drive', f'if=none,format=raw,id=data{index},file={folder}/{name}',
                        '-device', f'virtio-blk-pci,drive=data{index}']
        qmp = folder / 'qmp.sock'
        qmp.unlink(missing_ok=True)
        command += ['-drive', f'if=none,format=raw,id=fixture,readonly=on,file={folder}/fixture.img',
                    '-device', 'qemu-xhci,id=xhci', '-device', 'usb-storage,drive=fixture,bus=xhci.0',
                    '-device', 'usb-kbd,bus=xhci.0', '-qmp', f'unix:{qmp},server=on,wait=off']
        channel = folder / 'test-channel.sock'
        channel.unlink(missing_ok=True)
        command += ['-chardev', f'socket,id=test,path={channel},server=on,wait=off',
                    '-device', 'virtio-serial-pci',
                    '-device', 'virtserialport,chardev=test,name=zxs-test']
    with (folder / f'boot-{stage}.log').open('wb') as logfile:
        started = time.monotonic()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=0)
        console = Console(process, logfile, password)
        try:
            if VARIANT == 'debian':
                from graphical_console import GraphicalConsole
                console = GraphicalConsole(process, logfile, password, folder, qmp, channel, Console)
            if stage == 1:
                console.expect('ZXS_FIRST_BOOT:')
                console_ready = time.monotonic() - started
                console.send('')
                console.expect('New password:')
                console.send(password)
                console.expect(r'Retype(?: new)? password:')
                console.send(password)
            console.expect('zxs-linux login:')
            if stage == 2:
                console_ready = time.monotonic() - started
            console.send('root')
            console.expect('Password:')
            console.send(password)
            console.expect(r'^.*# $', timeout=30)
            if VARIANT == 'debian':
                console.activate_test_channel()
            marker = 'ZXS_TEST_PASS_' + nonce + '_' + str(stage)
            failure = 'ZXS_TEST_FAIL_' + nonce + '_' + str(stage)
            metrics_marker = 'ZXS_BOOT_METRICS_' + nonce + '_' + str(stage)
            start_https = ('systemctl start zxs-https.service' if SYSTEMD
                           else '/etc/init.d/S50stunnel start')
            expected_kernel = ((OUTPUT / 'kernel-version').read_text().strip()
                               if VARIANT == 'debian' else '6.18.58')
            tools_check = ("readlink /proc/$$/exe | grep -q '/dash$'\nls --version | grep -q 'GNU coreutils'"
                           if VARIANT == 'debian' else
                           "readlink /proc/$$/exe | grep -q '/busybox$'\nls --help 2>&1 | grep -q BusyBox")
            fixture_setup = '''
test -b /dev/nvme0n1
! grep -Eq 'console=(ttyS|hvc)|earlycon' /proc/cmdline
! grep -q ttyS /proc/consoles
test ! -e /dev/ttyS0
test -z "$(ls -A /sys/bus/serio/devices)"
systemctl is-enabled serial-getty@ttyS0.service | grep -qx masked
test -z "$(find /lib/modules -name 'ehci*.ko*' -o -name 'ohci*.ko*' -o -name 'uhci*.ko*' -o -name 'sr_mod.ko*' -o -name 'cdrom.ko*')"
mkdir -p /mnt/zxs-test
# These virtual NIC drivers are injected into the disposable test copy only.
cp -a /mnt/zxs-test/lib/modules/. /lib/modules/
depmod -a
sed -i '/^allowinterfaces /d' /etc/dhcpcd.conf
modprobe virtio_net
modprobe e1000e
udevadm trigger --type=devices --action=add
udevadm settle --timeout=30
systemctl restart dhcpcd.service
/mnt/zxs-test/usb-keyboard
''' if VARIANT == 'debian' else ''
            script = f'''set -eux
trap 'rc=$?; if [ "$rc" -ne 0 ]; then printf "\\n{failure}:%s\\n" "$rc"; fi' EXIT
uname -r
ip -4 addr
ip -4 route
test "$(uname -r)" = '{expected_kernel}'
test -e /etc/zxs/initialized
{tools_check}
printf '\\n{metrics_marker}:%s:%s\\n' "$(cut -d' ' -f1 /proc/uptime)" "$(grep '^MemAvailable:' /proc/meminfo | tr -s ' ' | cut -d' ' -f2)"
{fixture_setup}
bound_console=0
for console in /sys/class/vtconsole/vtcon*; do
    if grep -q 'frame buffer' "$console/name" && test "$(cat "$console/bind")" = 1; then
        cat "$console/name" "$console/bind"
        bound_console=1
    fi
done
test "$bound_console" = 1
printf 'ZXS Linux console ready\\n' > /dev/tty1
test "$(cat /proc/sys/net/ipv4/ip_forward)" = 0
test ! -e /proc/net/if_inet6
for interface in /sys/class/net/*; do test ! -d "$interface/bridge"; done
for i in $(seq 1 45); do ip -4 route show default | grep -q default && break; sleep 1; done
ip -4 route show default | grep -q default
test "$(curl --fail --silent --max-time 15 http://10.0.2.2:{port}/readiness)" = 'zxs-linux-network-ok'
for i in $(seq 1 60); do ip -4 addr show | grep -q 'inet 169[.]254[.]' && break; sleep 1; done
ip -4 addr show | grep -q 'inet 169[.]254[.]'
grep -qx 'zxs=absent' /etc/zxs-release
'''
            if SYSTEMD:
                script += '''test "$(cat /proc/1/comm)" = systemd
test -d /sys/fs/cgroup
for i in $(seq 1 30); do systemctl is-active --quiet multi-user.target && break; sleep 1; done
systemctl is-active --quiet multi-user.target
systemctl is-active dhcpcd.service systemd-journald.service systemd-udevd.service
busctl --system --no-pager list >/dev/null
test -z "$(systemctl --failed --no-legend --plain)"
test -s /etc/machine-id
'''
                script += ("grep -q '^Storage=persistent$' /etc/systemd/journald.conf.d/zxs.conf\n"
                           if VARIANT == 'debian' else
                           "grep -q '^Storage=volatile$' /etc/systemd/journald.conf.d/zxs.conf\n")
                if stage == 1:
                    script += 'cp /etc/machine-id /root/test-machine-id\n'
                else:
                    script += 'cmp -s /etc/machine-id /root/test-machine-id\n'
            else:
                script += 'test "$(cat /proc/1/comm)" = init\n'
            if stage == 1:
                script += f'''test ! -e /etc/zxs/https/server.pem
{start_https}
test ! -e /run/zxs-https/stunnel.pid
curl --fail --silent --max-time 15 http://10.0.2.2:{port}/test-backend -o /root/zxs-test-http
chmod 0700 /root/zxs-test-http
zxs-https generate DNS:zxs-test.lan IP:127.0.0.1
zxs-https certificate > /root/https-test.crt
openssl pkey -in /etc/zxs/https/server.pem -out /tmp/valid-key.pem
zxs-https install /root/https-test.crt /tmp/valid-key.pem
sha256sum /etc/zxs/https/server.pem > /root/https-test.sha
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 -out /tmp/wrong-key.pem
if zxs-https install /root/https-test.crt /tmp/wrong-key.pem; then exit 1; fi
sha256sum -c /root/https-test.sha
'''
            else:
                script += 'sha256sum -c /root/https-test.sha\n'
            script += f'''find /etc/zxs/https -maxdepth 0 -perm 0700 | grep -q .
find /etc/zxs/https/server.pem -maxdepth 0 -perm 0600 | grep -q .
test -s /run/zxs-https/stunnel.pid
https_pid=$(cat /run/zxs-https/stunnel.pid)
{start_https}
test "$(cat /run/zxs-https/stunnel.pid)" = "$https_pid"
'''
            if SYSTEMD:
                script += '''systemctl is-active --quiet zxs-https.service
test "$(systemctl show --property MainPID --value zxs-https.service)" = "$https_pid"
kill -KILL "$https_pid"
for i in $(seq 1 20); do
    new_pid=$(systemctl show --property MainPID --value zxs-https.service)
    if [ "$new_pid" -gt 0 ] && [ "$new_pid" != "$https_pid" ] && [ -s /run/zxs-https/stunnel.pid ]; then break; fi
    sleep 1
done
test "$new_pid" -gt 0
test "$new_pid" != "$https_pid"
https_pid=$new_pid
'''
            script += '''
awk '/^Uid:/ { if ($2 != 65534) exit 1; found=1 } END { if (!found) exit 1 }' /proc/"$https_pid"/status
/root/zxs-test-http &
for i in $(seq 1 10); do curl --fail --silent --max-time 2 http://127.0.0.1:8080/ >/dev/null && break; sleep 1; done
for version in 1.2 1.3; do
    test "$(curl --fail --silent --show-error --max-time 15 --tlsv$version --tls-max $version --cacert /root/https-test.crt https://127.0.0.1/)" = 'zxs-linux-https-ok'
done
test "$(curl --fail --silent --show-error --max-time 15 --resolve zxs-test.lan:443:127.0.0.1 --cacert /root/https-test.crt https://zxs-test.lan/)" = 'zxs-linux-https-ok'
if curl --fail --silent --max-time 10 https://127.0.0.1/ >/dev/null; then exit 1; fi
if curl --fail --silent --max-time 10 --resolve wrong.lan:443:127.0.0.1 --cacert /root/https-test.crt https://wrong.lan/ >/dev/null; then exit 1; fi
'''
            if VARIANT == 'debian':
                script += disk_checks(stage, nonce)
            if stage == 1:
                if SYSTEMD:
                    script += '''printf 'Testing native systemd coldplug recovery\\n'
systemctl stop dhcpcd.service
systemctl stop systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket
'''
                    if VARIANT == 'systemd':
                        script += 'systemctl stop systemd-udevd-varlink.socket\n'
                else:
                    script += '''printf 'Testing delayed udev coldplug recovery\\n'
killall dhcpcd
'''
                script += '''
sleep 1
for interface in /sys/class/net/*; do
    name=${interface##*/}
    if [ "$name" != lo ]; then
        ip link set "$name" down
        ip -4 addr flush dev "$name"
    fi
done
'''
                if VARIANT == 'busybox':
                    script += '''udevadm control --exit
for i in $(seq 1 30); do pidof udevd >/dev/null || break; sleep 1; done
! pidof udevd >/dev/null
'''
                script += '''
modprobe -r e1000e
rm -rf /run/udev/data
'''
                if SYSTEMD:
                    script += '''udevadm trigger --type=devices --action=add
test ! -d /sys/module/e1000e
sleep 5
systemctl restart systemd-udev-trigger.service
udevadm settle --timeout=30
test -d /sys/module/e1000e
systemctl start dhcpcd.service
'''
                else:
                    script += '''
(sleep 5; /sbin/udevd --daemon) >/dev/null 2>&1 &
udevadm trigger --type=devices --action=add
test ! -d /sys/module/e1000e
/etc/init.d/S11udev-coldplug start
test -d /sys/module/e1000e
dhcpcd --background
'''
                script += f'''for i in $(seq 1 45); do ip -4 route show default | grep -q default && break; sleep 1; done
ip -4 route show default | grep -q default
test "$(curl --fail --silent --max-time 15 http://10.0.2.2:{port}/readiness)" = 'zxs-linux-network-ok'
for i in $(seq 1 60); do ip -4 addr show | grep -q 'inet 169[.]254[.]' && break; sleep 1; done
ip -4 addr show | grep -q 'inet 169[.]254[.]'
'''
                script += f"printf '%s\\n' '{nonce}' > /etc/zxs/boot-test-persistence\n"
            else:
                script += f"test \"$(cat /etc/zxs/boot-test-persistence)\" = '{nonce}'\n"
            if VARIANT == 'debian':
                script += package_checks(stage, nonce)
            script += f"uname -r; ip -4 addr; printf '\\n{marker}\\n'; sync; poweroff\n"
            console.send('sh -c ' + shlex.quote(script))
            measured = console.expect(metric_pattern(metrics_marker), timeout=30)
            if VARIANT == 'debian':
                console.expect('^ZXS_USB_KEYBOARD_READY$', timeout=90)
                send_usb_key(qmp)
            outcome = console.expect('^(' + marker + '|' + failure + r':[0-9]+)$', timeout=600)
            if outcome.group(1) != marker:
                diagnostic_marker = 'ZXS_DIAGNOSTICS_' + nonce
                diagnostic = ('ip -br link; ip -4 addr; ip -4 route; '
                              'for c in /sys/class/vtconsole/vtcon*; do cat "$c/name" "$c/bind"; done; '
                              + ('journalctl -b -n 70 --no-pager; ' if SYSTEMD
                                 else 'tail -60 /var/log/messages; ')
                              + f"printf '\\n{diagnostic_marker}\\n'")
                try:
                    console.send('sh -c ' + shlex.quote(diagnostic))
                    console.expect('^' + diagnostic_marker + '$', timeout=15)
                except (RuntimeError, OSError):
                    pass
                raise RuntimeError(f'Guest check failed: {outcome.group(1)}; inspect {logfile.name}')
            if VARIANT != 'debian':
                console.expect('Power down', timeout=30)
            process.wait(timeout=30)
            if process.returncode != 0:
                raise RuntimeError(f'QEMU returned {process.returncode}')
            return {'boot': stage, 'console_ready_wall_seconds': round(console_ready, 3),
                    'login_guest_uptime_seconds': float(measured.group(1)),
                    'login_mem_available_kib': int(measured.group(2))}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            console.log(b'', final=True)
            if VARIANT == 'debian' and hasattr(console, 'close'):
                console.close()


def disk_checks(stage, nonce):
    script = 'mkdir -p /mnt/test-ext4 /mnt/test-btrfs\n'
    if stage == 1:
        script += '''printf '%s\\n' 'label: gpt' ',,L' | sfdisk /dev/vdb
udevadm settle --timeout=30
test -b /dev/vdb1
mkfs.ext4 -F -b 4096 /dev/vdb1 32768
e2fsck -fn /dev/vdb1
resize2fs /dev/vdb1
e2fsck -fn /dev/vdb1
mkfs.btrfs -f /dev/vdc
'''
    script += '''sfdisk --dump /dev/vdb | grep -qx 'label: gpt'
mount UUID="$(blkid -s UUID -o value /dev/vdb1)" /mnt/test-ext4
mount UUID="$(blkid -s UUID -o value /dev/vdc)" /mnt/test-btrfs
findmnt -n -o FSTYPE /mnt/test-ext4 | grep -qx ext4
findmnt -n -o FSTYPE /mnt/test-btrfs | grep -qx btrfs
'''
    if stage == 1:
        script += f'''printf '%s\\n' '{nonce}' > /mnt/test-ext4/persistent
btrfs subvolume create /mnt/test-btrfs/data
printf '%s\\n' '{nonce}' > /mnt/test-btrfs/data/persistent
btrfs subvolume snapshot -r /mnt/test-btrfs/data /mnt/test-btrfs/snapshot
printf 'modified\\n' > /mnt/test-btrfs/data/persistent
'''
    script += f'''test "$(cat /mnt/test-ext4/persistent)" = '{nonce}'
test "$(cat /mnt/test-btrfs/snapshot/persistent)" = '{nonce}'
test "$(cat /mnt/test-btrfs/data/persistent)" = modified
btrfs scrub start -B /mnt/test-btrfs
btrfs device stats /mnt/test-btrfs
sync
umount /mnt/test-ext4 /mnt/test-btrfs
e2fsck -fn /dev/vdb1
btrfs check --readonly /dev/vdc
'''
    # With an NVMe root the two virtio test data disks are vda/vdb.
    return script.replace('/dev/vdc', '/dev/DATA_BTRFS').replace('/dev/vdb', '/dev/vda').replace('/dev/DATA_BTRFS', '/dev/vdb')


def send_usb_key(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(str(path))
        stream = client.makefile('rwb', buffering=0)
        json.loads(stream.readline())
        def execute(command, arguments=None):
            stream.write((json.dumps({'execute': command, 'arguments': arguments or {}}) + '\n').encode())
            while True:
                response = json.loads(stream.readline())
                if 'error' in response:
                    raise RuntimeError(response['error'])
                if 'return' in response:
                    return
        execute('qmp_capabilities')
        execute('send-key', {'keys': [{'type': 'qcode', 'data': 'a'}]})


def package_checks(stage, nonce):
    script = '''for tool in apt apt-get apt-cache dpkg dpkg-query perl bash update-initramfs nvme smartctl strace tcpdump lspci lsusb; do
    command -v "$tool"
done
dpkg --audit
apt-get check
test "$(date +%Z)" = UTC
mountpoint -q /boot/efi
systemctl is-enabled apt-daily.timer | grep -qx masked
systemctl is-enabled apt-daily-upgrade.timer | grep -qx masked
'''
    if stage == 1:
        script += f'''logger 'ZXS_PERSISTENT_JOURNAL_{nonce}'
journalctl --sync
apt-get --yes install /mnt/zxs-test/zxs-apt-test.deb
dpkg-query -W zxs-apt-test
test "$(cat /usr/share/zxs-apt-test/marker)" = offline-package-test
test -s /boot/efi/EFI/BOOT/BOOTX64.EFI
'''
    else:
        script += f'''journalctl -b -1 --no-pager | grep 'ZXS_PERSISTENT_JOURNAL_{nonce}'
test "$(cat /usr/share/zxs-apt-test/marker)" = offline-package-test
dpkg-query -W zxs-apt-test
apt-get --yes purge zxs-apt-test
test ! -e /usr/share/zxs-apt-test/marker
'''
    script += '''test -z "$(find /lib/modules -name 'virtio_net.ko*' -o -name 'e1000e.ko*')"
test -z "$(systemctl --failed --no-legend --plain)"
'''
    return script


def make_debian_fixture(folder):
    tree = folder / 'fixture'
    shutil.rmtree(tree, ignore_errors=True)
    shutil.copytree(OUTPUT / 'test-modules', tree)
    (tree / 'console').write_text('''#!/bin/sh
set -eu
cp -a /mnt/zxs-test/lib/modules/. /lib/modules/
depmod -a
modprobe virtio_console
for attempt in $(seq 1 30); do
    test ! -e /dev/virtio-ports/zxs-test || break
    sleep 1
done
exec 3<> /dev/virtio-ports/zxs-test
printf 'ZXS_TEST_CHANNEL_READY\\n' >&3
exec /bin/sh -i <&3 >&3 2>&1
''')
    (tree / 'console').chmod(0o755)
    subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror',
                    str(ROOT / 'scripts/test-usb-keyboard.c'), '-o', str(tree / 'usb-keyboard')], check=True)
    package = folder / 'test-package'
    shutil.rmtree(package, ignore_errors=True)
    (package / 'DEBIAN').mkdir(parents=True)
    (package / 'DEBIAN/control').write_text('Package: zxs-apt-test\nVersion: 1.0\nArchitecture: all\nMaintainer: ZXS build test\nDescription: Offline package-management validation fixture\n')
    (package / 'usr/share/zxs-apt-test').mkdir(parents=True)
    (package / 'usr/share/zxs-apt-test/marker').write_text('offline-package-test\n')
    subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(package), str(tree / 'zxs-apt-test.deb')], check=True)
    image = folder / 'fixture.img'
    with image.open('wb') as stream:
        stream.truncate(32 * 1024**2)
    subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-d', str(tree), str(image)], check=True)


def main():
    identity = artifact_identity()
    require_current_report('verify', identity)
    folder = WORK / 'boot-test'
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cp', '--sparse=always', str(OUTPUT / 'images/disk.img'), str(folder / 'disk.img')], check=True)
    (folder / 'OVMF_VARS.fd').unlink(missing_ok=True)
    if VARIANT == 'debian':
        make_debian_fixture(folder)
        for name, size in (('ext4-test.img', 256), ('btrfs-test.img', 512)):
            with (folder / name).open('wb') as stream:
                stream.truncate(size * 1024**2)
    password = 'Test-' + secrets.token_urlsafe(20)
    nonce = secrets.token_hex(12)
    backend = folder / 'test-http-server'
    compiler = 'gcc' if VARIANT == 'debian' else str(OUTPUT / 'host/bin/x86_64-linux-gcc')
    subprocess.run([compiler, '-O2', '-Wall', '-Wextra', '-Werror',
                    str(ROOT / 'scripts/test-http-server.c'), '-o', str(backend)], check=True)
    Handler.backend_payload = backend.read_bytes()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    passed = []
    timings = []
    try:
        for stage in (1, 2):
            timings.append(boot(stage, folder, password, nonce, server.server_port))
            tools = 'Debian dash and GNU tools' if VARIANT == 'debian' else 'BusyBox shell and commands'
            passed.append(f'UEFI disk boot {stage}, password login, {tools}, framebuffer console, IPv4 DHCP, HTTP request, IPv4LL on isolated e1000e NIC, forwarding disabled')
            passed.append(f'HTTPS boot {stage}: TLS 1.2/1.3 to loopback HTTP, trusted custom certificate, DNS/IP SAN verification, rejection of unknown certificate and wrong hostname, unprivileged TLS daemon')
            if stage == 1:
                passed.append('HTTPS configuration: disabled without credentials, generate and import custom certificate, reject mismatched key without replacing certificate, private key permissions')
                passed.append(('Native systemd coldplug recovery' if SYSTEMD else 'Delayed udev startup')
                              + ': replay coldplug, autoload e1000e, restore DHCP/HTTP and IPv4LL')
            if SYSTEMD:
                passed.append(f'Systemd boot {stage}: native services, no failed units, HTTPS automatic restart after SIGKILL')
            if VARIANT == 'debian':
                passed.append(f'Disk boot {stage}: ext4 UUID mount, e2fsck, Btrfs read-only snapshot, scrub, check and persistent data')
            print(f'UEFI boot {stage}: passed')
        if VARIANT == 'debian':
            passed.append('Unmodified factory image: VGA/USB keyboard password setup and login, xHCI USB rescue media, physical serial and PS/2 disabled, no legacy USB/optical modules')
            passed.append('NVMe root; actual USB HID key input; UTC; complete apt/dpkg; disabled automatic updates')
            passed.append('Offline APT install, cold reboot through regenerated initramfs/EFI loader, package persistence and purge; hardware policy reapplied')
            passed.append('Persistent journal retains a diagnostic marker from the previous boot')
        passed.append('Root password, initialization state and test data survive a cold reboot')
        passed.append('HTTPS certificate and private key persist; HTTPS starts automatically after a cold reboot')
        path = report('boot-test', {
            'status': 'passed', 'checks': passed, 'artifacts': identity,
            'timings': timings,
            'timing_scope': ('QEMU TCG with VGA OCR and USB input; prompt recognition overhead included; two boots, not a hardware benchmark'
                             if VARIANT == 'debian' else
                             'QEMU TCG wall time from process launch to first console prompt; two boots, not a hardware benchmark'),
            'logs': [str(folder / f'boot-{stage}.log') for stage in (1, 2)],
            'network_test_scope': ('Virtual NIC drivers on separate test media, injected into test copy; factory disk contains only thunderbolt-net'
                                   if VARIANT == 'debian' else 'QEMU virtual wired NICs'),
            'unrun_physical_checks': (['288V Xe DP output', 'Thunderbolt/USB4 host-to-host link and hotplug',
                                       'Physical NVMe, USB devices and hardware boot performance']
                                      if VARIANT == 'debian' else ['Intel/AMD DP output', 'Thunderbolt/USB4 link and hotplug',
                                                                  'Physical PCIe/USB NIC throughput and hotplug']),
        })
        print(f'Boot validation passed; {path}')
    except Exception as error:
        report('boot-test', {'status': 'failed', 'checks_passed': passed, 'artifacts': identity, 'error': str(error)})
        raise
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Boot validation failed: {error}', file=sys.stderr)
        sys.exit(1)
