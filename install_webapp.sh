#!/usr/bin/env bash
# market_coach live dashboard — final install (needs sudo). Run:
#   bash /home/kierr/market_coach/install_webapp.sh
set -euo pipefail

echo "==> Installing systemd service (marketcoach-web)"
sudo cp /home/kierr/market_coach/marketcoach-web.service /etc/systemd/system/marketcoach-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now marketcoach-web.service
sleep 1
sudo systemctl --no-pager --lines=0 status marketcoach-web.service | head -n 4 || true

echo "==> Local health check"
curl -s http://127.0.0.1:8015/healthz || true
echo

echo "==> Adding Caddy site block (if missing)"
if ! grep -q "marketcoach.twofound.cv" /etc/caddy/Caddyfile; then
  echo "" | sudo tee -a /etc/caddy/Caddyfile >/dev/null
  sudo tee -a /etc/caddy/Caddyfile >/dev/null < /home/kierr/market_coach/caddy-block.txt
  echo "   added."
else
  echo "   already present, skipping."
fi

echo "==> Validating and reloading Caddy"
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy

echo
echo "Done. Once the DNS record for marketcoach.twofound.cv points at this box"
echo "and Caddy has a cert (usually under a minute), visit:"
echo "  https://marketcoach.twofound.cv"
echo "Login: kierr / (see paper_state/webapp_password.txt)"
