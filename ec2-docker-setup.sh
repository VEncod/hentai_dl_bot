#!/bin/bash
set -e

echo "=== Hentai DL Bot - Docker Setup for AWS EC2 ==="

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."

    # Clean up any previous failed Docker installation
    sudo rm -f /etc/apt/sources.list.d/docker.list
    sudo rm -f /etc/apt/keyrings/docker.gpg

    # Install prerequisites
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg lsb-release

    # Add Docker's official GPG key
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    # Add Docker repository
    ARCH=$(dpkg --print-architecture)
    CODENAME=$(lsb_release -cs)
    echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    # Install Docker
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

    # Add current user to docker group
    sudo groupadd -f docker
    sudo usermod -aG docker $USER

    echo "Docker installed successfully!"
else
    echo "Docker is already installed."
fi

# Ensure docker service is running
sudo systemctl start docker
sudo systemctl enable docker

# Clone or update the repository
REPO_DIR="$HOME/hentai_dl_bot"
if [ -d "$REPO_DIR" ]; then
    echo "Repository already exists. Pulling latest changes..."
    cd "$REPO_DIR"
    git pull
else
    echo "Cloning hentai_dl_bot repository..."
    git clone https://github.com/VEncod/hentai_dl_bot.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

# Create .env file with user input
echo ""
echo "Please provide the following environment variables for the bot:"
read -p "API_ID: " API_ID
read -p "API_HASH: " API_HASH
read -p "BOT_TOKEN: " BOT_TOKEN
read -p "MONGO_URL: " MONGO_URL
read -p "SESSION_STRING (optional, press Enter to skip): " SESSION_STRING

cat > "$REPO_DIR/.env" << EOF
API_ID=${API_ID}
API_HASH=${API_HASH}
BOT_TOKEN=${BOT_TOKEN}
MONGO_URL=${MONGO_URL}
SESSION_STRING=${SESSION_STRING}
EOF

echo ""
echo "Environment file created."

# Build and start with Docker Compose
echo "Building and starting the bot..."
sudo docker compose up -d --build

echo ""
echo "=== Setup Complete! ==="
echo "Your bot is now running in the background."
echo ""
echo "Useful commands:"
echo "  Check status:  sudo docker compose ps"
echo "  View logs:     sudo docker compose logs -f"
echo "  Stop bot:      sudo docker compose down"
echo "  Restart bot:   sudo docker compose restart"
