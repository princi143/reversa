"""CP-SAT option chooser (small, on purpose).

Pick exactly one flight and one hotel so that
    total price <= trip budget                                (hard constraint)
    only options with a published cancellation policy         (hard constraint)
and minimise the risk-adjusted cost
    sum( price + p * cancellation_fee )
where p is the traveller's estimated chance of cancelling (0-100 %).

With p = 0 this is "cheapest"; as p rises, options that are cheap but expensive
to undo lose to slightly dearer flexible ones. Everything is integer paise.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from .catalog import DEPARTS_IN_HOURS, cancel_fee
from .speech import say_rupees as rs


def risk_cost(item: dict, p_pct: int, hours: float = DEPARTS_IN_HOURS) -> int | None:
    fee = cancel_fee(item, hours)
    return None if fee is None else item["price"] + fee * p_pct // 100


def choose(options: list[dict], budget: int, p_pct: int, hours: float = DEPARTS_IN_HOURS) -> dict:
    p_pct = max(0, min(100, int(p_pct)))
    table, usable = [], []
    for o in options:
        fee = cancel_fee(o, hours)
        row = {"id": o["id"], "cat": o["cat"], "name": o["name"], "price": o["price"], "fee": fee,
               "risk_cost": risk_cost(o, p_pct, hours), "eligible": fee is not None,
               "why": "" if fee is not None else "excluded: cancellation fee unknown"}
        table.append(row)
        if fee is not None:
            usable.append(o)

    m = cp_model.CpModel()
    x = {o["id"]: m.NewBoolVar(o["id"]) for o in usable}
    for cat in ("flight", "hotel"):
        m.AddExactlyOne([x[o["id"]] for o in usable if o["cat"] == cat])
    m.Add(sum(x[o["id"]] * o["price"] for o in usable) <= budget)
    # scale by 100 so p% * fee stays an exact integer
    m.Minimize(sum(x[o["id"]] * (o["price"] * 100 + cancel_fee(o, hours) * p_pct) for o in usable))

    s = cp_model.CpSolver()
    s.parameters.num_workers = 1          # deterministic
    s.parameters.max_time_in_seconds = 5
    status = s.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"ok": False, "p_pct": p_pct, "table": table,
                "explanation": f"No flight + hotel pair with a known cancellation policy fits {rs(budget)}."}
    chosen = [o for o in usable if s.Value(x[o["id"]])]
    for r in table:
        r["chosen"] = r["id"] in {c["id"] for c in chosen}
    price = sum(c["price"] for c in chosen)
    fees = sum(cancel_fee(c, hours) for c in chosen)
    cheapest = {}
    for cat in ("flight", "hotel"):
        cand = [o for o in usable if o["cat"] == cat]
        cheapest[cat] = min(cand, key=lambda o: o["price"])["name"] if cand else None
    return {
        "ok": True, "p_pct": p_pct, "status": s.StatusName(status), "table": table,
        "chosen": [{"id": c["id"], "name": c["name"], "price": c["price"], "fee": cancel_fee(c, hours)} for c in chosen],
        "total_price": price, "total_fee": fees, "objective": price + fees * p_pct // 100,
        "explanation": (f"CP-SAT minimised price + {p_pct}% × cancellation fee over every flight+hotel pair "
                        f"within {rs(budget)}. Chosen: {' + '.join(c['name'] for c in chosen)} = {rs(price)}, "
                        f"cancellation exposure {rs(fees)}, risk-adjusted cost {rs(price + fees * p_pct // 100)}. "
                        f"Options with an unknown cancellation fee were excluded."),
    }
