"""Level 1: vision extraction + deterministic cleanup/validation."""
from __future__ import annotations
import re
from .schemas import BillFacts, LEVEL1_KEYS
from . import llm

EXTRACT_PROMPT = """You are reading ONE Pakistani electricity bill image (K-Electric, LESCO or IESCO).
It may be tilted, a photo, partly in Urdu. Names/addresses/IDs are hidden by grey boxes.
Return the JSON object defined by the response schema. Rules:

GENERAL
- Copy values AS PRINTED. Never recalculate, round or fix amounts.
- Amounts are plain numbers in PKR: remove 'Rs.' and commas. 'Rs. 19,964' -> 19964.
- Credits are negative: '38209CR' or '54770CR' -> -38209 / -54770. Subsidies are negative.
- Dates: YYYY-MM-DD (22nd Sep 2026 / 22 SEP 26 -> 2026-09-22). bill_month: YYYY-MM (MAR 2026 -> 2026-03).
- If a value is not printed, blank, covered by a grey box or unreadable -> null. NEVER guess. Empty list -> [].
- NEVER output names, addresses, CNIC, account/reference/consumer/meter numbers.

FIELDS
- provider: KE for K-Electric (KESC), LESCO, IESCO.
- tariff: code exactly as printed (A1-R, A-1a(01), A-1b(03)T ...).
- sanctioned_load_kw: 'Sanc Load' / 'S.Load' / 'San Load'.
- due_date: last date to pay WITHOUT late surcharge.
- previous_reading / current_reading / units_consumed: as printed. If several meter registers/rows exist
  (net metering import/export), use the FIRST/main import row and say so in notes.
- charges: one entry per printed line in the charges section (energy cost, fixed, FPA/FCA, QTA, surcharge,
  meter rent, subsidy...). Skip blank and zero lines. Keep repeated types as separate entries.
  type mapping: energy = cost of units / variable / energy charges; fixed; fpa = FPA and FCA;
  quarterly_adjustment = QTA / uniform quarterly adjustment; surcharge = additional/PHL/F.C surcharge;
  meter_rent = meter or service rent; subsidy = subsidy/relief (negative); other = anything else.
  If the bill only shows summary lines (Total Electricity Charges, Subsidies, Net Electricity Charges,
  Taxes, Current Bill) and no itemised lines, return charges [] and still fill the totals.
- total_charges: printed charges subtotal (e.g. 'Electricity Charges', 'Net Electricity Charges'), else null.
- taxes: one entry per line in the tax/government section; classify by SECTION. gst = sales tax/GST incl. GST on FPA;
  electricity_duty = ED; income_tax; municipal_tax = local/MUCT(KMC); other_tax = TV fee, extra/further tax.
  If only one combined tax amount is printed -> taxes [] and put it in total_taxes.
- total_taxes: printed tax subtotal else null.
- current_bill: the printed amount of THIS month's bill.
- arrears: previous dues carried forward, keep sign, null if blank.
- payable_within_due_date: amount payable by due date (keep sign, e.g. net-metering credit -54770).
- payable_after_due_date: if several late amounts exist use the HIGHEST. If it says 'NOT TO BE PAID' use null.

EXTRA FACTS (for answering customer questions)
- is_net_metering: true only if the bill says net metering / solar or shows import/export registers; put the
  exact evidence words in net_metering_evidence. Otherwise false and say what indicates a standard connection.
- fpa_month: month the fuel adjustment refers to (e.g. 'FCA Feb-26' -> 2026-02), fpa_label = printed label, fpa_amount = the FPA/FCA amount printed anywhere (e.g. 'Total FPA 267', or the FCA line).
- late_surcharges: every late-payment surcharge / late payable amount shown, each with its label/date window.
- energy_share_percent / tax_share_percent: only if printed (e.g. 83.39 / 16.61, 84.76 / 15.24).
- slabs: for each energy/variable line that prints units and a rate (e.g. 'Variable Upto 100 Units (Protected)  100.00  10.54  1,054.00'),
  return label, upto_units (the N in 'Upto N Units' if printed), units, rate, amount, protected (true/false/null from the label).
  If the bill prints no slab/rate lines, return [].
- history: EVERY row of the usage / billing & payment history table (previous months only, not the current month),
  month as YYYY-MM (infer the year: 'Sep25' -> 2025-09), units, bill_amount, payment_amount (null if blank),
  status text if printed. For a 13-month usage bar chart with only units, fill units and leave amounts null.
  Keep negative numbers negative (net metering export). Merge a usage chart and a billing table by month.
- notes: briefly mention anything unreadable or ambiguous.
"""


def _month_norm(m):
    if not m: return None
    m = str(m).strip()
    return m if re.fullmatch(r"\d{4}-\d{2}", m) else None


