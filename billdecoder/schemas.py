"""Pydantic schemas. BillFacts is what the vision model returns; Level1 is the graded JSON."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

ChargeType = Literal["energy", "fixed", "fpa", "quarterly_adjustment", "surcharge",
                     "meter_rent", "subsidy", "other"]
TaxType = Literal["gst", "electricity_duty", "income_tax", "municipal_tax", "other_tax"]


class ChargeLine(BaseModel):
    type: ChargeType
    amount: float


class TaxLine(BaseModel):
    type: TaxType
    amount: float


class HistoryRow(BaseModel):
    month: str = Field(description="YYYY-MM of the history row")
    units: Optional[float] = None
    bill_amount: Optional[float] = None
    payment_amount: Optional[float] = None
    status: Optional[str] = Field(None, description="status text printed in the row, if any")


class SlabLine(BaseModel):
    label: str = Field(description="printed label, e.g. 'Variable Upto 100 Units (Protected)'")
    upto_units: Optional[float] = Field(None, description="N from 'Upto N Units' if printed, else null")
    units: Optional[float] = Field(None, description="units billed in this slab line")
    rate: Optional[float] = Field(None, description="printed rate per unit")
    amount: Optional[float] = None
    protected: Optional[bool] = Field(None, description="true if label says Protected, false if Non-Protected/Unprotected, else null")


class LateSurcharge(BaseModel):
    label: str = Field(description="e.g. 'Till 22-Apr', 'After 24-Apr', 'LP surcharge'")
    amount: Optional[float] = None
    payable_total: Optional[float] = Field(None, description="total payable in that window")


class BillFacts(BaseModel):
    # ---- Level 1 fields (as printed) ----
    provider: Optional[Literal["KE", "LESCO", "IESCO"]] = None
    tariff: Optional[str] = None
    sanctioned_load_kw: Optional[float] = None
    bill_month: Optional[str] = None
    reading_date: Optional[str] = None
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    previous_reading: Optional[float] = None
    current_reading: Optional[float] = None
    units_consumed: Optional[float] = None
    charges: list[ChargeLine] = []
    total_charges: Optional[float] = None
    taxes: list[TaxLine] = []
    total_taxes: Optional[float] = None
    current_bill: Optional[float] = None
    arrears: Optional[float] = None
    payable_within_due_date: Optional[float] = None
    payable_after_due_date: Optional[float] = None
    # ---- Extra facts needed for Level 2 ----
    is_net_metering: bool = Field(False, description="true if bill says net metering / solar or has import-export registers")
    net_metering_evidence: Optional[str] = Field(None, description="exact words/clues on the bill")
    fpa_month: Optional[str] = Field(None, description="month the fuel adjustment refers to, YYYY-MM, if printed")
    fpa_amount: Optional[float] = Field(None, description="FPA/FCA amount printed anywhere on the bill, e.g. 'Total FPA 267'")
    fpa_label: Optional[str] = Field(None, description="printed label, e.g. 'FCA Feb-26'")
    late_surcharges: list[LateSurcharge] = []
    energy_share_percent: Optional[float] = Field(None, description="printed % split for energy charges, if any")
    tax_share_percent: Optional[float] = Field(None, description="printed % split for taxes, if any")
    slabs: list[SlabLine] = Field([], description="energy lines showing units and rate (slab info); [] if the bill prints none")
    history: list[HistoryRow] = Field([], description="every row of the usage/billing/payment history, previous months only")
    notes: Optional[str] = Field(None, description="anything unreadable or ambiguous")


LEVEL1_KEYS = [
    "provider", "tariff", "sanctioned_load_kw", "bill_month", "reading_date", "issue_date",
    "due_date", "previous_reading", "current_reading", "units_consumed", "charges",
    "total_charges", "taxes", "total_taxes", "current_bill", "arrears",
    "payable_within_due_date", "payable_after_due_date",
]
