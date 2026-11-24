"""Simulated suppliers. All money is integer paise. Swap this file for live GDS / hotel APIs.

A cancellation curve is a list of (min_hours_before_departure, fee_in_basis_points),
read top-down: the first row whose threshold is <= hours-to-departure applies.
This is E(t): what it costs to undo a booking, as a function of time.
"""
R = 100  # paise per rupee

FLIGHT_CURVE = [(72, 1000), (24, 5000), (0, 10000)]       # 10% -> 50% -> locked
HOTEL_CURVE = [(72, 1000), (24, 3000), (0, 10000)]        # 10% -> 30% -> locked
FLEX_ACTIVITY = [(48, 0), (0, 10000)]                     # free cancel until 48h
PAID_ACTIVITY = [(48, 1000), (0, 10000)]                  # 10% -> locked

VIBES = {"beach": "goa", "sea": "goa", "coast": "goa", "goa": "goa",
         "hills": "manali", "hill": "manali", "mountains": "manali", "mountain": "manali",
         "snow": "manali", "manali": "manali"}
DEST_NAMES = {"goa": "Goa", "manali": "Manali"}

# unit: "pp" = per person, "night" = per room-night, "pp_once" = per person, once
CATALOG = [
    # --- Goa -------------------------------------------------------------
    dict(id="fl_goa_malabar", cat="flight", dest="goa", name="Malabar Air return", unit="pp", price=6000 * R, score=75, curve=FLIGHT_CURVE),
    dict(id="fl_goa_premium", cat="flight", dest="goa", name="Coastline Airways flexi return", unit="pp", price=8000 * R, score=85, curve=FLIGHT_CURVE),
    dict(id="ho_goa_banyan", cat="hotel", dest="goa", name="Banyan Stays Candolim", unit="night", price=4000 * R, score=80, curve=HOTEL_CURVE, chain="banyan"),
    dict(id="ho_goa_shack", cat="hotel", dest="goa", name="Sunset Beach Shack", unit="night", price=2500 * R, score=55, curve=HOTEL_CURVE),
    dict(id="ac_goa_water", cat="activity", dest="goa", name="Water sports combo", unit="pp", price=900 * R, score=70, curve=FLEX_ACTIVITY),
    dict(id="ac_goa_scuba", cat="activity", dest="goa", name="Scuba dive", unit="pp", price=3000 * R, score=85, curve=PAID_ACTIVITY),
    # --- Manali ----------------------------------------------------------
    dict(id="fl_man_himal", cat="flight", dest="manali", name="Himal Air return (Kullu)", unit="pp", price=5500 * R, score=70, curve=FLIGHT_CURVE),
    dict(id="fl_man_premium", cat="flight", dest="manali", name="Summit Airways flexi return", unit="pp", price=7500 * R, score=85, curve=FLIGHT_CURVE),
    dict(id="ho_man_banyan", cat="hotel", dest="manali", name="Banyan Stays Old Manali", unit="night", price=4000 * R, score=80, curve=HOTEL_CURVE, chain="banyan"),
    dict(id="ho_man_pine", cat="hotel", dest="manali", name="Pine Lodge", unit="night", price=3000 * R, score=65, curve=HOTEL_CURVE),
    dict(id="ac_man_para", cat="activity", dest="manali", name="Solang paragliding", unit="pp", price=1000 * R, score=80, curve=PAID_ACTIVITY),
    dict(id="ac_man_walk", cat="activity", dest="manali", name="Old Manali heritage walk", unit="pp", price=300 * R, score=50, curve=FLEX_ACTIVITY),
]

# Same-chain bookings can be moved to a sister property instead of cancelled.
CHAIN_CHANGE_FEE = {"banyan": 0}

CATS = ("flight", "hotel", "activity")


def priced(dest: str, nights: int, people: int) -> list[dict]:
    """Catalog entries for a destination, priced for this trip."""
    out = []
    for it in CATALOG:
        if it["dest"] != dest:
            continue
        mult = nights if it["unit"] == "night" else people
        out.append({**it, "price": it["price"] * mult})
    return out


def fee_bps(curve: list, hours_to_departure: float) -> int:
    for min_hours, bps in curve:
        if hours_to_departure >= min_hours:
            return bps
    return 10000


def cancel_fee(item: dict, hours_to_departure: float) -> int:
    """Integer paise lost if this committed item is cancelled now. Holds cost nothing."""
    return item["price"] * fee_bps(item["curve"], hours_to_departure) // 10000
