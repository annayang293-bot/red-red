"""Video transcript helpers — fetch audio from v.redd.it CDN, transcribe via OpenAI Whisper.

Background (Anna 2026-06-01): the writer team needs the substance of Reddit video posts to
draft. Reddit's API/JSON wall blocks datacenter IPs (the whole reason `auth_mode=apify` exists
upstream), but **v.redd.it (Reddit's CDN) serves the underlying MP4 / audio MP4 without auth**
— verified on residential, expected to work from Apify-backed listing context too. So we don't
need yet another Apify actor for the audio bytes; a direct HTTP GET on the v.redd.it audio URL
is enough.

The harshmaur listing call already returns the v.redd.it base URL in `contentUrl` for video
posts (`postType: "hosted:video"`). From `https://v.redd.it/<id>`, the audio stream lives at
`<id>/CMAF_AUDIO_128.mp4`. We GET that, hand the bytes to OpenAI's whisper-1 transcription
endpoint, and stash the resulting text on the HotItem.

Failure policy (A' from the 2026-06-01 sizing discussion): every step here returns None / "" on
failure rather than raising. A single failed transcription should NOT collapse the whole run —
the post still ships with `transcript=NULL`. We log + add a sanity anomaly so the operator can
spot a wide-spread failure pattern; we don't bubble the error to `failed_sources`. If we ever
observe Apify-class IPs being blocked by v.redd.it in prod, the spec calls for adding a
proxy/Apify-passthrough fallback as v2 (~5 lines of try/except).

Cost on real load: Whisper is $0.006/min of audio. The Hinton spike (76s video) cost $0.0076.
Daily cron with ~1-3 video posts per run ≈ <$0.05/mo — negligible vs the Reddit listing cost.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-1"
WHISPER_PRICE_PER_MINUTE_USD = 0.006   # OpenAI list price as of 2026-06-01

# v.redd.it stream naming. Reddit produces a couple of bitrate variants under
# CMAF_AUDIO_<bitrate>.mp4; 128 kbps is the only audio stream Reddit currently emits and is
# what yt-dlp -g returns. If Reddit ever ships multi-bitrate audio we'll need to probe the
# DASH manifest — for now this is enough.
_VREDDIT_HOST_RE = re.compile(r"^https?://v\.redd\.it/([a-z0-9]+)(?:/.*)?$", re.IGNORECASE)
AUDIO_FILENAME = "CMAF_AUDIO_128.mp4"

# Bound the audio fetch + Whisper. v.redd.it audio for a typical 60-180s clip is < 5 MB and
# downloads in under 2s residential; Whisper round-trip averages 6-10s. 60s gives ample margin
# without letting one stuck video hang the whole pipeline.
_AUDIO_FETCH_TIMEOUT_S = 60
_WHISPER_TIMEOUT_S = 120
# Skip any audio file over this size — Whisper rejects >25 MB anyway, and a 30 MB+ download is
# almost certainly a long-form clip we don't want to spend budget on for the first-version
# integration. Operator can lift later if longer-form video posts become a thing.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


def is_reddit_video_post(source_native: dict | None) -> bool:
    """Return True if this post has a v.redd.it–hosted video that we can transcribe.

    Inputs that look like videos but don't qualify:
      - YouTube embeds (`postType: "rich:video"`) — different infra, not v.redd.it; supported
        in a later phase. Today these return False.
      - Image / GIF posts with `flair: "Video"` typo — guard against by requiring the URL too.
    """
    if not source_native:
        return False
    post_type = (source_native.get("post_type")
                 or source_native.get("postType")
                 or "")
    content_url = (source_native.get("content_url")
                   or source_native.get("contentUrl")
                   or "")
    if "hosted:video" not in post_type.lower():
        return False
    return bool(_VREDDIT_HOST_RE.match(content_url))


def vreddit_audio_url(content_url: str) -> str | None:
    """`https://v.redd.it/16akzxundn4h1` → `https://v.redd.it/16akzxundn4h1/CMAF_AUDIO_128.mp4`.

    Returns None on a non-v.redd.it URL (no other host we know is consistent in its CDN
    layout, so we don't try).
    """
    m = _VREDDIT_HOST_RE.match(content_url or "")
    if not m:
        return None
    video_id = m.group(1)
    return f"https://v.redd.it/{video_id}/{AUDIO_FILENAME}"


def download_audio(url: str, *, timeout_s: int = _AUDIO_FETCH_TIMEOUT_S,
                   max_bytes: int = _MAX_AUDIO_BYTES) -> bytes | None:
    """GET the audio MP4. Returns the bytes on 200, None on any failure.

    Reddit's CDN doesn't require auth, but it does sometimes throttle anonymous requests
    without a sensible UA. We send a real-browser-shape UA to keep it plain-vanilla; we do not
    rotate proxies (the A' decision deferred that).
    """
    if not url:
        return None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout_s, stream=True)
        r.raise_for_status()
        length = r.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            print(f"[transcribe] skip audio over {max_bytes // (1024*1024)} MB ({length} bytes): {url}")
            return None
        # Stream into memory so we can enforce the size cap even when the server doesn't send Content-Length.
        buf = bytearray()
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > max_bytes:
                print(f"[transcribe] audio exceeded {max_bytes // (1024*1024)} MB while downloading: {url}")
                return None
        return bytes(buf)
    except requests.RequestException as e:
        print(f"[transcribe] audio fetch failed for {url}: {e}")
        return None


def _probe_duration_seconds(audio_bytes: bytes) -> float | None:
    """Best-effort duration check via ffprobe. Returns None if ffprobe isn't on PATH or the
    file is unreadable — we proceed with transcription either way; this is only used to
    compute the Whisper cost line item upstream.
    """
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("ffprobe"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(audio_bytes)
        f.flush()
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", f.name],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:   # noqa: BLE001 — ffprobe is best-effort
            return None
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def transcribe_audio(audio_bytes: bytes, *, openai_key: str | None = None,
                     filename: str = "audio.mp4",
                     model: str = WHISPER_MODEL,
                     timeout_s: int = _WHISPER_TIMEOUT_S) -> dict | None:
    """Send the bytes to OpenAI Whisper, return {text, language, duration_seconds, cost_usd}.

    Returns None on any failure (missing key, HTTP error, malformed response). Caller treats
    None as "couldn't transcribe — move on" per the A' policy.
    """
    key = openai_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        print("[transcribe] OPENAI_API_KEY missing; skipping transcription")
        return None
    if not audio_bytes:
        return None

    # Build multipart/form-data by hand to avoid pulling in another dep (we already only use
    # `requests` everywhere else). Whisper accepts m4a/mp3/mp4/webm/etc; the v.redd.it audio
    # MP4 works as audio/mp4.
    import urllib.request
    boundary = "----whisper-boundary-1f5b"
    parts: list[bytes] = []
    def field(name: str, value: str):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8")
        )
    def file_field(name: str, fn: str, content: bytes, mime: str = "audio/mp4"):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fn}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + content + b"\r\n"
        )
    field("model", model)
    field("response_format", "verbose_json")
    file_field("file", filename, audio_bytes)
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    req = urllib.request.Request(
        WHISPER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — HTTPError, URLError, json decode all funnel here
        print(f"[transcribe] whisper request failed: {e}")
        return None

    text = (payload.get("text") or "").strip()
    if not text:
        return None
    duration = payload.get("duration")
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    if duration_seconds is None:
        duration_seconds = _probe_duration_seconds(audio_bytes)
    cost = (duration_seconds / 60.0 * WHISPER_PRICE_PER_MINUTE_USD) if duration_seconds else None
    return {
        "text": text,
        "language": payload.get("language") or "",
        "duration_seconds": duration_seconds,
        "cost_usd": round(cost, 6) if cost is not None else None,
    }


def transcribe_reddit_video(source_native: dict | None,
                            *, openai_key: str | None = None) -> dict | None:
    """Orchestrate: check eligibility → build audio URL → fetch bytes → Whisper.

    Returns the same shape as `transcribe_audio` on success, or None at any non-fatal failure
    point. The caller stashes the dict on the HotItem's source_native + writes to
    posts_archive's three transcript columns at store time.
    """
    if not is_reddit_video_post(source_native):
        return None
    content_url = (source_native.get("content_url")
                   or source_native.get("contentUrl"))
    audio_url = vreddit_audio_url(content_url)
    if not audio_url:
        return None
    t0 = time.time()
    audio_bytes = download_audio(audio_url)
    if not audio_bytes:
        return None
    print(f"[transcribe] audio bytes={len(audio_bytes)} fetched in {time.time()-t0:.1f}s for {audio_url}")
    t1 = time.time()
    result = transcribe_audio(audio_bytes, openai_key=openai_key)
    if not result:
        return None
    print(f"[transcribe] ok: lang={result['language']} dur={result['duration_seconds']}s "
          f"cost=${result['cost_usd']} elapsed={time.time()-t1:.1f}s")
    return result
