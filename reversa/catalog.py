"""Simulated travel supplier. All money is integer paise. Swap this file for a live GDS / hotel API.

A cancellation curve is a list of (min_hours_before_departure, fee_in_basis_points),
read top-down: the first row whose threshold is <= hours-to-departure applies.
`curve=None` means the supplier did not publish a cancellation policy, so the
cancellation fee is UNKNOWN and the safety engine will refuse to commit it.
"""
from __future__ import annotations

R = 100                    # paise per rupee
DEPARTS_IN_HOURS = 240     # every demo trip departs 10 days from "now"

VIBES = {"beach": "goa", "sea": "goa", "coast": "goa", "goa": "goa",
         "hills": "manali", "hill": "manali", "mountains": "manali", "manali": "manali"}
DEST_NAMES = {"goa": "Goa", "manali": "Manali"}

# Prices are for one traveller (return flight) or the whole stay (hotel, 3 nights).
CATALOG = [
    # ---- Goa flights -----------------------------------------------------------------
    dict(id="fl_goa_flexi", cat="flight", dest="goa", supplier="Coastline Airways", name="Coastline Airways Flexi (return)",
         price=14000 * R, curve=[(72, 1000), (24, 2500), (0, 10000)]),          # 10% -> 25% -> 100%
    dict(id="fl_goa_std", cat="flight", dest="goa", supplier="Konkan Jet", name="Konkan Jet Standard (return)",
         price=12500 * R, curve=[(168, 2500), (72, 5000), (0, 10000)]),          # 25% -> 50% -> 100%
    dict(id="fl_goa_saver", cat="flight", dest="goa", supplier="Malabar Air", name="Malabar Air Saver (return)",
         price=10900 * R, curve=[(0, 8500)]),                                    # 85% always (taxes back only)
    dict(id="fl_goa_charter", cat="flight", dest="goa", supplier="Sky Charter Co.", name="Sky Charter (return)",
         price=11800 * R, curve=None),                                           # policy not published
    # ---- Goa hotels (3 nights) ---------------------------------------------------------
    dict(id="ho_goa_banyan", cat="hotel", dest="goa", supplier="Banyan Stays", name="Banyan Stays Candolim, 3 nights",
         price=5400 * R, curve=[(48, 0), (0, 10000)]),                           # free until 48h
    dict(id="ho_goa_shack", cat="hotel", dest="goa", supplier="Sunset Shacks", name="Sunset Beach Shack, 3 nights",
         price=3900 * R, curve=[(72, 3000), (0, 10000)]),
    dict(id="ho_goa_palm", cat="hotel", dest="goa", supplier="Palm Grove Resorts", name="Palm Grove Resort, 3 nights",
         price=8700 * R, curve=[(72, 1000), (0, 10000)]),
    # ---- Manali (so the parser has a second destination) ----------------------------------
    dict(id="fl_man_std", cat="flight", dest="manali", supplier="Himal Air", name="Himal Air to Kullu (return)",
         price=11500 * R, curve=[(72, 1000), (0, 10000)]),
    dict(id="ho_man_pine", cat="hotel", dest="manali", supplier="Pine Lodge", name="Pine Lodge, 3 nights",
         price=6000 * R, curve=[(48, 0), (0, 10000)]),
]
BY_ID = {c["id"]: c for c in CATALOG}


def options(dest: str) -> list[dict]:
    return [dict(c) for c in CATALOG if c["dest"] == dest]


def fee_bps(curve: list | None, hours_to_departure: float) -> int | None:
    if curve is None:
        return None
    for min_hours, bps in curve:
        if hours_to_departure >= min_hours:
            return bps
    return 10000


def cancel_fee(item: dict, hours_to_departure: float = DEPARTS_IN_HOURS) -> int | None:
    """Integer paise lost if this booking is cancelled `hours_to_departure` before departure.
    None when the supplier has not published a policy (the fee is unknown)."""
    bps = fee_bps(item.get("curve"), hours_to_departure)
    return None if bps is None else item["price"] * bps // 10000


def exposure(item: dict, hours_to_departure: float = DEPARTS_IN_HOURS) -> dict:
    """Cancellation exposure for one booking: what you lose and what comes back."""
    fee = cancel_fee(item, hours_to_departure)
    return {"hours_to_departure": hours_to_departure,
            "fee": fee,
            "refundable": None if fee is None else item["price"] - fee,
            "fee_pct": None if fee is None else fee_bps(item["curve"], hours_to_departure) / 100,
            "known": fee is not None}
