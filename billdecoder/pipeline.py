"""Orchestration: bill image -> facts -> Level 1 + insights + answers. Disk cache saves free-tier quota."""
from __future__ import annotations
import csv, hashlib, json, logging, re, time
from pathlib import Path
from .schemas import BillFacts
from . import config, extract, answers, analytics, insights

log = logging.getLogger("billdecoder.pipeline")
KEY_RE = re.compile(r"[0-9a-f]{16}_[\w.\-]+")


def _dir() -> Path:
    p = Path(config.CACHE_DIR); p.mkdir(parents=True, exist_ok=True)
    return p


def valid_key(key: str) -> bool:
    return bool(KEY_RE.fullmatch(key or ""))


def facts_for(image_bytes: bytes, use_cache: bool = True) -> tuple[BillFacts, str]:
    key = hashlib.sha256(image_bytes).hexdigest()[:16] + "_" + config.MODEL_NAME.replace("/", "_")
    cp = _dir() / f"{key}.json"
    if use_cache and cp.exists():
        return BillFacts.model_validate_json(cp.read_text()), key
    facts = extract.extract_facts(image_bytes)
    cp.write_text(facts.model_dump_json())
    return facts, key


def load_facts(key: str) -> BillFacts | None:
    cp = _dir() / f"{key}.json"
    return BillFacts.model_validate_json(cp.read_text()) if valid_key(key) and cp.exists() else None


def load_result(key: str) -> dict | None:
    rp = _dir() / f"{key}.result.json"
    return json.loads(rp.read_text()) if valid_key(key) and rp.exists() else None


def analyze(image_bytes: bytes, questions: list[dict] | None = None) -> dict:
    facts, key = facts_for(image_bytes)
    computed = analytics.compute(facts)
    result = {
        "bill_key": key,
        "level1": extract.to_level1(facts),
        "warnings": extract.validate(facts),
        "facts": facts.model_dump(),
        "analytics": {k: v for k, v in computed.items() if not k.startswith("_")},
        "insights": insights.build(facts, computed),
    }
    if questions:
        result["answers"], result["answer_meta"] = answers.answer_all(facts, questions, computed)
    (_dir() / f"{key}.result.json").write_text(json.dumps(result, ensure_ascii=False))
    return result


# ---------------- CSV batch (competition format) ----------------
def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_MINIMAL)
        w.writeheader(); w.writerows(rows)


def find_image(bills_dir: Path, bill_id: str) -> Path:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = bills_dir / f"{bill_id}{ext}"
        if p.exists(): return p
    raise FileNotFoundError(f"No image for {bill_id} in {bills_dir}")


def run_batch(bills_dir: Path, csv_dir: Path, out_dir: Path) -> None:
    l1_rows, l2_rows = read_csv(csv_dir / "level1.csv"), read_csv(csv_dir / "level2.csv")
    by_bill: dict[str, list[dict]] = {}
    for r in l2_rows: by_bill.setdefault(r["bill_id"], []).append(r)

    cache: dict[str, dict] = {}
    for r in l1_rows:
        bid = r["bill_id"]
        try:
            res = analyze(find_image(bills_dir, bid).read_bytes(), by_bill.get(bid, []))
            cache[bid] = res
            for w in res["warnings"]: log.warning("%s: %s", bid, w)
            log.info("%s done", bid)
        except Exception as e:                       # one bad bill must not kill the run
            log.error("%s failed: %s", bid, e)
            cache[bid] = {"level1": {**{k: None for k in extract.LEVEL1_KEYS}, "charges": [], "taxes": []}, "answers": {}}
        time.sleep(config.REQUEST_DELAY_SECONDS)

    for r in l1_rows:
        r["json"] = json.dumps(cache[r["bill_id"]]["level1"], ensure_ascii=False, separators=(",", ":"))
    for r in l2_rows:
        r["answer"] = cache[r["bill_id"]].get("answers", {}).get(r["question_id"]) or \
            "The answer could not be generated for this bill because its image could not be processed."
    write_csv(out_dir / "level1.csv", l1_rows, ["bill_id", "json"])
    write_csv(out_dir / "level2.csv", l2_rows, ["bill_id", "question_id", "question", "answer"])
    (out_dir / "details.json").write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    log.info("Wrote %s", out_dir)
