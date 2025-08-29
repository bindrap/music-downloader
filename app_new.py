#!/usr/bin/env python3
import os
import json
import re
import subprocess
import requests
import threading
from flask import Flask, render_template, request, jsonify
from ytmusicapi import YTMusic
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
import shutil
from urllib.parse import urlparse, parse_qs
import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Environment setup
APP_ENV = os.environ.get("APP_ENV", "local")
if APP_ENV == "docker":
    MUSIC_DIR = "/app/Music"
    CONFIG_DIR = "/app/config"
    BEETS_CONFIG = "/app/config.yaml"
else:
    MUSIC_DIR = os.path.expanduser("~/Music")
    CONFIG_DIR = os.path.expanduser("~/.config/beets")
    BEETS_CONFIG = os.path.join(CONFIG_DIR, "config.yaml")

COOKIES_FILE = "cookies.txt"

# Ensure directories exist
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

# Set proper permissions for Navidrome
def set_file_permissions(path):
    """Set proper permissions for music files."""
    try:
        if os.path.isfile(path):
            os.chmod(path, 0o644)
        elif os.path.isdir(path):
            os.chmod(path, 0o755)
            for root, dirs, files in os.walk(path):
                for d in dirs:
                    os.chmod(os.path.join(root, d), 0o755)
                for f in files:
                    os.chmod(os.path.join(root, f), 0o644)
    except Exception as e:
        print(f"Warning: Could not set permissions: {e}")

# Download status tracking
download_status = {}
download_lock = threading.Lock()

