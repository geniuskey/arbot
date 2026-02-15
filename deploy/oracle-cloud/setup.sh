#!/bin/bash
set -euo pipefail

# =============================================================================
# ArBot - Oracle Cloud Always Free ARM VM Setup Script
# Target: Ubuntu 22.04+ ARM64 (Ampere A1)
# Resources: 4 OCPU, 24GB RAM, 200GB Block Volume
# =============================================================================

echo "=== ArBot Oracle Cloud Setup ==="

# --- 1. System Update ---
echo "[1/6] System update..."
sudo apt-get update && sudo apt-get upgrade -y

# --- 2. Install Docker ---
echo "[2/6] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "Docker installed. Re-login required for group changes."
fi

# --- 3. Install Docker Compose Plugin ---
echo "[3/6] Installing Docker Compose..."
sudo apt-get install -y docker-compose-plugin

# --- 4. Firewall Setup (iptables) ---
echo "[4/6] Configuring firewall..."
# Oracle Cloud uses iptables, not ufw
# Allow SSH (22), Grafana (3000), ArBot Dashboard (8080)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 3000 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true

echo "NOTE: Also open ports 3000, 8080 in OCI Security List (VCN > Subnet > Security List > Ingress Rules)"

# --- 5. Create ArBot Directory ---
echo "[5/6] Setting up ArBot directory..."
ARBOT_DIR="$HOME/arbot"
mkdir -p "$ARBOT_DIR"

# --- 6. Swap (optional, safety net) ---
echo "[6/6] Configuring swap..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Log out and back in (for docker group)"
echo "  2. cd $ARBOT_DIR"
echo "  3. git clone https://github.com/geniuskey/arbot.git ."
echo "  4. cp deploy/oracle-cloud/.env.example .env"
echo "  5. Edit .env with your API keys"
echo "  6. docker compose -f deploy/oracle-cloud/docker-compose.yml up -d"
echo "  7. Open OCI Security List ports: 3000 (Grafana), 8080 (Dashboard)"
echo ""
echo "Monitor: docker compose -f deploy/oracle-cloud/docker-compose.yml logs -f arbot"
