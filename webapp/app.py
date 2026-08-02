import csv
import io
import json
import os
import re
from typing import List, Dict, Optional, Tuple

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from flask import Flask, render_template, request, send_file

app = Flask(__name__)


def _get_playlist_id(url: str) -> str:
    match = re.search(r'playlist/([a-zA-Z0-9]+)', url)
    if not match:
        raise ValueError("Invalid Spotify playlist URL. Use a link like https://open.spotify.com/playlist/...")
    return match.group(1)


def fetch_playlist_tracks(playlist_url: str) -> Tuple[List[Dict[str, str]], Optional[int]]:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=os.environ['SPOTIFY_CLIENT_ID'],
        client_secret=os.environ['SPOTIFY_CLIENT_SECRET'],
    ))

    playlist_id = _get_playlist_id(playlist_url)
    results = sp.playlist_tracks(playlist_id, fields='items(track(name,artists(name))),next,total')
    total = results.get('total')

    tracks: List[Dict[str, str]] = []
    while results:
        for item in results['items']:
            track = item.get('track')
            if not track or not track.get('name'):
                continue
            tracks.append({
                'position': len(tracks) + 1,
                'song': track['name'],
                'artist': ', '.join(a['name'] for a in track['artists']),
            })
        results = sp.next(results) if results.get('next') else None

    return tracks, total or len(tracks)


def _tracks_to_csv(tracks: List[Dict[str, str]]) -> io.BytesIO:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Position Number", "Song - Artist"])
    for track in tracks:
        song = track.get("song", "").strip()
        artist = track.get("artist", "").strip()
        position = track.get("position", "")
        writer.writerow([position, f"{song} - {artist}".strip(" - ")])

    bytes_io = io.BytesIO()
    bytes_io.write(output.getvalue().encode("utf-8"))
    bytes_io.seek(0)
    return bytes_io


@app.route("/", methods=["GET", "POST"])
def index():
    error = None
    tracks: List[Dict[str, str]] = []
    expected_count: Optional[int] = None

    if request.method == "POST":
        playlist_url = request.form.get("playlist_url", "").strip()
        if not playlist_url:
            error = "Please paste a Spotify playlist link."
        else:
            try:
                tracks, expected_count = fetch_playlist_tracks(playlist_url)
                if not tracks:
                    error = "No tracks found. Make sure the playlist is public."
            except Exception as exc:
                error = f"Failed to fetch playlist: {exc}"

    return render_template(
        "index.html",
        error=error,
        tracks=tracks,
        expected_count=expected_count,
        tracks_json=tracks,
    )


@app.route("/download", methods=["POST"])
def download():
    tracks_json = request.form.get("tracks_json", "")
    if not tracks_json:
        return "Missing track data", 400

    tracks = json.loads(tracks_json)
    if isinstance(tracks, str):
        tracks = json.loads(tracks)
    csv_file = _tracks_to_csv(tracks)

    return send_file(
        csv_file,
        mimetype="text/csv",
        as_attachment=True,
        download_name="spotify_playlist.csv",
    )


if __name__ == "__main__":
    app.run(debug=True)