def run_download_with_fallback(output_template, url, cookies=True, quality='flac'):
    """Download audio with user-selected quality."""
    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--add-metadata",
        "--embed-thumbnail",
        "--embed-metadata",
        "-o", output_template,
        url
    ]
    if cookies:
        cmd += ["--cookies", COOKIES_FILE]

    # Set format
    fmt = "flac" if quality == "flac" else "mp3"
    cmd += ["--audio-format", fmt, "--audio-quality", "0"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Successfully downloaded in {fmt.upper()}")
    else:
        print(f"{fmt.upper()} download failed: {result.stderr}")
    return result


class MusicDownloader:
    def __init__(self):
        try:
            self.ytmusic = YTMusic()
        except Exception as e:
            print(f"YTMusic init failed: {e}")
            try:
                self.ytmusic = YTMusic(auth=None)
            except Exception as e2:
                print(f"YTMusic fallback failed: {e2}")
                self.ytmusic = None

    def sanitize_filename(self, name):
        return re.sub(r'[\\/*?:"<>|]', '', name)

    def normalize_title(self, title):
        """Normalize title for matching - remove all non-alphanumeric chars and lowercase"""
        return re.sub(r'\W+', '', title).lower()

    def search_albums(self, query):
        if not self.ytmusic or not query:
            return []
        try:
            results = self.ytmusic.search(query, filter="albums")
            return [
                {
                    'browseId': a.get('browseId'),
                    'title': a.get('title'),
                    'artist': a.get('artists', [{}])[0].get('name', 'Unknown'),
                    'year': a.get('year'),
                    'thumbnails': a.get('thumbnails', [])
                }
                for a in results[:10]
            ]
        except Exception as e:
            print(f"Search error: {e}")
            return []

    def search_album(self, query):
        """Search for a single album and return its info"""
        results = self.search_albums(query)
        if results:
            return results[0]
        return None

    def get_high_quality_album_art(self, album_data):
        """Fetch high-quality album art from thumbnails"""
        thumbs = album_data.get("thumbnails", [])
        if not thumbs:
            return None
        
        # Get the highest resolution thumbnail
        best = max(thumbs, key=lambda t: t.get("width", 0) * t.get("height", 0))
        
        # Sometimes the URL needs modification for max quality
        url = best["url"]
        if "maxresdefault" not in url:
            url = re.sub(r'=w\d+-h\d+', '=w1200-h1200', url)
            
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.content
        except Exception as e:
            print(f"Album art download failed: {e}")
            return None

    def embed_album_art(self, folder, img_data):
        """Embed album art into all audio files in folder"""
        if not img_data:
            return
            
        # Save cover.jpg for reference
        cover_path = os.path.join(folder, "cover.jpg")
        with open(cover_path, "wb") as f:
            f.write(img_data)

        for file in os.listdir(folder):
            path = os.path.join(folder, file)
            if file.lower().endswith(".mp3"):
                try:
                    audio = ID3(path)
                    audio.delall("APIC")  # Remove existing art
                    audio.add(APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,  # Cover (front)
                        desc="Cover",
                        data=img_data
                    ))
                    audio.save(v2_version=3)
                    print(f"✅ Embedded art in: {file}")
                except Exception as e:
                    print(f"MP3 art embed failed for {file}: {e}")
            elif file.lower().endswith(".flac"):
                try:
                    audio = FLAC(path)
                    audio.clear_pictures()  # Remove existing art
                    pic = Picture()
                    pic.type = 3  # Cover (front)
                    pic.mime = "image/jpeg"
                    pic.desc = "Cover"
                    pic.data = img_data
                    audio.add_picture(pic)
                    audio.save()
                    print(f"✅ Embedded art in: {file}")
                except Exception as e:
                    print(f"FLAC art embed failed for {file}: {e}")

    def clean_title(self, title):
        """Clean up track titles"""
        cleaned = re.sub(r"^\d+\s*[-.]?\s*", "", title)  # Remove leading track numbers
        cleaned = re.sub(r"\s*\(feat\..*?\)", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*\(Explicit\)", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*\[Explicit\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*\[.*?\]", "", cleaned)
        return cleaned.strip()

    def fix_track_metadata(self, folder, albumartist, album_title, album_data):
        """Fix track numbers and clean titles using YTMusic track order."""
        print(f"🎯 Fixing track metadata for: {albumartist} - {album_title}")
        
        # Build track order map from YTMusic data
        track_order = {}
        if album_data and "tracks" in album_data:
            for idx, track in enumerate(album_data["tracks"], start=1):
                raw_title = track.get("title", "")
                clean_title = self.clean_title(raw_title)
                
                # Map both normalized versions for better matching
                track_order[self.normalize_title(raw_title)] = idx
                track_order[self.normalize_title(clean_title)] = idx
                
                # Also try without parentheses content
                no_parens = re.sub(r'\([^)]*\)', '', raw_title).strip()
                track_order[self.normalize_title(no_parens)] = idx

        files = [f for f in os.listdir(folder) if f.lower().endswith((".mp3", ".flac"))]
        
        for file in files:
            path = os.path.join(folder, file)
            try:
                if file.lower().endswith(".mp3"):
                    try:
                        audio = EasyID3(path)
                    except ID3NoHeaderError:
                        audio = EasyID3()
                        audio.save(path)
                        audio = EasyID3(path)
                elif file.lower().endswith(".flac"):
                    audio = FLAC(path)
                else:
                    continue

                # Get current title
                title = audio.get("title", [os.path.splitext(file)[0]])[0]
                if isinstance(title, list):
                    title = title[0]
                    
                cleaned_title = self.clean_title(title)

                # Update metadata
                audio["albumartist"] = albumartist
                audio["artist"] = albumartist  # Also set artist
                audio["album"] = album_title
                audio["title"] = cleaned_title

                # Try multiple matching strategies for track number
                track_num = None
                
                # Try exact cleaned match
                norm_cleaned = self.normalize_title(cleaned_title)
                track_num = track_order.get(norm_cleaned)
                
                # Try original title match
                if not track_num:
                    norm_orig = self.normalize_title(title)
                    track_num = track_order.get(norm_orig)
                
                # Try partial matching (first few words)
                if not track_num:
                    words = cleaned_title.split()[:3]
                    if words:
                        partial = self.normalize_title(' '.join(words))
                        for key, num in track_order.items():
                            if key.startswith(partial):
                                track_num = num
                                break

                if track_num:
                    audio["tracknumber"] = str(track_num)
                    print(f"✅ Track {track_num}: {cleaned_title}")
                else:
                    print(f"⚠️ No track number match for: {cleaned_title}")

                audio.save()
                
            except Exception as e:
                print(f"❌ Failed to update {file}: {e}")

    def run_beets_on_album(self, album_folder, artist_name, album_name):
        """Run beets with autotagging to fix metadata and organize"""
        print(f"🎵 Running Beets on: {album_folder}")
        
        if not os.path.exists(album_folder):
            print("❌ Album path does not exist")
            return

        # Prepare beets command with autotagging
        cmd = [
            "beet", "import",
            "--write",           # Write tags to files
            "--autotag",        # Enable autotagging
            "--quiet",          # Quiet mode
            "--search-id", f"{artist_name} {album_name}",  # Help beets find the right album
            album_folder
        ]

        env = os.environ.copy()
        env["BEETSDIR"] = CONFIG_DIR
        env["HOME"] = "/home/appuser" if APP_ENV == "docker" else os.path.expanduser("~")

        try:
            # Run beets and auto-accept the best match
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
                input="A\n"  # Auto-accept the first match
            )
            
            if result.returncode == 0:
                print("✅ Beets import successful")
            else:
                print(f"⚠️ Beets had issues but may have succeeded: {result.stderr}")
                
                # Fallback: try without autotagging if it failed
                cmd_fallback = [
                    "beet", "import",
                    "--write",
                    "--noautotag",  # Disable autotagging
                    "--move",
                    "--quiet",
                    album_folder
                ]
                
                result_fallback = subprocess.run(
                    cmd_fallback,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    input="\n"
                )
                
                if result_fallback.returncode == 0:
                    print("✅ Beets fallback import successful")
                    
        except subprocess.TimeoutExpired:
            print("❌ Beets timed out")
        except Exception as e:
            print(f"❌ Beets error: {e}")

    def download_album(self, album_info, base_dir, artist_name, album_name, download_id, quality='flac'):
        try:
            with download_lock:
                download_status[download_id]['status'] = 'searching'
                download_status[download_id]['message'] = 'Fetching album metadata...'

            if not self.ytmusic:
                raise Exception("YTMusic API not available")

            browse_id = album_info['browseId']
            
            # Always fetch full album data for proper metadata
            try:
                album_data = self.ytmusic.get_album(browse_id)
                
                # Ensure we have thumbnails
                if not album_data.get("thumbnails") and album_info.get("thumbnails"):
                    album_data["thumbnails"] = album_info["thumbnails"]
                    
                # Get the actual artist name from album data if available
                if album_data.get("artists"):
                    actual_artist = album_data["artists"][0].get("name", artist_name)
                    if actual_artist:
                        artist_name = actual_artist
                        
                # Get the actual album title from album data
                if album_data.get("title"):
                    album_name = album_data["title"]
                    
            except Exception as e:
                print(f"⚠️ Could not fetch full album data: {e}")
                album_data = {"tracks": [], "thumbnails": album_info.get("thumbnails", [])}

            safe_artist = self.sanitize_filename(artist_name)
            safe_album = self.sanitize_filename(album_name)
            album_folder = os.path.join(base_dir, safe_artist, safe_album)
            os.makedirs(album_folder, exist_ok=True)

            # Save metadata for reference
            with open(os.path.join(album_folder, "album_info.json"), "w") as f:
                json.dump(album_data, f, indent=2)

            # Build download URL
            search_url = f"https://music.youtube.com/browse/{browse_id}"
            output_template = os.path.join(album_folder, "%(title)s.%(ext)s")

            with download_lock:
                download_status[download_id]['status'] = 'downloading'
                download_status[download_id]['message'] = f'Downloading tracks from {artist_name} - {album_name}...'

            # Download tracks
            result = run_download_with_fallback(output_template, search_url, cookies=True, quality=quality)
            if result.returncode != 0:
                raise Exception(f"Download failed: {result.stderr}")

            # Step 1: Fetch and embed high-quality album art
            with download_lock:
                download_status[download_id]['message'] = 'Fetching high-quality album art...'
            
            img_data = self.get_high_quality_album_art(album_data)
            if img_data:
                self.embed_album_art(album_folder, img_data)
            else:
                print("⚠️ Could not fetch album art")

            # Step 2: Fix track metadata (numbers, titles, artist, album)
            with download_lock:
                download_status[download_id]['message'] = 'Fixing track metadata and order...'
            
            self.fix_track_metadata(album_folder, artist_name, album_name, album_data)

            # Step 3: Run Beets to organize and further fix metadata
            with download_lock:
                download_status[download_id]['message'] = 'Organizing with Beets...'
            
            self.run_beets_on_album(album_folder, artist_name, album_name)

            # Set proper permissions
            set_file_permissions(album_folder)

            with download_lock:
                download_status[download_id]['status'] = 'completed'
                download_status[download_id]['message'] = f'Successfully processed {artist_name} - {album_name}'

        except Exception as e:
            with download_lock:
                download_status[download_id]['status'] = 'error'
                download_status[download_id]['message'] = f'Error: {str(e)}'
            print(f"❌ Album download failed: {e}")

    def extract_video_id(self, url):
        parsed = urlparse(url)
        if parsed.hostname == "youtu.be":
            return parsed.path.lstrip("/")
        if parsed.hostname in ("www.youtube.com", "youtube.com", "music.youtube.com"):
            return parse_qs(parsed.query).get("v", [None])[0]
        return None

    def download_song(self, url, download_id, quality='flac'):
        try:
            with download_lock:
                download_status[download_id]['status'] = 'downloading'
                download_status[download_id]['message'] = 'Analyzing song...'
                
            video_id = self.extract_video_id(url)
            song_artist = "Unknown Artist"
            album_name = None
            
            if video_id and self.ytmusic:
                try:
                    details = self.ytmusic.get_song(video_id)
                    if "videoDetails" in details:
                        song_artist = self.sanitize_filename(details["videoDetails"].get("author", song_artist))
                        title = details["videoDetails"]["title"]
                        
                        # Search for the song to get album info
                        results = self.ytmusic.search(f"{song_artist} - {title}", filter="songs")
                        if results and results[0].get("album"):
                            album_name = self.sanitize_filename(results[0]["album"]["name"])
                except Exception as e:
                    print(f"Could not get song details: {e}")
                    
            output_template = os.path.join(MUSIC_DIR, f"{song_artist}", f"{album_name or 'Singles'}/%(title)s.%(ext)s")
            
            result = run_download_with_fallback(output_template, url, cookies=False, quality=quality)
            if result.returncode != 0:
                raise Exception(result.stderr)
                
            with download_lock:
                download_status[download_id]['status'] = 'completed'
                download_status[download_id]['message'] = f'Saved to {os.path.dirname(output_template)}'
                
        except Exception as e:
            with download_lock:
                download_status[download_id]['status'] = 'error'
                download_status[download_id]['message'] = f'Error: {str(e)}'

    def download_artist_song(self, artist, title, download_id, quality='flac'):
        try:
            with download_lock:
                download_status[download_id]['status'] = 'downloading'
                download_status[download_id]['message'] = f'Searching for {artist} - {title}...'
                
            results = self.ytmusic.search(f"{artist} - {title}", filter="songs")
            if not results:
                raise Exception("No song found")
                
            song = results[0]
            video_id = song.get("videoId")
            song_artist = self.sanitize_filename(song.get("artists", [{}])[0].get("name", artist))
            album_name = self.sanitize_filename(song["album"]["name"]) if song.get("album") else None
            
            output_template = os.path.join(MUSIC_DIR, f"{song_artist}", f"{album_name or 'Singles'}/%(title)s.%(ext)s")
            url = f"https://music.youtube.com/watch?v={video_id}"
            
            result = run_download_with_fallback(output_template, url, cookies=False, quality=quality)
            if result.returncode != 0:
                raise Exception(result.stderr)
                
            with download_lock:
                download_status[download_id]['status'] = 'completed'
                download_status[download_id]['message'] = f'Downloaded: {title} by {song_artist}'
                
        except Exception as e:
            with download_lock:
                download_status[download_id]['status'] = 'error'
                download_status[download_id]['message'] = f'Error: {str(e)}'

    def delete_artist_folder(self, artist):
        path = os.path.join(MUSIC_DIR, self.sanitize_filename(artist))
        if os.path.isdir(path):
            shutil.rmtree(path)
            return True
        return False

    def delete_artist_album(self, artist, album):
        path = os.path.join(MUSIC_DIR, self.sanitize_filename(artist), self.sanitize_filename(album))
        if os.path.isdir(path):
            shutil.rmtree(path)
            return True
        return False

    def get_library_structure(self):
        library = {}
        if not os.path.exists(MUSIC_DIR):
            return library
            
        for artist in os.listdir(MUSIC_DIR):
            artist_path = os.path.join(MUSIC_DIR, artist)
            if os.path.isdir(artist_path):
                library[artist] = []
                for album in os.listdir(artist_path):
                    album_path = os.path.join(artist_path, album)
                    if os.path.isdir(album_path):
                        count = len([f for f in os.listdir(album_path) 
                                   if f.lower().endswith(('.mp3', '.flac'))])
                        library[artist].append({'name': album, 'track_count': count})
        return library


