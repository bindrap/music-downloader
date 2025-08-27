# Music Downloader Web App

A Flask-based web application that downloads music from YouTube Music with high-quality metadata, album art, and automatic organization. Built with Docker for easy deployment and includes integration with beets for music library management.

## Features

- 🎵 **Album Downloads**: Search and download complete albums from YouTube Music
- 🎤 **Single Track Downloads**: Download individual songs by URL or artist/title search
- 🖼️ **High-Quality Album Art**: Automatically embeds official album artwork (not YouTube thumbnails)
- 🏷️ **Smart Metadata**: Cleans track titles, sets proper track numbers, and organizes files
- 📚 **Beets Integration**: Optional automatic import into beets music library
- 🌐 **Web Interface**: Clean, responsive web UI for managing downloads
- 📁 **Automatic Organization**: Files organized as `Artist/Album/Track.mp3`
- 🔄 **Real-time Status**: Live download progress and status updates
- 🗑️ **Library Management**: Delete artists or albums directly from the web interface

## Screenshots

The web interface provides an intuitive way to:
- Search and download albums by "Artist - Album" format
- Download individual tracks by YouTube URL or artist/title search
- View your current music library structure
- Monitor download progress in real-time
- Manage your collection (delete artists/albums)

## Prerequisites

- Docker and Docker Compose
- YouTube Music cookies (optional, but recommended for better access)

## Quick Start

### 1. Clone the Repository
```bash
git clone <your-repo-url>
cd music-downloader
```

### 2. Setup YouTube Music Cookies (Recommended)

For best results, export your YouTube Music cookies:

1. Install a browser extension like "Get cookies.txt LOCALLY"
2. Go to music.youtube.com and log in
3. Export cookies and save as `cookies.txt` in the project root

### 3. Run Setup Script
```bash
chmod +x setup.sh
./setup.sh
```

This script will:
- Detect your user/group IDs for proper file permissions
- Create necessary directories (`~/Music`, `~/.config/beets`)
- Set up a basic beets configuration
- Create environment variables for Docker

### 4. Start the Application
```bash
# Build and run in background
docker-compose up -d --build

# Or run with logs visible
docker-compose up --build
```

### 5. Access the Web Interface

Open your browser to: http://localhost:5000

## Usage

### Download Albums
1. Enter album queries in "Artist - Album" format
2. Multiple albums can be downloaded by separating with commas
3. Example: `Sting - Nothing Like the Sun, RZA - Bobby Digital in Stereo`

### Download Single Tracks
- **By URL**: Paste any YouTube or YouTube Music URL
- **By Search**: Enter artist and track title to search YouTube Music

### Library Management
- View your current music collection on the main page
- Delete individual albums or entire artist folders
- Real-time download status updates

## File Structure

Downloaded music is organized in your `~/Music` directory:

```
~/Music/
├── Artist Name/
│   ├── Album Name/
│   │   ├── 01 Track Title.mp3
│   │   ├── 02 Another Track.mp3
│   │   ├── cover.jpg
│   │   └── album_info.json
│   └── Singles/
│       └── Single Track.mp3
```

## Configuration

### Beets Configuration

The setup script creates a basic beets config at `~/.config/beets/config.yaml`:

```yaml
directory: ~/Music
library: ~/.config/beets/musiclibrary.db

import:
    move: yes
    write: yes
    
paths:
    default: $albumartist/$album/$track $title

plugins: fetchart embedart info
```

You can customize this configuration for your needs.

### Environment Variables

- `APP_ENV=docker` - Tells the app it's running in Docker
- `USER_ID` / `GROUP_ID` - Set automatically by setup.sh for proper file permissions

## Docker Configuration

### Build Arguments
- `USER_ID`: Your host user ID (set automatically)
- `GROUP_ID`: Your host group ID (set automatically)

### Volumes
- `~/Music:/app/Music` - Your music library
- `./cookies.txt:/app/cookies.txt` - YouTube Music authentication
- `~/.config/beets:/home/appuser/.config/beets` - Beets configuration

## Troubleshooting

### Files Not Appearing Locally
```bash
# Check permissions
ls -la ~/Music

# Test container write access
docker exec -it music-downloader touch /app/Music/test.txt
ls -la ~/Music/test.txt
```

### Beets Not Working
```bash
# Check beets config in container
docker exec -it music-downloader cat /home/appuser/.config/beets/config.yaml

# Test beets
docker exec -it music-downloader beet version
```

### Download Failures
```bash
# Check container logs
docker-compose logs -f music-downloader

# Common issues:
# - Missing or invalid cookies.txt
# - Network connectivity
# - YouTube rate limiting
```

### Permission Issues

If you encounter permission issues:

1. Make sure you ran the setup script
2. Check that your user owns the Music directory: `sudo chown -R $USER:$USER ~/Music`
3. Verify the .env file has correct USER_ID and GROUP_ID

## Development

### Local Development
To run without Docker for development:

```bash
# Install dependencies
pip install -r requirements.txt

# Install system dependencies
sudo apt install ffmpeg yt-dlp

# Set environment
export APP_ENV=local

# Run the app
python app.py
```

### Project Structure
```
.
├── app.py                 # Main Flask application
├── templates/
│   └── index.html        # Web interface
├── requirements.txt      # Python dependencies
├── Dockerfile           # Container configuration
├── docker-compose.yml   # Docker Compose setup
├── setup.sh            # Setup script
└── README.md           # This file
```

## Dependencies

### Python Packages
- Flask - Web framework
- ytmusicapi - YouTube Music API
- yt-dlp - Video/audio downloader
- mutagen - Audio metadata handling
- requests - HTTP requests
- beets - Music library management (optional)

### System Dependencies
- ffmpeg - Audio processing
- curl - Health checks and downloads

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with Docker
5. Submit a pull request

## License

This project is for personal use only. Respect YouTube's Terms of Service and only download content you have the right to download.

## Disclaimer

This tool is intended for personal use with music you own or have permission to download. Users are responsible for complying with applicable laws and terms of service.