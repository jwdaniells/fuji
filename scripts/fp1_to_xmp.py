#!/usr/bin/env python3
"""Convert Fujifilm X RAW Studio .FP1/.txt recipe files into Lightroom / Camera Raw XMP presets.

Usage:  fp1_to_xmp.py OUTDIR FILE [FILE ...]

The mapping is an APPROXIMATION: Lightroom has no Color Chrome, Grain Size or
D-Range Priority equivalents. All tuning constants live at the top of this file.
"""
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

# --- Film simulation -> Adobe "Camera Matching" profile name -----------------
# Keys are the codes found in recipes/source/*.txt. Names are the profile names
# Lightroom shows for Fuji bodies; edit here if your Lightroom spells them differently.
PROFILE = {
    "Provia": "Camera PROVIA/Standard",
    "Velvia": "Camera Velvia/Vivid",
    "Astia": "Camera ASTIA/Soft",
    "Classic": "Camera CLASSIC CHROME",
    "ClassicNEGA": "Camera CLASSIC Neg.",
    "NostalgicNEGA": "Camera Nostalgic Neg.",
    "Reala": "Camera REALA ACE",
    "Eterna": "Camera ETERNA/Cinema",
    "BleachBypass": "Camera ETERNA BLEACH BYPASS",
    "NEGAhi": "Camera PRO Neg. Hi",
    "NEGAStd": "Camera PRO Neg. Std",
    "ProNegStd": "Camera PRO Neg. Std",
    "Acros": "Camera ACROS",
    "AcrosR": "Camera ACROS+R",
    "AcrosG": "Camera ACROS+G",
    "AcrosYe": "Camera ACROS+Ye",
    "BW": "Camera MONOCHROME",
    "BR": "Camera MONOCHROME+R",
    "BG": "Camera MONOCHROME+G",
    "Sepia": "Camera SEPIA",
}

# --- Tuning constants (per in-camera step) -----------------------------------
HIGHLIGHT_PER_STEP = 10   # in-camera Highlight Tone +1  -> LR Highlights +10
SHADOW_PER_STEP = 10      # in-camera Shadow Tone +1     -> LR Shadows -10 (camera + = darker)
SATURATION_PER_STEP = 8   # Color +1 -> LR Saturation +8
CLARITY_PER_STEP = 10     # Clarity +1 -> LR Clarity +10
SHARPEN_BASE, SHARPEN_PER_STEP = 40, 10
LUM_NR_PER_STEP = 4       # NR -4 -> 0 ... +4 -> 32
WB_TEMP_PER_STEP = 60     # kelvin per net (R - B) shift step
WB_TINT_PER_STEP = 2      # tint per (R + B) shift step (+ = magenta)
GRAIN_AMOUNT = {"WEAK": 25, "STRONG": 45}
GRAIN_SIZE = {"SMALL": 25, "LARGE": 50}
CHROME_LUM = {"WEAK": -3, "STRONG": -6}        # all colours, HSL luminance
CHROME_BLUE_LUM = {"WEAK": -4, "STRONG": -8}   # blue (aqua/purple get half)
DR_HIGHLIGHTS = {"100": 0, "200": -8, "400": -16}
DR_SHADOWS = {"100": 0, "200": 0, "400": 8}

# Fixed white balances: (Lightroom WB name, kelvin, tint)
FIXED_WB = {
    "Daylight": ("Daylight", 5500, 10), "Fine": ("Daylight", 5500, 10),
    "Shade": ("Shade", 7500, 10),
    "Incandescent": ("Tungsten", 2850, 0),
    "FLight1": ("Custom", 6700, 0), "FLight2": ("Custom", 5000, 0),
    "FLight3": ("Custom", 4200, 0), "FluorescentNormal": ("Custom", 4200, 0),
    "FluorescentWarm": ("Custom", 3000, 0), "UWater": ("Custom", 8000, 0),
}
AUTO_WB = {"Auto", "AutoAmbiance", "Auto_White", "INVALID", ""}


