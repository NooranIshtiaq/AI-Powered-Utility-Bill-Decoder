"""Deterministic product features: slab alert, what-if calculator, savings tips. All math via numpy, no LLM."""
from __future__ import annotations
import numpy as np
from .schemas import BillFacts
from . import analytics as an


# ---------------------------------------------------------------- cost model
def cost_model(f: BillFacts) -> dict | None:
    """Effective-rate model from THIS bill: fixed part stays, the rest scales with units."""
    u, bill = f.units_consumed, f.current_bill
    if f.is_net_metering or not u or u <= 0 or bill is None or bill <= 0:
        return None
    fixed = float(sum(c.amount for c in f.charges if c.type in ("fixed", "meter_rent") and c.amount > 0))
    if fixed >= bill:
        fixed = 0.0
    return {"fixed": round(fixed, 2), "rate_per_unit": float((bill - fixed) / u), "units": float(u), "bill": float(bill)}


def estimate(model: dict, units) -> np.ndarray:
    return model["fixed"] + model["rate_per_unit"] * np.maximum(np.asarray(units, dtype=float), 0)


def _thresholds(f: BillFacts) -> list[float]:
    return sorted({float(s.upto_units) for s in f.slabs if s.upto_units})


def _is_protected(s) -> bool:
    if s.protected is not None:
        return bool(s.protected)
    low = s.label.lower()
    return "protected" in low and not any(x in low for x in ("non", "un-", "unprot"))


def what_if(f: BillFacts, units: float) -> dict:
    m = cost_model(f)
    if not m:
        return {"available": False, "reason": "A what-if estimate needs a standard (non net-metered) bill with units and a bill amount."}
    est = float(estimate(m, units))
    lo, hi = sorted((m["units"], float(units)))
    crossed = [t for t in _thresholds(f) if lo < t <= hi]
    note = None
    if crossed:
        note = (f"This crosses the {', '.join(f'{t:g}' for t in crossed)}-unit slab limit printed on your bill. "
                "A higher slab can raise the rate per unit, so the real bill may rise faster than this estimate.")
    return {"available": True, "units": float(units), "estimated_bill": round(est, 2),
            "delta": round(est - m["bill"], 2), "delta_pct": round((est - m["bill"]) / m["bill"] * 100, 1),
            "slab_note": note,
            "method": "Estimate: fixed charges stay the same; the rest of this bill is scaled by units (effective rate from this bill)."}


def curve(f: BillFacts, points: int = 41) -> list[dict]:
    m = cost_model(f)
    if not m:
        return []
    xs = np.linspace(0, max(2 * m["units"], 100), points)
    return [{"units": round(float(x), 1), "bill": round(float(y), 2)} for x, y in zip(xs, estimate(m, xs))]


# ---------------------------------------------------------------- slab alert
def slab_alert(f: BillFacts, forecast: float | None) -> dict:
    th = _thresholds(f)
    if not th:
        return {"available": False, "level": "none",
                "message": "This bill does not print slab or protected-status information, so no slab alert can be given."}
    protected = sorted({float(s.upto_units) for s in f.slabs if s.upto_units and _is_protected(s)})
    cur = f.units_consumed
    if cur is None:
        return {"available": False, "level": "none", "message": "Units for this bill could not be read, so no slab alert can be given."}
    tag = " (labelled protected on your bill)" if protected else ""
    above = [t for t in th if t >= cur]
    if not above:
        return {"available": True, "level": "info", "thresholds": th, "protected_limits": protected,
                "message": f"Your {cur:g} units are above the highest slab limit printed on this bill ({max(th):g} units)."}
    t = above[0]
    margin = t - cur
    is_prot = t in protected
    tag = " (labelled protected on your bill)" if is_prot else ""
    if forecast is not None and forecast > t:
        level = "warning"
        msg = (f"Heads up: you used {cur:g} units, {margin:g} under the {t:g}-unit slab limit{tag}. Your trend suggests about "
               f"{forecast:.0f} units next month (estimate), which could cross it. Check with your provider what changes above {t:g} units.")
    elif margin <= 0.15 * t:
        level = "watch"
        msg = f"You are only {margin:g} units below the {t:g}-unit slab limit{tag} printed on your bill. Small increases could cross it."
    else:
        level = "ok"
        msg = f"You are {margin:g} units below the {t:g}-unit slab limit{tag} printed on your bill, with a comfortable margin."
    past = [p for p in protected if cur > p]
    if past:
        msg += f" You are already above the protected limit of {max(past):g} units."
    return {"available": True, "level": level, "thresholds": th, "protected_limits": protected, "margin": margin, "message": msg}


