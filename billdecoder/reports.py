"""Exports: CSV (spreadsheet-friendly) and a PDF report. Built from the saved analysis result, no model calls."""
from __future__ import annotations
import csv, io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from xml.sax.saxutils import escape
from .questions import PRESET_QUESTIONS

QUESTIONS = {q['question_id']: q['question'] for q in PRESET_QUESTIONS}

NAMES = {"energy": "Energy charges", "fixed": "Fixed charges", "fpa": "Fuel price adjustment", "quarterly_adjustment": "Quarterly adjustment",
         "surcharge": "Surcharge", "meter_rent": "Meter rent", "subsidy": "Subsidy", "other": "Other", "gst": "Sales tax (GST)",
         "electricity_duty": "Electricity duty", "income_tax": "Income tax", "municipal_tax": "Municipal tax", "other_tax": "Other tax"}
ACCENT = colors.HexColor("#E8920C")
INK = colors.HexColor("#1B2140")


def _money(x) -> str:
    return "n/a" if x is None else f"{'-' if x < 0 else ''}PKR {abs(x):,.2f}"


def csv_bytes(res: dict) -> bytes:
    l1, f = res["level1"], res["facts"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "item", "value", "extra"])
    for k, v in l1.items():
        if k not in ("charges", "taxes"):
            w.writerow(["summary", k, "" if v is None else v, ""])
    for c in l1["charges"]:
        w.writerow(["charge", c["type"], c["amount"], ""])
    for t in l1["taxes"]:
        w.writerow(["tax", t["type"], t["amount"], ""])
    for h in f["history"]:
        w.writerow(["history", h["month"], "" if h["units"] is None else h["units"],
                    f"bill={h['bill_amount']};payment={h['payment_amount']}"])
    for k, v in res.get("answers", {}).items():
        w.writerow(["answer", k, v, ""])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")        # BOM so Excel opens UTF-8 correctly


def _table(rows, widths):
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5D9E8")),
                           ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def _chart(history, cur_month, cur_units):
    rows = [(h["month"], h["units"]) for h in history if h["units"] is not None]
    if cur_month and cur_units is not None and cur_month not in dict(rows):
        rows.append((cur_month, cur_units))
    rows.sort()
    if not rows:
        return None
    d = Drawing(480, 150)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 30, 25, 440, 110
    bc.data = [[v for _, v in rows]]
    bc.categoryAxis.categoryNames = [m[2:4] + "-" + m[5:] for m, _ in rows]
    bc.categoryAxis.labels.fontSize = 7
    bc.valueAxis.labels.fontSize = 7
    bc.valueAxis.valueMin = min(0, min(v for _, v in rows))
    bc.bars[0].fillColor = ACCENT
    bc.barLabelFormat = "%d"; bc.barLabels.fontSize = 6; bc.barLabels.nudge = 6
    d.add(bc)
    return d


def pdf_bytes(res: dict) -> bytes:
    l1, f, ins = res["level1"], res["facts"], res.get("insights", {})
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=INK, fontSize=20, alignment=0)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=ACCENT, fontSize=12, spaceBefore=10)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12, textColor=INK)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, textColor=colors.grey)
    P = lambda t, s=body: Paragraph(escape(str(t)), s)
    story = [P("Utility Bill Decoder: report", h1),
             P(f"{l1.get('provider') or ''} · Tariff {l1.get('tariff') or 'n/a'} · Bill month {l1.get('bill_month') or 'n/a'}", body), Spacer(1, 6)]

    summary = [["Pay by due date", _money(l1["payable_within_due_date"])], ["Due date", l1.get("due_date") or "n/a"],
               ["Pay after due date", _money(l1["payable_after_due_date"])], ["This month's bill", _money(l1["current_bill"])],
               ["Units consumed", f"{l1['units_consumed']:g}" if l1["units_consumed"] is not None else "n/a"],
               ["Arrears", _money(l1["arrears"])]]
    story += [P("Summary", h2), _table(summary, [90 * mm, 70 * mm])]

    for title, key, tot in (("Charges", "charges", "total_charges"), ("Taxes", "taxes", "total_taxes")):
        rows = [[NAMES.get(x["type"], x["type"]), _money(x["amount"])] for x in l1[key]] or [["No itemised lines printed", ""]]
        rows.append(["Total (as printed)", _money(l1[tot])])
        story += [P(title, h2), _table(rows, [90 * mm, 70 * mm])]

    ch = _chart(f["history"], l1.get("bill_month"), l1.get("units_consumed"))
    if ch:
        story += [KeepTogether([P("Units per month", h2), ch])]

    alert = ins.get("slab_alert")
    if alert:
        story += [P("Slab alert", h2), P(alert["message"])]
    if ins.get("tips"):
        story.append(P("Savings tips", h2))
        story += [P(f"• {t['title']}: {t['text']}") for t in ins["tips"]]
    if res.get("answers"):
        story.append(P("Your bill, explained", h2))
        for qid, ans in res["answers"].items():
            story += [P(QUESTIONS.get(qid, qid), ParagraphStyle("q", parent=body, fontName="Helvetica-Bold")), P(ans), Spacer(1, 3)]
    if res.get("warnings"):
        story += [P("Extraction checks", h2)] + [P("• " + w) for w in res["warnings"]]
    story += [Spacer(1, 10), P("Generated by AI from the bill image; figures are as printed. Estimates are labelled and based only on this bill. "
                               "Always check the original bill for official amounts.", small)]
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title="Utility Bill Decoder report").build(story)
    return out.getvalue()