def num(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def sgn(v):
    v = int(round(v))
    return f"+{v}" if v > 0 else str(v)


def parse_fp1(path):
    root = ET.fromstring(Path(path).read_text(encoding="utf-8", errors="ignore").lstrip("﻿"))
    grp = root.find("PropertyGroup")
    vals = {c.tag: (c.text or "").strip() for c in grp}
    vals["_label"] = grp.get("label") or Path(path).stem
    return vals


def build_settings(v):
    s = {}  # crs attribute -> value (strings)
    notes = []

    sim = v.get("FilmSimulation", "")
    prof = PROFILE.get(sim)
    if prof:
        s["CameraProfile"] = prof
    else:
        notes.append(f"Unmapped film simulation '{sim}' - profile left unchanged")

    # tone
    h, sh = num(v.get("HighlightTone")), num(v.get("ShadowTone"))
    dr = v.get("DynamicRange", "100")
    hl = h * HIGHLIGHT_PER_STEP + DR_HIGHLIGHTS.get(dr, 0)
    sd = -sh * SHADOW_PER_STEP + DR_SHADOWS.get(dr, 0)
    s["Highlights2012"] = sgn(clamp(hl, -100, 100))
    s["Shadows2012"] = sgn(clamp(sd, -100, 100))
    s["Saturation"] = sgn(clamp(num(v.get("Color")) * SATURATION_PER_STEP, -100, 100))
    s["Clarity2012"] = sgn(clamp(num(v.get("Clarity")) * CLARITY_PER_STEP, -100, 100))

    # detail / noise
    s["Sharpness"] = str(int(clamp(SHARPEN_BASE + num(v.get("Sharpness")) * SHARPEN_PER_STEP, 0, 150)))
    s["LuminanceSmoothing"] = str(int(clamp((num(v.get("NoisReduction")) + 4) * LUM_NR_PER_STEP, 0, 100)))

    # grain
    g = v.get("GrainEffect", "OFF")
    if g in GRAIN_AMOUNT:
        s["GrainAmount"] = str(GRAIN_AMOUNT[g])
        s["GrainSize"] = str(GRAIN_SIZE.get(v.get("GrainEffectSize", "SMALL"), 25))
        s["GrainFrequency"] = "50"
    else:
        s["GrainAmount"] = "0"

    # white balance
    wb = v.get("WhiteBalance", "Auto")
    r, b = num(v.get("WBShiftR")), num(v.get("WBShiftB"))
    d_temp = (r - b) * WB_TEMP_PER_STEP
    d_tint = (r + b) * WB_TINT_PER_STEP
    if wb in AUTO_WB:
        s["WhiteBalance"] = "As Shot"
        if d_temp or d_tint:
            s["IncrementalTemperature"] = sgn(d_temp)
            s["IncrementalTint"] = sgn(d_tint)
    elif wb == "Temperature":
        k = int(num(re.sub(r"[^0-9.]", "", v.get("WBColorTemp", "5200")), 5200))
        s["WhiteBalance"] = "Custom"
        s["Temperature"] = str(int(k + d_temp))
        s["Tint"] = sgn(d_tint)
    elif wb in FIXED_WB:
        name, k, t = FIXED_WB[wb]
        s["WhiteBalance"] = name
        s["Temperature"] = str(int(k + d_temp))
        s["Tint"] = sgn(t + d_tint)
    else:
        notes.append(f"Unmapped white balance '{wb}' - left as shot")

    # colour chrome (approximation via HSL luminance)
    cc, cb = v.get("ChromeEffect", "OFF"), v.get("ColorChromeBlue", "OFF")
    lum = {c: CHROME_LUM.get(cc, 0) for c in
           ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]}
    bl = CHROME_BLUE_LUM.get(cb, 0)
    lum["Blue"] += bl
    lum["Aqua"] += bl // 2
    lum["Purple"] += bl // 2
    for c, val in lum.items():
        if val:
            s[f"LuminanceAdjustment{c}"] = sgn(val)

    return s, notes


def to_xmp(label, settings, notes, fp1_name):
    attrs = "\n".join(f'   crs:{k}="{escape(str(val), {chr(34): "&quot;"})}"' for k, val in settings.items())
    desc = f"Approximation of Fujifilm recipe '{label}' (from {fp1_name})."
    if notes:
        desc += " " + "; ".join(notes) + "."
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000 1.000000, 0000/00/00-00:00:00">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
   xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:PresetType="Normal"
   crs:Cluster=""
   crs:UUID="{str(uuid.uuid4()).upper()}"
   crs:SupportsAmount="False"
   crs:SupportsColor="True"
   crs:SupportsMonochrome="True"
   crs:SupportsHighDynamicRange="True"
   crs:SupportsNormalDynamicRange="True"
   crs:SupportsSceneReferred="True"
   crs:SupportsOutputReferred="True"
   crs:CameraModelRestriction=""
   crs:Copyright=""
   crs:ContactInfo=""
   crs:Version="16.0"
   crs:ProcessVersion="15.4"
   crs:HasSettings="True"
{attrs}>
   <crs:Name><rdf:Alt><rdf:li xml:lang="x-default">{escape(label)}</rdf:li></rdf:Alt></crs:Name>
   <crs:ShortName><rdf:Alt><rdf:li xml:lang="x-default"/></rdf:Alt></crs:ShortName>
   <crs:SortName><rdf:Alt><rdf:li xml:lang="x-default"/></rdf:Alt></crs:SortName>
   <crs:Group><rdf:Alt><rdf:li xml:lang="x-default">Fuji Recipes</rdf:li></rdf:Alt></crs:Group>
   <crs:Description><rdf:Alt><rdf:li xml:lang="x-default">{escape(desc)}</rdf:li></rdf:Alt></crs:Description>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for f in argv[2:]:
        v = parse_fp1(f)
        label = v["_label"]
        s, notes = build_settings(v)
        safe = re.sub(r'[\\/:*?"<>|]', "", label).strip()
        (out / f"{safe}.xmp").write_text(to_xmp(label, s, notes, Path(f).name), encoding="utf-8")
        print(f"{label}: {len(s)} settings" + (f"  NOTES: {notes}" if notes else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
