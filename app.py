#!/usr/bin/env python3
import os
import sys
import json
import re
import subprocess
import requests
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from ytmusicapi import YTMusic
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from mutagen.flac import FLAC
from mutagen.id3 import ID3NoHeaderError
import shutil
from urllib.parse import urlparse, parse_qs

import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)  # Generates a secure random key

#MUSIC_DIR = os.path.expanduser("~/Music")
# app.py (top of file)
APP_ENV = os.environ.get("APP_ENV", "local")

if APP_ENV == "docker":
    MUSIC_DIR = "/app/Music"
else:
    MUSIC_DIR = os.path.expanduser("~/Music")
COOKIES_FILE = "cookies.txt"

# Ensure proper permissions for Navidrome
def set_file_permissions(path):
    """Set proper permissions for music files so Navidrome can read them."""
    try:
        # Set 644 for files, 755 for directories
        if os.path.isfile(path):
            os.chmod(path, 0o644)
        elif os.path.isdir(path):
            os.chmod(path, 0o755)
            # Recursively set permissions for contents
            for root, dirs, files in os.walk(path):
                for d in dirs:
                    os.chmod(os.path.join(root, d), 0o755)
                for f in files:
                    os.chmod(os.path.join(root, f), 0o644)
    except Exception as e:
        print(f"Warning: Could not set permissions for {path}: {e}")

# Global variables for download status
download_status = {}
download_lock = threading.Lock()

def run_download_with_fallback(output_template, url, cookies=True):
    """Try FLAC first, fallback to MP3 if FLAC fails."""
    base_cmd = [
        "yt-dlp",
        "--extract-audio",
        "--add-metadata",
        "--embed-thumbnail",
        "-o", output_template,
        url
    ]
    if cookies:
        base_cmd += ["--cookies", COOKIES_FILE]

    # Try FLAC first
    cmd_flac = base_cmd + ["--audio-format", "flac", "--audio-quality", "0"]
    result = subprocess.run(cmd_flac, capture_output=True, text=True)
    if result.returncode == 0:
        print("Successfully downloaded in FLAC format")
        return result

    print("FLAC failed or unavailable, retrying with MP3...")
    # Fallback: MP3 320 kbps
    cmd_mp3 = base_cmd + ["--audio-format", "mp3", "--audio-quality", "0"]
    result = subprocess.run(cmd_mp3, capture_output=True, text=True)
    if result.returncode == 0:
        print("Successfully downloaded in MP3 format")
    return result