def clean(facts: BillFacts) -> BillFacts:
    """Deterministic post-processing: drop zero lines, normalise months."""
    facts.charges = [c for c in facts.charges if c.amount != 0]
    facts.taxes = [t for t in facts.taxes if t.amount != 0]
    facts.bill_month = _month_norm(facts.bill_month)
    facts.fpa_month = _month_norm(facts.fpa_month)
    facts.history = [h for h in facts.history if _month_norm(h.month)]
    seen, uniq = set(), []
    for h in facts.history:                       # de-duplicate months, keep first occurrence
        if h.month not in seen:
            seen.add(h.month); uniq.append(h)
    facts.history = sorted(uniq, key=lambda h: h.month)
    return facts


def _month_index(m: str) -> int:
    y, mo = m.split("-")
    return int(y) * 12 + int(mo)


def validate(facts: BillFacts) -> list[str]:
    """Sanity checks. They only WARN: Level 1 values stay exactly as printed."""
    w = []
    f = facts
    if f.previous_reading is not None and f.current_reading is not None and f.units_consumed is not None:
        diff = f.current_reading - f.previous_reading
        if abs(diff - f.units_consumed) > 1:
            w.append(f"Readings differ by {diff:g} but units printed are {f.units_consumed:g} (multi-register or multiplier bill?)")
    if f.charges and f.total_charges is not None:
        s = sum(c.amount for c in f.charges)
        if abs(s - f.total_charges) > 2:
            w.append(f"Charge lines add up to {s:,.2f} but printed charges subtotal is {f.total_charges:,.2f}")
    if f.taxes and f.total_taxes is not None:
        s = sum(t.amount for t in f.taxes)
        if abs(s - f.total_taxes) > 2:
            w.append(f"Tax lines add up to {s:,.2f} but printed tax subtotal is {f.total_taxes:,.2f}")
    if f.total_charges is not None and f.total_taxes is not None and f.current_bill is not None:
        if abs(f.total_charges + f.total_taxes - f.current_bill) > 5:
            w.append("Charges + taxes do not match current bill (other items may be included)")
    for k in ("bill_month", "due_date", "current_bill", "payable_within_due_date"):
        if getattr(f, k) is None:
            w.append(f"{k} could not be read (null)")
    ds = [(k, getattr(f, k)) for k in ("reading_date", "issue_date", "due_date")]
    if all(v for _, v in ds) and not (f.reading_date <= f.issue_date <= f.due_date):
        w.append("Dates are out of order (expected reading <= issue <= due date)")
    if f.bill_month and f.issue_date and abs(_month_index(f.issue_date[:7]) - _month_index(f.bill_month)) > 2:
        w.append("Issue date is not plausible for the bill month")
    if f.payable_after_due_date is not None and f.payable_within_due_date is not None \
            and f.payable_within_due_date > 0 and f.payable_after_due_date < f.payable_within_due_date:
        w.append("Amount after due date is lower than amount within due date (not plausible)")
    if f.units_consumed is not None and f.units_consumed < 0 and not f.is_net_metering:
        w.append("Negative units on a bill not flagged as net metering")
    if not f.history:
        w.append("No billing history table could be read; history-based answers will be limited")
    return w


REVIEW_PROMPT = """You previously extracted the JSON below from this same bill image, but automatic checks found problems.
Re-read the image carefully and return the CORRECTED full JSON (same schema, same rules as before: values as printed,
null if unreadable, never guess). Only change a value if the image clearly shows a different value.

CHECK FAILURES:
{warnings}

PREVIOUS JSON:
{previous}
"""

# Warnings worth a second look by the model (missing fields are only retried if the bill probably prints them)
_RETRY_MARKERS = ("add up to", "do not match", "Readings differ", "could not be read", "out of order", "not plausible")


def _needs_review(warnings: list[str]) -> bool:
    return any(any(m in w for m in _RETRY_MARKERS) for w in warnings)


def extract_facts(image_path_or_bytes, review: bool = True) -> BillFacts:
    """Extract -> validate -> (if checks fail) one self-review pass; keep the better of the two results."""
    img = llm.prepare_image(image_path_or_bytes)
    facts = clean(llm.generate_structured(EXTRACT_PROMPT, BillFacts, img))
    warnings = validate(facts)
    if review and _needs_review(warnings):
        try:
            prompt = REVIEW_PROMPT.format(warnings="\n".join("- " + w for w in warnings),
                                          previous=facts.model_dump_json())
            second = clean(llm.generate_structured(prompt, BillFacts, img))
            if len(validate(second)) < len(warnings):          # accept only if it strictly improves
                facts = second
        except Exception:
            pass                                                # keep the first extraction
    return facts


def to_level1(facts: BillFacts) -> dict:
    d = facts.model_dump()
    return {k: d[k] for k in LEVEL1_KEYS}
