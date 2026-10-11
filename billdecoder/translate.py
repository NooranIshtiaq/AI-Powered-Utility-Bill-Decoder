"""Urdu / Roman Urdu translation of generated text with a number-preservation check."""
from __future__ import annotations
import logging
from pydantic import BaseModel
from . import llm, validation

log = logging.getLogger("billdecoder.translate")

LANGS = {
    "roman_ur": "Roman Urdu: natural, conversational Pakistani Urdu written in Latin letters (e.g. 'Aap ka bill is mahine Rs 3,430 hai')",
    "ur": "Urdu in Urdu script, simple standard Urdu",
}


class Items(BaseModel):
    items: list[str]


def translate(texts: list[str], lang: str) -> list[dict]:
    """Returns [{'text','translated'}] same length as input. Any item whose numbers change stays in English."""
    if lang not in LANGS or not texts:
        return [{"text": t, "translated": False} for t in texts]
    prompt = (f"Translate each item into {LANGS[lang]}.\n"
              "Rules: keep EVERY number exactly as written using Western digits 0-9 (never convert digits, never add or remove numbers); "
              "keep currency as 'PKR' or 'Rs'; keep the meaning exact, do not add advice; return the same number of items in the same order.\n\n"
              + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)))
    try:
        items = llm.generate_structured(prompt, Items).items
    except Exception as e:
        log.error("translation failed: %s", e)
        items = []
    if len(items) != len(texts):
        return [{"text": t, "translated": False} for t in texts]
    out = []
    for src, dst in zip(texts, items):
        ok = bool(dst.strip()) and validation.translation_ok(src, dst)
        out.append({"text": dst.strip() if ok else src, "translated": ok})
    return out
