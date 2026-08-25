"""Device profiles. Kindle Oasis 9th gen (2017) is the primary target."""

DEVICES = {
    "kindle_oasis_9": {
        "name": "Kindle Oasis 9th Generation (2017)",
        "screen_px": (1264, 1680),
        "ppi": 300,
        # usable page area in points (1/72 in) for fixed-layout PDF/AZW3
        "page_pt": (1264 / 300 * 72, 1680 / 300 * 72),   # 303.4 x 403.2 pt
        "greyscale_levels": 16,
        "calibre_profile": "kindle_oasis",
        # margins for the reflow engine, in px of the 1264x1680 canvas
        "reflow_margin_px": 26,
    }
}

DEFAULT_DEVICE = "kindle_oasis_9"


def get(name: str = DEFAULT_DEVICE) -> dict:
    return DEVICES[name]