# Initialize
downloader = MusicDownloader()


# === Routes ===
@app.route('/')
def index():
    return render_template('index.html', library=downloader.get_library_structure())

@app.route('/search-albums', methods=['POST'])
def search_albums():
    query = request.get_json().get('query', '').strip()
    if not query:
        return jsonify([])
    return jsonify(downloader.search_albums(query))

@app.route('/download-album', methods=['POST'])
def download_album():
    data = request.get_json()
    query = data.get('query', '').strip()
    browse_id = data.get('browseId')
    quality = data.get('quality', 'flac')
    
    if not query:
        return jsonify({'error': 'Query required'}), 400
        
    download_id = str(len(download_status) + 1)
    with download_lock:
        download_status[download_id] = {'status': 'searching', 'message': 'Searching...', 'type': 'album'}
    
    def task():
        entries = [e.strip() for e in query.split(',')]
        for entry in entries:
            if '-' not in entry and not browse_id:
                with download_lock:
                    download_status[download_id]['status'] = 'error'
                    download_status[download_id]['message'] = f'Invalid format: {entry}'
                return
                
            if browse_id:
                # Download from search result with browseId
                artist, album = [x.strip() for x in entry.split('-', 1)] if '-' in entry else ("Unknown", entry)
            else:
                artist, album = [x.strip() for x in entry.split('-', 1)]
                
            with download_lock:
                download_status[download_id]['message'] = f'Searching {artist} - {album}...'
                
            if browse_id:
                # Use the provided browseId directly
                album_info = {
                    'browseId': browse_id,
                    'thumbnails': []
                }
            else:
                # Search for the album
                album_info = downloader.search_album(f"{artist} {album}")
                
            if album_info:
                downloader.download_album(album_info, MUSIC_DIR, artist, album, download_id, quality)
            else:
                with download_lock:
                    download_status[download_id]['status'] = 'error'
                    download_status[download_id]['message'] = f'Not found: {artist} - {album}'
                return
                
    threading.Thread(target=task).start()
    return jsonify({'download_id': download_id})

