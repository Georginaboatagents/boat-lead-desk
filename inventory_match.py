"""
inventory_match.py — Boat Lead Desk, step 1.

Drop this next to webpush_lib.py in the repo. The hourly task imports it.

  from inventory_match import load_inventory, match_lead, diff_new_listings

Two jobs:
  1. match_lead(lead, inventory)      -> matched boats for a Tier 1 BUYING lead
  2. diff_new_listings(inv, snapshot) -> boats that appeared since last run
"""

import json, re, urllib.request

INVENTORY_URL = "https://georginaboatagents.github.io/<repo>/inventory.json"

# Flip to False if Grade D (size-only, no make named) turns out noisy.
ALERT_ON_SIZE_ONLY = True

LENGTH_WINDOW_FT = 5
MAX_MATCHES_PER_LEAD = 3

# ---------------------------------------------------------------- inventory

def load_inventory(url=INVENTORY_URL):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())["listings"]


def diff_new_listings(inventory, snapshot_ids):
    """Returns (new_boats, current_ids). Empty snapshot seeds silently."""
    current = [b["id"] for b in inventory]
    if not snapshot_ids:                      # first run ever — no push storm
        return [], current
    known = set(snapshot_ids)
    return [b for b in inventory if b["id"] not in known], current

# ---------------------------------------------------------------- lead parsing

# A buyer asking the group "anyone selling a X?" is a BUY lead, not a seller.
# Checked before SELL_PAT so the word "selling" doesn't misfile them.
BUY_OVERRIDE_PAT = re.compile(
    r"\b(any(one|body)|some(one|body)|who\s?'?s|know\s+any(one|body))\s+"
    r"(is\s+)?(selling|got|has|have|with)\b", re.I)

# A seller talks about their OWN boat.
SELL_PAT = re.compile(
    r"\b(i\s?'?m\s+selling|we\s?'?re\s+selling|selling\s+(my|our|her|his)|"
    r"listing\s+(my|our)|list\s+my|(want|looking|need)\s+to\s+sell|"
    r"for\s+sale\s+by\s+owner|offloading\s+(my|our)|parting\s+with\s+(my|our)|"
    r"putting\s+(my|our)\s+\w+\s+on\s+the\s+market)\b", re.I)

BUY_PAT = re.compile(
    r"\b(buy|buying|to buy|purchas\w*|wtb|looking|in the market|market for|"
    r"shopping|searching|trying to find|where (can|do) i find|"
    r"interested in|considering|hunting|recommend\w*)\b", re.I)

# "42 ft", "42'", "42-foot", "42 footer", "in the 40s"
LEN_PAT = re.compile(r"\b(\d{2})\s*(?:'|ft\b|feet\b|foot\b|footer\b)", re.I)
RANGE_PAT = re.compile(r"\b(\d{2})\s*[-–to]+\s*(\d{2})\s*(?:'|ft\b|feet\b|foot\b|footer\b)", re.I)
BUDGET_PAT = re.compile(r"\$\s?([\d.,]+)\s*([kmKM])?\b")


def lead_intent(text):
    """'buy', 'sell' or None.

    Order matters. "anyone selling a Galeon?" is a BUYER post even though it
    contains the word 'selling', so the override is checked first. Only
    first-person possession ("selling my boat") counts as a seller.
    """
    if BUY_OVERRIDE_PAT.search(text): return "buy"
    if SELL_PAT.search(text):         return "sell"
    if BUY_PAT.search(text):          return "buy"
    return None


def lead_length_bands(text):
    """Acceptable LOA bands (lo, hi) in feet.

    An explicit range is taken at face value -- "50-55ft" means 50-55, because
    the buyer already told us their tolerance. A single number gets the +/-5 ft
    window. Without this, "50-55ft" would silently widen to 45-60.
    """
    bands, ranged = [], []
    for lo, hi in RANGE_PAT.findall(text):
        lo, hi = int(lo), int(hi)
        if lo > hi: lo, hi = hi, lo
        if 15 <= lo <= 150 and 15 <= hi <= 150:
            bands.append((lo, hi)); ranged += [lo, hi]
    for m in LEN_PAT.findall(text):
        n = int(m)
        if 15 <= n <= 150 and n not in ranged:
            bands.append((n - LENGTH_WINDOW_FT, n + LENGTH_WINDOW_FT))
    return bands


