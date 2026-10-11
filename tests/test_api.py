import io, json
from unittest import mock
import pytest
from billdecoder import config, extract, answers, translate as tr
from billdecoder.schemas import BillFacts, ChargeLine, TaxLine, HistoryRow, SlabLine

PNG = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "API_KEY", "x")
    import app as A
    return A.app.test_client()


def make_facts():
    return BillFacts(provider="KE", tariff="A1-R", bill_month="2026-04", units_consumed=151, current_bill=3430.24, total_charges=2860.62,
                     total_taxes=569.62, due_date="2026-04-21", payable_within_due_date=3430, payable_after_due_date=3716,
                     charges=[ChargeLine(type="fixed", amount=900)], taxes=[TaxLine(type="gst", amount=520.21)],
                     slabs=[SlabLine(label="Upto 200", upto_units=200)],
                     history=[HistoryRow(month=f"2025-{m:02d}", units=100 + 10 * m, bill_amount=2000, payment_amount=2000) for m in range(5, 13)])


def upload(client, content=b"img1"):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (40, 40), "white").save(b, "PNG")
    return client.post("/api/analyze", data={"bill": (io.BytesIO(b.getvalue() + content), "b.png", "image/png")})


def test_home_page_keeps_controls_without_decorative_emojis(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert all(f'id="{control}"' in html for control in ("drop", "file", "theme", "tabs", "xpdf", "xcsv", "xcard"))
    assert not any(mark in html for mark in "⚡📄💡✅⚠🟢🟡🔴ℹ⬇🖼✓✔")
    ask_tab = html.split('id="p-qa"', 1)[1].split('id="p-da"', 1)[0]
    assert ask_tab.index('data-i="ask_t"') < ask_tab.index('data-i="common"')


def test_full_flow_with_hallucination_guard(client):
    bad = answers.QAList(answers=[answers.QA(question_id="Q2", answer="Your highest month used 9999 units."),
                                  answers.QA(question_id="Q1", answer="This is a standard connection.")])
    with mock.patch.object(extract, "extract_facts", return_value=make_facts()), \
         mock.patch.object(answers.llm, "generate_structured", return_value=bad):
        r = upload(client)
        assert r.status_code == 200
        d = r.json
    assert d["answers"]["Q1"] == "This is a standard connection." and d["answer_meta"]["Q1"]["source"] == "llm"
    assert "9999" not in d["answers"]["Q2"] and d["answer_meta"]["Q2"]["source"] == "computed"     # hallucinated number replaced
    assert len(d["answers"]) == 12 and d["insights"]["slab_alert"]["available"]
    key = d["bill_key"]

    w = client.post("/api/whatif", json={"bill_key": key, "units": 100}).json
    assert w["available"] and w["estimated_bill"] < 3430
    assert client.post("/api/whatif", json={"bill_key": key, "units": "abc"}).status_code == 400
    assert client.post("/api/whatif", json={"bill_key": "../etc/passwd", "units": 1}).status_code == 404

    pdf = client.get(f"/api/export/pdf/{key}")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    csv_ = client.get(f"/api/export/csv/{key}")
    assert csv_.status_code == 200 and b"section,item,value" in csv_.data
    assert client.get("/api/export/xls/" + key).status_code == 404


def test_translation_guard(client):
    with mock.patch.object(extract, "extract_facts", return_value=make_facts()), \
         mock.patch.object(answers.llm, "generate_structured", side_effect=RuntimeError("down")):
        key = upload(client, b"img2").json["bill_key"]
    res = client.post("/api/translate", json={"bill_key": key, "lang": "fr"})
    assert res.status_code == 400

    def fake(prompt, schema, image=None):
        n = prompt.count("\n") 
        items = []
        for line in prompt.split("\n"):
            if line[:1].isdigit() and ". " in line:
                text = line.split(". ", 1)[1]
                items.append("TRANSLATED " + text.replace("3,430", "3,999"))     # tampered numbers must be rejected
        return tr.Items(items=items)
    with mock.patch.object(tr.llm, "generate_structured", side_effect=fake):
        d = client.post("/api/translate", json={"bill_key": key, "lang": "roman_ur"}).json
    src = client.post("/api/ask", json={"bill_key": key, "question": ""}).status_code
    assert src == 400
    assert any(v.startswith("TRANSLATED") for v in d["answers"].values())
    assert not any("3,999" in v for v in d["answers"].values())


def test_upload_validation(client):
    assert client.post("/api/analyze", data={}).status_code == 400
    assert client.post("/api/analyze", data={"bill": (io.BytesIO(b"x"), "a.txt", "text/plain")}).status_code == 400
