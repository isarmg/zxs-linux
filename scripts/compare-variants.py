#!/usr/bin/env python3
"""Compare the two packaged, validated variants without treating QEMU as hardware."""
import json

from common import ROOT, sha256

summary = {
    'scope': 'Two QEMU TCG observations per variant, not a physical-device benchmark. '
             'Use the same idle host; first-boot prompt and initialized login are distinct cases.',
    'variants': {},
}
for variant, suffix, prefix in (('busybox', '', ''), ('systemd', '-systemd', 'systemd-')):
    folder = ROOT / 'dist' / ('zxs-linux-amd64' + suffix)
    validation = json.loads((folder / (prefix + 'validation.json')).read_text())
    image, boot = validation['image'], validation['boot']
    if not (validation.get('variant') == variant and image['status'] == boot['status'] == 'passed'
            and image['artifacts'] == boot['artifacts'] and len(boot.get('timings', [])) == 2):
        raise SystemExit(f'Rebuild, verify, test and package the current {variant} variant first.')
    checksums = dict((name, digest) for digest, name in (
        line.split('  ', 1) for line in (folder / (prefix + 'SHA256SUMS')).read_text().splitlines()))
    compressed = folder / (prefix + 'disk.img.xz')
    for path in (compressed, folder / (prefix + 'validation.json')):
        if sha256(path) != checksums[path.name]:
            raise SystemExit(f'Checksum mismatch: {path}')
    summary['variants'][variant] = {'image_xz_bytes': compressed.stat().st_size,
                                   'artifacts': boot['artifacts'], 'timings': boot['timings']}

destination = ROOT / 'dist/variant-comparison.json'
destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print('| Variant | Image MiB | First console seconds | Initialized login seconds |')
print('| --- | ---: | ---: | ---: |')
for variant, data in summary['variants'].items():
    first, second = data['timings']
    print(f"| {variant} | {data['image_xz_bytes'] / 1024**2:.2f} | "
          f"{first['console_ready_wall_seconds']:.3f} | {second['console_ready_wall_seconds']:.3f} |")
print(f'QEMU observations only; {destination}')
