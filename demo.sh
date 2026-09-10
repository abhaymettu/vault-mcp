#!/usr/bin/env bash
# Proves the wire protocol: starts the server on a throwaway vault and sends
# initialize + initialized + one tools/call over stdio. Prints raw JSON-RPC replies.
set -euo pipefail
cd "$(dirname "$0")"
VAULT="$(mktemp -d)"
trap 'rm -rf "$VAULT"' EXIT
mkdir -p "$VAULT/projects"
printf '# Alpha\n\nfirst note about cats\n' > "$VAULT/alpha.md"
printf '# Beta\n\ncats and dogs\n' > "$VAULT/projects/beta.md"

send() { printf '%s\n' "$1"; sleep 0.5; }
{
  send '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"demo","version":"0"}}}'
  send '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  send '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_notes","arguments":{"query":"cats"}}}'
} | .venv/bin/python server.py "$VAULT"
