#!/bin/bash
set -e

# Setup script for a fresh Ubuntu 24.04 EC2 instance
# Usage: scp this file to the instance and run: bash setup.sh

echo "==> Installing AWS CLI v2"
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
sudo apt-get update
sudo apt-get install -y unzip
unzip -qo /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install
rm -rf /tmp/awscliv2.zip /tmp/aws

echo "==> Installing Docker"
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "==> Adding ubuntu user to docker group"
sudo usermod -aG docker ubuntu

echo "==> Creating ~/medsecure directory"
mkdir -p ~/medsecure

echo "==> Setting up SQLite backup cron"
sudo cp /home/ubuntu/medsecure/scripts/backup-sqlite.sh /usr/local/bin/medsecure-backup
sudo chmod +x /usr/local/bin/medsecure-backup
(crontab -l 2>/dev/null; echo "0 */6 * * * /usr/local/bin/medsecure-backup") | crontab -

echo ""
echo "Setup complete! Next steps:"
echo "  1. Log out and back in (for docker group to take effect)"
echo "  2. docker login ghcr.io -u watate"
echo "  3. Copy docker-compose.yml, Caddyfile to ~/medsecure/"
echo "  4. Run: cd ~/medsecure && bash deploy.sh"
