#!/usr/bin/env bash
# Start Mana Ledger in the background if it isn't running yet.
# Runs every time the Codespace starts or you open it, so the app is always up.
cd "$(dirname "$0")/.." || exit 1

if (echo > /dev/tcp/127.0.0.1/8000) 2>/dev/null; then
  echo "Mana Ledger is already running on port 8000."
  exit 0
fi

python manage.py migrate --noinput -v0 || echo "Couldn't update the database tables; starting anyway."
# setsid + nohup keep the server running after this setup step finishes.
setsid nohup python manage.py runserver 0.0.0.0:8000 > /tmp/manaledger.log 2>&1 < /dev/null &

# Wait (up to 20 seconds) until it answers, so the browser tab opens on a working page.
for _ in $(seq 1 40); do
  if (echo > /dev/tcp/127.0.0.1/8000) 2>/dev/null; then
    echo "Mana Ledger is running: open the Ports tab or the tab that just opened."
    echo "Server log: /tmp/manaledger.log"
    exit 0
  fi
  sleep 0.5
done
echo "Mana Ledger didn't answer yet. See /tmp/manaledger.log, or run: python manage.py runserver 0.0.0.0:8000"