@app.route('/download-song', methods=['POST'])
def download_song():
    data = request.get_json()
    url = data.get('url', '').strip()
    quality = data.get('quality', 'flac')
    
    if not url:
        return jsonify({'error': 'URL required'}), 400
        
    download_id = str(len(download_status) + 1)
    with download_lock:
        download_status[download_id] = {'status': 'starting', 'message': 'Starting...', 'type': 'song'}
        
    threading.Thread(target=lambda: downloader.download_song(url, download_id, quality)).start()
    return jsonify({'download_id': download_id})

@app.route('/download-track', methods=['POST'])
def download_track():
    data = request.get_json()
    artist = data.get('artist', '').strip()
    title = data.get('title', '').strip()
    quality = data.get('quality', 'flac')
    
    if not artist or not title:
        return jsonify({'error': 'Artist and title required'}), 400
        
    download_id = str(len(download_status) + 1)
    with download_lock:
        download_status[download_id] = {'status': 'starting', 'message': f'Downloading {artist} - {title}...', 'type': 'track'}
        
    threading.Thread(target=lambda: downloader.download_artist_song(artist, title, download_id, quality)).start()
    return jsonify({'download_id': download_id})

@app.route('/delete-artist', methods=['POST'])
def delete_artist():
    artist = request.get_json().get('artist', '').strip()
    if not artist:
        return jsonify({'error': 'Artist required'}), 400
        
    if downloader.delete_artist_folder(artist):
        return jsonify({'message': f'Deleted {artist}'})
    return jsonify({'error': 'Not found'}), 404

@app.route('/delete-album', methods=['POST'])
def delete_album():
    data = request.get_json()
    artist = data.get('artist', '').strip()
    album = data.get('album', '').strip()
    
    if not artist or not album:
        return jsonify({'error': 'Artist and album required'}), 400
        
    if downloader.delete_artist_album(artist, album):
        return jsonify({'message': f'Deleted {album} by {artist}'})
    return jsonify({'error': 'Not found'}), 404

@app.route('/download-status/<download_id>')
def get_download_status(download_id):
    with download_lock:
        return jsonify(download_status.get(download_id, {'status': 'not_found'}))

@app.route('/library')
def get_library():
    return jsonify(downloader.get_library_structure())

if __name__ == '__main__':
    os.makedirs(MUSIC_DIR, exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)