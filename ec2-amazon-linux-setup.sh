#!/bin/bash
set -e

echo "=== Hentai DL Bot - Docker Setup for AWS EC2 (Amazon Linux) ==="

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."

    # Install Docker on Amazon Linux
    sudo yum update -y
    sudo yum install -y docker git

    # Start Docker service
    sudo systemctl start docker
    sudo systemctl enable docker

    # Add current user to docker group
    sudo usermod -aG docker $USER

    echo "Docker installed successfully!"
else
    echo "Docker is already installed."
fi

# Ensure docker service is running
sudo systemctl start docker
sudo systemctl enable docker

# Install Docker Compose plugin if not present
if ! sudo docker compose version &> /dev/null; then
    echo "Installing Docker Compose plugin..."
    sudo mkdir -p /usr/local/lib/docker/cli-plugins
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ]; then
        COMPOSE_ARCH="aarch64"
    else
        COMPOSE_ARCH="x86_64"
    fi
    sudo curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${COMPOSE_ARCH}" -o /usr/local/lib/docker/cli-plugins/docker-compose
    sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    echo "Docker Compose installed successfully!"
else
    echo "Docker Compose is already installed."
fi

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
