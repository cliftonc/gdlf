#!/usr/bin/env bash
# Generate the mitmproxy CA cert by booting mitmproxy briefly and letting it
# initialise its certdir. Idempotent — skips if the CA already exists.
set -euo pipefail

cd "$(dirname "$0")/.."

CA="config/mitmproxy/mitmproxy-ca-cert.pem"

if [[ -f "$CA" ]]; then
  echo "mitmproxy CA already present at $CA — skipping."
  exit 0
fi

mkdir -p config/mitmproxy
echo "Booting mitmproxy once to generate the CA…"
docker run --rm \
  -v "$(pwd)/config/mitmproxy:/home/mitmproxy/.mitmproxy" \
  --entrypoint mitmdump \
  mitmproxy/mitmproxy:latest \
  --set confdir=/home/mitmproxy/.mitmproxy \
  --listen-port 8088 \
  -q &
PID=$!

# Poll until the CA exists, up to 30s.
for _ in $(seq 1 30); do
  if [[ -f "$CA" ]]; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    echo "CA generated: $CA"
    exit 0
  fi
  sleep 1
done

kill "$PID" 2>/dev/null || true
echo "Failed to generate CA — check that the mitmproxy image pulled correctly." >&2
exit 1
