"""pipeline/transcribe.py — mocked tests. No real Apify / OpenAI calls.

Covers the A' design from 2026-06-01: direct v.redd.it audio fetch + Whisper, fail-soft on
every step.

Run: python3 system1-app/pipeline/tests/test_transcribe.py
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline import transcribe  # noqa: E402


# ---- helpers ----
@contextmanager
def _env(**vars):
    saved = {}
    for k, v in vars.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeResp:
    def __init__(self, status_code: int, payload: bytes = b"", headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=64 * 1024):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i:i + chunk_size]


# ---- is_reddit_video_post / vreddit_audio_url ----
def test_is_reddit_video_post_happy():
    assert transcribe.is_reddit_video_post({
        "postType": "hosted:video",
        "contentUrl": "https://v.redd.it/abc123",
    })
    # Also accept lowercased keys (snake_case future-proofing)
    assert transcribe.is_reddit_video_post({
        "post_type": "hosted:video",
        "content_url": "https://v.redd.it/abc123/",
    })
    print("✅ test_is_reddit_video_post_happy")


def test_is_reddit_video_post_rejects_youtube_embeds():
    # YouTube via Reddit cross-post: postType is rich:video, contentUrl points at youtu.be —
    # not in scope today (would need separate fetcher), so we say no.
    assert not transcribe.is_reddit_video_post({
        "postType": "rich:video",
        "contentUrl": "https://youtu.be/dQw4w9WgXcQ",
    })
    print("✅ test_is_reddit_video_post_rejects_youtube_embeds")


def test_is_reddit_video_post_rejects_missing_or_mismatched():
    assert not transcribe.is_reddit_video_post(None)
    assert not transcribe.is_reddit_video_post({})
    assert not transcribe.is_reddit_video_post({"postType": "hosted:video"})  # no contentUrl
    assert not transcribe.is_reddit_video_post({"contentUrl": "https://v.redd.it/abc"})  # no postType
    assert not transcribe.is_reddit_video_post({
        "postType": "image",
        "contentUrl": "https://v.redd.it/abc",
    })
    print("✅ test_is_reddit_video_post_rejects_missing_or_mismatched")


def test_vreddit_audio_url_constructs_correctly():
    assert (transcribe.vreddit_audio_url("https://v.redd.it/16akzxundn4h1")
            == "https://v.redd.it/16akzxundn4h1/CMAF_AUDIO_128.mp4")
    # Trailing slash tolerated.
    assert (transcribe.vreddit_audio_url("https://v.redd.it/16akzxundn4h1/")
            == "https://v.redd.it/16akzxundn4h1/CMAF_AUDIO_128.mp4")
    # http (vs https) tolerated.
    assert (transcribe.vreddit_audio_url("http://v.redd.it/xyz")
            == "https://v.redd.it/xyz/CMAF_AUDIO_128.mp4")
    print("✅ test_vreddit_audio_url_constructs_correctly")


def test_vreddit_audio_url_returns_none_for_non_vreddit():
    assert transcribe.vreddit_audio_url("https://youtu.be/abc") is None
    assert transcribe.vreddit_audio_url("https://www.reddit.com/r/x/comments/...") is None
    assert transcribe.vreddit_audio_url("") is None
    assert transcribe.vreddit_audio_url(None) is None
    print("✅ test_vreddit_audio_url_returns_none_for_non_vreddit")


# ---- download_audio ----
def test_download_audio_happy_path_returns_bytes():
    payload = b"\x00\x00\x00\x20ftypmp42" + b"x" * 1024  # ISO MP4 magic + body
    fake = _FakeResp(200, payload, headers={"Content-Length": str(len(payload))})
    with patch("pipeline.transcribe.requests.get", return_value=fake):
        got = transcribe.download_audio("https://v.redd.it/x/CMAF_AUDIO_128.mp4")
    assert got == payload
    print(f"✅ test_download_audio_happy_path_returns_bytes ({len(got)} bytes)")


def test_download_audio_rejects_oversized_via_content_length():
    huge = 50 * 1024 * 1024
    fake = _FakeResp(200, b"x" * 100, headers={"Content-Length": str(huge)})
    with patch("pipeline.transcribe.requests.get", return_value=fake):
        got = transcribe.download_audio("https://v.redd.it/x/CMAF_AUDIO_128.mp4")
    assert got is None
    print("✅ test_download_audio_rejects_oversized_via_content_length")


def test_download_audio_rejects_oversized_when_no_content_length():
    """Servers don't always send Content-Length (chunked transfer-encoding). The streaming
    loop has its own cap so we don't blow memory."""
    over_cap = transcribe._MAX_AUDIO_BYTES + 100
    fake = _FakeResp(200, b"y" * over_cap, headers={})  # no Content-Length
    with patch("pipeline.transcribe.requests.get", return_value=fake):
        got = transcribe.download_audio("https://v.redd.it/x/CMAF_AUDIO_128.mp4")
    assert got is None
    print("✅ test_download_audio_rejects_oversized_when_no_content_length")


def test_download_audio_returns_none_on_http_error():
    fake = _FakeResp(403, b"forbidden")
    with patch("pipeline.transcribe.requests.get", return_value=fake):
        got = transcribe.download_audio("https://v.redd.it/x/CMAF_AUDIO_128.mp4")
    assert got is None
    print("✅ test_download_audio_returns_none_on_http_error")


def test_download_audio_returns_none_on_connection_failure():
    import requests as _r

    def boom(*a, **kw):
        raise _r.ConnectionError("simulated DNS / TLS / TCP fail")

    with patch("pipeline.transcribe.requests.get", side_effect=boom):
        got = transcribe.download_audio("https://v.redd.it/x/CMAF_AUDIO_128.mp4")
    assert got is None
    print("✅ test_download_audio_returns_none_on_connection_failure")


