"""Web UI + JSON API.  python app.py  ->  http://127.0.0.1:5000"""
import logging
from flask import Flask, Response, jsonify, request, send_from_directory
from billdecoder import config, pipeline, answers, insights, translate, reports
from billdecoder.questions import PRESET_QUESTIONS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__, static_folder="web", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
ALLOWED = {"image/png", "image/jpeg", "image/webp"}


def _err(msg, code):
    return jsonify(error=msg), code


@app.get("/")
def index():
    return send_from_directory("web", "index.html")


@app.get("/api/config")
def cfg():
    return jsonify(model=config.MODEL_NAME, questions=PRESET_QUESTIONS, ready=bool(config.API_KEY))


@app.post("/api/analyze")
def analyze():
    f = request.files.get("bill")
    if not f:
        return _err("Please upload a bill image.", 400)
    if f.mimetype not in ALLOWED:
        return _err("Unsupported file type. Use PNG, JPG or WEBP.", 400)
    try:
        return jsonify(pipeline.analyze(f.read(), PRESET_QUESTIONS))
    except RuntimeError as e:
        return _err(str(e), 500)
    except Exception:
        logging.exception("analyze failed")
        return _err("Could not read this bill. Try a clearer image or retry in a minute (free-tier rate limit).", 502)


@app.post("/api/ask")
def ask():
    d = request.get_json(silent=True) or {}
    q = (d.get("question") or "").strip()
    if not q or len(q) > 500:
        return _err("Ask a question (max 500 characters).", 400)
    facts = pipeline.load_facts(str(d.get("bill_key", "")))
    if not facts:
        return _err("Bill not found. Upload it again.", 404)
    try:
        return jsonify(answers.answer_one(facts, q))
    except Exception:
        logging.exception("ask failed")
        return _err("The assistant is busy. Please retry.", 502)


@app.post("/api/whatif")
def whatif():
    d = request.get_json(silent=True) or {}
    facts = pipeline.load_facts(str(d.get("bill_key", "")))
    try:
        units = float(d.get("units"))
    except (TypeError, ValueError):
        return _err("units must be a number", 400)
    if not facts:
        return _err("Bill not found. Upload it again.", 404)
    if not 0 <= units <= 100000:
        return _err("units out of range", 400)
    return jsonify(insights.what_if(facts, units))


@app.post("/api/translate")
def translate_api():
    d = request.get_json(silent=True) or {}
    key, lang = str(d.get("bill_key", "")), d.get("lang")
    res = pipeline.load_result(key)
    if not res:
        return _err("Bill not found. Upload it again.", 404)
    if lang not in translate.LANGS:
        return _err("Unsupported language.", 400)
    ans = res.get("answers", {})
    tips = res["insights"]["tips"]
    alert = res["insights"]["slab_alert"]["message"]
    texts = list(ans.values()) + [t["title"] for t in tips] + [t["text"] for t in tips] + [alert]
    try:
        out = [x["text"] for x in translate.translate(texts, lang)]
    except Exception:
        logging.exception("translate failed")
        return _err("Translation is unavailable right now.", 502)
    n, m = len(ans), len(tips)
    return jsonify(answers=dict(zip(ans.keys(), out[:n])), tip_titles=out[n:n + m], tip_texts=out[n + m:n + 2 * m], slab_message=out[-1])


@app.get("/api/export/<fmt>/<key>")
def export(fmt, key):
    res = pipeline.load_result(key)
    if not res:
        return _err("Bill not found. Upload it again.", 404)
    month = res["level1"].get("bill_month") or "bill"
    if fmt == "csv":
        return Response(reports.csv_bytes(res), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=bill-{month}.csv"})
    if fmt == "pdf":
        return Response(reports.pdf_bytes(res), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename=bill-report-{month}.pdf"})
    return _err("Unknown export format.", 404)


if __name__ == "__main__":
    app.run(debug=False, port=5000)
