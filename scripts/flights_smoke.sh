#!/bin/bash
# Smoke test for the Swift app's local API (flight 1): health, MCP, one tool call, /v1. Quit the old app first (port 8000).
# Usage: scripts/flights_smoke.sh [agent key]   (the key from Settings › Agents; asked for when omitted)
set -u
KEY="${1:-}"
if [ -z "$KEY" ]; then read -r -p "Agent key (lb-…): " KEY; fi
H="http://127.0.0.1:8000"
step() { printf '\n== %s\n' "$1"; }
step "health"; curl -s "$H/health"; echo
step "MCP initialize"
SID=$(curl -s -D - -o /tmp/lb_init.json -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' "$H/mcp" | awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')
cat /tmp/lb_init.json; echo; echo "session: ${SID:-none}"
step "MCP tools/list (names only)"
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "Accept: application/json" ${SID:+-H "Mcp-Session-Id: $SID"} \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' "$H/mcp" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(", ".join(t["name"] for t in d["result"]["tools"]))'
step "MCP tools/call list_notebooks"
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "Accept: application/json" ${SID:+-H "Mcp-Session-Id: $SID"} \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_notebooks","arguments":{}}}' "$H/mcp" | head -c 1200; echo
step "MCP tools/call search_notebook (first notebook, 'late shipment')"
NB=$(curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "Accept: application/json" ${SID:+-H "Mcp-Session-Id: $SID"} \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_notebooks","arguments":{}}}' "$H/mcp" | python3 -c 'import json,sys
d=json.load(sys.stdin); c=d["result"]["content"][0]["text"]; j=json.loads(c); nbs=j.get("notebooks", j if isinstance(j, list) else []); print(nbs[0]["id"] if nbs else "")' 2>/dev/null)
echo "notebook: ${NB:-none}"
[ -n "$NB" ] && curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "Accept: application/json" ${SID:+-H "Mcp-Session-Id: $SID"} \
  -d "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/call\",\"params\":{\"name\":\"search_notebook\",\"arguments\":{\"notebook_id\":\"$NB\",\"query\":\"late shipment credit\",\"k\":3}}}" "$H/mcp" | head -c 1500; echo
step "/v1/models"; curl -s -H "Authorization: Bearer $KEY" "$H/v1/models"; echo
step "/v1/chat/completions (short)"
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"localbook","messages":[{"role":"user","content":"Say hello in five words."}],"max_tokens":40}' "$H/v1/chat/completions" | head -c 800; echo
step "a bad key is refused (expect 401)"
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer lb-wrong" "$H/v1/models"
