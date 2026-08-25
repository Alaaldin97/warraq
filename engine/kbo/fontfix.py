"""Make any Arabic font usable with pre-shaped text.

The Kindle Oasis does not apply contextual shaping (GSUB) to *embedded* fonts,
so we write text as Unicode presentation forms instead. That only works if the
font's cmap maps those presentation codepoints - and most modern Arabic fonts
map only the base letters and rely on GSUB.

This module fixes that: it reads the font's own init/medi/fina/isol lookups and
adds direct cmap entries for every presentation form, pointing at the glyph the
shaper would have produced. No glyph outlines are invented - we only add
mappings to shapes the font already contains.
"""
from __future__ import annotations

import unicodedata

from fontTools.ttLib import TTFont

FORM_TAG = {"<initial>": "init", "<medial>": "medi",
            "<final>": "fina", "<isolated>": "isol"}
PRES_RANGES = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def _decompose(cp: int):
    """Return (form_feature, [base_codepoints]) for a presentation form."""
    d = unicodedata.decomposition(chr(cp))
    if not d:
        return None, None
    parts = d.split()
    if not parts or not parts[0].startswith("<"):
        return None, None
    feat = FORM_TAG.get(parts[0])
    if not feat:
        return None, None
    try:
        bases = [int(x, 16) for x in parts[1:]]
    except ValueError:
        return None, None
    return feat, bases


def _single_sub_maps(font: TTFont) -> dict[str, dict[str, str]]:
    """feature tag -> {base glyph: shaped glyph} from single substitutions."""
    out = {f: {} for f in ("init", "medi", "fina", "isol")}
    if "GSUB" not in font:
        return out
    gsub = font["GSUB"].table
    if not gsub.FeatureList or not gsub.LookupList:
        return out
    feats = gsub.FeatureList.FeatureRecord
    lookups = gsub.LookupList.Lookup
    for fr in feats:
        tag = fr.FeatureTag
        if tag not in out:
            continue
        for li in fr.Feature.LookupListIndex:
            if li >= len(lookups):
                continue
            lk = lookups[li]
            for st in lk.SubTable:
                # Type 1 = single substitution
                if getattr(st, "LookupType", lk.LookupType) == 1 or \
                        hasattr(st, "mapping"):
                    m = getattr(st, "mapping", None)
                    if m:
                        out[tag].update(m)
    return out


def _ligature_maps(font: TTFont) -> dict[tuple, str]:
    """(glyph1, glyph2) -> ligature glyph, from rlig/liga lookups."""
    out = {}
    if "GSUB" not in font:
        return out
    gsub = font["GSUB"].table
    if not gsub.FeatureList or not gsub.LookupList:
        return out
    lookups = gsub.LookupList.Lookup
    for fr in gsub.FeatureList.FeatureRecord:
        if fr.FeatureTag not in ("rlig", "liga", "dlig"):
            continue
        for li in fr.Feature.LookupListIndex:
            if li >= len(lookups):
                continue
            for st in lookups[li].SubTable:
                ligs = getattr(st, "ligatures", None)
                if not ligs:
                    continue
                for first, items in ligs.items():
                    for lig in items:
                        comp = tuple([first] + list(lig.Component))
                        out[comp] = lig.LigGlyph
    return out


