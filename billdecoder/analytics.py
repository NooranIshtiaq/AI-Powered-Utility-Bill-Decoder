"""Deterministic calculations for Level 2. The LLM never does arithmetic: it only phrases these results."""
from __future__ import annotations
from datetime import date
import numpy as np
from .schemas import BillFacts

def mean(xs) -> float:
    return float(np.mean(list(xs)))


def slope(values) -> float:
    """Least-squares units/month trend via numpy."""
    v = list(values)
    return float(np.polyfit(np.arange(len(v)), v, 1)[0]) if len(v) >= 2 else 0.0


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def label(m: str) -> str:
    y, mo = m.split("-")
    return f"{MONTHS[int(mo) - 1]} {y}"


def money(x) -> str:
    return "n/a" if x is None else (f"PKR {x:,.2f}".replace(".00", "") if x >= 0 else f"-PKR {abs(x):,.2f}".replace(".00", ""))


def num(x) -> str:
    return "n/a" if x is None else f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def shift(m: str, k: int) -> str:
    y, mo = map(int, m.split("-"))
    t = y * 12 + (mo - 1) + k
    return f"{t // 12}-{t % 12 + 1:02d}"


def unit_series(f: BillFacts) -> list[tuple[str, float, bool]]:
    """(month, units, is_current) sorted; history = previous months, plus the bill's own month."""
    rows = {h.month: (h.units, False) for h in f.history if h.units is not None}
    if f.bill_month and f.units_consumed is not None and f.bill_month not in rows:
        rows[f.bill_month] = (f.units_consumed, True)
    return [(m, u, cur) for m, (u, cur) in sorted(rows.items())]


