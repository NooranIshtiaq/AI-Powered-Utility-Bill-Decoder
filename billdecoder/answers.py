"""Level 2: the LLM phrases verified numbers. Every answer is number-checked; unsupported figures fall back to code-written text."""
from __future__ import annotations
import json, logging
from pydantic import BaseModel
from .schemas import BillFacts, LEVEL1_KEYS
from . import llm, analytics, insights, validation

log = logging.getLogger("billdecoder.answers")

STYLE = """You are a friendly electricity-bill assistant for Pakistani customers.
Rules:
- Use ONLY the bill data, the pre-computed results and the derived figures below. No outside tariff/rate knowledge.
- Every number must come from that data. Do NOT do your own arithmetic; reuse the given figures. If a needed figure is not given, say it cannot be determined from the bill.
- Plain English, 2-4 sentences, specific (name months, units, PKR amounts). English only, even if the bill has Urdu.
- Label estimates as estimates and say how they were calculated. If the bill lacks information, say so clearly.
- If a question has two reasonable meanings, state which you used (or answer both briefly).
- Never mention names, addresses, account/reference/meter numbers.
"""


class QA(BaseModel):
    question_id: str
    answer: str


class QAList(BaseModel):
    answers: list[QA]


def grounding(facts: BillFacts, computed: dict) -> str:
    d = facts.model_dump()
    bill = {k: d[k] for k in LEVEL1_KEYS}
    for k in ("is_net_metering", "net_metering_evidence", "fpa_month", "fpa_amount", "history", "late_surcharges",
              "slabs", "energy_share_percent", "tax_share_percent", "notes"):
        bill[k] = d[k]
    pre = {k: v for k, v in computed.items() if not k.startswith("_")}
    return ("BILL DATA:\n" + json.dumps(bill, ensure_ascii=False)
            + "\n\nPRE-COMPUTED RESULTS (verified, computed with Python):\n" + json.dumps(pre, ensure_ascii=False)
            + "\n\nDERIVED FIGURES (verified):\n" + json.dumps(insights.derived(facts), ensure_ascii=False))


def answer_all(facts: BillFacts, questions: list[dict], computed: dict | None = None):
    """Returns (answers {qid: text}, meta {qid: {verified, source}})."""
    computed = computed or analytics.compute(facts)
    ctx = grounding(facts, computed)
    allowed = validation.numbers_in(ctx)
    qtext = "\n".join(f"{q['question_id']}: {q['question']}" for q in questions)
    prompt = (STYLE + "\n" + ctx + "\n\nQUESTIONS:\n" + qtext
              + "\n\nFor each question write the final answer using the matching pre-computed result as the factual base "
                "(for questions without one, derive it from BILL DATA / DERIVED FIGURES). Return one answer per question_id.")
    try:
        got = {a.question_id: a.answer.strip() for a in llm.generate_structured(prompt, QAList).answers if a.answer.strip()}
    except Exception as e:
        log.error("LLM answering failed, using computed drafts: %s", e)
        got = {}
    answers, meta = {}, {}
    for q in questions:
        qid, draft, text = q["question_id"], computed.get(q["question_id"]), got.get(q["question_id"])
        if text and not (bad := validation.unsupported(text, allowed)):
            answers[qid], meta[qid] = text, {"verified": True, "source": "llm"}
        elif draft:
            if text:
                log.warning("%s: unsupported numbers %s -> using computed answer", qid, bad)
            answers[qid], meta[qid] = draft, {"verified": True, "source": "computed"}
        else:
            answers[qid] = text or "The bill does not contain enough readable information to answer this."
            meta[qid] = {"verified": False, "source": "llm" if text else "none"}
    return answers, meta


def answer_one(facts: BillFacts, question: str) -> dict:
    computed = analytics.compute(facts)
    ctx = grounding(facts, computed)
    allowed = validation.numbers_in(ctx)
    prompt = STYLE + "\n" + ctx + f"\n\nCUSTOMER QUESTION: {question}\nAnswer directly in plain English using the data above."
    text = llm.generate_text(prompt)
    bad = validation.unsupported(text, allowed)
    if bad:                                           # one corrective retry, naming the offending figures
        text = llm.generate_text(prompt + f"\n\nYour previous answer used figures that are not in the data: {bad}. "
                                          "Rewrite using only the given figures, or say it cannot be determined from the bill.")
        bad = validation.unsupported(text, allowed)
    if bad:
        return {"answer": text + "\n(Note: some figures in this answer could not be verified against the bill.)", "verified": False}
    return {"answer": text, "verified": True}
