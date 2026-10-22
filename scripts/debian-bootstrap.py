#!/usr/bin/env python3
"""Import the digest-pinned official Debian OCI rootfs via the inherited HTTPS proxy."""
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
pin = json.loads((root / 'debian/bootstrap.json').read_text())
cache = root / '.cache/debian'
cache.mkdir(parents=True, exist_ok=True)
archive = cache / (pin['sha256'] + '.tar.gz')

def valid():
    return (archive.exists() and archive.stat().st_size == pin['size']
            and hashlib.sha256(archive.read_bytes()).hexdigest() == pin['sha256'])

if not valid():
    url = f"https://raw.githubusercontent.com/{pin['repository']}/{pin['commit']}/{pin['path']}"
    temporary = archive.with_suffix('.part')
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as stream:
        while block := response.read(1024**2):
            stream.write(block)
    temporary.replace(archive)
    if not valid():
        raise RuntimeError('Official Debian rootfs digest or size mismatch')
label = 'org.zxs.bootstrap.sha256'
existing = subprocess.run(['docker', 'image', 'inspect', '--format',
                           '{{index .Config.Labels "' + label + '"}}', 'zxs-debian-bootstrap:13'],
                          capture_output=True, text=True)
if existing.returncode or existing.stdout.strip() != pin['sha256']:
    subprocess.run(['docker', 'import', '--change', 'CMD ["bash"]', '--change',
                    f'LABEL {label}={pin["sha256"]}', str(archive), 'zxs-debian-bootstrap:13'], check=True)