def compute(f: BillFacts) -> dict:
    out: dict = {}
    series = unit_series(f)
    hist = [(m, u) for m, u, cur in series if not cur]
    hist12 = hist[-12:]
    cur_units = f.units_consumed

    # Q1 net metering
    if f.is_net_metering:
        out["Q1"] = (f"This is a net-metered (solar) connection. Clue on the bill: {f.net_metering_evidence or 'net metering wording / export register'}.")
    else:
        out["Q1"] = ("This looks like a standard connection: the bill shows no net-metering wording and no separate export register. "
                     + (f"({f.net_metering_evidence})" if f.net_metering_evidence else ""))

    # Q2 highest month
    if hist12:
        m, u = max(hist12, key=lambda x: x[1])
        s = f"{label(m)} had the highest units in the 12 months of history: {num(u)} units."
        if cur_units is not None and cur_units > u:
            s += f" The current month ({num(cur_units)} units) is even higher."
        out["Q2"] = s
    else:
        out["Q2"] = "The bill does not show a readable usage history, so the highest month cannot be determined."

    # Q3 FPA
    fpa_lines = [c.amount for c in f.charges if c.type == "fpa"]
    fpa_total = sum(fpa_lines) if fpa_lines else f.fpa_amount
    if fpa_total is not None:
        mo = f" for {label(f.fpa_month)}" if f.fpa_month else ""
        lab = f" (printed as '{f.fpa_label}')" if f.fpa_label else ""
        out["Q3"] = f"You are charged {money(fpa_total)} as fuel adjustment{mo}{lab}." + ("" if f.fpa_month else " The bill does not state which month it applies to.")
    else:
        out["Q3"] = "No fuel adjustment (FPA/FCA) amount is printed on this bill."

    # Q4 late surcharge
    ls = [x for x in f.late_surcharges if x.amount is not None or x.payable_total is not None]
    if ls:
        parts = []
        for x in ls:
            seg = x.label
            if x.amount is not None: seg += f": surcharge {money(x.amount)}"
            if x.payable_total is not None: seg += f", total payable {money(x.payable_total)}"
            parts.append(seg)
        out["Q4"] = "Late payment charges shown: " + "; ".join(parts) + "."
    elif f.payable_after_due_date is not None and f.payable_within_due_date is not None:
        d = f.payable_after_due_date - f.payable_within_due_date
        out["Q4"] = f"After the due date you pay {money(f.payable_after_due_date)} instead of {money(f.payable_within_due_date)}, i.e. {money(d)} extra."
    else:
        out["Q4"] = "The bill does not show a late payment surcharge (or states the amount is not payable)."

    # Q5 same month last year
    if f.bill_month and cur_units is not None:
        ly = shift(f.bill_month, -12)
        d = dict(hist)
        if ly in d:
            diff = cur_units - d[ly]
            word = "more" if diff > 0 else "fewer" if diff < 0 else "the same number of"
            out["Q5"] = (f"{label(f.bill_month)} used {num(cur_units)} units vs {num(d[ly])} in {label(ly)}: "
                         f"{'exactly the same' if diff == 0 else f'{num(abs(diff))} {word} units'}"
                         + (f" ({diff / d[ly] * 100:+.1f}%)." if d[ly] else "."))
        else:
            out["Q5"] = f"The history on this bill does not include {label(ly)}, so a year-on-year comparison is not possible."
    else:
        out["Q5"] = "Current units or bill month could not be read, so the comparison is not possible."

    # Q6 average
    if hist12:
        avg = mean(u for _, u in hist12)
        s = f"Average over the {len(hist12)} previous months on the bill: {avg:,.1f} units/month."
        if cur_units is not None:
            diff = cur_units - avg
            s += f" This month ({num(cur_units)} units) is {abs(diff):,.1f} units {'above' if diff > 0 else 'below'} that average ({diff / avg * 100:+.1f}%)." if avg else ""
        out["Q6"] = s
        out["_avg_units"] = avg
    else:
        out["Q6"] = "No usage history is readable on this bill, so an average cannot be calculated."

    # Q7 summer vs winter (all months available incl. current)
    summer = [(m, u) for m, u, _ in series if int(m[5:]) in (6, 7, 8)]
    winter = [(m, u) for m, u, _ in series if int(m[5:]) in (12, 1, 2)]
    if summer and winter:
        st, wt = sum(u for _, u in summer), sum(u for _, u in winter)
        sa, wa = st / len(summer), wt / len(winter)
        out["Q7"] = (f"Summer (Jun-Aug, {len(summer)} month(s) available) totals {num(st)} units, avg {sa:,.1f}/month. "
                     f"Winter (Dec-Feb, {len(winter)} month(s) available) totals {num(wt)} units, avg {wa:,.1f}/month. "
                     f"Per month, summer is {sa - wa:+,.1f} units vs winter; total difference {st - wt:+,.0f} units"
                     + ("" if len(summer) == len(winter) == 3 else " (not all 3 months of a season are on the bill, so compare averages)") + ".")
    else:
        out["Q7"] = "The bill history does not cover both summer (Jun-Aug) and winter (Dec-Feb) months, so this cannot be calculated."

    # Q8 payments
    rows = [h for h in f.history if h.bill_amount is not None]
    if rows:
        unpaid, partial, unknown = [], [], []
        for h in rows:
            if h.payment_amount is None: unknown.append(h.month)
            elif h.bill_amount > 0 and h.payment_amount <= 0: unpaid.append(h.month)
            elif h.bill_amount > 0 and h.payment_amount < h.bill_amount - 1: partial.append(h.month)
        if not unpaid and not partial and not unknown:
            out["Q8"] = f"Yes. For all {len(rows)} months shown, the payment recorded matches the bill amount."
        else:
            bits = []
            if unpaid: bits.append("no payment recorded for " + ", ".join(label(m) for m in unpaid))
            if partial: bits.append("payment lower than the bill for " + ", ".join(label(m) for m in partial))
            if unknown: bits.append("payment column blank for " + ", ".join(label(m) for m in unknown))
            out["Q8"] = "Not every bill shows as paid in full: " + "; ".join(bits) + f" (out of {len(rows)} months)."
    else:
        out["Q8"] = "The bill has no readable payment history table, so payments cannot be checked."

    # Q9 last 3 months
    last3 = series[-3:]
    if len(last3) == 3:
        u = [x[1] for x in last3]
        trail = " -> ".join(f"{label(m)}: {num(v)}" for m, v, _ in last3)
        if u[0] < u[1] < u[2]: d = "gone up steadily"
        elif u[0] > u[1] > u[2]: d = "gone down steadily"
        else: d = f"been mixed overall ({'up' if u[2] > u[0] else 'down' if u[2] < u[0] else 'flat'} from {label(last3[0][0])} to {label(last3[2][0])})"
        out["Q9"] = f"Over the last 3 months your units have {d}: {trail}. Linear trend: {slope(u):+.1f} units per month."
    else:
        out["Q9"] = "Fewer than 3 months of units are readable, so a 3-month trend cannot be given."

    # Q10 forecast (estimate)
    if f.bill_month and len(series) >= 3:
        nxt = shift(max(m for m, _, _ in series), 1)
        d = {m: u for m, u, _ in series}
        r3 = [u for _, u, _ in series[-3:]]
        avg_all = mean(u for _, u, _ in series[-12:])
        ly_next, ly_base = d.get(shift(nxt, -12)), [d.get(shift(nxt, -k)) for k in (13, 14, 15)]
        if ly_next is not None and all(v is not None for v in ly_base) and mean(ly_base) > 0:
            est = ly_next * (mean(r3) / mean(ly_base))
            how = (f"seasonal estimate: {label(shift(nxt, -12))} used {num(ly_next)} units, scaled by how recent months compare "
                   f"with the same months a year earlier ({mean(r3):,.0f} vs {mean(ly_base):,.0f} avg)")
        else:
            est = 0.5 * mean(r3) + 0.5 * avg_all
            how = f"blend of the last-3-month average ({mean(r3):,.0f}) and the {len(series[-12:])}-month average ({avg_all:,.0f})"
        out["Q10"] = f"Estimate for {label(nxt)}: about {est:,.0f} units ({how}). Weather and usage changes can move the real figure."
        out["_forecast"] = est
    else:
        out["Q10"] = "Not enough monthly history is readable to estimate next month's units."

    # Q11 spread evenly
    amts = [h.bill_amount for h in f.history if h.bill_amount is not None]
    if amts:
        a = mean(amts)
        out["Q11"] = (f"Average bill across the {len(amts)} months shown is about {money(round(a))} per month "
                      f"(total {money(sum(amts))} / {len(amts)}). Setting aside roughly that much monthly would cover a typical year (estimate based on past bills only).")
        out["_avg_bill"] = a
    elif f.current_bill is not None:
        out["Q11"] = f"No bill history is readable; using only this month's bill, about {money(f.current_bill)} per month (rough estimate)."
    else:
        out["Q11"] = "No billing amounts are readable, so a monthly set-aside cannot be estimated."

    # Q12 same units as last month
    if f.current_bill is not None:
        s = (f"Roughly {money(f.current_bill)}: this bill already covers {num(cur_units)} units for {label(f.bill_month) if f.bill_month else 'this month'}, "
             f"so repeating that usage at the same rates would give a similar bill (estimate; fuel adjustments, taxes and arrears can change it).")
        if hist and any(h.bill_amount is not None for h in f.history):
            pm = [h for h in f.history if h.bill_amount is not None][-1]
            s += f" For comparison, {label(pm.month)} used {num(pm.units)} units and was billed {money(pm.bill_amount)}."
        out["Q12"] = s
    else:
        out["Q12"] = "The current bill amount could not be read, so a bill estimate is not possible."
    return out
