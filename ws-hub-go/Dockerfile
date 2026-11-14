FROM golang:1.26-alpine AS builder
WORKDIR /src
COPY go.mod ./
COPY main.go main_test.go ./
RUN go mod tidy && CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /out/penpot-ws-go .

FROM alpine:3.23
RUN apk upgrade --no-cache && adduser -D -u 10001 wsuser
COPY --from=builder /out/penpot-ws-go /usr/local/bin/penpot-ws-go
USER wsuser
EXPOSE 4402
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["/usr/local/bin/penpot-ws-go", "-healthcheck"]
ENTRYPOINT ["/usr/local/bin/penpot-ws-go"]
