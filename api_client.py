"""
Thin HTTP client the bot uses to reach its own REST API (api.py) for the
AI-chat and QR-code features, instead of calling ai_handler.py / qr_handler.py
directly in-process. Both processes run in the same container (start.sh)
so this defaults to localhost, but can point anywhere via API_BASE_URL.
"""

import httpx
from config import Config

# api.py can take a moment to come up after start.sh launches both
# processes together; a couple of quick retries covers that race
# without making the user wait long on a real failure.
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_RETRIES = 2

# Guard against a trailing slash in API_BASE_URL (e.g. "https://host.app/")
# producing a double slash like ".../ /api/ai/chat", which triggers a 308
# redirect that httpx won't follow by default — silently breaking every call.
_BASE_URL = (Config.API_BASE_URL or "").rstrip("/")


def _headers():
    h = {}
    if Config.API_KEY:
        h["X-API-Key"] = Config.API_KEY
    return h


async def ai_chat(user_id: int, prompt: str):
    """Calls POST /api/ai/chat. Returns (response_text_or_None, status_message)."""
    url = f"{_BASE_URL}/api/ai/chat"
    last_error = None
    for attempt in range(_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                r = await client.post(url, json={"user_id": user_id, "prompt": prompt}, headers=_headers())
            if r.status_code == 200:
                data = r.json()
                return data.get("response"), data.get("status", "✅")
            if r.status_code == 429:
                # Daily limit hit — API returns the limit message in "detail"
                return None, r.json().get("detail", "❌ Daily limit khatam.")
            if r.status_code == 404:
                return None, "❌ User not found. Please /start the bot first."
            if r.status_code == 401:
                return None, "❌ API auth failed — check API_KEY matches on both services."
            last_error = f"API error {r.status_code}: {r.text[:200]}"
        except httpx.RequestError as e:
            last_error = f"API unreachable: {e}"
        if attempt < _RETRIES:
            import asyncio
            await asyncio.sleep(1)
    return None, f"❌ AI service abhi available nahi hai. ({last_error})"


async def generate_qr(text: str):
    """Calls POST /api/qr. Returns (png_bytes_or_None, error_message_or_None)."""
    url = f"{_BASE_URL}/api/qr"
    last_error = None
    for attempt in range(_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                r = await client.post(url, json={"text": text}, headers=_headers())
            if r.status_code == 200:
                return r.content, None
            if r.status_code == 401:
                return None, "❌ API auth failed — check API_KEY matches on both services."
            last_error = f"API error {r.status_code}: {r.text[:200]}"
        except httpx.RequestError as e:
            last_error = f"API unreachable: {e}"
        if attempt < _RETRIES:
            import asyncio
            await asyncio.sleep(1)
    return None, f"❌ QR service abhi available nahi hai. ({last_error})"
