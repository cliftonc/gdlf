#!/usr/bin/env bash
# Seed AdGuard with an admin user so the setup wizard is skipped on first run.
# Idempotent: if AdGuardHome.yaml already exists, do nothing.
#
# - Reads ADGUARD_ADMIN_PASSWORD from .env. If unset / "change-me" / empty,
#   generates a strong random password and writes it back to .env.
# - bcrypt-hashes it via a one-shot httpd:alpine container.
# - Writes a minimal AdGuardHome.yaml; AdGuard fills in the rest on first boot.
set -euo pipefail

cd "$(dirname "$0")/.."

CONF="config/adguard/conf/AdGuardHome.yaml"
ENV_FILE=".env"

if [[ -f "$CONF" ]]; then
  echo "AdGuard config already present at $CONF — skipping."
  exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "no .env found — run ./gdlf init from the repo root first" >&2
  exit 1
fi

current=$(grep -E '^ADGUARD_ADMIN_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)

# Regenerate if missing or still the example default.
if [[ -z "$current" || "$current" == "change-me" ]]; then
  # 20 chars, alphanumeric (avoids shell-quoting headaches in .env / yaml).
  pw=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)
  if grep -qE '^ADGUARD_ADMIN_PASSWORD=' "$ENV_FILE"; then
    # macOS sed needs '' after -i; GNU sed doesn't. Use a temp-file dance
    # to stay portable.
    tmp=$(mktemp)
    awk -v pw="$pw" '/^ADGUARD_ADMIN_PASSWORD=/ {print "ADGUARD_ADMIN_PASSWORD=" pw; next} {print}' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '\nADGUARD_ADMIN_PASSWORD=%s\n' "$pw" >> "$ENV_FILE"
  fi
  echo "generated ADGUARD_ADMIN_PASSWORD and saved to .env"
else
  pw="$current"
  echo "using existing ADGUARD_ADMIN_PASSWORD from .env"
fi

# bcrypt the password. httpd's htpasswd emits "$2y$..."; AdGuard's Go
# bcrypt accepts both $2a and $2y but the rest of the codebase uses $2a,
# so normalise.
echo "hashing password (one-shot httpd:alpine container)…"
raw=$(docker run --rm --entrypoint htpasswd httpd:2-alpine -bnBC 10 "" "$pw")
hash=${raw#:}                 # strip leading colon
hash=${hash//$'\n'/}          # strip newline
hash="${hash/\$2y\$/\$2a\$}"  # $2y$ -> $2a$

mkdir -p "$(dirname "$CONF")"
cat > "$CONF" <<YAML
# Seeded by ./gdlf init. AdGuard fills in defaults for everything else
# on first boot and rewrites this file in place.
http:
  address: 0.0.0.0:80
users:
  - name: admin
    password: $hash
dns:
  bind_hosts:
    - 0.0.0.0
  port: 53
schema_version: 34
YAML

echo "wrote $CONF (admin / <see .env>)"
