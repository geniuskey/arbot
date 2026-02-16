#!/bin/bash
set -euo pipefail

# =============================================================================
# ArBot - Oracle Cloud Always Free ARM VM Setup Script
# Target: Ubuntu 22.04+ ARM64 (Ampere A1)
# Resources: 4 OCPU, 24GB RAM, 200GB Block Volume
#
# Installs: Python 3.12, PostgreSQL 16, Redis 7, Prometheus, Grafana
# No Docker required - direct systemd deployment
# =============================================================================

echo "=== ArBot Oracle Cloud Setup ==="

# --- 1. System Update ---
echo "[1/8] System update..."
sudo apt-get update && sudo apt-get upgrade -y

# --- 2. uv + Python 3.12 ---
echo "[2/8] Installing uv..."
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
sudo apt-get install -y gcc

# --- 3. PostgreSQL 16 ---
echo "[3/8] Installing PostgreSQL 16..."
if ! command -v psql &> /dev/null; then
    sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg
    sudo apt-get update
    sudo apt-get install -y postgresql-16
fi
sudo systemctl enable postgresql
sudo systemctl start postgresql

# --- 4. Redis 7 ---
echo "[4/8] Installing Redis..."
if ! command -v redis-server &> /dev/null; then
    sudo apt-get install -y redis-server
fi
sudo systemctl enable redis-server
sudo systemctl start redis-server

# --- 5. Prometheus ---
echo "[5/8] Installing Prometheus..."
if ! command -v prometheus &> /dev/null; then
    sudo apt-get install -y prometheus
fi
sudo systemctl enable prometheus
sudo systemctl start prometheus

# --- 6. Grafana ---
echo "[6/8] Installing Grafana..."
if ! command -v grafana-server &> /dev/null; then
    sudo apt-get install -y apt-transport-https
    curl -fsSL https://apt.grafana.com/gpg.key | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/grafana.gpg
    echo "deb https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
    sudo apt-get update
    sudo apt-get install -y grafana
fi
sudo systemctl enable grafana-server
sudo systemctl start grafana-server

# --- 7. Firewall ---
echo "[7/8] Configuring firewall..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 3000 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true

echo "NOTE: Also open ports 3000, 8080 in OCI Security List"

# --- 8. Swap ---
echo "[8/8] Configuring swap..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# --- Setup ArBot ---
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. cd ~/arbot"
echo "  2. cp .env.example .env"
echo "  3. nano .env  (API keys, DB password 입력)"
echo "  4. chmod +x deploy/oracle-cloud/install.sh"
echo "  5. ./deploy/oracle-cloud/install.sh"
echo ""
