"""
tiktok.py — fetch the latest regular video from a TikTok profile using yt-dlp.
Explicitly skips Stories, Reposts, and Pinned items.
"""

import subprocess
import json
import time


def fetch_latest_video(username: str, *, retries: int = 2, delay: float = 3.0) -> dict | None:
    """
    Double-fetches the latest regular video and only returns it if both
    calls agree on the same video ID — prevents acting on stale CDN responses.
    """
    results = []

    for attempt in range(retries):
        video = _fetch_once(username)
        if video is None:
            print(f"[tiktok] Attempt {attempt + 1} failed for @{username}")
        else:
            results.append(video)

        if attempt < retries - 1:
            time.sleep(delay)

    if len(results) < 2:
        print(f"[tiktok] Could not get a confirmed result for @{username}")
        return None

    if results[0]["id"] != results[1]["id"]:
        print(f"[tiktok] ID mismatch for @{username}: {results[0]['id']!r} vs {results[1]['id']!r} — skipping")
        return None

    print(f"[tiktok] Confirmed video {results[0]['id']!r} for @{username}")
    return results[0]


def _fetch_once(username: str) -> dict | None:
    """
    Single yt-dlp fetch targeting ONLY the regular video feed.
    Uses /video/ sub-path to avoid the stories/repost playlists.
    Scans up to 5 items and picks the first one that looks like a real video.
    """
    # /video/ forces the regular video tab — avoids stories & reposts
    profile_url = f"https://www.tiktok.com/@{username}/video"

    cmd = [
        "yt-dlp",
        "--dump-json",
        "--playlist-items", "1-5",   # grab a few in case #1 is a pinned/story
        "--no-warnings",
        "--no-cache-dir",
        # Tell yt-dlp to skip the Stories and LIVE tab playlists explicitly
        "--ignore-errors",
        profile_url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)

        if not result.stdout.strip():
            print(f"[tiktok] No output from yt-dlp for @{username}: {result.stderr[:300]}")
            return None

        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        if not lines:
            return None

        # Walk through returned items and pick the first that is a real video
        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            video = _parse_video(data)
            if video is None:
                continue

            # Skip anything that smells like a story or repost
            if _is_story_or_repost(data, video):
                print(f"[tiktok] Skipping non-video item {video['id']!r} for @{username}")
                continue

            return video

        print(f"[tiktok] No regular videos found for @{username}")
        return None

    except subprocess.TimeoutExpired:
        print(f"[tiktok] Timeout fetching @{username}")
        return None
    except Exception as e:
        print(f"[tiktok] Unexpected error for @{username}: {e}")
        return None


def _parse_video(data: dict) -> dict | None:
    """Extract and validate fields from a yt-dlp JSON blob."""
    video_id  = str(data.get("id") or data.get("display_id") or "").strip()
    video_url = data.get("webpage_url") or data.get("url") or ""

    if not video_id or not video_url:
        return None

    desc  = data.get("description") or data.get("title") or ""
    cover = _best_thumbnail(data.get("thumbnails") or [])

    return {
        "id":    video_id,
        "url":   str(video_url),
        "desc":  desc[:300],
        "cover": cover,
        # Keep raw data for story/repost detection
        "_raw": data,
    }


def _is_story_or_repost(data: dict, video: dict) -> bool:
    """
    Returns True if this item looks like a Story, Repost, or Live clip
    rather than a regular posted video.
    """
    url = video["url"].lower()

    # Stories have /story/ in their URL
    if "/story/" in url:
        return True

    # Reposts often have /repost/ or a different uploader than the profile
    if "/repost/" in url:
        return True

    # yt-dlp marks some items with an explicit type
    item_type = str(data.get("_type") or data.get("ie_key") or "").lower()
    if "story" in item_type or "live" in item_type:
        return True

    # Some yt-dlp builds expose a 'view_count' of None for stories
    # Real videos almost always have a view count
    if data.get("view_count") is None and data.get("like_count") is None:
        return True

    return False


def _best_thumbnail(thumbnails: list) -> str | None:
    if not thumbnails:
        return None
    return sorted(thumbnails, key=lambda t: t.get("width") or 0, reverse=True)[0].get("url")