class MusicDownloader:
    def __init__(self):
        try:
            self.ytmusic = YTMusic()
        except Exception as e:
            print(f"Error initializing YTMusic: {e}")
            # Try to initialize without authentication
            try:
                self.ytmusic = YTMusic(auth=None)
            except Exception as e2:
                print(f"Error initializing YTMusic without auth: {e2}")
                self.ytmusic = None

    def sanitize_filename(self, name):
        """Remove characters not allowed in file names."""
        return re.sub(r'[\\/*?:"<>|]', '', name)

    def normalize_title(self, title):
        """Normalize a title for matching (remove punctuation, lowercase)."""
        return re.sub(r'\W+', '', title).lower()

    def search_album(self, query):
        """Search YouTube Music for an album and return the first result."""
        try:
            results = self.ytmusic.search(query, filter="albums")
            return results[0] if results else None
        except Exception as e:
            print(f"Error searching for album '{query}': {e}")
            return None

    def clean_track_titles_and_set_albumartist(self, folder, albumartist, album_data):
        """Clean titles, set albumartist, and set proper track numbers based on album metadata."""
        print(f"Setting albumartist = '{albumartist}', cleaning titles, and fixing track numbers...")

        # Build mapping: normalized title -> track number
        track_order = {}
        for idx, track in enumerate(album_data.get("tracks", []), start=1):
            raw_title = track["title"]

            # Clean common junk from titles
            clean_title = re.sub(r"\(feat\. .*?\)", "", raw_title, flags=re.IGNORECASE)
            clean_title = re.sub(r"\(Explicit\)", "", clean_title, flags=re.IGNORECASE)
            clean_title = re.sub(r"\[Explicit\]", "", clean_title, flags=re.IGNORECASE)
            clean_title = clean_title.strip()

            track_order[self.normalize_title(raw_title)] = idx
            track_order[self.normalize_title(clean_title)] = idx

        # Get both MP3 and FLAC files
        audio_files = [f for f in os.listdir(folder) if f.lower().endswith((".mp3", ".flac"))]
        
        for fallback_idx, file in enumerate(sorted(audio_files), start=1):
            path = os.path.join(folder, file)
            try:
                # Handle both MP3 and FLAC files
                if file.lower().endswith(".mp3"):
                    try:
                        audio = EasyID3(path)
                    except ID3NoHeaderError:
                        # Create ID3 tag if it doesn't exist
                        audio = EasyID3()
                        audio.save(path)
                        audio = EasyID3(path)
                elif file.lower().endswith(".flac"):
                    audio = FLAC(path)
                else:
                    continue

                audio["albumartist"] = albumartist

                # Clean title
                title = audio.get("title", [os.path.splitext(file)[0]])[0]
                if isinstance(title, list):
                    title = title[0]
                
                cleaned_title = re.sub(r"^\d+\s+", "", title)  # remove leading numbers
                cleaned_title = re.sub(r"\(feat\. .*?\)", "", cleaned_title, flags=re.IGNORECASE)
                cleaned_title = re.sub(r"\(Explicit\)", "", cleaned_title, flags=re.IGNORECASE)
                cleaned_title = re.sub(r"\[Explicit\]", "", cleaned_title, flags=re.IGNORECASE)
                cleaned_title = re.sub(r"\[.*?\]", "", cleaned_title).strip()
                audio["title"] = cleaned_title

                # Try to match with YTMusic track order
                norm_cleaned = self.normalize_title(cleaned_title)
                norm_original = self.normalize_title(title)
                track_num = track_order.get(norm_cleaned) or track_order.get(norm_original)

                if track_num:
                    audio["tracknumber"] = str(track_num)
                else:
                    # fallback: assign sequential number based on file order
                    audio["tracknumber"] = str(fallback_idx)
                    print(f"Fallback track number {fallback_idx} assigned to: {cleaned_title}")

                audio.save()
            except Exception as e:
                print(f"Could not update tags for {file}: {e}")

    def embed_real_album_art(self, folder, album_data):
        """Replace YouTube thumbnail with actual album art from YTMusic metadata."""
        thumbnails = album_data.get("thumbnails", [])
        if not thumbnails:
            print("No album art found in metadata.")
            return

        # Pick highest resolution thumbnail
        best_thumb = sorted(thumbnails, key=lambda t: t.get("width", 0), reverse=True)[0]
        img_data = requests.get(best_thumb["url"]).content

        # Save cover to folder
        cover_path = os.path.join(folder, "cover.jpg")
        with open(cover_path, "wb") as img_file:
            img_file.write(img_data)

        print("Embedding real album art into audio files...")
        for file in os.listdir(folder):
            if file.lower().endswith(".mp3"):
                path = os.path.join(folder, file)
                try:
                    audio = ID3(path)
                    audio.delall("APIC")  # remove existing (YT thumbnail)
                    audio.add(APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,  # Cover (front)
                        desc="Cover",
                        data=img_data
                    ))
                    audio.save(v2_version=3)
                except Exception as e:
                    print(f"Could not embed art for {file}: {e}")
            elif file.lower().endswith(".flac"):
                path = os.path.join(folder, file)
                try:
                    audio = FLAC(path)
                    audio.clear_pictures()  # remove existing pictures
                    pic = mutagen.flac.Picture()
                    pic.type = 3  # Cover (front)
                    pic.mime = "image/jpeg"
                    pic.desc = "Cover"
                    pic.data = img_data
                    audio.add_picture(pic)
                    audio.save()
                except Exception as e:
                    print(f"Could not embed art for {file}: {e}")

    def run_beets_on_album(self, path):
        """Run beets import with autotagging."""
        print(f"\nImporting to beets library: {path}")
        try:
            subprocess.run([
                "beet", "import",
                "--quiet",
                "--autotag",
                path
            ], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Beets not available or failed, skipping...")

    def download_album(self, album_info, base_dir, artist_name, album_name, download_id):
        """Download an album from YouTube Music and process it."""
        try:
            with download_lock:
                download_status[download_id]['status'] = 'downloading'
                download_status[download_id]['message'] = 'Starting download...'

            if not self.ytmusic:
                raise Exception("YTMusic API not available")

            browse_id = album_info['browseId']
            
            with download_lock:
                download_status[download_id]['message'] = 'Fetching album metadata...'

            try:
                album_data = self.ytmusic.get_album(browse_id)
            except Exception as e:
                # If we can't get album data, we'll work with what we have
                print(f"Warning: Could not fetch full album data: {e}")
                album_data = {
                    "tracks": [],
                    "thumbnails": album_info.get("thumbnails", [])
                }

            safe_artist = self.sanitize_filename(artist_name)
            safe_album = self.sanitize_filename(album_name)
            album_folder = os.path.join(base_dir, safe_artist, safe_album)
            os.makedirs(album_folder, exist_ok=True)

            # Save album metadata
            metadata_path = os.path.join(album_folder, "album_info.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(album_data, f, indent=2)

            search_url = f"https://music.youtube.com/browse/{browse_id}"
            print(f"\nDownloading '{album_name}' by {artist_name}...\nFrom: {search_url}\n")

            with download_lock:
                download_status[download_id]['message'] = f'Downloading tracks from {artist_name} - {album_name}...'

            # Download tracks with fallback
            output_template = os.path.join(album_folder, "%(title)s.%(ext)s")
            result = run_download_with_fallback(output_template, search_url, cookies=True)
            
            if result.returncode != 0:
                raise Exception(f"Download failed: {result.stderr}")

            with download_lock:
                download_status[download_id]['message'] = 'Processing album art and metadata...'

            # Replace thumbnail with real album art
            self.embed_real_album_art(album_folder, album_data)

            # Clean titles & set correct track order
            self.clean_track_titles_and_set_albumartist(album_folder, artist_name, album_data)

            # Import into beets if available
            self.run_beets_on_album(album_folder)

            # Set proper file permissions for Navidrome
            set_file_permissions(album_folder)

            with download_lock:
                download_status[download_id]['status'] = 'completed'
                download_status[download_id]['message'] = f'Successfully downloaded {artist_name} - {album_name}'

        except Exception as e:
            with download_lock:
                download_status[download_id]['status'] = 'error'
                download_status[download_id]['message'] = f'Error downloading {artist_name} - {album_name}: {str(e)}'
            print(f"Download failed: {e}")

    def extract_video_id(self, url):
        """Extracts videoId from YouTube or YouTube Music URLs."""
        parsed = urlparse(url)
        if parsed.hostname in ("youtu.be",):
            return parsed.path.lstrip("/")
        if parsed.hostname in ("www.youtube.com", "music.youtube.com", "youtube.com"):
            query = parse_qs(parsed.query)
            return query.get("v", [None])[0]
        return None

    def download_song(self, url, download_id):
        """Download a single song from a YouTube URL."""
        try:
            with download_lock:
                download_status[download_id]['status'] = 'downloading'
                download_status[download_id]['message'] = 'Analyzing song...'

            video_id = self.extract_video_id(url)
            song_artist = "Unknown Artist"
            album_name = None

            if video_id:
                try:
                    # Search song metadata by videoId
                    search_results = self.ytmusic.get_song(video_id)
                    if "videoDetails" in search_results:
                        details = search_results["videoDetails"]
                        song_artist = self.sanitize_filename(details.get("author", song_artist))
                        title = details.get("title", "Unknown Title")

                        # Try to fetch album info via search
                        ytm_results = self.ytmusic.search(f"{song_artist} - {title}", filter="songs")
                        if ytm_results and "album" in ytm_results[0] and ytm_results[0]["album"]:
                            album_name = self.sanitize_filename(ytm_results[0]["album"]["name"])
                except Exception:
                    pass  # fallback if API fails

            # Decide folder
            if album_name:
                output_template = os.path.join(MUSIC_DIR, f"{song_artist}/{album_name}/%(title)s.%(ext)s")
            else:
                output_template = os.path.join(MUSIC_DIR, f"{song_artist}/Singles/%(title)s.%(ext)s")

            with download_lock:
                download_status[download_id]['message'] = 'Downloading song...'

            result = run_download_with_fallback(output_template, url, cookies=False)
            if result.returncode != 0:
                raise Exception(result.stderr or "Unknown error during song download.")

            with download_lock:
                download_status[download_id]['status'] = 'completed'
                download_status[download_id]['message'] = f"Song downloaded to {os.path.dirname(output_template)}"

        except Exception as e:
            with download_lock:
                download_status[download_id]['status'] = 'error'
                download_status[download_id]['message'] = f'Error: {str(e)}'

    def download_artist_song(self, artist, title, download_id):
        """Search YouTube Music for 'artist - title' and download."""
        try:
            with download_lock:
                download_status[download_id]['status'] = 'downloading'
                download_status[download_id]['message'] = f'Searching for {artist} - {title}...'

            search_query = f"{artist} - {title}"
            results = self.ytmusic.search(search_query, filter="songs")

            if not results:
                raise Exception("No matching song found.")

            song = results[0]
            video_id = song.get("videoId")
            song_artist = self.sanitize_filename(song.get("artists", [{}])[0].get("name", artist))
            album_name = None

            if "album" in song and song["album"]:
                album_name = self.sanitize_filename(song["album"]["name"])

            if album_name:
                output_template = os.path.join(MUSIC_DIR, f"{song_artist}/{album_name}/%(title)s.%(ext)s")
            else:
                output_template = os.path.join(MUSIC_DIR, f"{song_artist}/Singles/%(title)s.%(ext)s")

            url = f"https://music.youtube.com/watch?v={video_id}"
            
            with download_lock:
                download_status[download_id]['message'] = 'Downloading song...'

            result = run_download_with_fallback(output_template, url, cookies=False)
            if result.returncode != 0:
                raise Exception(result.stderr or "Unknown error during artist/song download.")

            with download_lock:
                download_status[download_id]['status'] = 'completed'
                download_status[download_id]['message'] = f"Downloaded '{title}' by {song_artist} ({album_name or 'Single'}) successfully."

        except Exception as e:
            with download_lock:
                download_status[download_id]['status'] = 'error'
                download_status[download_id]['message'] = f'Error: {str(e)}'

    def delete_artist_folder(self, artist):
        """Deletes the folder for an artist."""
        artist_folder = os.path.join(MUSIC_DIR, self.sanitize_filename(artist))
        if os.path.isdir(artist_folder):
            shutil.rmtree(artist_folder)
            return True
        return False

    def delete_artist_album(self, artist, album):
        """Deletes a specific album folder for an artist."""
        album_folder = os.path.join(MUSIC_DIR, self.sanitize_filename(artist), self.sanitize_filename(album))
        if os.path.isdir(album_folder):
            shutil.rmtree(album_folder)
            return True
        return False

    def get_library_structure(self):
        """Get the current library structure for display."""
        library = {}
        if not os.path.exists(MUSIC_DIR):
            return library

        for artist_dir in os.listdir(MUSIC_DIR):
            artist_path = os.path.join(MUSIC_DIR, artist_dir)
            if os.path.isdir(artist_path):
                library[artist_dir] = []
                for album_dir in os.listdir(artist_path):
                    album_path = os.path.join(artist_path, album_dir)
                    if os.path.isdir(album_path):
                        # Count audio files in album (both MP3 and FLAC)
                        audio_count = len([f for f in os.listdir(album_path) 
                                         if f.lower().endswith(('.mp3', '.flac'))])
                        library[artist_dir].append({
                            'name': album_dir,
                            'track_count': audio_count
                        })

        return library

# Initialize the downloader
downloader = MusicDownloader()

@app.route('/')
def index():
    """Main page."""
    library = downloader.get_library_structure()
    return render_template('index.html', library=library)

@app.route('/download-album', methods=['POST'])
def download_album():
    """Handle album download requests."""
    data = request.get_json()
    query = data.get('query', '').strip()
    
    if not query:
        return jsonify({'error': 'Query is required'}), 400

    download_id = str(len(download_status) + 1)
    
    with download_lock:
        download_status[download_id] = {
            'status': 'searching',
            'message': 'Searching for album...',
            'type': 'album'
        }

    def process_albums():
        try:
            entries = [entry.strip() for entry in query.split(',')]
            for entry in entries:
                if '-' not in entry:
                    with download_lock:
                        download_status[download_id]['status'] = 'error'
                        download_status[download_id]['message'] = f'Invalid format: "{entry}". Use "Artist - Album" format.'
                    return
                    
                artist = entry.split('-', 1)[0].strip()
                album = entry.split('-', 1)[1].strip()
                
                with download_lock:
                    download_status[download_id]['message'] = f'Searching for {artist} - {album}...'
                
                album_info = downloader.search_album(f"{artist} {album}")
                if album_info:
                    downloader.download_album(album_info, MUSIC_DIR, artist, album, download_id)
                else:
                    with download_lock:
                        download_status[download_id]['status'] = 'error'
                        download_status[download_id]['message'] = f'Album not found: {artist} - {album}'
                    return
        except Exception as e:
            with download_lock:
                download_status[download_id]['status'] = 'error'
                download_status[download_id]['message'] = f'Error processing albums: {str(e)}'

    thread = threading.Thread(target=process_albums)
    thread.start()
    
    return jsonify({'download_id': download_id})

@app.route('/download-song', methods=['POST'])
def download_song():
    """Handle single song download from URL."""
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    download_id = str(len(download_status) + 1)
    
    with download_lock:
        download_status[download_id] = {
            'status': 'starting',
            'message': 'Starting song download...',
            'type': 'song'
        }

    def process_song():
        downloader.download_song(url, download_id)

    thread = threading.Thread(target=process_song)
    thread.start()
    
    return jsonify({'download_id': download_id})

@app.route('/download-track', methods=['POST'])
def download_track():
    """Handle track download by artist and title."""
    data = request.get_json()
    artist = data.get('artist', '').strip()
    title = data.get('title', '').strip()
    
    if not artist or not title:
        return jsonify({'error': 'Both artist and title are required'}), 400

    download_id = str(len(download_status) + 1)
    
    with download_lock:
        download_status[download_id] = {
            'status': 'starting',
            'message': f'Starting download for {artist} - {title}...',
            'type': 'track'
        }

    def process_track():
        downloader.download_artist_song(artist, title, download_id)

    thread = threading.Thread(target=process_track)
    thread.start()
    
    return jsonify({'download_id': download_id})

@app.route('/delete-artist', methods=['POST'])
def delete_artist():
    """Delete entire artist folder."""
    data = request.get_json()
    artist = data.get('artist', '').strip()
    
    if not artist:
        return jsonify({'error': 'Artist name is required'}), 400

    if downloader.delete_artist_folder(artist):
        return jsonify({'message': f'Deleted folder for artist "{artist}" and all contained songs.'})
    else:
        return jsonify({'error': f'No folder found for artist "{artist}"'}), 404

@app.route('/delete-album', methods=['POST'])
def delete_album():
    """Delete specific album."""
    data = request.get_json()
    artist = data.get('artist', '').strip()
    album = data.get('album', '').strip()
    
    if not artist or not album:
        return jsonify({'error': 'Both artist and album are required'}), 400

    if downloader.delete_artist_album(artist, album):
        return jsonify({'message': f'Deleted album "{album}" by "{artist}".'})
    else:
        return jsonify({'error': f'Album "{album}" by "{artist}" not found'}), 404

@app.route('/download-status/<download_id>')
def get_download_status(download_id):
    """Get download status."""
    with download_lock:
        status = download_status.get(download_id, {
            'status': 'not_found',
            'message': 'Download ID not found'
        })
    return jsonify(status)

@app.route('/library')
def get_library():
    """Get current library structure."""
    library = downloader.get_library_structure()
    return jsonify(library)

if __name__ == '__main__':
    # Ensure music directory exists
    os.makedirs(MUSIC_DIR, exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)