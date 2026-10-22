"""Debian artifact checksum manifest shared with license collection."""
from common import DIST, sha256


def checksums():
    (DIST / 'debian-SHA256SUMS').write_text(''.join(
        f'{sha256(path)}  {path.name}\n' for path in sorted(DIST.iterdir())
        if path.is_file() and path.name != 'debian-SHA256SUMS'))
