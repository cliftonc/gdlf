FROM mitmproxy/mitmproxy:latest

# Add httpx (used by our addon to call rules-svc).
# mitmproxy ships with pip; httpx pulls in httpcore + h11 only.
USER root
RUN pip install --no-cache-dir --break-system-packages httpx
# Don't switch USER — the base image's entrypoint runs as root and drops
# to the mitmproxy user via su-exec.
