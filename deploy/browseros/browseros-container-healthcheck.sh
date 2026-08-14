#!/bin/sh
set -eu

endpoint=http://127.0.0.1:9000/mcp
headers='Content-Type: application/json'
accept='Accept: application/json, text/event-stream'

curl --fail --silent --show-error --max-time 4 \
    -H "$headers" -H "$accept" \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"browseros-healthcheck","version":"1"}}}' \
    "$endpoint" | jq -e '.result.capabilities.tools' >/dev/null

curl --fail --silent --show-error --max-time 4 \
    -H "$headers" -H "$accept" \
    --data '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    "$endpoint" | jq -e '.result.tools | type == "array" and length > 0' >/dev/null