# ---------------------------------------------------------------- tips
def tips(f: BillFacts, computed: dict, alert: dict) -> list[dict]:
    out, m = [], cost_model(f)
    u = f.units_consumed
    series = an.unit_series(f)
    hist = [x for _, x, cur in series if not cur][-12:]
    if m:
        s = float(estimate(m, u) - estimate(m, 0.9 * u))
        out.append({"id": "cut10", "title": "A 10% cut adds up",
                    "text": f"Using 10% fewer units (about {0.1 * u:.0f} units) would lower a bill like this by roughly PKR {s:,.0f} (estimate: fixed charges stay, the rest scales with units).",
                    "saving_pkr": round(s)})
    if hist and u:
        avg = float(np.mean(hist))
        if avg > 0 and u > 1.15 * avg:
            txt = f"This month's {u:g} units are {(u / avg - 1) * 100:.0f}% above your {len(hist)}-month average of {avg:.0f}."
            sv = None
            if m:
                sv = float(estimate(m, u) - estimate(m, avg))
                txt += f" Getting back to the average would save roughly PKR {sv:,.0f} (estimate)."
            out.append({"id": "above_avg", "title": "Higher than your usual", "text": txt, "saving_pkr": round(sv) if sv else None})
    summer = [x for mo, x, _ in series if int(mo[5:]) in (6, 7, 8)]
    winter = [x for mo, x, _ in series if int(mo[5:]) in (12, 1, 2)]
    if summer and winter and np.mean(winter) > 0 and np.mean(summer) > 1.25 * np.mean(winter):
        out.append({"id": "season", "title": "Summer is your peak",
                    "text": f"Your summer months average {np.mean(summer):.0f} units vs {np.mean(winter):.0f} in winter ({(np.mean(summer) / np.mean(winter) - 1) * 100:.0f}% more). Summer is where cutting usage matters most.",
                    "saving_pkr": None})
    if f.payable_after_due_date is not None and f.payable_within_due_date is not None and f.payable_after_due_date > f.payable_within_due_date:
        d = f.payable_after_due_date - f.payable_within_due_date
        when = f" by {f.due_date}" if f.due_date else ""
        out.append({"id": "pay_on_time", "title": "Pay on time",
                    "text": f"Paying{when} avoids about PKR {d:,.0f} in late charges.", "saving_pkr": round(d)})
    if alert.get("level") in ("watch", "warning"):
        out.append({"id": "slab", "title": "Stay under the slab limit", "text": alert["message"], "saving_pkr": None})
    if f.total_taxes and f.current_bill and f.current_bill > 0:
        share = f.total_taxes / f.current_bill * 100
        out.append({"id": "tax", "title": "Where taxes fit", "text": f"Taxes and duties are PKR {f.total_taxes:,.0f}, about {share:.1f}% of this bill. Your units are the main thing you control.", "saving_pkr": None})
    return out


# ---------------------------------------------------------------- numbers the LLM may quote
def derived(f: BillFacts) -> dict:
    d: dict = {}
    if f.total_taxes is not None and f.current_bill:
        d["tax_share_percent"] = round(f.total_taxes / f.current_bill * 100, 2)
        d["non_tax_share_percent"] = round(100 - d["tax_share_percent"], 2)
    if f.payable_after_due_date is not None and f.payable_within_due_date is not None:
        d["late_payment_extra"] = round(f.payable_after_due_date - f.payable_within_due_date, 2)
    m = cost_model(f)
    if m:
        d["effective_rate_per_unit"] = round(m["rate_per_unit"], 2)
        d["fixed_charges_in_estimate"] = m["fixed"]
        d["what_if_units_change"] = {f"{p:+d}%": {"units": round(m["units"] * (1 + p / 100), 1),
                                                   "estimated_bill": round(float(estimate(m, m["units"] * (1 + p / 100))), 2)}
                                     for p in (-30, -20, -10, 10, 20, 30)}
    return d


def build(f: BillFacts, computed: dict) -> dict:
    alert = slab_alert(f, computed.get("_forecast"))
    return {"slab_alert": alert, "cost_model": cost_model(f), "curve": curve(f),
            "tips": tips(f, computed, alert), "derived": derived(f)}
