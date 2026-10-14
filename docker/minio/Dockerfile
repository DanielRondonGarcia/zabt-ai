FROM golang:1.24.8-alpine3.22@sha256:3d78beb141d98f42337f1252ecf2a5f20374109929a4c3f6817f9e4179cc0ae5 AS build

ARG TARGETARCH
ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a

RUN apk add --no-cache git

WORKDIR /src
RUN git clone --depth 1 --branch "${MINIO_VERSION}" https://github.com/minio/minio.git . \
    && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}"

ENV CGO_ENABLED=0 \
    GOOS=linux \
    GOARCH=${TARGETARCH}

RUN mkdir -p /out \
    && release_date="${MINIO_VERSION#RELEASE.}" \
    && ldflags="$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go "${release_date}")" \
    && go build -tags kqueue -trimpath --ldflags "${ldflags}" -o /out/minio

FROM alpine:3.22.2@sha256:4b7ce07002c69e8f3d704a9c5d6fd3053be500b7f1c69fc0d80990c2ad8dd412

ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z

LABEL org.opencontainers.image.source="https://github.com/danielrondongarcia/zabt-ai" \
    org.opencontainers.image.title="Zabt MinIO server" \
    org.opencontainers.image.upstream.version="${MINIO_VERSION}"

RUN apk add --no-cache ca-certificates \
    && update-ca-certificates

COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/licenses/minio/LICENSE

WORKDIR /data
VOLUME ["/data"]
EXPOSE 9000 9001

ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