def test_download_audio_handles_empty_url():
    assert transcribe.download_audio("") is None
    assert transcribe.download_audio(None) is None
    print("✅ test_download_audio_handles_empty_url")


# ---- transcribe_audio ----
class _FakeWhisperResp:
    """Mocks the urllib.request.urlopen response context manager."""
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def test_transcribe_audio_happy_path():
    fake_whisper_payload = {
        "text": "Yes, I do.",
        "language": "english",
        "duration": 76.0,
    }
    with _env(OPENAI_API_KEY="sk-test-fake"), \
         patch("urllib.request.urlopen", return_value=_FakeWhisperResp(fake_whisper_payload)):
        got = transcribe.transcribe_audio(b"\x00\x00ftypmp42" + b"x" * 1024)
    assert got is not None
    assert got["text"] == "Yes, I do."
    assert got["language"] == "english"
    assert got["duration_seconds"] == 76.0
    # 76s at $0.006/min → $0.0076
    assert abs(got["cost_usd"] - 0.0076) < 1e-6, got["cost_usd"]
    print(f"✅ test_transcribe_audio_happy_path (cost=${got['cost_usd']})")


def test_transcribe_audio_missing_key_returns_none():
    with _env(OPENAI_API_KEY=None):
        got = transcribe.transcribe_audio(b"some bytes")
    assert got is None
    print("✅ test_transcribe_audio_missing_key_returns_none")


def test_transcribe_audio_empty_bytes_returns_none():
    with _env(OPENAI_API_KEY="sk-test-fake"):
        got = transcribe.transcribe_audio(b"")
    assert got is None
    print("✅ test_transcribe_audio_empty_bytes_returns_none")


def test_transcribe_audio_http_error_returns_none():
    import urllib.error

    def boom(*a, **kw):
        raise urllib.error.HTTPError("u", 500, "Server Error", {}, io.BytesIO(b""))

    with _env(OPENAI_API_KEY="sk-test-fake"), \
         patch("urllib.request.urlopen", side_effect=boom):
        got = transcribe.transcribe_audio(b"some bytes")
    assert got is None
    print("✅ test_transcribe_audio_http_error_returns_none")


def test_transcribe_audio_empty_text_returns_none():
    """Whisper sometimes returns 200 with text=''. That's not useful — treat as no-transcript."""
    with _env(OPENAI_API_KEY="sk-test-fake"), \
         patch("urllib.request.urlopen", return_value=_FakeWhisperResp({"text": "", "duration": 10})):
        got = transcribe.transcribe_audio(b"some bytes")
    assert got is None
    print("✅ test_transcribe_audio_empty_text_returns_none")


# ---- transcribe_reddit_video (end-to-end orchestrator) ----
def test_transcribe_reddit_video_e2e_success():
    source_native = {
        "postType": "hosted:video",
        "contentUrl": "https://v.redd.it/16akzxundn4h1",
    }
    audio_payload = b"\x00\x00ftypmp42" + b"a" * 4096
    fake_audio = _FakeResp(200, audio_payload, headers={"Content-Length": str(len(audio_payload))})
    fake_whisper = _FakeWhisperResp({"text": "transcript here", "language": "english", "duration": 30.0})
    with _env(OPENAI_API_KEY="sk-test-fake"), \
         patch("pipeline.transcribe.requests.get", return_value=fake_audio), \
         patch("urllib.request.urlopen", return_value=fake_whisper):
        got = transcribe.transcribe_reddit_video(source_native)
    assert got and got["text"] == "transcript here"
    assert got["language"] == "english"
    print("✅ test_transcribe_reddit_video_e2e_success")


def test_transcribe_reddit_video_skips_non_video():
    assert transcribe.transcribe_reddit_video({"postType": "image", "contentUrl": "https://i.redd.it/x.jpg"}) is None
    print("✅ test_transcribe_reddit_video_skips_non_video")


def test_transcribe_reddit_video_returns_none_on_audio_fetch_failure():
    """🐞 Regression: audio fetch 403 (DC-IP block scenario from 2026-06-01 spec) → return None,
    don't crash, don't bubble up. Per A': the post still ships with transcript=NULL."""
    source_native = {"postType": "hosted:video", "contentUrl": "https://v.redd.it/abc"}
    fake_403 = _FakeResp(403, b"forbidden")
    with _env(OPENAI_API_KEY="sk-test-fake"), \
         patch("pipeline.transcribe.requests.get", return_value=fake_403):
        got = transcribe.transcribe_reddit_video(source_native)
    assert got is None
    print("✅ test_transcribe_reddit_video_returns_none_on_audio_fetch_failure")


def test_transcribe_reddit_video_returns_none_when_whisper_fails():
    source_native = {"postType": "hosted:video", "contentUrl": "https://v.redd.it/abc"}
    audio_payload = b"\x00\x00ftypmp42" + b"a" * 1024
    fake_audio = _FakeResp(200, audio_payload, headers={"Content-Length": str(len(audio_payload))})
    import urllib.error

    def whisper_boom(*a, **kw):
        raise urllib.error.HTTPError("u", 429, "rate limit", {}, io.BytesIO(b""))

    with _env(OPENAI_API_KEY="sk-test-fake"), \
         patch("pipeline.transcribe.requests.get", return_value=fake_audio), \
         patch("urllib.request.urlopen", side_effect=whisper_boom):
        got = transcribe.transcribe_reddit_video(source_native)
    assert got is None
    print("✅ test_transcribe_reddit_video_returns_none_when_whisper_fails")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
