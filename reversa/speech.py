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
             "room": "hotel", "activity": "activity", "activities": "activity"}


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


def parse(text: str) -> dict:
    """Utterance -> {'intent': ..., slots}."""
    t = normalize_numbers(text)
    words = re.findall(r"[a-z]+|\d+", t)
    dest = next((VIBES[w] for w in words if w in VIBES and not _negated(words, w)), None)
    cat = next((CAT_WORDS[w] for w in words if w in CAT_WORDS), None)

    if words[:1] == ["confirm"] or " confirm " in f" {t} ":
        return {"intent": "confirm", "number": spoken_number(t.split("confirm", 1)[1])}
    if "reconcile" in words or "audit" in words:
        return {"intent": "reconcile"}
    if "exposure" in words or ("how" in words and "lose" in words):
        return {"intent": "exposure"}
    if any(w in words for w in ("cancel", "drop", "remove")) and "confirm" not in words:
        return {"intent": "cancel", "cat": cat, "everything": any(w in words for w in ("everything", "all", "trip"))}
    nights = _num_before(t, r"nights?")
    if nights or re.search(r"\b(budget|rupees)\b", t) and dest:
        people = _num_before(t, r"(?:people|persons?|adults?|travell?ers?|of us)") or 2
        big = [int(n) for n in re.findall(r"\d+", t) if int(n) >= 1000]
        return {"intent": "trip", "dest": dest, "nights": nights, "people": people,
                "budget": (big[0] * 100 if big else None)}
    if dest and any(w in words for w in ("actually", "instead", "switch", "change", "rather", "not")):
        return {"intent": "replan", "dest": dest}
    if any(p in t for p in ("book it", "book them", "go ahead", "book the trip", "book this")):
        return {"intent": "book"}
    if dest:
        return {"intent": "replan", "dest": dest}
    return {"intent": "unknown"}


def _negated(words: list[str], w: str) -> bool:
    i = words.index(w)
    return i > 0 and words[i - 1] in ("not", "no")


def _num_before(t: str, unit: str) -> int | None:
    m = re.search(rf"(\d+)\s+{unit}\b", t)
    return int(m.group(1)) if m else None


def say_rupees(paise: int) -> str:
    return f"₹{paise // 100:,}" + (f".{paise % 100:02d}" if paise % 100 else "")
