"""Product gift identities. Asset IDs are transport keys, never their meanings."""
from __future__ import annotations

import re

# Keep white-pebble compatible with shipped clients, images and GLB assets.
GIFT_CATALOG = {
    "white-pebble": ("饭团", "food"),
    "rice-ball": ("饭团", "food"),
    "baozi": ("包子", "food"),
    "golden-croissant": ("牛角包", "food"),
    "sealed-envelope": ("信封", "letter"),
}


def normalize_gift(dm):
    if getattr(dm, "kind", "chat") != "gift":
        return dm
    identity = GIFT_CATALOG.get(getattr(dm, "gift_id", ""))
    if identity is None:
        return dm
    dm.gift_name, dm.gift_category = identity
    text = dm.text or ""
    if dm.gift_name == "饭团":
        # Old clients sent "Sent Pebble". Correct this before either LLM sees it.
        text = re.sub(r"white-pebble|\b(?:white\s+)?pebble\b|\bstone\b|白色鹅卵石|石头", "饭团", text, flags=re.I)
    if dm.gift_name not in text:
        text = f"送来了{dm.gift_name}。" + text
    dm.text = text
    return dm
