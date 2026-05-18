import csv
import io
import json
import re
import time
from typing import List, Dict, Optional, Tuple

from bs4 import BeautifulSoup
from flask import Flask, render_template, request, send_file
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

app = Flask(__name__)


def _build_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)


def _extract_tracks(soup: BeautifulSoup) -> List[Dict[str, str]]:
    tracks: List[Dict[str, str]] = []
    seen = set()

    rows = soup.select('[data-testid="tracklist-row"]')
    if not rows:
        rows = soup.select('div[role="row"]')

    for row in rows:
        position: Optional[int] = None
        title = None
        artist = None

        index_cell = row.select_one('[aria-colindex="1"]')
        if index_cell:
            index_text = index_cell.get_text(strip=True)
            if index_text.isdigit():
                position = int(index_text)

        title_cell = row.select_one('[aria-colindex="2"]')
        if title_cell:
            title_tag = title_cell.select_one('[data-testid="tracklist-row-title"]')
            if not title_tag:
                title_tag = title_cell.select_one('a[data-testid="internal-track-link"]')
            if title_tag:
                title = title_tag.get_text(strip=True)

            artist_tags = title_cell.select('a[href^="/artist/"]')
            if artist_tags:
                artist = ", ".join(
                    t.get_text(strip=True) for t in artist_tags if t.get_text(strip=True)
                )

        if not title:
            title_tag = row.select_one('a[data-testid="internal-track-link"]')
            if title_tag:
                title = title_tag.get_text(strip=True)

        if not artist:
            artist_tags = row.select('a[href^="/artist/"]')
            if artist_tags:
                artist = ", ".join(
                    t.get_text(strip=True) for t in artist_tags if t.get_text(strip=True)
                )

        if not artist:
            text_nodes = [t.strip() for t in row.stripped_strings if t.strip()]
            if len(text_nodes) >= 2:
                artist = text_nodes[1]
            else:
                artist = ""

        if not title:
            continue

        key = (position, title, artist)
        if key in seen:
            continue

        seen.add(key)
        tracks.append({"position": position, "song": title, "artist": artist})

    return sorted(
        tracks,
        key=lambda item: item["position"] if item.get("position") is not None else 10**9,
    )


def _extract_total_count(soup: BeautifulSoup) -> Optional[int]:
    text = " ".join(soup.stripped_strings)
    match = re.search(r"(\d{1,3}(?:,\d{3})*)\s+songs\b", text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def fetch_playlist_tracks(
    playlist_url: str,
    max_scrolls: int = 1000,
    manual_wait_seconds: float = 10.0,
) -> Tuple[List[Dict[str, str]], Optional[int]]:
    driver = _build_driver()
    try:
        driver.get(playlist_url)
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="tracklist-row"], div[role="row"]')))

        initial_soup = BeautifulSoup(driver.page_source, "html.parser")
        expected_count = _extract_total_count(initial_soup)

        if manual_wait_seconds > 0:
            time.sleep(manual_wait_seconds)

        driver.execute_script(
            """
            const mainScroller = document.querySelector('[role="main"]');
            if (mainScroller) {
                mainScroller.scrollTop = 0;
            }
            """
        )
        time.sleep(0.8)

        collected = {}
        positions = set()

        def merge(tracks: List[Dict[str, str]]) -> None:
            for track in tracks:
                key = track.get("position") or track["song"]
                collected[key] = track
                if track.get("position") is not None:
                    positions.add(track["position"])

        soup = BeautifulSoup(driver.page_source, "html.parser")
        merge(_extract_tracks(soup))

        for _ in range(20):
            if 1 in positions:
                break
            driver.execute_script(
                """
                const mainScroller = document.querySelector('[role="main"]');
                if (mainScroller) {
                    mainScroller.scrollTop = 0;
                }
                """
            )
            time.sleep(0.8)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            merge(_extract_tracks(soup))

        if 1 not in positions:
            time.sleep(1.0)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            merge(_extract_tracks(soup))

        last_count = 0
        idle_rounds = 0
        for _ in range(max_scrolls):
            time.sleep(0.4)
            driver.execute_script(
                """
                const mainScroller = document.querySelector('[role="main"]');
                if (mainScroller) {
                    mainScroller.scrollTop += 400;
                }
                """
            )

            soup = BeautifulSoup(driver.page_source, "html.parser")
            merge(_extract_tracks(soup))

            if len(collected) == last_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
                last_count = len(collected)

            if expected_count is not None and len(positions) >= expected_count and 1 in positions:
                break

            if idle_rounds >= 15:
                break

        return sorted(
            collected.values(),
            key=lambda item: item["position"] if item.get("position") is not None else 10**9,
        ), expected_count
    finally:
        driver.quit()


def _tracks_to_csv(tracks: List[Dict[str, str]]) -> io.BytesIO:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Position Number", "Song - Artist"])
    for index, track in enumerate(tracks, start=1):
        song = track.get("song", "").strip()
        artist = track.get("artist", "").strip()
        position = track.get("position")
        writer.writerow([position or index, f"{song} - {artist}".strip(" - ")])

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
                    error = "No tracks found. Try a different playlist or retry."
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
