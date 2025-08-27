#!/bin/bash

# Setup script for Music Downloader Docker container

echo "Setting up Music Downloader..."

# Get current user ID and group ID
export USER_ID=$(id -u)
export GROUP_ID=$(id -g)

echo "User ID: $USER_ID"
echo "Group ID: $GROUP_ID"

# Create necessary directories
echo "Creating necessary directories..."
mkdir -p ~/Music
mkdir -p ~/.config/beets

# Set proper permissions for Music directory
echo "Setting permissions for Music directory..."
chmod 755 ~/Music

# Check if beets config exists, if not create a basic one
if [ ! -f ~/.config/beets/config.yaml ]; then
    echo "Creating basic beets configuration..."
    cat > ~/.config/beets/config.yaml << EOF
directory: ~/Music
library: ~/.config/beets/musiclibrary.db

import:
    move: yes
    write: yes
    
paths:
    default: \$albumartist/\$album/\$track \$title

plugins: fetchart embedart info
EOF
fi

# Make sure beets config directory has proper permissions
chmod -R 755 ~/.config/beets

# Create .env file for docker-compose
echo "Creating .env file..."
cat > .env << EOF
USER_ID=$USER_ID
GROUP_ID=$GROUP_ID
EOF

echo "Setup complete!"
echo ""
echo "Now you can run:"
echo "  docker-compose up --build"
echo ""
echo "Or to run in background:"
echo "  docker-compose up -d --build"