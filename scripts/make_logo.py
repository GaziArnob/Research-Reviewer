"""Cut the logo assets out of the designer's artwork (brand/neural-lens-source.jpg) into transparent PNGs.

The artwork has a light half (big mark + dark wordmark) and a dark half (small mark + light wordmark). The backgrounds
are removed by "difference matting" against the known background colour, so the PNGs sit cleanly on any surface.
Run:  python scripts/make_logo.py        (writes webapp/static/brand/*.png and webapp/static/favicon.png)
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand" / "neural-lens-source.jpg"
OUT = ROOT / "webapp" / "static" / "brand"
H = 120  # pixel height of the horizontal logos (shown at 40 px or less: sharp on 3x screens)


def matte(rgb: np.ndarray, bg: np.ndarray, dark_bg: bool) -> Image.Image:
    """RGBA from a picture on a flat background colour: exact when composited back on that colour."""
    p = rgb.astype(np.float64)
    if dark_bg:
        a_raw = ((p - bg) / (255 - bg)).max(axis=2)
    else:
        a_raw = ((bg - p) / bg).max(axis=2)
    a = np.where(a_raw < 0.04, 0.0, np.clip(a_raw, 0, 1))  # drop JPEG noise only; the colour below uses the same alpha
    safe = np.maximum(a, 1e-3)[..., None]
    fg = np.clip(bg + (p - bg) / safe, 0, 255)
    return Image.fromarray(np.dstack([fg, a * 255]).astype(np.uint8), "RGBA")


def bands(mask: np.ndarray, axis: int, gap: int) -> list[tuple[int, int]]:
    """Runs of rows (axis=1) or columns (axis=0) that contain content, merging runs closer than `gap`."""
    on = np.where(mask.any(axis=axis))[0]
    out = []
    for i in on:
        if out and i - out[-1][1] <= gap:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(a, b + 1) for a, b in out]


def content_mask(rgb: np.ndarray, bg: np.ndarray, thr: float = 40) -> np.ndarray:
    return np.abs(rgb.astype(float) - bg).max(axis=2) > thr


def shift_hue(img: Image.Image) -> Image.Image:
    """Move the artwork's cyan/blue range to violet/pink so the logo matches the site's gradient (white and grey stay as they are)."""
    a = img.getchannel("A")
    h, sat, val = img.convert("RGB").convert("HSV").split()
    deg = np.asarray(h).astype(float) * 360 / 255
    s_ = np.asarray(sat).astype(float) / 255
    hit = (deg >= 170) & (deg <= 290) & (s_ > 0.25)
    moved = np.where(hit, 266 + (np.clip(deg, 190, 275) - 190) * 0.68, deg)
    h2 = Image.fromarray(np.clip(moved * 255 / 360, 0, 255).astype(np.uint8), "L")
    s2 = Image.fromarray(np.where(hit, s_ * 0.74, s_).__mul__(255).clip(0, 255).astype(np.uint8), "L")  # lighter on dark backgrounds
    out = Image.merge("HSV", (h2, s2, val)).convert("RGB")
    out.putalpha(a)
    return out


def put(canvas: Image.Image, img: Image.Image, xy: tuple[int, int]) -> None:
    """Alpha-composite img onto canvas (paste with an RGBA mask would square the transparency)."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(img, xy)
    canvas.alpha_composite(layer)


def fit_height(img: Image.Image, h: int) -> Image.Image:
    return img.resize((round(img.width * h / img.height), h), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(Image.open(SRC).convert("RGB"))
    split = next(y for y in range(rgb.shape[0]) if rgb[y, 5].sum() < 150)  # first dark row = top of the dark half
    light, dark = rgb[:split], rgb[split:]
    bg_l = np.median(light[:20, :20].reshape(-1, 3), axis=0)
    bg_d = np.median(dark[-20:, :20].reshape(-1, 3), axis=0)

    # light half: big mark above, wordmark below
    rows = bands(content_mask(light, bg_l), 1, 40)
    mark_rows, word_rows = rows[0], rows[1]

    def crop(arr, y0, y1, bg):
        m = content_mask(arr[y0:y1], bg)
        x0, x1 = bands(m, 0, 10_000)[0]
        pad = 6
        return arr[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]

    big_mark = shift_hue(matte(crop(light, *mark_rows, bg_l), bg_l, False))
    word_light = matte(crop(light, *word_rows, bg_l), bg_l, False)

    # dark half: one row with the small mark and the wordmark, then the tagline
    drows = bands(content_mask(dark, bg_d), 1, 40)
    lockup = crop(dark, *drows[0], bg_d)
    cols = bands(content_mask(lockup, bg_d), 0, 40)  # [mark, wordmark]
    small_mark = shift_hue(matte(lockup[:, cols[0][0]:cols[0][1]], bg_d, True))
    lockup_dark = shift_hue(matte(lockup, bg_d, True))

    # a horizontal lockup for light surfaces: small mark + the dark wordmark of the light half
    # the checkmark in the small mark is white (made for dark surfaces): make it navy so it shows on light ones
    arr = np.asarray(small_mark).copy()
    white = (arr[..., :3].min(axis=2) > 225) & (arr[..., 3] > 120)
    arr[white, :3] = (15, 23, 42)
    sm, wl = fit_height(Image.fromarray(arr, "RGBA"), H), fit_height(word_light, round(H * 0.62))
    canvas = Image.new("RGBA", (sm.width + 36 + wl.width, H), (0, 0, 0, 0))
    put(canvas, sm, (0, 0))
    put(canvas, wl, (sm.width + 36, (H - wl.height) // 2))

    fit_height(lockup_dark, H).save(OUT / "logo-dark.png", optimize=True)
    canvas.save(OUT / "logo-light.png", optimize=True)
    fit_height(big_mark, 360).save(OUT / "mark-large.png", optimize=True)

    # icons: the small mark on the dark brand colour, rounded
    for size, name in ((64, "favicon.png"), (192, "icon-192.png"), (512, "icon-512.png")):
        tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(tile).rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=(13, 14, 18, 255))
        mk = fit_height(small_mark, round(size * 0.66))
        put(tile, mk, ((size - mk.width) // 2, (size - mk.height) // 2))
        tile.save((OUT.parent if name == "favicon.png" else OUT) / name, optimize=True)
    print("split", split, "bg light", bg_l.astype(int), "bg dark", bg_d.astype(int))
    print("big mark", mark_rows, "wordmark", word_rows, "dark rows", drows, "dark cols", cols)


if __name__ == "__main__":
    main()
