"""CP-SAT planner. One model does both jobs:

  * first plan:  pick one flight, hotel and activity within free cash.
  * re-plan:     given what is already committed, decide per booking whether to
                 KEEP it, CHANGE it (same chain, small fee) or CANCEL it (fee from
                 the curve), and what to BUY - minimising switching fees first.

The cash fence is a hard constraint: a cancellation's refund is pending, so it
can never pay for a new booking in the same plan.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from .catalog import CATS, CHAIN_CHANGE_FEE, cancel_fee, priced

SCORE_WEIGHT = 20000   # paise of value per quality point
FEE_WEIGHT = 1000      # switching fees dominate everything else


class NoPlan(Exception):
    pass


def solve(dest: str, nights: int, people: int, free_cash: int,
          committed: list[dict] | None = None, hours_to_departure: float = 240) -> dict:
    committed = committed or []
    cands = priced(dest, nights, people)
    m = cp_model.CpModel()

    x = {c["id"]: m.NewBoolVar(f"x_{c['id']}") for c in cands}        # in the final trip
    buy = {c["id"]: m.NewBoolVar(f"buy_{c['id']}") for c in cands}    # paid from free cash
    keep, cancel, change = {}, {}, {}
    for o in committed:
        cancel[o["id"]] = m.NewBoolVar(f"cancel_{o['id']}")
        opts = [cancel[o["id"]]]
        if any(c["id"] == o["id"] for c in cands):                    # same item still fits
            keep[o["id"]] = m.NewBoolVar(f"keep_{o['id']}")
            opts.append(keep[o["id"]])
        for c in cands:
            if c["id"] != o["id"] and c["cat"] == o["cat"] and o.get("chain") and o.get("chain") == c.get("chain"):
                change[(o["id"], c["id"])] = m.NewBoolVar(f"chg_{o['id']}_{c['id']}")
                opts.append(change[(o["id"], c["id"])])
        m.AddExactlyOne(opts)

    for cat in CATS:
        m.AddExactlyOne([x[c["id"]] for c in cands if c["cat"] == cat])
    for c in cands:                                                   # how each chosen item is obtained
        sources = [buy[c["id"]]] + [v for (o, n), v in change.items() if n == c["id"]]
        if c["id"] in keep:
            sources.append(keep[c["id"]])
        m.Add(sum(sources) == x[c["id"]])

    old = {o["id"]: o for o in committed}
    new = {c["id"]: c for c in cands}
    chg_fee = {k: CHAIN_CHANGE_FEE[old[k[0]]["chain"]] for k in change}
    chg_up = {k: max(0, new[k[1]]["price"] - old[k[0]]["price"]) for k in change}
    can_fee = {o["id"]: cancel_fee(o, hours_to_departure) for o in committed}

    cash_out = (sum(buy[c["id"]] * c["price"] for c in cands)
                + sum(v * (chg_fee[k] + chg_up[k]) for k, v in change.items()))
    m.Add(cash_out <= free_cash)                                      # the cash fence

    fees = sum(cancel[o] * can_fee[o] for o in cancel) + sum(v * chg_fee[k] for k, v in change.items())
    value = sum(x[c["id"]] * (c["score"] * SCORE_WEIGHT - c["price"]) for c in cands)
    m.Maximize(value - FEE_WEIGHT * fees)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5
    solver.parameters.num_workers = 1                                 # deterministic
    if solver.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise NoPlan(f"no {dest} plan fits ₹{free_cash // 100} of free cash "
                     "(pending refunds cannot be spent until they settle)")

    val = solver.Value
    plan = {
        "dest": dest,
        "keep": [old[o] for o, v in keep.items() if val(v)],
        "cancel": [{"item": old[o], "fee": can_fee[o], "refund": old[o]["price"] - can_fee[o]}
                   for o, v in cancel.items() if val(v)],
        "change": [{"from": old[k[0]], "to": new[k[1]], "fee": chg_fee[k],
                    "diff": new[k[1]]["price"] - old[k[0]]["price"]} for k, v in change.items() if val(v)],
        "buy": [c for c in cands if val(buy[c["id"]])],
    }
    plan["fees"] = sum(c["fee"] for c in plan["cancel"]) + sum(c["fee"] for c in plan["change"])
    plan["cash_needed"] = (sum(c["price"] for c in plan["buy"])
                           + sum(c["fee"] + max(0, c["diff"]) for c in plan["change"]))
    plan["naive_fees"] = sum(can_fee.values())                        # cancel everything and start over
    plan["total"] = sum(c["price"] for c in cands if val(x[c["id"]]))
    return plan
