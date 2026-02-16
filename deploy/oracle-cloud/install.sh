#!/bin/bash
set -euo pipefail

# =============================================================================
# ArBot - Install & Configure
# Run after setup.sh. Sets up DB, venv, systemd service.
# =============================================================================

ARBOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ARBOT_DIR/deploy/oracle-cloud/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found. Copy .env.example first."
    exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

echo "=== ArBot Install ==="

# --- 1. PostgreSQL DB setup ---
echo "[1/4] Configuring PostgreSQL..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='arbot'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER arbot WITH PASSWORD '${POSTGRES_PASSWORD}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='arbot'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE arbot OWNER arbot;"

# --- 2. Redis password ---
echo "[2/4] Configuring Redis..."
if [ -n "${REDIS_PASSWORD:-}" ]; then
    sudo sed -i "s/^# requirepass .*/requirepass ${REDIS_PASSWORD}/" /etc/redis/redis.conf
    sudo sed -i "s/^requirepass .*/requirepass ${REDIS_PASSWORD}/" /etc/redis/redis.conf
    sudo systemctl restart redis-server
fi

# --- 3. Python venv + install ---
echo "[3/4] Setting up Python environment..."
cd "$ARBOT_DIR"
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .

# --- 4. Systemd service ---
echo "[4/4] Installing systemd service..."
sudo tee /etc/systemd/system/arbot.service > /dev/null <<UNIT
[Unit]
Description=ArBot Crypto Arbitrage System
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$ARBOT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$ARBOT_DIR/.venv/bin/python -m arbot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable arbot

echo ""
echo "=== Install Complete ==="
echo ""
echo "Commands:"
echo "  sudo systemctl start arbot     # start"
echo "  sudo systemctl stop arbot      # stop"
echo "  sudo systemctl restart arbot   # restart"
echo "  journalctl -u arbot -f         # logs"
echo ""
echo "Update:"
echo "  cd $ARBOT_DIR && git pull && .venv/bin/pip install . && sudo systemctl restart arbot"
echo ""
