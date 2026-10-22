#!/usr/bin/env python3
"""Boot the actual GPT/UEFI disk twice, exercising login, DHCP, HTTP and persistence."""
import http.server
import os
import re
import secrets
import select
import shlex
import shutil
import subprocess
import sys
import threading
import time

from common import CACHE, OUTPUT, WORK, artifact_identity, report, require_current_report


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b'zxs-linux-network-ok\n'
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class Console:
    def __init__(self, process, logfile, password):
        self.process, self.logfile, self.pending = process, logfile, ''
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
        complete = re.sub(rb'((?:New password|Retype password|Password):)[^\r\n]*', rb'\1 [redacted]', complete)
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
            ready, _, _ = select.select([self.process.stdout], [], [], 1)
            if ready:
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError(f'QEMU exited before {pattern!r}; inspect {self.logfile.name}')
                self.log(data)
                self.pending += data.decode(errors='replace')
                if len(self.pending) > 1024 * 1024:
                    self.pending = self.pending[-512 * 1024:]

    def send(self, line):
        self.process.stdin.write((line + '\n').encode())
        self.process.stdin.flush()


def boot(stage, folder, password, nonce, port):
    prefix = CACHE / 'host/usr/share'
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
    with (folder / f'boot-{stage}.log').open('wb') as logfile:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=0)
        console = Console(process, logfile, password)
        try:
            if stage == 1:
                console.expect('ZXS_FIRST_BOOT:')
                console.send('')
                console.expect('New password:')
                console.send(password)
                console.expect('Retype password:')
                console.send(password)
            console.expect('zxs-linux login:')
            console.send('root')
            console.expect('Password:')
            console.send(password)
            console.expect(r'^.*# $', timeout=30)
            marker = 'ZXS_TEST_PASS_' + nonce + '_' + str(stage)
            failure = 'ZXS_TEST_FAIL_' + nonce + '_' + str(stage)
            script = f'''set -eux
trap 'rc=$?; if [ "$rc" -ne 0 ]; then printf "\\n{failure}:%s\\n" "$rc"; fi' EXIT
uname -r
ip -4 addr
ip -4 route
test "$(uname -r)" = '6.18.58'
test -e /etc/zxs/initialized
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
            if stage == 1:
                script += f"printf '%s\\n' '{nonce}' > /etc/zxs/boot-test-persistence\n"
            else:
                script += f"test \"$(cat /etc/zxs/boot-test-persistence)\" = '{nonce}'\n"
            script += f"uname -r; ip -4 addr; printf '\\n{marker}\\n'; sync; poweroff\n"
            console.send('sh -c ' + shlex.quote(script))
            outcome = console.expect('^(' + marker + '|' + failure + r':[0-9]+)$', timeout=150)
            if outcome.group(1) != marker:
                diagnostic_marker = 'ZXS_DIAGNOSTICS_' + nonce
                diagnostic = ('ip -br link; ip -4 addr; ip -4 route; '
                              'for c in /sys/class/vtconsole/vtcon*; do cat "$c/name" "$c/bind"; done; '
                              'tail -60 /var/log/messages; '
                              f"printf '\\n{diagnostic_marker}\\n'")
                try:
                    console.send('sh -c ' + shlex.quote(diagnostic))
                    console.expect('^' + diagnostic_marker + '$', timeout=15)
                except (RuntimeError, OSError):
                    pass
                raise RuntimeError(f'Guest check failed: {outcome.group(1)}; inspect {logfile.name}')
            console.expect('Power down', timeout=30)
            process.wait(timeout=30)
            if process.returncode != 0:
                raise RuntimeError(f'QEMU returned {process.returncode}')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            console.log(b'', final=True)


def main():
    identity = artifact_identity()
    require_current_report('verify', identity)
    folder = WORK / 'boot-test'
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cp', '--sparse=always', str(OUTPUT / 'images/disk.img'), str(folder / 'disk.img')], check=True)
    (folder / 'OVMF_VARS.fd').unlink(missing_ok=True)
    password = 'Test-' + secrets.token_urlsafe(20)
    nonce = secrets.token_hex(12)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    passed = []
    try:
        for stage in (1, 2):
            boot(stage, folder, password, nonce, server.server_port)
            passed.append(f'UEFI disk boot {stage}, password login, framebuffer console, IPv4 DHCP, HTTP request, IPv4LL on isolated e1000e NIC, forwarding disabled')
            print(f'UEFI boot {stage}: passed')
        passed.append('Root password, initialization state and test data survive a cold reboot')
        path = report('boot-test', {
            'status': 'passed', 'checks': passed, 'artifacts': identity,
            'logs': [str(folder / f'boot-{stage}.log') for stage in (1, 2)],
            'unrun_physical_checks': ['Intel/AMD DP output', 'Thunderbolt/USB4 link and hotplug',
                                      'Physical PCIe/USB NIC throughput and hotplug'],
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

