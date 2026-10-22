"""Exercise the unmodified local-console image through QMP USB keys and VGA OCR.

After real password setup/login, a read-only fixture supplies a temporary virtio
character channel for scripted checks. No physical serial console is enabled.
"""
import json
import os
import re
import socket
import subprocess
import time


def enlarge_ppm(source, destination):
    """Double QEMU RGB pixels for reliable OCR of the console's small font."""
    with source.open('rb') as image:
        if image.readline().strip() != b'P6':
            raise RuntimeError('Unexpected QEMU screenshot format')
        width, height = map(int, image.readline().split())
        if image.readline().strip() != b'255':
            raise RuntimeError('Unexpected QEMU screenshot depth')
        pixels = image.read()
    if len(pixels) != width * height * 3:
        raise RuntimeError('Incomplete QEMU screenshot')
    with destination.open('wb') as image:
        image.write(f'P6\n{width * 2} {height * 2}\n255\n'.encode())
        for y in range(height):
            row = pixels[y * width * 3:(y + 1) * width * 3]
            expanded = bytearray(width * 6)
            for channel in range(3):
                expanded[channel::6] = expanded[channel + 3::6] = row[channel::3]
            image.write(expanded)
            image.write(expanded)


class QMP:
    def __init__(self, path):
        self.client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.client.settimeout(15)
        deadline = time.monotonic() + 20
        while True:
            try:
                self.client.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    self.client.close()
                    raise
                time.sleep(0.1)
        self.stream = self.client.makefile('rwb', buffering=0)
        json.loads(self.stream.readline())
        self.execute('qmp_capabilities')

    def execute(self, name, arguments=None):
        self.stream.write((json.dumps({'execute': name, 'arguments': arguments or {}}) + '\n').encode())
        while True:
            line = self.stream.readline()
            if not line:
                raise RuntimeError('QMP connection closed')
            response = json.loads(line)
            if 'error' in response:
                raise RuntimeError(response['error'])
            if 'return' in response:
                return response['return']

    def close(self):
        self.stream.close()
        self.client.close()


class GraphicalConsole:
    def __init__(self, process, logfile, password, folder, qmp, channel, console_class):
        self.base = console_class(process, logfile, password)
        self.process, self.folder = process, folder
        self.qmp = QMP(qmp)
        self.channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.channel.settimeout(15)
        self.channel.connect(str(channel))
        self.graphics = True
        self.last_screen = ''
        self.stream = None

    def expect(self, pattern, timeout=180):
        if not self.graphics:
            return self.base.expect(pattern, timeout)
        if pattern == r'^.*# $':
            # A bare # is often read as 3. Prove that a local shell executes
            # a command, instead of relying on recognition of the prompt glyph.
            self.send("printf '\\nlocal console login ready\\n'")
            pattern = r'^local\s+console\s+login\s+ready\s*$'
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('QEMU exited during graphical console login')
            screenshot = self.folder / 'screen.ppm'
            self.qmp.execute('screendump', {'filename': str(screenshot)})
            enlarged = self.folder / 'screen-ocr.ppm'
            enlarge_ppm(screenshot, enlarged)
            result = subprocess.run(['tesseract', str(enlarged), 'stdout', '--psm', '6'],
                                    check=True, capture_output=True, text=True,
                                    env={**os.environ, 'OMP_THREAD_LIMIT': '1'})
            screen = result.stdout.replace('\r', '').replace('—', '-').replace('–', '-')
            # OCR omits the trailing space of a shell prompt.
            screen = re.sub(r'#[ \t]*$', '# ', screen, flags=re.MULTILINE)
            if screen != self.last_screen:
                self.base.log(('[VGA]\n' + screen + '\n').encode())
                self.last_screen = screen
            # Match the human prompt when OCR confuses the marker's Z/2 glyphs.
            screen_pattern = {'ZXS_FIRST_BOOT:': r'press Enter to set the local root password',
                              'zxs-linux login:': r'\blogin:'}.get(pattern, pattern)
            screen_pattern = screen_pattern.replace('password', 'pass[wu]ord').replace('Password', 'Pass[wu]ord')
            if screen_pattern.endswith(':'):
                screen_pattern = screen_pattern[:-1] + r'\s*:'
            matched = re.search(screen_pattern, screen, re.MULTILINE)
            if matched:
                return matched
            time.sleep(0.5)
        raise RuntimeError(f'Graphical console timeout waiting for {pattern!r}; inspect {screenshot}')

    def send(self, line):
        if not self.graphics:
            return self.base.send(line)
        punctuation = {' ': 'spc', '-': 'minus', '=': 'equal', '/': 'slash', '.': 'dot',
                       ',': 'comma', ';': 'semicolon', "'": 'apostrophe', '[': 'bracket_left',
                       ']': 'bracket_right', '\\': 'backslash', '`': 'grave_accent'}
        shifted = {'_': 'minus', ':': 'semicolon', '"': 'apostrophe', '+': 'equal',
                   '?': 'slash', '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
                   '^': '6', '&': '7', '*': '8', '(': '9', ')': '0', '<': 'comma',
                   '>': 'dot', '{': 'bracket_left', '}': 'bracket_right', '|': 'backslash',
                   '~': 'grave_accent'}
        for character in line + '\n':
            keys = []
            if character.isupper() or character in shifted:
                keys.append({'type': 'qcode', 'data': 'shift'})
            if character == '\n':
                code = 'ret'
            else:
                code = shifted.get(character, punctuation.get(character, character.lower()))
            keys.append({'type': 'qcode', 'data': code})
            self.qmp.execute('send-key', {'keys': keys, 'hold-time': 10})
            time.sleep(0.03)

    def activate_test_channel(self):
        self.send('mkdir -p /mnt/zxs-test; mount -o ro /dev/sda /mnt/zxs-test; exec /mnt/zxs-test/console')
        self.qmp.close()
        self.qmp = None
        self.stream = self.channel.makefile('rwb', buffering=0)
        self.base.reader = self.base.writer = self.stream
        self.graphics = False
        self.expect('^ZXS_TEST_CHANNEL_READY$', timeout=90)
        self.expect(r'^.*# $', timeout=30)

    def log(self, data, final=False):
        self.base.log(data, final)

    def close(self):
        if self.qmp is not None:
            self.qmp.close()
        if self.stream is not None:
            self.stream.close()
        self.channel.close()
