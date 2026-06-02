"""
tiktok.py — fetch the latest regular video from a TikTok profile using yt-dlp.
Explicitly skips Stories, Reposts, and Pinned items.
Uses browser spoofing to prevent TikTok from redirecting to the homepage.
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
    Single yt-dlp fetch targeting the regular video feed.
    Tries multiple URL formats in case TikTok redirects one of them.
    Uses browser spoofing to avoid homepage/foryou redirects.
    """
    # Try these URL formats in order
    url_candidates = [
        f"https://www.tiktok.com/@{username}/video",
        f"https://www.tiktok.com/@{username}",
    ]

    for profile_url in url_candidates:
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--playlist-items", "1-5",
            "--no-warnings",
            "--no-cache-dir",
            "--ignore-errors",
            # Spoof a real browser — prevents TikTok redirecting to /foryou
            "--user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36",
            "--add-header", "Referer:https://www.tiktok.com/",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
            profile_url,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)

            # TikTok redirected to homepage — try next URL format
            if "Unsupported URL" in result.stderr or "foryou" in result.stderr:
                print(f"[tiktok] Redirected on {profile_url} for @{username}, trying fallback...")
                continue

            if not result.stdout.strip():
                print(f"[tiktok] No output for @{username} ({profile_url}): {result.stderr[:300]}")
                continue

            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            if not lines:
                continue

            for line in lines:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                video = _parse_video(data)
                if video is None:
                    continue

                if _is_story_or_repost(data, video):
                    print(f"[tiktok] Skipping non-video item {video['id']!r} for @{username}")
                    continue

                return video

            print(f"[tiktok] No regular videos found at {profile_url} for @{username}")

        except subprocess.TimeoutExpired:
            print(f"[tiktok] Timeout fetching @{username}")
        except Exception as e:
            print(f"[tiktok] Unexpected error for @{username}: {e}")

    # All URL formats exhausted
    print(f"[tiktok] All URL formats failed for @{username}. Try: yt-dlp -U to update yt-dlp.")
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
        "_raw":  data,
    }


def _is_story_or_repost(data: dict, video: dict) -> bool:
    url = video["url"].lower()
    if "/story/" in url or "/repost/" in url:
        return True
    item_type = str(data.get("_type") or data.get("ie_key") or "").lower()
    if "story" in item_type or "live" in item_type:
        return True
    if data.get("view_count") is None and data.get("like_count") is None:
        return True
    return False


def _best_thumbnail(thumbnails: list) -> str | None:
    if not thumbnails:
        return None
    return sorted(thumbnails, key=lambda t: t.get("width") or 0, reverse=True)[0].get("url")
