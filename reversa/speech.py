"""Turning speech-to-text output into structured intent. Deterministic on purpose:
this is the stand-in for the LLM, and numbers never depend on a model's arithmetic."""
from __future__ import annotations

import re

from .catalog import VIBES

UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
MULT = {"hundred": 100, "thousand": 1000, "k": 1000, "lakh": 100000, "lac": 100000}
CAT_WORDS = {"flight": "flight", "flights": "flight", "plane": "flight", "hotel": "hotel", "stay": "hotel",
             "room": "hotel"}


def normalize_numbers(text: str) -> str:
    """'forty thousand' -> '40000', '₹40,000' -> '40000', '40k' -> '40000', 'two people' -> '2 people'."""
    t = text.lower().replace("₹", " ").replace("rs.", " ").replace("-", " ")
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    t = re.sub(r"(\d)(k|lakh|lac)\b", r"\1 \2", t)
    toks = re.findall(r"\d+|[a-z']+|[^\sa-z\d]", t)
    out, total, cur, active = [], 0, 0, False

    def flush():
        nonlocal total, cur, active
        if active:
            out.append(str(total + cur))
        total, cur, active = 0, 0, False

    i = 0
    while i < len(toks):
        w = toks[i]
        if w.isdigit():
            flush()
            n = int(w)
            while i + 1 < len(toks) and toks[i + 1] in MULT:
                n *= MULT[toks[i + 1]]
                i += 1
            out.append(str(n))
        elif w in UNITS or w in TENS:
            cur += UNITS.get(w, TENS.get(w, 0))
            active = True
        elif w in MULT and active:
            if MULT[w] == 100:
                cur *= 100
            else:
                total += max(cur, 1) * MULT[w]
                cur = 0
        elif w == "and" and active:
            pass
        else:
            flush()
            out.append(w)
        i += 1
    flush()
    return " ".join(out)


def spoken_number(text: str) -> int | None:
    """The number in a confirmation phrase. 'confirm 2 5 8 0 0' (digits read one by one) also works."""
    t = normalize_numbers(text)
    nums = re.findall(r"\d+", t)
    if not nums:
        return None
    if len(nums) > 1 and all(len(n) == 1 for n in nums):
        return int("".join(nums))
    return int(nums[0]) if len(nums) == 1 else None      # two different numbers = ambiguous = refuse


BOOK_WORDS = ("book", "select", "choose", "take", "reserve", "hold")


def parse(text: str) -> dict:
    """Utterance -> {'intent': ..., slots}. Rule-based stand-in for an LLM's intent extraction.
    It only extracts slots; it never decides whether money may move."""
    t = normalize_numbers(text)
    words = re.findall(r"[a-z]+|\d+", t)
    dest = next((VIBES[w] for w in words if w in VIBES and not _negated(words, w)), None)
    cat = next((CAT_WORDS[w] for w in words if w in CAT_WORDS), None)
    big = [int(n) for n in re.findall(r"\d+", t) if int(n) >= 100]

    if "confirm" in words or "approve" in words:
        key = "confirm" if "confirm" in words else "approve"
        n = spoken_number(t.split(key, 1)[1])
        return {"intent": "confirm", "amount": None if n is None else n * 100}
    if any(w in words for w in ("cancel", "drop", "remove")):
        return {"intent": "cancel", "cat": cat}
    if "reconcile" in words or "ledger" in words or "audit" in words or "balance" in words:
        return {"intent": "ledger"}
    if any(w in words for w in ("recommend", "optimise", "optimize", "best", "safest")):
        return {"intent": "optimise"}
    if dest and (any(w in words for w in ("plan", "trip", "under", "budget", "within")) or not
                 any(w in words for w in BOOK_WORDS)):
        nights = _num_before(t, r"nights?") or 3
        return {"intent": "trip", "dest": dest, "nights": nights,
                "budget": (max(n for n in big if n >= 1000) * 100 if any(n >= 1000 for n in big) else None)}
    if any(w in words for w in BOOK_WORDS):
        return {"intent": "book", "cat": cat, "amount": (big[0] * 100 if len(big) == 1 else None),
                "ambiguous": len(big) > 1}
    if "exposure" in words or ("how" in words and "lose" in words):
        return {"intent": "exposure"}
    return {"intent": "unknown"}


def _negated(words: list[str], w: str) -> bool:
    i = words.index(w)
    return i > 0 and words[i - 1] in ("not", "no")


def _num_before(t: str, unit: str) -> int | None:
    m = re.search(rf"(\d+)\s+{unit}\b", t)
    return int(m.group(1)) if m else None


def say_rupees(paise: int) -> str:
    return f"₹{paise // 100:,}" + (f".{paise % 100:02d}" if paise % 100 else "")
