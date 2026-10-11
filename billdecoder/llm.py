"""Thin Gemini wrapper: image preprocessing, structured output, retry with backoff."""
from __future__ import annotations
import io, json, time, logging
from pathlib import Path
from PIL import Image, ImageOps
from google import genai
from google.genai import types
from . import config

log = logging.getLogger("billdecoder.llm")
_client = None


def client() -> genai.Client:
    global _client
    if _client is None:
        if not config.API_KEY:
            raise RuntimeError("API_KEY is not set. Copy .env.example to .env and add your Gemini key.")
        _client = genai.Client(api_key=config.API_KEY)
    return _client


def prepare_image(path_or_bytes) -> tuple[bytes, str]:
    """Fix EXIF rotation, upscale tiny photos, cap huge scans. Returns (bytes, mime)."""
    raw = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    longest = max(img.size)
    if longest < 1600:                      # small screenshots: upscale so tiny digits stay legible
        scale = 1800 / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    elif longest > 2800:
        scale = 2800 / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "image/png"


def _call(fn):
    delay = 5
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # rate limit / transient server errors -> back off
            msg = str(e)
            transient = any(t in msg for t in ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "timed out"))
            if attempt == config.MAX_RETRIES or not transient:
                raise
            log.warning("Gemini call failed (%s). Retry %d in %ds", msg[:80], attempt, delay)
            time.sleep(delay)
            delay *= 2


def generate_structured(prompt: str, schema, image: tuple[bytes, str] | None = None):
    """Return a parsed pydantic object (schema) from Gemini, temperature 0."""
    parts = []
    if image:
        parts.append(types.Part.from_bytes(data=image[0], mime_type=image[1]))
    parts.append(prompt)
    cfg = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=schema,
    )

    def go():
        resp = client().models.generate_content(model=config.MODEL_NAME, contents=parts, config=cfg)
        if getattr(resp, "parsed", None) is not None:
            return resp.parsed
        return schema.model_validate(json.loads(resp.text))

    return _call(go)


def generate_text(prompt: str) -> str:
    cfg = types.GenerateContentConfig(temperature=0.2)
    return _call(lambda: client().models.generate_content(
        model=config.MODEL_NAME, contents=[prompt], config=cfg).text.strip())
