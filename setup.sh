#!/bin/bash

# Setup script for music downloader
echo "Setting up music downloader environment..."

# Create necessary directories
mkdir -p beets_config
mkdir -p "${HOME}/Music"

# Create cookies file if it doesn't exist
if [ ! -f cookies.txt ]; then
    echo "# Add your cookies here for premium quality downloads" > cookies.txt
    echo "Created cookies.txt - add your YouTube cookies for better quality downloads"
fi

# Set proper permissions for beets config directory
chmod 755 beets_config

# Get user ID and group ID for Docker
export USER_ID=$(id -u)
export GROUP_ID=$(id -g)

echo "USER_ID=${USER_ID}" > .env
echo "GROUP_ID=${GROUP_ID}" >> .env

echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Build and run the container: docker-compose up --build"
echo "2. The app will be available at http://localhost:5000"
echo "3. For better quality downloads, add your YouTube cookies to cookies.txt"
echo ""
echo "The beets configuration will:"
echo "- Fix track ordering based on YouTube Music metadata"
echo "- Download high-quality album art from multiple sources"
echo "- Clean up track titles (remove 'Explicit' tags, track numbers, etc.)"
echo "- Embed proper album art replacing YouTube thumbnails"
echo "- Set correct albumartist tags for compilation detection"