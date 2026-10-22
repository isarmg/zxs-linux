ARG DEBIAN_BASE=debian:13-slim
FROM ${DEBIAN_BASE}
RUN --mount=type=secret,id=proxy_ca,required=true,mode=0444 \
    sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca update \
    && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca \
       install --assume-yes --no-install-recommends ca-certificates mmdebstrap \
       debian-archive-keyring gpgv e2fsprogs dosfstools fdisk grub-efi-amd64-bin \
       mtools python3 xz-utils zstd curl qemu-system-x86 ovmf gcc libc6-dev git kmod cpio \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
