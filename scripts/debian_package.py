"""Debian build input identity and artifact checksum manifest."""
import json
import os
import subprocess

from common import DIST, OUTPUT, ROOT, sha256


def build_inputs():
    names = subprocess.run(['git', '-c', 'safe.directory=' + str(ROOT), '-C', str(ROOT),
                            'ls-files', '-c', '-o', '--exclude-standard', '-z'],
                           check=True, capture_output=True).stdout.split(b'\0')
    return {os.fsdecode(name): sha256(ROOT / os.fsdecode(name))
            for name in sorted(set(names)) if name and (ROOT / os.fsdecode(name)).is_file()}


def require_build_inputs():
    metadata = json.loads((OUTPUT / 'build.json').read_text())
    if metadata.get('build_inputs_sha256') != build_inputs():
        raise RuntimeError('Source inputs changed since the Debian image was built; rebuild and revalidate.')
    if metadata.get('package_manifest_sha256') != sha256(OUTPUT / 'packages.json'):
        raise RuntimeError('Installed-package manifest changed since the Debian image was built.')


def checksums():
    (DIST / 'debian-SHA256SUMS').write_text(''.join(
        f'{sha256(path)}  {path.name}\n' for path in sorted(DIST.iterdir())
        if path.is_file() and path.name != 'debian-SHA256SUMS'))
