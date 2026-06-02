#!/bin/bash
# Starts cloudflared tunnel and writes the public URL to tunnel-url.txt
URL_FILE="$(dirname "$0")/tunnel-url.txt"
cloudflared tunnel --url http://localhost:3000 2>&1 | tee /tmp/cloudflared.log | while IFS= read -r line; do
  if [[ "$line" =~ https://[a-z0-9-]+\.trycloudflare\.com ]]; then
    echo "${BASH_REMATCH[0]}" > "$URL_FILE"
    echo "Tunnel URL: ${BASH_REMATCH[0]}"
  fi
  echo "$line"
done
