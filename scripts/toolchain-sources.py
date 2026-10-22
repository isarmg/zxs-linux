#!/usr/bin/env python3
"""Produce a standalone source/license supplement for the pinned Bootlin SDK."""
import tarfile

from common import CACHE, ROOT, sha256
from redistribution import fetch_sources

entries, paths = fetch_sources()
destination = ROOT / 'dist/toolchain-sources'
destination.mkdir(parents=True, exist_ok=True)
archive_path = destination / 'toolchain-source-licenses.tar.gz'
temporary = archive_path.with_name(archive_path.name + '.part')
readme = CACHE / 'redistribution/TOOLCHAIN-SOURCES.md'
readme.write_text('''# Bootlin 2025.08-1 SDK corresponding sources and licenses

This supplements the SDK included by Buildroot legal-info in the release's
`source-licenses.tar.gz`. It includes the sources, applied patches and license
texts of the SDK components, plus its Buildroot source snapshot. The main
source/license archive contains the exact SDK build configuration, component
manifest and README. Every download's HTTPS origin and SHA256 are recorded in
`redistribution.json`.
''')
with tarfile.open(temporary, 'w:gz', compresslevel=1) as archive:
    for entry, path in zip(entries, paths):
        archive.add(path, arcname=entry['path'])
    archive.add(ROOT / 'keys/redistribution.json', arcname='redistribution.json')
    archive.add(readme, arcname=readme.name)
temporary.replace(archive_path)
(destination / 'toolchain-source-licenses.SHA256SUMS').write_text(
    f'{sha256(archive_path)}  {archive_path.name}\n')
print(f'Packaged {len(entries)} pinned SDK source/license files ({archive_path.stat().st_size / 1024**2:.1f} MiB)')
