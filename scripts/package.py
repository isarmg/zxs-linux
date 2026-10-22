#!/usr/bin/env python3
"""Package only artifacts whose current hashes passed image and boot validation."""
import json
import os
import shutil
import subprocess
import tarfile

from common import CACHE, KERNEL_VERSION, OUTPUT, ROOT, artifact_identity, require_current_report, sha256

identity = artifact_identity()
verification = require_current_report('verify', identity)
boot_test = require_current_report('boot-test', identity)
destination = ROOT / 'dist/zxs-linux-amd64'
destination.mkdir(parents=True, exist_ok=True)
image = destination / 'disk.img.xz'
with image.open('wb') as stream:
    subprocess.run(['xz', '-T', os.environ.get('JOBS', '4'), '-3', '--stdout', str(OUTPUT / 'images/disk.img')],
                   stdout=stream, check=True)
shutil.copyfile(OUTPUT / 'images/bzImage', destination / 'bzImage')
shutil.copyfile(OUTPUT / '.config', destination / 'buildroot.config')
shutil.copyfile(OUTPUT / 'build' / ('linux-' + KERNEL_VERSION) / '.config', destination / 'linux.config')
shutil.copyfile(ROOT / 'README.md', destination / 'README.md')
manifest = subprocess.run([
    'make', '--no-print-directory', '-s', '-C', str(CACHE / 'buildroot-2026.08'),
    'O=' + str(OUTPUT), 'BR2_EXTERNAL=' + str(ROOT / 'br2-external'),
    'BR2_DL_DIR=' + str(CACHE / 'downloads'), 'show-info',
], check=True, capture_output=True, text=True).stdout
packages = json.loads(manifest)
(destination / 'packages.json').write_text(json.dumps(packages, indent=2) + '\n')
(destination / 'validation.json').write_text(json.dumps({'image': verification, 'boot': boot_test}, indent=2) + '\n')
files = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-c', '-o', '--exclude-standard', '-z'],
                       check=True, capture_output=True).stdout.split(b'\0')
with tarfile.open(destination / 'build-sources.tar.gz', 'w:gz') as archive:
    for raw in sorted(set(files)):
        if raw:
            relative = os.fsdecode(raw)
            archive.add(ROOT / relative, arcname='zxs-linux/' + relative, recursive=False)
(destination / 'SHA256SUMS').write_text(''.join(
    f'{sha256(path)}  {path.name}\n' for path in sorted(destination.iterdir())
    if path.is_file() and path.name != 'SHA256SUMS'))
print(f'Packaged verified image and build sources in {destination}')
