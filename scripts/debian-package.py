#!/usr/bin/env python3
"""Package a Debian image only after matching static and two-boot validation."""
import json
import os
import shutil
import subprocess
import tarfile

from common import DIST, OUTPUT, ROOT, artifact_identity, require_current_report
from debian_package import checksums, require_build_inputs

require_build_inputs()
identity = artifact_identity()
verification = require_current_report('verify', identity)
boot = require_current_report('boot-test', identity)
DIST.mkdir(parents=True, exist_ok=True)
with (DIST / 'debian-disk.img.xz').open('wb') as stream:
    subprocess.run(['xz', '-T', os.environ.get('JOBS', '4'), '-3', '--stdout', str(OUTPUT / 'images/disk.img')],
                   check=True, stdout=stream)
for source, name in ((OUTPUT / 'images/bzImage', 'bzImage'), (OUTPUT / 'images/initrd.img', 'initrd.img'),
                     (OUTPUT / 'linux.config', 'linux.config'), (OUTPUT / 'packages.json', 'packages.json'),
                     (OUTPUT / 'build.json', 'build.json'), (ROOT / 'debian/README.md', 'README.md')):
    shutil.copyfile(source, DIST / ('debian-' + name))
(DIST / 'debian-validation.json').write_text(json.dumps({'variant': 'debian', 'image': verification, 'boot': boot}, indent=2) + '\n')
files = subprocess.run(['git', '-c', 'safe.directory=' + str(ROOT), '-C', str(ROOT), 'ls-files', '-c', '-o', '--exclude-standard', '-z'],
                       check=True, capture_output=True).stdout.split(b'\0')
with tarfile.open(DIST / 'debian-build-sources.tar.gz', 'w:gz') as archive:
    for relative in sorted(set(files)):
        if relative:
            name = os.fsdecode(relative)
            archive.add(ROOT / name, arcname='zxs-linux/' + name, recursive=False)
checksums()
print(f'Packaged validated Debian image: {DIST}')
