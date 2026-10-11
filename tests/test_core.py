import pytest
from billdecoder.schemas import BillFacts, ChargeLine, TaxLine, HistoryRow, SlabLine
from billdecoder import analytics, insights, validation, extract


def hist(units, start=4):
    return [HistoryRow(month=f"2025-{m:02d}", units=u, bill_amount=2000, payment_amount=2000) for m, u in zip(range(start, start + len(units)), units)]


@pytest.fixture
def facts():
    return BillFacts(provider="KE", bill_month="2026-04", units_consumed=151, current_bill=3430.24, total_charges=2860.62,
                     total_taxes=569.62, payable_within_due_date=3430, payable_after_due_date=3716, due_date="2026-04-21",
                     charges=[ChargeLine(type="fixed", amount=900), ChargeLine(type="energy", amount=1054)],
                     taxes=[TaxLine(type="gst", amount=520.21)],
                     slabs=[SlabLine(label="Variable Upto 100 Units (Protected)", upto_units=100, protected=True),
                            SlabLine(label="Variable Upto 200 Units (Protected)", upto_units=200, protected=True)],
                     history=hist([141, 138, 197, 239, 203, 179, 173, 187, 124]))


def test_average_and_highest(facts):
    c = analytics.compute(facts)
    assert "239" in c["Q2"] and "Jul 2025" in c["Q2"]
    assert abs(c["_avg_units"] - 175.67) < 0.01


def test_same_month_last_year(facts):
    assert "10 more units" in analytics.compute(facts)["Q5"]          # Apr 2026 (151) vs Apr 2025 (141)
    facts.bill_month = "2026-03"                                        # Mar 2025 is not in the history
    assert "does not include" in analytics.compute(facts)["Q5"]


def test_payment_check_flags_unpaid(facts):
    facts.history[2].payment_amount = 0
    assert "Jun 2025" in analytics.compute(facts)["Q8"]


def test_cost_model_and_whatif(facts):
    m = insights.cost_model(facts)
    assert m["fixed"] == 900 and abs(m["rate_per_unit"] * 151 + 900 - 3430.24) < 1e-6
    w = insights.what_if(facts, 220)
    assert w["estimated_bill"] > 3430 and "slab" in w["slab_note"]
    assert insights.what_if(facts, 120)["slab_note"] is None or "100" in insights.what_if(facts, 120)["slab_note"]


def test_whatif_unavailable_for_net_metering(facts):
    facts.is_net_metering = True
    assert insights.what_if(facts, 100)["available"] is False


def test_slab_alert_levels(facts):
    assert insights.slab_alert(facts, None)["level"] == "ok"
    assert insights.slab_alert(facts, 230)["level"] == "warning"
    facts.units_consumed = 190
    assert insights.slab_alert(facts, None)["level"] == "watch"
    facts.slabs = []
    assert insights.slab_alert(facts, 500)["available"] is False


def test_number_validation():
    allowed = validation.numbers_in("Bill PKR 3,430.24, taxes 569.62, 16.61 percent, 151 units, 2026")
    assert not validation.unsupported("Taxes are PKR 569.62, about 16.6% of 3,430.", allowed)
    assert validation.unsupported("You will pay PKR 9,999.", allowed) == [9999.0]
    assert not validation.unsupported("In 12 months, on 21 April 2026.", allowed)     # calendar numbers are free


def test_translation_keeps_numbers():
    assert validation.translation_ok("Pay PKR 3,430 by 21 Apr.", "Aap ko 21 Apr tak PKR 3,430 ada karna hai.")
    assert not validation.translation_ok("Pay PKR 3,430 by 21 Apr.", "Aap ko PKR 3,340 ada karna hai.")
    assert validation.translation_ok("Pay PKR 3,430.", "ادائیگی PKR ۳۴۳۰ کریں")  # Urdu digits normalised -> 3430


def test_extract_validation_warnings(facts):
    facts.total_charges = 5000
    facts.reading_date, facts.issue_date = "2026-04-10", "2026-04-03"
    w = " ".join(extract.validate(facts))
    assert "charges subtotal" in w and "out of order" in w
