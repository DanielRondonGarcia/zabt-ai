FROM golang:1.23.10-alpine3.21@sha256:9c9700c95b03ded3fdaf8c6f55f1d333ba1e683ff398781d240b466bc8a81612 AS build

ARG TARGETARCH
ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z
ARG MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096

RUN apk add --no-cache git

WORKDIR /src
RUN git clone --depth 1 --branch "${MC_VERSION}" https://github.com/minio/mc.git . \
    && test "$(git rev-parse HEAD)" = "${MC_COMMIT}"

RUN mkdir -p /out \
    && release_date="${MC_VERSION#RELEASE.}" \
    && ldflags="$(MC_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${release_date}")" \
    && GO111MODULE=on GOOS=linux GOARCH=${TARGETARCH} CGO_ENABLED=0 \
        go build -trimpath -tags kqueue --ldflags "${ldflags}" -o /out/mc

FROM alpine:3.21.4@sha256:b6a6be0ff92ab6db8acd94f5d1b7a6c2f0f5d10ce3c24af348d333ac6da80685

ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z

LABEL org.opencontainers.image.source="https://github.com/danielrondongarcia/zabt-ai" \
    org.opencontainers.image.title="Zabt MinIO Client" \
    org.opencontainers.image.upstream.version="${MC_VERSION}"

RUN apk add --no-cache ca-certificates \
    && update-ca-certificates

COPY --from=build /out/mc /usr/local/bin/mc
COPY --from=build /src/LICENSE /usr/share/licenses/minio-mc/LICENSE

WORKDIR /work

ENTRYPOINT ["/usr/local/bin/mc"]
CMD ["--help"]
