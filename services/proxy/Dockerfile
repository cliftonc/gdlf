# Custom Caddy build with the Cloudflare DNS provider plugin baked in.
# Needed so Caddy can do DNS-01 ACME challenges (we listen on a non-standard
# port behind a router forward, so HTTP-01 and TLS-ALPN-01 are unreachable).
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
