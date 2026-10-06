FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential binutils git perl liblzma-dev ca-certificates && \
    rm -rf /var/lib/apt/lists/*
ARG IPXE_REVISION=12798ec29aa8a64d8675c4378b99f5fe28447afb
RUN git init /build && git -C /build remote add origin https://github.com/ipxe/ipxe.git && \
    git -C /build fetch --depth=1 origin "$IPXE_REVISION" && git -C /build checkout --detach FETCH_HEAD && \
    test "$(git -C /build rev-parse HEAD)" = "$IPXE_REVISION"
COPY bootstrap.ipxe /build/bootstrap.ipxe
RUN mkdir -p /build/src/config/local && \
    printf '#define DOWNLOAD_PROTO_HTTPS\n' > /build/src/config/local/general.h && \
    make -C /build/src -j2 bin-x86_64-efi/ipxe.efi EMBED=/build/bootstrap.ipxe \
    TRUST=/etc/ssl/certs/ISRG_Root_X1.pem CERT=/etc/ssl/certs/ISRG_Root_X1.pem
WORKDIR /output
CMD ["/bin/sh", "-ec", "install -m 0644 /build/src/bin-x86_64-efi/ipxe.efi homelab-ipxe.efi; sha256sum homelab-ipxe.efi > homelab-ipxe.efi.sha256"]
