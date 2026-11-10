"""Draw the Deep Dusk Pomona icon (refined Solid fruit) and render every PNG the app ships.

Usage, from the repo root (needs the resvg-py renderer, which uv fetches):
    ~/.local/bin/uv run --no-project --with resvg-py python make_app_icons.py .

Writes static/icons/ (web icons + the three source SVGs), the iOS AppIcon set
(light, dark, tinted) and the iOS launch-screen splash. Every PNG is re-encoded
without an alpha channel, which the App Store requires.
"""
import sys, pathlib, resvg_py

ROOT = pathlib.Path(sys.argv[1])

# The fruit mark, drawn on a 24-unit grid (same paths as the in-app mark).
BODY = "M12 20.4c-3.1 0-5.6-2.6-5.6-5.9 0-3.2 2.5-5.7 5.6-5.7s5.6 2.5 5.6 5.7c0 3.3-2.5 5.9-5.6 5.9z"
STEM = "M12 9V6.2"
LEAF = "M12.5 7.1c.9-2.2 3.2-3.1 5.3-2.7.4 2.2-.7 4.3-2.8 4.7-1.4.3-2.5-.6-2.5-2z"
CREASE = "M12 10.2c-1.1 1.6-1.1 6.3 0 8.2"

INK, APRICOT, LEAF_BLUE, CREAM, DARK_TILE = "#0F1F3D", "#FFAC52", "#3F6AAE", "#FFFAF3", "#0E1729"


def mark(body, leaf, crease, cx=50, cy=48, s=3.5):
    """Designer's revision: leaf 15% bigger and set off the stem, crease 1.5, fruit centred at y=48."""
    return (
        f'<g transform="translate({cx} {cy}) scale({s}) translate(-12.1 -12.35)" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{BODY}" fill="{body}"/>'
        f'<path d="{STEM}" stroke="{body}" stroke-width="1.7" fill="none"/>'
        f'<g transform="translate(12.5 7.1) translate(.42 -.42) scale(1.15) translate(-12.5 -7.1)">'
        f'<path d="{LEAF}" fill="{leaf}"/></g>'
        f'<path d="{CREASE}" stroke="{crease}" stroke-width="1.5" fill="none"/>'
        f'</g>'
    )


def icon_svg(bg, body, leaf):
    # Full-bleed square: iOS and Android round the corners themselves.
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<rect width="100" height="100" fill="{bg}"/>{mark(body, leaf, bg)}</svg>'
    )


LIGHT = icon_svg(APRICOT, INK, LEAF_BLUE)
DARK = icon_svg(DARK_TILE, APRICOT, LEAF_BLUE)
TINTED = icon_svg("#000000", "#FFFFFF", "#B3B3B3")

# Launch screen: the icon as a rounded tile on Warm Cream, the colour the first screen opens on.
SPLASH = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2732 2732">'
    f'<rect width="2732" height="2732" fill="{CREAM}"/>'
    '<defs><clipPath id="t"><rect x="1146" y="1146" width="440" height="440" rx="98.6"/></clipPath></defs>'
    '<g clip-path="url(#t)"><g transform="translate(1146 1146) scale(4.4)">'
    f'<rect width="100" height="100" fill="{APRICOT}"/>{mark(INK, LEAF_BLUE, APRICOT)}</g></g></svg>'
)


def png(svg, width, out):
    data = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=width)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(data))
    print("wrote", out.relative_to(ROOT), width)


icons = ROOT / "static/icons"
appicon = ROOT / "ios-app/ios/App/App/Assets.xcassets/AppIcon.appiconset"
splash = ROOT / "ios-app/ios/App/App/Assets.xcassets/Splash.imageset"

# Source drawings, kept so the icon can be redrawn without this script.
(icons / "pomona-icon.svg").write_text(LIGHT)
(icons / "pomona-icon-dark.svg").write_text(DARK)
(icons / "pomona-icon-tinted.svg").write_text(TINTED)

png(LIGHT, 192, icons / "icon-192.png")
png(LIGHT, 512, icons / "icon-512.png")
png(LIGHT, 1024, appicon / "AppIcon-512@2x.png")
png(DARK, 1024, appicon / "AppIcon-dark-1024.png")
png(TINTED, 1024, appicon / "AppIcon-tinted-1024.png")
for name in ("splash-2732x2732.png", "splash-2732x2732-1.png", "splash-2732x2732-2.png"):
    png(SPLASH, 2732, splash / name)


# --- App Store rule: icons must have no alpha channel. Re-encode every PNG as plain RGB. ---
import zlib, struct


def to_rgb(path):
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(raw):
        n = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind, body = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            ihdr = body
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h, depth, ctype = struct.unpack(">IIBB", ihdr[:10])
    if ctype == 2:
        return
    assert depth == 8 and ctype == 6, (depth, ctype)
    data, bpp, stride = zlib.decompress(idat), 4, w * 4
    prev, out, i = bytearray(stride), bytearray(), 0
    for _ in range(h):
        f = data[i]; line = bytearray(data[i + 1:i + 1 + stride]); i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        out += b"\x00" + bytes(v for k, v in enumerate(line) if k % 4 != 3)

    def chunk(k, d):
        return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(out), 9)) + chunk(b"IEND", b""))
    print("flattened", path.relative_to(ROOT))


for p in [icons / "icon-192.png", icons / "icon-512.png", *appicon.glob("*.png"), *splash.glob("*.png")]:
    to_rgb(p)