def flatten(src_path: str, dst_path: str) -> dict:
    """Write a copy of the font with presentation forms mapped in the cmap."""
    font = TTFont(src_path, fontNumber=0)
    cmap_tables = font["cmap"].tables
    base_map = {}
    for t in cmap_tables:
        if t.isUnicode():
            base_map.update(t.cmap)
    glyphs = set(font.getGlyphOrder())

    subs = _single_sub_maps(font)
    ligs = _ligature_maps(font)

    added, missing, lig_added = 0, 0, 0
    new_entries: dict[int, str] = {}

    for lo, hi in PRES_RANGES:
        for cp in range(lo, hi + 1):
            if cp in base_map:
                continue
            feat, bases = _decompose(cp)
            if not feat or not bases:
                continue
            base_glyphs = [base_map.get(b) for b in bases]
            if any(g is None for g in base_glyphs):
                missing += 1
                continue

            if len(base_glyphs) == 1:
                g = base_glyphs[0]
                shaped = subs.get(feat, {}).get(g, g if feat == "isol" else None)
                if shaped and shaped in glyphs:
                    new_entries[cp] = shaped
                    added += 1
                else:
                    missing += 1
            else:
                # Lam-alef style ligature. Within the ligature the LAST
                # component takes the final form, and the first takes the
                # initial form (isolated ligature) or medial form (final
                # ligature) - e.g. U+FEFB = lam.init + alef.fina.
                first_feat = "medi" if feat == "fina" else "init"
                feats_for = ([first_feat] * (len(base_glyphs) - 1)) + ["fina"]
                shaped_parts = [subs.get(f, {}).get(g, g)
                                for f, g in zip(feats_for, base_glyphs)]
                lg = (ligs.get(tuple(shaped_parts))
                      or ligs.get(tuple(base_glyphs))
                      or ligs.get(tuple(
                          subs.get("init", {}).get(g, g) for g in base_glyphs)))
                if lg and lg in glyphs:
                    new_entries[cp] = lg
                    added += 1
                    lig_added += 1
                else:
                    missing += 1

    if new_entries:
        for t in cmap_tables:
            if t.isUnicode() and t.format in (4, 12):
                t.cmap.update(new_entries)
        # a format-4 subtable cannot hold codepoints above U+FFFF, but all
        # Arabic presentation forms are in the BMP, so format 4 is sufficient
    font.save(dst_path)
    font.close()
    return {"added": added, "missing": missing, "ligatures": lig_added,
            "out": dst_path}


def ensure_preshape_font(src_path: str, cache_dir: str) -> tuple[str, dict]:
    """Return a font path guaranteed to render pre-shaped Arabic."""
    import os
    os.makedirs(cache_dir, exist_ok=True)
    name = os.path.basename(src_path)
    dst = os.path.join(cache_dir, name.replace(".ttf", "-flat.ttf"))
    if os.path.exists(dst) and os.path.getmtime(dst) > os.path.getmtime(src_path):
        return dst, {"cached": True}
    info = flatten(src_path, dst)
    return dst, info


# Lam-alef ligatures decomposed into (lam form, alef form). Used when a font
# lacks the combined ligature glyph: rendering lam + alef separately is always
# correct, just slightly less elegant than the true ligature.
LIGATURE_FALLBACK = {
    "\uFEF5": "\uFEDF\uFE82",   # lam-alef madda, isolated
    "\uFEF6": "\uFEE0\uFE82",   # lam-alef madda, final
    "\uFEF7": "\uFEDF\uFE84",   # lam-alef hamza above, isolated
    "\uFEF8": "\uFEE0\uFE84",   # lam-alef hamza above, final
    "\uFEF9": "\uFEDF\uFE88",   # lam-alef hamza below, isolated
    "\uFEFA": "\uFEE0\uFE88",   # lam-alef hamza below, final
    "\uFEFB": "\uFEDF\uFE8E",   # lam-alef, isolated
    "\uFEFC": "\uFEE0\uFE8E",   # lam-alef, final
}


def font_codepoints(path: str) -> set[int]:
    f = TTFont(path, fontNumber=0, lazy=True)
    cps: set[int] = set()
    for t in f["cmap"].tables:
        if t.isUnicode():
            cps.update(t.cmap.keys())
    f.close()
    return cps


def adapt_text_to_font(text: str, font_path: str) -> tuple[str, dict]:
    """Rewrite pre-shaped text so the given font can render all of it."""
    cps = font_codepoints(font_path)
    replaced = 0
    out = []
    for ch in text:
        if ord(ch) not in cps and ch in LIGATURE_FALLBACK:
            alt = LIGATURE_FALLBACK[ch]
            if all(ord(c) in cps for c in alt):
                out.append(alt)
                replaced += 1
                continue
        out.append(ch)
    result = "".join(out)
    still_missing = {c for c in result if ord(c) not in cps and not c.isspace()}
    return result, {"ligatures_decomposed": replaced,
                    "unrenderable": len(still_missing),
                    "missing_sample": sorted(still_missing)[:10]}
