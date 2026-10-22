#!/usr/bin/env python3
"""Package only artifacts whose current hashes passed image and boot validation."""
import json
import os
import shutil
import subprocess
import tarfile

from common import ASSET_PREFIX, CACHE, DIST, KERNEL_VERSION, OUTPUT, ROOT, VARIANT, artifact_identity, require_current_report, sha256

identity = artifact_identity()
verification = require_current_report('verify', identity)
boot_test = require_current_report('boot-test', identity)
destination = DIST
destination.mkdir(parents=True, exist_ok=True)
def asset(name):
    return destination / (ASSET_PREFIX + name)

image = asset('disk.img.xz')
with image.open('wb') as stream:
    subprocess.run(['xz', '-T', os.environ.get('JOBS', '4'), '-3', '--stdout', str(OUTPUT / 'images/disk.img')],
                   stdout=stream, check=True)
shutil.copyfile(OUTPUT / 'images/bzImage', asset('bzImage'))
shutil.copyfile(OUTPUT / '.config', asset('buildroot.config'))
shutil.copyfile(OUTPUT / 'build' / ('linux-' + KERNEL_VERSION) / '.config', asset('linux.config'))
shutil.copyfile(ROOT / 'README.md', asset('README.md'))
manifest = subprocess.run([
    'make', '--no-print-directory', '-s', '-C', str(CACHE / 'buildroot-2026.08'),
    'O=' + str(OUTPUT), 'BR2_EXTERNAL=' + str(ROOT / 'br2-external'),
    'BR2_DL_DIR=' + str(CACHE / 'downloads'), 'show-info',
], check=True, capture_output=True, text=True).stdout
packages = json.loads(manifest)
asset('packages.json').write_text(json.dumps(packages, indent=2) + '\n')
asset('validation.json').write_text(json.dumps({'variant': VARIANT, 'image': verification, 'boot': boot_test}, indent=2) + '\n')
files = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-c', '-o', '--exclude-standard', '-z'],
                       check=True, capture_output=True).stdout.split(b'\0')
with tarfile.open(asset('build-sources.tar.gz'), 'w:gz') as archive:
    for raw in sorted(set(files)):
        if raw:
            relative = os.fsdecode(raw)
            archive.add(ROOT / relative, arcname='zxs-linux/' + relative, recursive=False)
asset('SHA256SUMS').write_text(''.join(
    f'{sha256(path)}  {path.name}\n' for path in sorted(destination.iterdir())
    if path.is_file() and path.name != ASSET_PREFIX + 'SHA256SUMS'))
print(f'Packaged verified image and build sources in {destination}')
