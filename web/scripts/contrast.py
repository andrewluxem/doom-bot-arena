"""Contrast of tile text over real video (milestone 1B acceptance).

    python3 web/scripts/contrast.py web/public/sample/<id>          (needs numpy and ffmpeg)

The nameplate and HP footer sit on a flat scrim over the POV video: surface-0 at 86% (web/src/app.css),
composited in sRGB like the browser does. This decodes every frame of the eight videos and, inside the
bands those overlays cover, finds the pixel that leaves the least contrast for each text colour:
  nameplate band: top 23% of the frame (18px nameplate over the 80px-tall phone strip thumbnail, the
  smallest tile); footer band: bottom 8% (20px footer over the 265px wall tile at 1440x900).
Dark theme text is light, so its worst case is the brightest pixel; paper theme text is dark, so its
worst case is the darkest pixel. It also reports the darkest and brightest frames by mean luminance.
"""
import json
import subprocess
import sys

import numpy as np

SCRIM_ALPHA = 0.86
THEMES = {
    "dark": {"surface-0": (10, 11, 12), "ink": (236, 235, 230), "ink-muted": (154, 160, 167)},
    "paper": {"surface-0": (242, 240, 234), "ink": (17, 17, 16), "ink-muted": (79, 76, 69)},
}
H, W = 240, 320
BANDS = {"nameplate": (0, round(0.23 * H)), "footer": (H - round(0.08 * H), H)}


def lin(c):
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb):
    l = lin(rgb)
    return 0.2126 * l[..., 0] + 0.7152 * l[..., 1] + 0.0722 * l[..., 2]


def ratio(y1, y2):
    hi, lo = np.maximum(y1, y2), np.minimum(y1, y2)
    return (hi + 0.05) / (lo + 0.05)


def frames(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def main(bundle):
    with open(f"{bundle}/match.json") as f:
        match = json.load(f)
    worst = {(t, b): {"y": None} for t in THEMES for b in BANDS}
    darkest = {"mean": 2.0}
    brightest = {"mean": -1.0}
    total = 0
    for pov in match["pov"]:
        v = frames(f"{bundle}/{pov['path']}")
        total += len(v)
        means = luminance(v).mean(axis=(1, 2))
        i, j = int(means.argmin()), int(means.argmax())
        if means[i] < darkest["mean"]:
            darkest = {"mean": float(means[i]), "slot": pov["slot"], "frame": i, "tic": match["frame0_tic"] + i}
        if means[j] > brightest["mean"]:
            brightest = {"mean": float(means[j]), "slot": pov["slot"], "frame": j, "tic": match["frame0_tic"] + j}
        for theme, tok in THEMES.items():
            s = np.array(tok["surface-0"], dtype=np.float64)
            for band, (y0, y1) in BANDS.items():
                comp = SCRIM_ALPHA * s + (1 - SCRIM_ALPHA) * v[:, y0:y1].astype(np.float64)
                y = luminance(comp)
                flat = y.reshape(len(v), -1)
                k = flat.max(axis=1) if theme == "dark" else flat.min(axis=1)
                f = int(k.argmax() if theme == "dark" else k.argmin())
                w = worst[(theme, band)]
                if w["y"] is None or (k[f] > w["y"] if theme == "dark" else k[f] < w["y"]):
                    worst[(theme, band)] = {"y": float(k[f]), "slot": pov["slot"], "frame": f}
                if pov["slot"] == darkest.get("slot") and band == "nameplate":
                    darkest.setdefault("bands", {})[theme] = float(flat[darkest["frame"]].max() if theme == "dark" else flat[darkest["frame"]].min())

    print(f"{total} frames from {len(match['pov'])} videos; scrim surface-0 at {SCRIM_ALPHA:.0%}, sRGB composite")
    out = {"scrim_alpha": SCRIM_ALPHA, "frames": total, "worst": [], "darkest_frame": darkest, "brightest_frame": brightest}
    for (theme, band), w in worst.items():
        tok = THEMES[theme]
        for text in ("ink", "ink-muted"):
            r = float(ratio(luminance(np.array(tok[text])), w["y"]))
            floor_px = (255, 255, 255) if theme == "dark" else (0, 0, 0)
            floor_bg = SCRIM_ALPHA * np.array(tok["surface-0"]) + (1 - SCRIM_ALPHA) * np.array(floor_px)
            floor = float(ratio(luminance(np.array(tok[text])), luminance(floor_bg)))
            row = {"theme": theme, "band": band, "text": text, "min_ratio": round(r, 2), "slot": w["slot"],
                   "frame": w["frame"], "tic": match["frame0_tic"] + w["frame"], "floor_any_video": round(floor, 2)}
            out["worst"].append(row)
            print(f"  {theme:5s} {band:9s} {text:9s} min {r:5.2f}:1 at slot {w['slot']} frame {w['frame']} "
                  f"(floor over a {'white' if theme == 'dark' else 'black'} pixel {floor:.2f}:1)")
    d = darkest
    print(f"darkest frame: slot {d['slot']} frame {d['frame']} (tic {d['tic']}), mean luminance {d['mean']:.4f}")
    for theme, y in d.get("bands", {}).items():
        for text in ("ink", "ink-muted"):
            r = float(ratio(luminance(np.array(THEMES[theme][text])), y))
            print(f"  {theme:5s} nameplate {text:9s} {r:5.2f}:1 on the darkest frame")
            out.setdefault("darkest_frame_ratios", []).append({"theme": theme, "text": text, "ratio": round(r, 2)})
    b = brightest
    print(f"brightest frame: slot {b['slot']} frame {b['frame']} (tic {b['tic']}), mean luminance {b['mean']:.4f}")
    json.dump(out, sys.stderr, indent=1)
    return 0 if all(r["min_ratio"] >= 4.5 for r in out["worst"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/")))
