"""Cut the character out of your wallpaper to skin the panel (see src/skin.py).

    .venv\\Scripts\\python.exe scripts\\make_skin.py [image]

Without an argument it uses your current Windows wallpaper. Works best on art
with a mostly flat background. Writes skin/mascot.png (the cut-out),
skin/mascot_bust.png (the strip mascot) and skin/palette.json (the theme).
skin/ is gitignored: wallpaper art usually belongs to someone else, so it stays
on your machine.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

ROOT = Path(__file__).resolve().parents[1]
BUST_FRACTION = 0.48      # head and shoulders
FADE_BOTTOM = 0.30        # share of the bust that fades out at the bottom...
FADE_TOP = 0.07           # ...and at the top, where wallpapers often cut the hair
HOLE_MIN_PIXELS = 300     # enclosed background patches at least this big are removed


def pixels(image):
    image = image.convertToFormat(QImage.Format_RGBA8888)
    return np.frombuffer(image.bits(), np.uint8).reshape(image.height(), image.width(), 4).copy()


def _flood(mask, seeds):
    """Scanline flood fill of `mask` from `seeds` [(x, y)]; returns the region."""
    h, w = mask.shape
    region = np.zeros((h, w), bool)
    stack = list(seeds)
    while stack:
        x, y = stack.pop()
        if region[y, x] or not mask[y, x]:
            continue
        left = x
        while left > 0 and mask[y, left-1] and not region[y, left-1]:
            left -= 1
        right = x
        while right+1 < w and mask[y, right+1] and not region[y, right+1]:
            right += 1
        region[y, left:right+1] = True
        for yy in (y-1, y+1):
            if 0 <= yy < h:
                runs = mask[yy, left:right+1] & ~region[yy, left:right+1]
                starts = np.flatnonzero(runs & ~np.r_[False, runs[:-1]])
                stack.extend((left+int(i), yy) for i in starts)
    return region


def build(path):
    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f"Cannot read image: {path}")
    rgb = pixels(image)[..., :3].astype(float)
    h, w = rgb.shape[:2]
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    bg = np.median(border, axis=0)
    distance = np.max(np.abs(rgb - bg), axis=2)
    tolerance = float(np.clip(np.percentile(np.max(abs(border-bg), axis=1), 60)+8, 10, 30))
    candidate = distance < tolerance
    # Background reached from the border; enclosed dark details (eyes, tie) stay.
    seeds = [(x, y) for y in (0, h-1) for x in range(w) if candidate[y, x]]
    seeds += [(x, y) for x in (0, w-1) for y in range(h) if candidate[y, x]]
    outside = _flood(candidate, seeds)
    # Background seen through a gap (between the hair and an arm) is enclosed,
    # so the border fill misses it. An enclosed patch of exactly the background
    # colour that is bigger than a speck is background too.
    rest = candidate & ~outside
    for y, x in np.argwhere(rest):
        if rest[y, x]:
            patch = _flood(rest, [(x, y)])
            rest &= ~patch
            if patch.sum() >= HOLE_MIN_PIXELS:
                outside |= patch
    alpha = (~outside).astype(float)
    # A two-pixel inward feather removes the matte without expanding a gray fringe.
    inner = ~outside
    for amount in (0.35, 0.8):
        padded = np.pad(inner, 1, constant_values=True)
        eroded = inner.copy()
        for dy, dx in ((0, 1), (2, 1), (1, 0), (1, 2)):
            eroded &= padded[dy:dy+h, dx:dx+w]
        alpha[inner & ~eroded] = amount
        inner = eroded
    # Undo background contribution only at the matte edge, preserving opaque art.
    edge = (alpha > 0) & (alpha < 1)
    rgb[edge] = np.clip((rgb[edge]-bg*(1-alpha[edge, None]))/alpha[edge, None], 0, 255)
    rgba = np.dstack((rgb, alpha*255)).astype(np.uint8)
    ys, xs = np.where(alpha > 0)
    if not len(xs):
        raise ValueError("No character found; use an image with a mostly uniform border.")
    rgba = np.ascontiguousarray(rgba[ys.min():ys.max()+1, xs.min():xs.max()+1])
    out = ROOT / "skin"
    out.mkdir(exist_ok=True)
    result = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0], QImage.Format_RGBA8888).copy()
    if not result.save(str(out / "mascot.png")):
        raise OSError("Could not save mascot.png")
    if not _bust(rgba).save(str(out / "mascot_bust.png")):
        raise OSError("Could not save mascot_bust.png")
    samples = rgba[rgba[..., 3] > 240, :3][::3]
    colors = samples.astype(float)/255
    mx, mn = colors.max(1), colors.min(1)
    sat = (mx-mn)/np.maximum(mx, .001)
    def choose(mask, fallback):
        subset = samples[mask]
        return QColor(*np.median(subset, axis=0).astype(int)).name() if len(subset) else fallback
    r, g, b = colors.T
    accent = QColor(choose((r > g*1.3) & (b > g*1.15) & (sat > .38), "#EE77B6"))
    # Accent text must remain legible even when a source has very dark eyes.
    while luminance(accent) < .34:
        accent = accent.lighter(110)
    palette = dict(accent=accent.name(), accent_soft=accent.lighter(135).name(),
                   highlight=choose((r > .65) & (g > .48) & (b < g*.85), "#F4CD86"),
                   secondary=choose((b > r*1.15) & (g > r*1.1) & (sat > .25), "#78B9D1"),
                   panel_tint=accent.darker(650).name())
    (out / "palette.json").write_text(json.dumps(palette, indent=2)+"\n", encoding="utf-8")
    for name in ("mascot.png", "mascot_bust.png", "palette.json"):
        print(f"Wrote {out / name}")
    print(json.dumps(palette))


def _bust(rgba):
    """Head and shoulders with soft top and bottom edges, so the mascot doesn't
    end in a hard straight line."""
    rows = round(rgba.shape[0]*BUST_FRACTION)
    bust = rgba[:rows].copy()
    fade = np.ones(rows)
    bottom, top = round(rows*FADE_BOTTOM), round(rows*FADE_TOP)
    fade[rows-bottom:] = np.linspace(1, 0, bottom)**1.5
    fade[:top] = np.linspace(0, 1, top)**.7
    bust[..., 3] = (bust[..., 3]*fade[:, None]).astype(np.uint8)
    bust = np.ascontiguousarray(bust)
    return QImage(bust.data, bust.shape[1], bust.shape[0], bust.strides[0],
                  QImage.Format_RGBA8888).copy()


def luminance(color):
    values = np.array(color.getRgbF()[:3])
    linear = np.where(values <= .04045, values/12.92, ((values+.055)/1.055)**2.4)
    return float(linear @ [.2126, .7152, .0722])


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            source = sys.argv[1]
        else:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
                source = winreg.QueryValueEx(key, "Wallpaper")[0]
        build(source)
    except (OSError, ValueError) as error:
        sys.exit(str(error))
