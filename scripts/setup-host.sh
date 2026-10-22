#!/usr/bin/env bash
# Rootless Debian 13 host dependencies, authenticated by APT's Debian archive keyring.
set -euo pipefail
ROOT=${ZXS_ROOT:-$(cd -- "$(dirname -- "$0")/.." && pwd)}
CACHE=${ZXS_CACHE_DIR:-"$ROOT/.cache"}
APT_DIR="$CACHE/apt"
PREFIX="$CACHE/host"
setup_digest=$(sha256sum "$0" | cut -d ' ' -f 1)
if [[ -f $PREFIX/.ready ]] && [[ $(cat "$PREFIX/.ready") == "$setup_digest" ]] && [[ -x $PREFIX/bin/qemu-system-x86_64 ]]; then
    echo "Host dependencies retained in $PREFIX"
    exit 0
fi
[[ -x /usr/bin/apt-get && -f /usr/share/keyrings/debian-archive-keyring.gpg ]] || {
    echo 'Use a Debian 13 host with APT and debian-archive-keyring.' >&2; exit 1;
}
mkdir -p "$APT_DIR/lists/partial" "$APT_DIR/archives/partial" "$APT_DIR/empty.conf.d" "$PREFIX"
python3 - "$APT_DIR" <<'PY'
import json, os, sys
from pathlib import Path
base=Path(sys.argv[1])
(base/'sources.list').write_text(
    'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main\n'
    'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://security.debian.org/debian-security trixie-security main\n')
settings={
 'Dir::Etc::parts':str(base/'empty.conf.d'), 'Dir::Etc::main':'-',
 'Dir::Etc::sourcelist':str(base/'sources.list'), 'Dir::Etc::sourceparts':str(base/'empty.conf.d'),
 'Dir::State':str(base), 'Dir::State::lists':str(base/'lists'),
 'Dir::State::status':'/var/lib/dpkg/status', 'Dir::Cache':str(base),
 'Dir::Cache::archives':str(base/'archives'), 'Debug::NoLocking':'true',
 'APT::Install-Recommends':'false', 'APT::Install-Suggests':'false',
 'Acquire::Retries':'2', 'Acquire::https::Timeout':'30',
}
if os.environ.get('SSL_CERT_FILE'):
    settings['Acquire::https::CaInfo']=os.environ['SSL_CERT_FILE']
(base/'apt.conf').write_text(''.join(f'{key} {json.dumps(value)};\n' for key,value in settings.items()))
PY
export APT_CONFIG="$APT_DIR/apt.conf"
export http_proxy=${http_proxy:-${HTTP_PROXY:-}}
export https_proxy=${https_proxy:-${HTTPS_PROXY:-}}
/usr/bin/apt-get update
/usr/bin/apt-get --assume-yes --no-upgrade --download-only install \
    build-essential curl file git gpg patch perl python3 unzip \
    rsync bc cpio bison flex qemu-system-x86 ovmf shellcheck mtools dosfstools gawk wget xz-utils bzip2 gpgv
for package in "$APT_DIR"/archives/*.deb; do
    dpkg-deb --extract "$package" "$PREFIX"
done
python3 - "$PREFIX" <<'PY'
import os, shlex, sys
from pathlib import Path
prefix=Path(sys.argv[1])
wrappers=prefix/'bin'
wrappers.mkdir(exist_ok=True)
library_path=f'{prefix}/usr/lib/x86_64-linux-gnu:{prefix}/lib/x86_64-linux-gnu'
for directory in ('usr/bin','usr/sbin','sbin'):
    for binary in (prefix/directory).glob('*'):
        if binary.is_file() and os.access(binary,os.X_OK):
            wrapper=wrappers/binary.name
            wrapper.write_text('#!/bin/sh\nLD_LIBRARY_PATH='+shlex.quote(library_path)+
                '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\nexport LD_LIBRARY_PATH\nexec '+
                shlex.quote(str(binary))+' "$@"\n')
            wrapper.chmod(0o755)
PY
printf '%s\n' "$setup_digest" > "$PREFIX/.ready"

