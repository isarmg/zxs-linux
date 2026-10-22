#!/usr/bin/env python3
"""Bundle Buildroot legal-info with the external toolchain's complete component sources."""
import tarfile

from common import CACHE, OUTPUT, ROOT, artifact_identity, require_current_report, sha256
from redistribution import fetch_sources

identity = artifact_identity()
require_current_report('verify', identity)
require_current_report('boot-test', identity)
legal = OUTPUT / 'legal-info'
if not (legal / 'manifest.csv').is_file():
    raise SystemExit('Run ./build.sh legal-info to collect package sources first.')

entries, downloaded = fetch_sources()
cache = CACHE / 'redistribution'

destination = ROOT / 'dist/zxs-linux-amd64'
destination.mkdir(parents=True, exist_ok=True)
archive_path = destination / 'source-licenses.tar.gz'
temporary = archive_path.with_name(archive_path.name + '.part')
supplement = cache / 'SUPPLEMENT.md'
supplement.write_text('''# Corresponding sources and licenses

`buildroot-legal-info/` is the unmodified Buildroot legal-info output, including
package sources, applied patches, licenses, manifests and build configuration.
Its generated README reports two automatic collection limitations; the files
below supplement both rather than changing that generated report.

`buildroot/` contains the verified Buildroot 2026.08 release and signature.
`toolchain/` contains the complete Bootlin 2025.08-1 SDK component sources,
applied patches and license texts, including GCC 14.3.0 and glibc 2.41.
Its build configuration, component manifest and
README come from the exact SHA256-pinned Bootlin 2025.08-1 SDK used to build
this image. The toolchain's Buildroot source snapshot is included too.
`redistribution.json` records pinned HTTPS origins and SHA256 for supplements.

The separate `build-sources.tar.gz` release asset contains this project's
external tree, image layout, kernel fragments and build/validation scripts.
See its README for build instructions and the component manifests for licenses.
''')
with tarfile.open(temporary, 'w:gz', compresslevel=1) as archive:
    archive.add(legal, arcname='buildroot-legal-info')
    for name in ['buildroot-2026.08.tar.xz', 'buildroot-2026.08.tar.xz.sign']:
        archive.add(CACHE / 'sources' / name, arcname='buildroot/' + name)
    archive.add(ROOT / 'keys/buildroot-release.asc', arcname='buildroot/release-key.asc')
    for entry, path in zip(entries, downloaded):
        archive.add(path, arcname=entry['path'])
    toolchain = OUTPUT / 'host/opt/ext-toolchain'
    for name in ['README.txt', 'buildroot.config', 'summary.csv']:
        archive.add(toolchain / name, arcname='toolchain/' + name)
    archive.add(ROOT / 'keys/redistribution.json', arcname='redistribution.json')
    archive.add(supplement, arcname='SUPPLEMENT.md')
temporary.replace(archive_path)
(destination / 'SHA256SUMS').write_text(''.join(
    f'{sha256(path)}  {path.name}\n' for path in sorted(destination.iterdir())
    if path.is_file() and path.name != 'SHA256SUMS'))
print(f'Source/license archive: {archive_path} ({archive_path.stat().st_size / 1024**2:.1f} MiB)')
