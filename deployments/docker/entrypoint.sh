#!/bin/bash
# Container entrypoint: optional corporate/custom CA injection, then exec the app.
#
# Enterprise deployments behind a MITM proxy need the proxy's CA in the trust
# store, or every outbound TLS call (LLM APIs, MCP servers, OAuth) fails.
# Two ways to provide it (adapted from template-agent's entrypoint):
#   CUSTOM_CA_PATH — path to a mounted PEM file (preferred, no network call)
#   CUSTOM_CA_URL  — URL to download the PEM from (fallback)
set -euo pipefail

CA_PEM=""
if [ -n "${CUSTOM_CA_PATH:-}" ] && [ -f "$CUSTOM_CA_PATH" ]; then
  CA_PEM="$CUSTOM_CA_PATH"
elif [ -n "${CUSTOM_CA_URL:-}" ]; then
  if curl -fso /tmp/custom-ca.pem "$CUSTOM_CA_URL"; then
    CA_PEM="/tmp/custom-ca.pem"
  else
    echo "WARNING: CUSTOM_CA_URL download failed; continuing without custom CA" >&2
  fi
fi

if [ -n "$CA_PEM" ]; then
  # /app is root-owned and the app runs as non-root — use a writable path.
  BUNDLE_PATH="${TMPDIR:-/tmp}/.ca-bundle.pem"

  # Base bundle: certifi if present, else the system store.
  if command -v python3 &>/dev/null && python3 -m certifi &>/dev/null; then
    cp "$(python3 -m certifi)" "$BUNDLE_PATH"
  elif [ -f /etc/ssl/certs/ca-certificates.crt ]; then
    cp /etc/ssl/certs/ca-certificates.crt "$BUNDLE_PATH"
  else
    touch "$BUNDLE_PATH"
  fi
  chmod u+w "$BUNDLE_PATH"
  cat "$CA_PEM" >>"$BUNDLE_PATH"
  [ "$CA_PEM" = "/tmp/custom-ca.pem" ] && rm -f /tmp/custom-ca.pem

  # The standard variables Python/httpx/requests/curl/Node all honor.
  export REQUESTS_CA_BUNDLE="$BUNDLE_PATH"
  export SSL_CERT_FILE="$BUNDLE_PATH"
  export CURL_CA_BUNDLE="$BUNDLE_PATH"
  export PIP_CERT="$BUNDLE_PATH"
  export NODE_EXTRA_CA_CERTS="$BUNDLE_PATH"
  echo "Custom CA injected into $BUNDLE_PATH" >&2
fi

exec "$@"
