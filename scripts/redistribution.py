"""Fetch the pinned corresponding sources and licenses of the Bootlin SDK."""
import concurrent.futures
import json
import shutil
import urllib.request

from common import CACHE, ROOT, sha256


def fetch_sources():
    entries = json.loads((ROOT / 'keys/redistribution.json').read_text())

    def fetch(entry):
        relative = entry['path']
        if relative.startswith('/') or '..' in relative.split('/'):
            raise ValueError(f'Unsafe source path: {relative}')
        target = CACHE / 'redistribution' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and sha256(target) == entry['sha256']:
            return target
        temporary = target.with_name(target.name + '.part')
        if not entry['url'].startswith('https://'):
            raise ValueError('Source downloads require HTTPS')
        with urllib.request.urlopen(entry['url'], timeout=60) as response, temporary.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        if sha256(temporary) != entry['sha256']:
            raise RuntimeError(f'Source checksum mismatch: {relative}')
        temporary.replace(target)
        return target

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(fetch, entries))
    return entries, paths