def lead_budget(text):
    best = None
    for num, suf in BUDGET_PAT.findall(text):
        try: v = float(num.replace(",", ""))
        except ValueError: continue
        s = (suf or "").lower()
        if s == "k": v *= 1_000
        elif s == "m": v *= 1_000_000
        elif v < 10_000: v *= 1_000        # "$500" in boat talk means $500k
        if best is None or v > best: best = v
    return best

# ---------------------------------------------------------------- matching

def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def _make_hit(text, keys):
    for name in [keys["make"]] + keys.get("make_aliases", []):
        if re.search(r"\b" + re.escape(name) + r"\b", text):
            return True
    return False


def _model_hit(text, keys):
    tokens = [t for t in keys.get("model_tokens", []) if len(t) >= 2]
    if not tokens: return False
    # a distinctive token ("d36", "cantius", "sundancer", "425") is enough
    return any(re.search(r"\b" + re.escape(t) + r"\b", text) for t in tokens)


def match_lead(lead, inventory):
    """lead = {"text": str, "tier": "tier1"|"tier2", ...}
    Returns [] or a ranked list of {boat, grade, why}."""
    if lead.get("tier") != "tier1":
        return []

    raw = lead.get("text", "") or ""
    text = _norm(raw)                 # for make/model word matching
    if lead_intent(raw) != "buy":
        return []

    # lengths and budget read the RAW text — _norm strips ' and $
    bands = lead_length_bands(raw)
    budget = lead_budget(raw)
    hits = []

    for boat in inventory:
        keys = boat["match_keys"]
        make = _make_hit(text, keys)
        model = make and _model_hit(text, keys)
        loa = keys.get("length_ft")
        near = bool(loa) and any(lo <= loa <= hi for lo, hi in bands)

        # MAKE IS THE PRIORITY (Georgina, Aug 20): any lead naming a make we
        # carry matches, and every make-based grade outranks size-only.
        if model:
            grade, why = "A", f"named {boat['make']} {boat['model']}"
        elif make and near:
            grade, why = "B", f"named {boat['make']} at ~{loa}ft"
        elif make:
            grade, why = "C", f"named {boat['make']}"
        elif near:
            if not ALERT_ON_SIZE_ONLY:
                continue
            grade, why = "D", f"size match ~{loa}ft"
        else:
            continue

        # budget only ranks, never excludes
        gap = abs(boat["price_usd"] - budget) if (budget and boat.get("price_usd")) else 10**9
        # dual-showroom hulls ("NY + FL showrooms") are the most local of all
        region_rank = 1 if boat["region"] == "Other / Central agency" else 0
        hits.append({
            "boat": boat, "grade": grade, "why": why,
            "_sort": ("ABCD".index(grade), region_rank, gap),
        })

    hits.sort(key=lambda h: h["_sort"])
    for h in hits: h.pop("_sort")
    return hits[:MAX_MATCHES_PER_LEAD]

# ---------------------------------------------------------------- push text

def _where(b):
    locs = b.get("locations")
    if locs:
        return " & ".join(f"{l['city']} {l['state']}" for l in locs)
    return f"{b['city']} {b['state']}"


def match_push(lead, hits):
    b = hits[0]["boat"]
    body = f"{b['year']} {b['make']} {b['model']} · {b['price_display']} · {_where(b)}"
    if len(hits) > 1:
        body += f"  (+{len(hits)-1} more)"
    return {"title": "🔴 INVENTORY MATCH · Tier 1", "body": body,
            "tag": f"match-{lead.get('id','')}", "url": b.get("listing_url") or ""}


def new_listing_push(boat):
    return {"title": "🚤 NEW LISTING · Yacht Hampton",
            "body": f"{boat['year']} {boat['make']} {boat['model']} · "
                    f"{boat['price_display']} · {_where(boat)}",
            "tag": f"new-{boat['id']}", "url": boat.get("listing_url") or ""}
