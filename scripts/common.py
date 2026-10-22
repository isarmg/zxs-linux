"""Paths and artifact identity shared by image validation and packaging."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get('ZXS_ROOT', Path(__file__).resolve().parents[1]))
VARIANT = os.environ.get('ZXS_VARIANT', 'busybox')
if VARIANT not in ('busybox', 'systemd'):
    raise ValueError('ZXS_VARIANT must be busybox or systemd')
CACHE = Path(os.environ.get('ZXS_CACHE_DIR', ROOT / '.cache'))
WORK = Path(os.environ.get('ZXS_WORK_DIR', ROOT / ('.work/systemd' if VARIANT == 'systemd' else '.work')))
OUTPUT = Path(os.environ.get('ZXS_OUTPUT_DIR', WORK / 'output'))
RESULTS = WORK / 'results'
DIST = ROOT / 'dist' / ('zxs-linux-amd64-systemd' if VARIANT == 'systemd' else 'zxs-linux-amd64')
ASSET_PREFIX = 'systemd-' if VARIANT == 'systemd' else ''
KERNEL_VERSION = '6.18.58'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def config(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith('# ') and line.endswith(' is not set'):
            values[line[2:-11]] = 'n'
        elif '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value
    return values


def artifact_identity():
    return {name: sha256(OUTPUT / 'images' / name) for name in ('disk.img', 'bzImage')}


def report(name, value):
    RESULTS.mkdir(parents=True, exist_ok=True)
    value['recorded_at'] = datetime.now(timezone.utc).isoformat()
    value['variant'] = VARIANT
    path = RESULTS / (name + '.json')
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    return path


def require_current_report(name, identity):
    value = json.loads((RESULTS / (name + '.json')).read_text())
    if (value.get('status') != 'passed' or value.get('artifacts') != identity
            or value.get('variant', 'busybox') != VARIANT):
        raise RuntimeError(f'{name} has not passed for the current image; rerun that stage.')
    return value
