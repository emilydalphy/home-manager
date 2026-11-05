"""Pomona plates: reusable food parts. A plate is a list of parts drawn in order."""
import hashlib, math, random


def blob(rng, cx, cy, rx, ry, n=10, j=0.12):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        k = 1 + rng.uniform(-j, j)
        pts.append((cx + rx * k * math.cos(a), cy + ry * k * math.sin(a)))
    d = ""
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        m = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        d += (f"M{m[0]:.1f} {m[1]:.1f}" if i == 0 else "")
    for i in range(1, n + 1):
        p, q = pts[i % n], pts[(i + 1) % n]
        m = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        d += f"Q{p[0]:.1f} {p[1]:.1f} {m[0]:.1f} {m[1]:.1f}"
    return d + "z"


def spots(rng, n, r, cx=50, cy=50):
    off = rng.uniform(0, 6.28)
    for i in range(n):
        d = r * math.sqrt((i + 0.5) / n) * rng.uniform(0.9, 1.05)
        a = off + i * 2.39996
        yield cx + d * math.cos(a), cy + d * math.sin(a), rng.uniform(0, 180)


def grains(rng, cx, cy, rx, ry, n, col):
    o = ""
    for _ in range(n):
        a, d = rng.uniform(0, 6.28), math.sqrt(rng.uniform(0, 1))
        x, y = cx + rx * d * math.cos(a), cy + ry * d * math.sin(a)
        o += f'<rect x="{x-1.5:.1f}" y="{y-.55:.1f}" width="3" height="1.1" rx=".55" fill="{col}" transform="rotate({rng.uniform(0,180):.0f} {x:.1f} {y:.1f})"/>'
    return o


def base_op(rng, o):
    k = o[0]
    if k == "well":
        return '<circle cx="50" cy="50" r="37" fill="#F4F1EA"/>'
    if k == "pool":  # sauce or broth, with a soft shine
        c = o[1]; r = o[2] if len(o) > 2 else 37
        return (f'<circle cx="50" cy="50" r="{r}" fill="{c}"/>'
                f'<path d="M{50-r*.62:.1f} {50-r*.5:.1f}a{r*.8:.1f} {r*.8:.1f} 0 0 1 {r*.75:.1f} -{r*.26:.1f}" fill="none" stroke="#FFFFFF" stroke-width="2.4" stroke-linecap="round" opacity=".22"/>')
    if k == "rice":  # a soft mound with visible grains
        _, cx, cy, rx, ry, c, g = o
        return (f'<path d="{blob(rng,cx,cy,rx,ry)}" fill="{c}"/>' + grains(rng, cx, cy, rx * .85, ry * .85, int(rx * ry / 7), g))
    if k == "mash":  # a soft stippled mound
        _, cx, cy, rx, ry, c, g = o
        s = f'<path d="{blob(rng,cx,cy,rx,ry,12,.08)}" fill="{c}"/>'
        for x, y, _r in spots(rng, 40, min(rx, ry) * .95, cx, cy):
            s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r=".8" fill="{g}"/>'
        return s
    if k == "band":  # a thick stripe of topping across a rice bowl
        _, y, c, g = o
        half = math.sqrt(max(33 ** 2 - (y - 50) ** 2, 1)) - 6
        s = f'<path d="M{50-half:.1f} {y}H{50+half:.1f}" stroke="{c}" stroke-width="15" stroke-linecap="round"/>'
        return s + grains(rng, 50, y, half, 5, 34, g)
    if k == "strips":  # shredded cabbage, kimchi, onion, ginger
        _, c, n, ln, w, r = o[:6]
        cx = o[6] if len(o) > 6 else 50; cy = o[7] if len(o) > 7 else 50
        s = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            b = rng.uniform(-ln * .55, ln * .55)
            s += f'<path d="M{-ln/2:.1f} 0Q0 {b:.1f} {ln/2:.1f} 0" transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})" fill="none" stroke="{c}" stroke-width="{w}" stroke-linecap="round"/>'
        return s
    if k == "slices":  # thin meat slices with a pale fat edge
        _, c, e, n, r = o
        s = ""
        for x, y, a in spots(rng, n, r):
            s += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><path d="M-9 -3Q0 -7 9 -3Q11 2 8 5Q0 8 -8 5Q-11 1 -9 -3z" fill="{c}"/>'
                  f'<path d="M-8 -3.2Q0 -7 8 -3.2" fill="none" stroke="{e}" stroke-width="1.8" stroke-linecap="round"/></g>')
        return s
    if k == "nuggets":  # glazed pieces with a highlight
        _, c, h, n, sz, r = o[:6]
        cx = o[6] if len(o) > 6 else 50; cy = o[7] if len(o) > 7 else 50
        s = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            s += f'<path d="{blob(rng,x,y,sz,sz*.8,8,.16)}" fill="{c}"/><ellipse cx="{x-sz*.3:.1f}" cy="{y-sz*.3:.1f}" rx="{sz*.32:.1f}" ry="{sz*.18:.1f}" fill="{h}" opacity=".8" transform="rotate(-25 {x:.1f} {y:.1f})"/>'
        return s
    if k == "cubes":
        _, c, n, sz, r = o[:5]
        cx = o[5] if len(o) > 5 else 50; cy = o[6] if len(o) > 6 else 50
        s = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            s += f'<rect x="{x-sz/2:.1f}" y="{y-sz/2:.1f}" width="{sz}" height="{sz}" rx="1.2" fill="{c}" transform="rotate({a:.0f} {x:.1f} {y:.1f})"/>'
        return s
    if k == "dots":
        _, c, n, sz, r = o[:5]
        cx = o[5] if len(o) > 5 else 50; cy = o[6] if len(o) > 6 else 50
        return "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{sz}" fill="{c}"/>' for x, y, _ in spots(rng, n, r, cx, cy))
    if k == "rings":  # sliced scallion or chili
        _, c, n, r = o[:4]
        cx = o[4] if len(o) > 4 else 50; cy = o[5] if len(o) > 5 else 50
        return "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.8" fill="none" stroke="{c}" stroke-width="1.3"/>' for x, y, _ in spots(rng, n, r, cx, cy))
    if k == "leaves":
        _, c, n, sz, r = o[:5]
        s = ""
        for x, y, a in spots(rng, n, r):
            s += f'<path d="M{-sz} 0Q0 {-sz*.7:.1f} {sz} 0Q0 {sz*.7:.1f} {-sz} 0z" fill="{c}" transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"/>'
        return s
    if k == "frisee":  # curly lettuce
        _, c, n, sz, r = o
        s = ""
        for x, y, a in spots(rng, n, r):
            s += f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round">'
            for t in (-48, -24, 0, 24, 48):
                s += f'<path d="M0 0Q{sz*.5:.1f} {t*.08:.1f} {sz:.1f} {t*.2:.1f}"/><path d="M0 0Q{-sz*.5:.1f} {t*.08:.1f} {-sz:.1f} {t*.2:.1f}"/>'
            s += "</g>"
        return s
    if k == "cashews":
        _, c, n, r = o
        return "".join(f'<path d="M-3.4 1A3.8 3.8 0 0 1 3.4 1" transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})" fill="none" stroke="{c}" stroke-width="2.8" stroke-linecap="round"/>' for x, y, a in spots(rng, n, r))
    if k == "poached":
        _, x, y = o
        return (f'<path d="{blob(rng,x,y,13,11.5,10,.1)}" fill="#FFFAF0"/><circle cx="{x}" cy="{y}" r="6" fill="#EFB02E"/>'
                f'<ellipse cx="{x-1.8}" cy="{y-2}" rx="1.9" ry="1.1" fill="#FBE29A"/>')
    if k == "wontons":
        _, n, r = o
        s = ""
        for x, y, a in spots(rng, n, r):
            s += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><path d="M-10 3Q-9 -6 0 -7Q9 -6 10 3Q12 8 6 6Q0 3 -6 6Q-12 8 -10 3z" fill="#FBF3DF"/>'
                  f'<ellipse cx="0" cy="-1.5" rx="5.2" ry="3.6" fill="#E8C49B"/><path d="M-3.5 2.5Q0 4 3.5 2.5" fill="none" stroke="#E2D2B2" stroke-width="1" stroke-linecap="round"/></g>')
        return s
    if k == "bokchoy":
        _, n, r = o
        s = ""
        for x, y, a in spots(rng, n, r):
            s += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><path d="M0 -3C8 -13 16 -8 13 0C16 8 8 13 0 3z" fill="#5F8445"/>'
                  f'<path d="M-11 -2.6Q0 -4 3 0Q0 4 -11 2.6Q-13 0 -11 -2.6z" fill="#E9EFD0"/></g>')
        return s
    if k == "baguette":  # a split baguette, filling showing
        s = '<g transform="rotate(-28 50 50)">'
        for dy in (-14, 14):
            s += (f'<path d="M23 {50+dy-9}Q50 {50+dy-13} 77 {50+dy-9}Q83 {50+dy} 77 {50+dy+9}Q50 {50+dy+13} 23 {50+dy+9}Q17 {50+dy} 23 {50+dy-9}z" fill="#D9A45C"/>'
                  f'<path d="M23 {50+dy-3}H77" stroke="#F3E3C0" stroke-width="5" stroke-linecap="round"/>'
                  f'<path d="M26 {50+dy+0.5}H74" stroke="#E8894A" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="7 3"/>'
                  f'<path d="M28 {50+dy-5}H72" stroke="#6E9150" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="4 7"/>')
            for x in (30, 44, 58, 70):
                s += f'<path d="M{x} {50+dy+4}l6 4" stroke="#B9823F" stroke-width="1.8" stroke-linecap="round"/>'
        return s + "</g>"
    if k == "flautas":  # crisp rolled tubes, crema, salsa
        s = '<g transform="rotate(-18 50 50)">'
        for dy in (-15, 0, 15):
            s += (f'<rect x="20" y="{50+dy-6.5}" width="60" height="13" rx="6.5" fill="#C98A3F"/>'
                  f'<rect x="20" y="{50+dy-6.5}" width="60" height="4" rx="2" fill="#E0AE63"/>'
                  f'<ellipse cx="79" cy="{50+dy}" rx="2.8" ry="5" fill="#8A5A33"/>')
        s += '<path d="M27 38l9 22 9-22 9 22 9-22 9 22" fill="none" stroke="#FFF8EC" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></g>'
        return s
    if k == "phyllo":  # a crisp phyllo square, cut diamonds, sugar
        s = ('<g transform="rotate(-6 50 50)"><rect x="23" y="27" width="54" height="46" rx="4" fill="#CE9848"/>'
             '<rect x="25" y="25" width="50" height="44" rx="4" fill="#E6BB72"/>')
        for x in (26, 42, 58):
            s += f'<path d="M{x} 25l16 44M{x+16} 25l-16 44" stroke="#C48C3E" stroke-width="1.5" opacity=".75"/>'
        s += "</g>"
        for x, y, _ in spots(rng, 46, 25):
            s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r=".9" fill="#FFFFFF"/>'
        for x, y, _ in spots(rng, 12, 22):
            s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r=".8" fill="#7A4A2B"/>'
        return s
    if k == "loaf":  # a slice of plain cake, crumb showing
        s = ('<path d="M26 66V42Q26 30 40 30H60Q74 30 74 42V66Q74 70 70 70H30Q26 70 26 66z" fill="#B97C35"/>'
             '<path d="M30 66V43Q30 34 41 34H59Q70 34 70 43V66z" fill="#F2D795"/>')
        for x, y, _ in spots(rng, 24, 17, 50, 51):
            s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1" fill="#E3BE72"/>'
        return s + '<path d="M36 30Q50 37 64 30" fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" opacity=".85"/>'
    if k == "patties":  # hand-pressed ovals with finger ridges, on lettuce
        _, c, h = o
        s = ""
        for x, y, a in ((35, 40, 28), (64, 40, -24), (36, 62, -18), (64, 62, 20)):
            s += (f'<path d="{blob(rng,x,y,15,10,10,.1)}" fill="#7FA653"/>'
                  f'<g transform="rotate({a} {x} {y})"><ellipse cx="{x}" cy="{y}" rx="11" ry="6.4" fill="{c}"/>'
                  f'<path d="M{x-6} {y-3}q1.5 3 0 6M{x-1} {y-4}q1.5 4 0 8M{x+4} {y-3.5}q1.5 3.5 0 7" fill="none" stroke="{h}" stroke-width="1.5" stroke-linecap="round"/></g>')
        return s + '<path d="M50 50l9-3a9 9 0 0 1-3 10z" fill="#F1D65A"/>'
    raise ValueError(k)



def op(rng, o):
    k = o[0]
    if k == "rounds":  # sliced potato, tomato, cucumber
        _, c, i, n, sz, r = o
        return "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{sz}" fill="{c}"/><circle cx="{x:.1f}" cy="{y:.1f}" r="{sz*.68:.1f}" fill="{i}"/>' for x, y, _ in spots(rng, n, r))
    if k == "yolk":
        _, x, y = o
        return (f'<circle cx="{x}" cy="{y}" r="10" fill="#F2B62E"/><circle cx="{x}" cy="{y}" r="10" fill="none" stroke="#E29C1C" stroke-width="1"/>'
                f'<ellipse cx="{x-3}" cy="{y-3.5}" rx="3" ry="1.8" fill="#FCE5A0"/>')
    if k == "ringbread":
        s = '<circle cx="50" cy="50" r="23" fill="none" stroke="#A9682A" stroke-width="16"/><circle cx="50" cy="50" r="23" fill="none" stroke="#C98A3F" stroke-width="12"/>'
        for i in range(70):
            a = rng.uniform(0, 6.28); d = rng.uniform(17, 29)
            x, y = 50 + d * math.cos(a), 50 + d * math.sin(a)
            s += f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="1.1" ry=".65" fill="#F7E8C6" transform="rotate({rng.uniform(0,180):.0f} {x:.1f} {y:.1f})"/>'
        return s + '<rect x="44" y="44" width="12" height="12" rx="2" fill="#FFF8EA" transform="rotate(14 50 50)"/>'
    if k == "skewers":  # optional: colour or list of colours, ridge colour ("" for none), shape "block", "ball" or "long"
        plain = len(o) == 1
        cols = o[1] if len(o) > 1 else "#E3A24F"
        cols = [cols] if isinstance(cols, str) else cols
        ridge = o[2] if len(o) > 2 else "#A9662A"
        shape = o[3] if len(o) > 3 else "block"
        s = ""; j = 0
        for dy in (-12, 12):
            y = 50 + dy
            s += f'<g transform="rotate(-24 50 50)"><path d="M16.5 {y}H83.5" stroke="#B98A5E" stroke-width="1.8" stroke-linecap="round"/>'
            if shape == "long":  # one pressed kofta along the stick
                s += f'<rect x="19" y="{y-6.5}" width="56" height="13" rx="6.5" fill="{cols[j % len(cols)]}"/>'
                j += 1
                if ridge:
                    for x in (29, 41, 53, 65):
                        s += f'<path d="M{x} {y-5.5}q2.4 5.5 0 11" fill="none" stroke="{ridge}" stroke-width="1.5" stroke-linecap="round"/>'
                s += f'<path d="M25 {y-3.6}H68" stroke="#FFFFFF" stroke-width="1.5" stroke-linecap="round" opacity=".25"/>'
            else:
                for x in (24, 38, 52, 66):
                    c = cols[j % len(cols)]; j += 1
                    if shape == "ball":
                        s += f'<circle cx="{x}" cy="{y}" r="7" fill="{c}"/><ellipse cx="{x-2}" cy="{y-2.6}" rx="2.4" ry="1.4" fill="#FFFFFF" opacity=".3"/>'
                    else:
                        s += f'<rect x="{x-6.5}" y="{y-7}" width="13" height="14" rx="4" fill="{c}"/>'
                        if ridge:
                            s += f'<path d="M{x-3} {y-6}v12M{x+3} {y-6}v12" stroke="{ridge}" stroke-width="1.5" stroke-linecap="round"/>'
            s += "</g>"
        return s + ('<path d="M36 72q5-6 12-1q-2 7-9 6q-3-1-3-5z" fill="#FFF8EA"/>' if plain else "")
    if k == "tacos":  # optional: filling, fleck1, fleck2, lettuce; "" drops a fleck or the lettuce
        fill = o[1] if len(o) > 1 else "#5E2F1C"
        f1 = o[2] if len(o) > 2 else ("#C9503A" if len(o) == 1 else "")
        f2 = o[3] if len(o) > 3 else ("#F2CE5A" if len(o) == 1 else "")
        green = o[4] if len(o) > 4 else "#7FA653"
        s = ""
        # the bare default keeps its approved spot; filled tacos sit a little further in so they stay inside the well
        for x, y, rot in (((34, 54, -16), (56, 46, -16), (72, 60, -16)) if len(o) == 1 else ((35, 54, -16), (54, 46, -16), (66, 58, -16))):
            s += f'<g transform="rotate({rot} {x} {y})">'
            if green:
                s += f'<path d="M{x-16} {y+2}a16 15 0 0 1 32 0z" fill="{green}" transform="translate(0 -5)"/>'
            s += f'<path d="M{x-15} {y+2}a15 13 0 0 1 30 0z" fill="{fill}" transform="translate(0 -2)"/>'
            if f1:
                s += f'<circle cx="{x-5}" cy="{y-9}" r="1.8" fill="{f1}"/>'
            if f2:
                s += f'<circle cx="{x+5}" cy="{y-8}" r="1.8" fill="{f2}"/>'
            s += (f'<path d="M{x-17} {y+9}a17 16 0 0 1 34 0z" fill="#E7B553"/><path d="M{x-10} {y+6}h3M{x+1} {y+3}h3" stroke="#C98F35" stroke-width="1.4" stroke-linecap="round"/></g>')
        return s
    if k == "sandwiches":
        s = ""
        for x, y, r in ((37, 42, 15), (63, 60, -20)):
            s += (f'<path d="{blob(rng,x,y,20,18,10,.06)}" fill="#FFFAF0"/>'
                  f'<rect x="{x-17}" y="{y-17}" width="34" height="34" rx="3" fill="#F2B83B" transform="rotate({r+45} {x} {y})"/>'
                  f'<circle cx="{x}" cy="{y}" r="19" fill="#C98F4A"/><circle cx="{x}" cy="{y}" r="15.5" fill="#DDAE6A"/>')
            for dx, dy, _ in spots(rng, 16, 13, x, y):
                s += f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r=".8" fill="#F7EAD0"/>'
        return s
    if k == "toast":  # optional: topping, speck, fleck; "" drops any of them, so ["toast", "", "", ""] is plain toast
        top = o[1] if len(o) > 1 else "#F1CF5E"
        speck = o[2] if len(o) > 2 else "#F9E59A"
        fleck = o[3] if len(o) > 3 else "#6E9150"
        s = ""
        for x, y, r in ((38, 48, -12), (62, 54, 10)):
            s += (f'<g transform="rotate({r} {x} {y})"><path d="M{x-18} {y+18}V{y-10}q0-10 9-10h18q9 0 9 10V{y+18}q0 3-3 3H{x-15}q-3 0-3-3z" fill="#B97C35"/>'
                  f'<path d="M{x-15} {y+18}V{y-9}q0-8 7-8h16q7 0 7 8V{y+18}z" fill="#E9C27C"/></g>')
            d = blob(rng, x, y + 1, 12.5, 13, 10, .14)
            if top:
                s += f'<path d="{d}" fill="{top}"/>'
            for dx, dy, _ in spots(rng, 8, 10, x, y + 1):
                if speck:
                    s += f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="1.4" fill="{speck}"/>'
            for dx, dy, _ in spots(rng, 4, 9, x, y + 1):
                if fleck:
                    s += f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="1.1" fill="{fleck}"/>'
        return s
    if k == "cups":  # optional: fleck1, fleck2, fill, crust; "" drops a fleck
        f1 = o[1] if len(o) > 1 else "#D9573A"
        f2 = o[2] if len(o) > 2 else "#6E9150"
        fill = o[3] if len(o) > 3 else "#F3CE5C"
        crust = o[4] if len(o) > 4 else "#C98F35"
        s = ""
        for x, y in ((36, 40), (64, 42), (50, 65)):
            s += f'<circle cx="{x}" cy="{y}" r="14" fill="{crust}"/><path d="{blob(rng,x,y,11.5,11.5,9,.08)}" fill="{fill}"/>'
            for dx, dy, _ in spots(rng, 4, 8, x, y):
                if f1:
                    s += f'<rect x="{dx-1.5:.1f}" y="{dy-1.5:.1f}" width="3" height="3" rx=".8" fill="{f1}"/>'
            for dx, dy, _ in spots(rng, 4, 8, x, y):
                if f2:
                    s += f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="1" fill="{f2}"/>'
        return s
    if k == "glass":  # a tall drink, seen from the side
        _, c, top = o
        return (f'<path d="M35 25h30l-4 50a5 5 0 0 1-5 5H44a5 5 0 0 1-5-5z" fill="{c}"/>'
                f'<path d="M35 25h30l-.9 11H35.9z" fill="{top}"/>'
                '<path d="M41 40l2 33" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" opacity=".35"/>'
                '<rect x="57" y="13" width="3.4" height="30" rx="1.7" fill="#FFFFFF" stroke="#E6E3DB" stroke-width=".6" transform="rotate(12 59 28)"/>')
    if k == "wrap":  # a wrap or burrito cut in half, fillings showing
        _, c, f1, f2 = o
        out = ""
        for x, y, r in ((36, 40, -28), (62, 60, -28)):
            out += (f'<g transform="rotate({r} {x} {y})"><rect x="{x-21}" y="{y-11}" width="42" height="22" rx="11" fill="{c}"/>'
                    f'<path d="M{x-14} {y-7}Q{x} {y-4} {x+12} {y-7}M{x-14} {y+6}Q{x} {y+3} {x+12} {y+6}" fill="none" stroke="#FFFFFF" stroke-width="1.2" opacity=".35"/>'
                    f'<ellipse cx="{x+19}" cy="{y}" rx="6" ry="10" fill="#FFF6E3"/><ellipse cx="{x+19}" cy="{y-2.5}" rx="4" ry="3.6" fill="{f1}"/>'
                    f'<ellipse cx="{x+19}" cy="{y+3.2}" rx="4" ry="3.2" fill="{f2}"/></g>')
        return out
    if k == "pizza":  # crust, sauce, melted cheese, toppings
        _, sauce, t1, t2 = o
        out = f'<circle cx="50" cy="50" r="36" fill="#C9944E"/><circle cx="50" cy="50" r="31" fill="{sauce}"/>'
        for x, y, _a in spots(rng, 9, 22):
            out += f'<path d="{blob(rng,x,y,9,8,8,.2)}" fill="#F6DF9A"/>'
        for x, y, _a in spots(rng, 6, 24):
            out += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.6" fill="{t1}"/>'
        for x, y, a in spots(rng, 5, 25):
            out += f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="3.6" ry="2.1" fill="{t2}" transform="rotate({a:.0f} {x:.1f} {y:.1f})"/>'
        return out + '<path d="M50 14v72M14 50h72M24.5 24.5l51 51M75.5 24.5l-51 51" stroke="#A9702F" stroke-width=".8" opacity=".4"/>'
    if k == "pancakes":  # a stack with butter and syrup
        _, c, syrup = o
        out = ""
        for dx, dy in ((-4, 6), (3, 3), (-1, 0)):
            out += f'<circle cx="{50+dx}" cy="{50+dy}" r="27" fill="#B9823F"/><circle cx="{50+dx}" cy="{49+dy}" r="25.5" fill="{c}"/>'
        return (out + f'<path d="{blob(rng,49,48,17,15,10,.2)}" fill="{syrup}"/><path d="M60 58q5 7 2 13" fill="none" stroke="{syrup}" stroke-width="4" stroke-linecap="round"/>'
                '<rect x="43" y="41" width="12" height="10" rx="2" fill="#FBE9A6" transform="rotate(12 49 46)"/>')
    if k == "flatbread":  # a round bread with char spots
        _, c, char = o
        out = f'<path d="{blob(rng,50,50,33,31,14,.05)}" fill="{c}"/>'
        for x, y, a in spots(rng, 13, 26):
            out += f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rng.uniform(2,4.5):.1f}" ry="{rng.uniform(1.4,2.6):.1f}" fill="{char}" opacity=".75" transform="rotate({a:.0f} {x:.1f} {y:.1f})"/>'
        return out
    if k == "dipbowl":  # a small bowl of dip with a swirl and oil; dippers go around it
        _, c, oil = o
        return (f'<circle cx="50" cy="50" r="21" fill="#FFFFFF" stroke="#E0DBD0"/><circle cx="50" cy="50" r="17.5" fill="{c}"/>'
                f'<path d="M40 50a10 10 0 1 1 10 10a6 6 0 1 1 -3-11" fill="none" stroke="{oil}" stroke-width="2.4" stroke-linecap="round"/>')
    if k == "bowl":  # a small bowl holding loose food; add parts with r up to 20
        _, c = o
        return f'<circle cx="50" cy="50" r="34" fill="#FFFFFF" stroke="#E0DBD0"/><circle cx="50" cy="50" r="30" fill="{c}"/>'
    if k == "pastries":  # stuffed pastries; style tri or half
        _, c, shade, style = o
        out = ""
        for x, y, r in ((36, 40, -12), (64, 42, 16), (50, 66, 180)):
            if style == "tri":
                out += (f'<g transform="rotate({r} {x} {y})"><path d="M{x} {y-14}q2 0 3 2l11 20q1 3-2 3H{x-12}q-3 0-2-3l11-20q1-2 3-2z" fill="{c}"/>'
                        f'<path d="M{x} {y-9}V{y+8}" stroke="{shade}" stroke-width="1.6" stroke-linecap="round"/></g>')
            else:
                out += (f'<g transform="rotate({r} {x} {y})"><path d="M{x-15} {y+6}a15 15 0 0 1 30 0q0 2-2 2H{x-13}q-2 0-2-2z" fill="{c}"/>'
                        f'<path d="M{x-12} {y+6.5}h24" stroke="{shade}" stroke-width="2" stroke-dasharray="2.4 2.4" stroke-linecap="round"/></g>')
        return out
    if k == "stuffed":  # halved stuffed vegetables, filling showing
        _, skin, fill = o
        out = ""
        for x, y, r in ((50, 33, 6), (50, 51, -5), (50, 69, 5)):
            out += (f'<g transform="rotate({r} {x} {y})"><rect x="{x-25}" y="{y-8}" width="50" height="16" rx="8" fill="{skin}"/>'
                    f'<rect x="{x-20}" y="{y-5}" width="40" height="10" rx="5" fill="{fill}"/>' )
            for dx, dy, _a in spots(rng, 9, 4, x, y):
                out += f'<circle cx="{(dx-x)*4+x:.1f}" cy="{dy:.1f}" r=".9" fill="#FFFFFF" opacity=".55"/>'
            out += "</g>"
        return out
    if k == "cutlet":  # a large breaded or seared piece
        _, c, h, cx, cy, rx, ry = o
        out = f'<path d="{blob(rng,cx,cy,rx,ry,12,.08)}" fill="{c}"/>'
        for x, y, _a in spots(rng, 26, min(rx, ry) * .95, cx, cy):
            out += f'<circle cx="{x+(x-cx)*.4:.1f}" cy="{y:.1f}" r=".85" fill="{h}"/>'
        return out
    if k == "block":  # a soft block, tofu or cheese, with a shaded side
        _, c, shade, x, y, w, h = o
        return (f'<rect x="{x-w/2}" y="{y-h/2+3}" width="{w}" height="{h}" rx="3.5" fill="{shade}"/><rect x="{x-w/2}" y="{y-h/2}" width="{w}" height="{h}" rx="3.5" fill="{c}"/>')
    if k == "rolls":  # short crisp fried rolls
        _, c, h = o
        out = ""
        for x, y, r in ((37, 36, 30), (63, 38, -24), (35, 62, -18), (64, 63, 26)):
            out += (f'<g transform="rotate({r} {x} {y})"><rect x="{x-14}" y="{y-6.5}" width="28" height="13" rx="6" fill="{c}"/>'
                    f'<path d="M{x-10} {y-3}q3 3 0 6M{x-3} {y-3.6}q3 3.5 0 7M{x+4} {y-3.6}q3 3.5 0 7" fill="none" stroke="{h}" stroke-width="1.3" stroke-linecap="round"/></g>')
        return out
    if k == "wedges":  # fruit slices with skin
        _, c, skin, n, r = o
        return "".join(f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><path d="M-12 3a13 13 0 0 1 24 0q-12 5-24 0z" fill="{c}"/><path d="M-12 3a13 13 0 0 1 24 0" fill="none" stroke="{skin}" stroke-width="2.2" stroke-linecap="round"/></g>' for x, y, a in spots(rng, n, r))
    if k == "noodles":  # long flowing strands twirled into a nest
        _, c, shade, w, r = o
        out = ""
        turns = 8
        for layer, col, wid in ((0, shade, w + 1.1), (1, c, w)):
            st = random.Random(rng.random() if layer == 0 else 0)
            for t in range(turns):
                a0 = t / turns * 6.283
                rad = r * (0.32 + 0.68 * (t % 4 + 1) / 4)
                pts = []
                for i in range(16):
                    a = a0 + i * 0.3
                    q = rad * (1 + 0.1 * math.sin(i * 0.9 + t))
                    pts.append((50 + q * math.cos(a), 50 + q * 0.94 * math.sin(a)))
                d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
                for i in range(1, len(pts) - 1):
                    mx, my = (pts[i][0] + pts[i+1][0]) / 2, (pts[i][1] + pts[i+1][1]) / 2
                    d += f"Q{pts[i][0]:.1f} {pts[i][1]:.1f} {mx:.1f} {my:.1f}"
                out += f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{wid}" stroke-linecap="round"/>'
        return out
    if k == "wedgering":  # fruit or potato slices fanned evenly around the plate
        _, c, skin, n, r = o
        out = ""
        for i in range(n):
            a = 6.283 * i / n
            x, y = 50 + r * math.cos(a), 50 + r * math.sin(a)
            out += f'<g transform="translate({x:.1f} {y:.1f}) rotate({math.degrees(a)+24:.0f})"><path d="M-11 2Q0 -9 11 2Q0 7 -11 2z" fill="{c}"/><path d="M-11 2Q0 -9 11 2" fill="none" stroke="{skin}" stroke-width="2.2" stroke-linecap="round"/></g>'
        return out
    if k == "potatoes":  # whole baked potatoes, split and filled
        _, skin, flesh, top = o
        out = ""
        for x, y, r in ((37, 39, -20), (63, 61, -20)):
            out += (f'<g transform="rotate({r} {x} {y})"><ellipse cx="{x}" cy="{y}" rx="22" ry="14.5" fill="{skin}"/>'
                    f'<ellipse cx="{x}" cy="{y}" rx="17" ry="9.5" fill="{flesh}"/></g>'
                    f'<path d="{blob(rng,x,y,10,6,8,.2)}" fill="{top}"/>')
        return out
    if k == "chop":  # a bone-in chop with a fat edge, bone and grill marks
        _, c, fat, x, y, rot = o
        return (f'<g transform="translate({x} {y}) rotate({rot}) scale(1.05)">'
                '<path d="M-20 -4C-22 -16 -6 -19 8 -16C20 -13 25 -5 22 3C19 11 8 12 2 11C-4 16 -16 15 -19 8C-20 4 -20 0 -20 -4z" '
                f'fill="{fat}"/>'
                '<path d="M-17 -4C-18 -13 -5 -16 7 -13C17 -10 22 -4 19 2C17 8 7 9 1 8C-4 12 -14 12 -16 6C-17 3 -17 -1 -17 -4z" '
                f'fill="{c}"/>'
                '<path d="M-3 3Q4 -3 3 -11" fill="none" stroke="#F7EFDF" stroke-width="3.2" stroke-linecap="round"/>'
                '<path d="M-13 -6l9 6M-8 -11l11 8M4 -12l10 8M10 -5l6 5" stroke="#6B3A1E" stroke-width="1.5" stroke-linecap="round" opacity=".75"/></g>')
    if k == "bacon":  # two wavy rashers with pale fat stripes
        _, x, y, rot = o
        out = f'<g transform="translate({x} {y}) rotate({rot})">'
        for dy in (-5, 5):
            d = f"M-14 {dy*.8}q4.7 -4 9.3 0t9.3 0t9.3 0"
            out += (f'<path d="{d}" fill="none" stroke="#8E3B26" stroke-width="5.4" stroke-linecap="round"/>'
                    f'<path d="{d}" fill="none" stroke="#F0CDA8" stroke-width="1.3" stroke-linecap="round"/>')
        return out + "</g>"
    if k == "fillet":  # a salmon or fish fillet: tapered, flaky, seared, with a glossy highlight
        _, c, flake, sear, x, y, rot, sc = o
        out = (f'<g transform="translate({x} {y}) rotate({rot}) scale({sc})">'
               f'<path d="M-25 2C-25 -10 -12 -15 2 -15C16 -15 26 -7 28 0C26 8 14 15 0 15C-13 15 -25 12 -25 2z" fill="{sear}"/>'
               f'<path d="M-22 2C-22 -8 -11 -12 2 -12C14 -12 23 -6 25 0C23 6 13 12 0 12C-12 12 -22 10 -22 2z" fill="{c}"/>')
        for i, px in enumerate((-13, -5, 3, 11, 18)):
            h = (9, 10.5, 10.5, 9, 6)[i]
            out += f'<path d="M{px} {-h}Q{px+5} 0 {px} {h}" fill="none" stroke="{flake}" stroke-width="1.7" stroke-linecap="round"/>'
        return out + '<path d="M-14 -7Q0 -11 13 -7" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" opacity=".42"/></g>'
    if k == "tortellini":  # little folded rings of filled pasta
        _, c, shade, n, r = o
        out = ""
        for x, y, a in spots(rng, n, r):
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})">'
                    f'<path d="M-8 3A8.5 8.5 0 1 1 8 3Q5 8 2 5Q0 3 -2 5Q-5 8 -8 3z" fill="{c}"/>'
                    f'<path d="M-4.5 1A4.8 4.8 0 1 1 4.5 1" fill="none" stroke="{shade}" stroke-width="1.5" stroke-linecap="round"/>'
                    f'<circle cx="0" cy="0.5" r="2.2" fill="{shade}"/></g>')
        return out
    if k == "omelette":  # a folded omelette with filling peeking out
        _, c, shade, f1, f2 = o
        return ('<g transform="rotate(-10 50 50)">'
                f'<path d="M17 58Q15 32 50 30Q85 32 83 58Q80 70 50 70Q20 70 17 58z" fill="{shade}"/>'
                f'<path d="M20 55Q19 35 50 33Q81 35 80 55Q50 66 20 55z" fill="{c}"/>'
                f'<path d="M24 60Q50 70 76 60" fill="none" stroke="{f1}" stroke-width="3.4" stroke-linecap="round" stroke-dasharray="6 5"/>'
                f'<path d="M28 63Q50 71 72 63" fill="none" stroke="{f2}" stroke-width="2.6" stroke-linecap="round" stroke-dasharray="4 8"/>'
                '<path d="M32 42Q50 36 68 42" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" opacity=".4"/></g>')
    if k == "steak":  # a seared steak with grill marks and a fat edge
        _, c, fat, x, y, rot, sc = o
        return (f'<g transform="translate({x} {y}) rotate({rot}) scale({sc})">'
                f'<path d="M-24 -6C-22 -16 -4 -18 10 -15C24 -12 28 -2 24 7C20 15 2 16 -10 14C-22 12 -26 4 -24 -6z" fill="{fat}"/>'
                f'<path d="M-21 -5C-19 -13 -4 -15 9 -12C21 -9 25 -2 21 6C17 12 2 13 -9 11C-19 9 -23 3 -21 -5z" fill="{c}"/>'
                '<path d="M-15 -8l12 16M-5 -11l14 19M6 -11l12 16" stroke="#3A1E10" stroke-width="1.7" stroke-linecap="round" opacity=".7"/></g>')
    if k == "bread":  # a split crusty roll or baguette with your own filling colours
        _, crust, crumb, f1, f2 = o
        out = '<g transform="rotate(-28 50 50)">'
        for dy in (-14, 14):
            out += (f'<path d="M23 {50+dy-9}Q50 {50+dy-13} 77 {50+dy-9}Q83 {50+dy} 77 {50+dy+9}Q50 {50+dy+13} 23 {50+dy+9}Q17 {50+dy} 23 {50+dy-9}z" fill="{crust}"/>'
                    f'<path d="M23 {50+dy-3}H77" stroke="{crumb}" stroke-width="5" stroke-linecap="round"/>'
                    f'<path d="M26 {50+dy+0.5}H74" stroke="{f1}" stroke-width="2.4" stroke-linecap="round"/>'
                    f'<path d="M28 {50+dy-5}H72" stroke="{f2}" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="5 5"/>')
        return out + "</g>"
    if k == "shrimp":  # plump curled prawns, evenly spaced, with shell bands and a fanned tail
        _, c, stripe, n, r = o[:5]
        cx = o[5] if len(o) > 5 else 50; cy = o[6] if len(o) > 6 else 50
        out = ""
        for i in range(n):
            a = 6.283 * i / n + 0.5
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({math.degrees(a)+150:.0f}) scale(.85)">'
                    f'<path d="M-10 3C-10 -8 6 -11 10 -2C12 3 9 7 5 8" fill="none" stroke="{c}" stroke-width="7.6" stroke-linecap="round"/>'
                    f'<path d="M-8 -2.5l3.6 3.4M-3 -6l2 4.2M3 -6.6l-.8 4.4M8 -3l-3 3" stroke="{stripe}" stroke-width="1.5" stroke-linecap="round"/>'
                    f'<path d="M4 7Q-1 13 -6 12Q-2 8 3 5z" fill="#E2603A"/><path d="M5 7Q5 14 0 17Q-1 11 3 6z" fill="#E2603A"/></g>')
        return out
    if k == "ribbons":  # wide flat noodles folding back and forth
        _, c, shade, w, n = o
        out = '<g transform="rotate(-18 50 50)">'
        for i in range(n):
            y = 50 + (i - (n - 1) / 2) * (w + 2.2)
            half = math.sqrt(max(31 ** 2 - (y - 50) ** 2, 40)) - w / 2
            a = 3.4 if i % 2 else -3.4
            d = f"M{50-half:.1f} {y:.1f}q{half/2:.1f} {a*2:.1f} {half:.1f} 0t{half:.1f} 0"
            out += (f'<path d="{d}" fill="none" stroke="{shade}" stroke-width="{w+1.6}" stroke-linecap="round"/>'
                    f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{w}" stroke-linecap="round"/>'
                    f'<path d="{d}" fill="none" stroke="#FFFFFF" stroke-width="{w*.22:.1f}" stroke-linecap="round" opacity=".3"/>')
        return out + "</g>"
    if k == "eggplant":  # batons of eggplant: glossy purple skin, pale flesh
        _, n, r = o[:3]
        cx = o[3] if len(o) > 3 else 50; cy = o[4] if len(o) > 4 else 50
        out = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><rect x="-12" y="-5.5" width="24" height="11" rx="5.5" fill="#55286B"/>'
                    '<rect x="-10" y="-5.5" width="20" height="7" rx="3.5" fill="#EAD9B4"/>'
                    '<path d="M-7 3.6H5" stroke="#8A5AA0" stroke-width="1.3" stroke-linecap="round"/></g>')
        return out
    if k == "drumstick":  # a chicken leg with a bone end; optional crumb colour for breaded legs
        _, c, shade, x, y, rot, sc = o[:7]
        crumb = o[7] if len(o) > 7 else ""
        out = (f'<g transform="translate({x} {y}) rotate({rot}) scale({sc})">'
               '<path d="M12 0H25" stroke="#E6D2AC" stroke-width="5.4" stroke-linecap="round"/>'
               '<circle cx="26.5" cy="-2.8" r="3.7" fill="#E6D2AC"/><circle cx="26.5" cy="2.8" r="3.7" fill="#E6D2AC"/><path d="M15 -1.2H26" stroke="#FFFFFF" stroke-width="1.2" stroke-linecap="round" opacity=".6"/>'
               f'<path d="M-23 1C-23 -13 -9 -16 2 -10C8 -7 12 -5 16 -3.4V3.4C12 5 8 7 2 10C-9 16 -23 14 -23 1z" fill="{shade}"/>'
               f'<path d="M-23 -1C-23 -13 -9 -15.5 2 -10C8 -7 12 -5 16 -3.4V1C10 2 5 4 0 6C-10 10 -23 9 -23 -1z" fill="{c}"/>')
        if crumb:
            for dx, dy, _ in spots(rng, 16, 11, -8, -1):
                out += f'<circle cx="{dx*1.2+1.6:.1f}" cy="{dy:.1f}" r="1.2" fill="{crumb}"/>'
        return out + '<ellipse cx="-11" cy="-7" rx="7" ry="2.6" fill="#FFFFFF" opacity=".32" transform="rotate(-8 -11 -7)"/></g>'
    if k == "muffin":  # three muffins from the side, wide domed top in a pleated paper case; optional specks
        c, case = o[1], o[2]
        s1 = o[3] if len(o) > 3 else ""
        s2 = o[4] if len(o) > 4 else ""
        out = ""
        for x, y in ((34, 39), (66, 39), (50, 64)):
            out += f'<path d="M{x-13.5} {y+1}L{x-10.5} {y+16.5}Q{x} {y+19} {x+10.5} {y+16.5}L{x+13.5} {y+1}z" fill="{case}"/>'
            for dx in (-8, -2.7, 2.7, 8):
                out += f'<path d="M{x+dx*1.2:.1f} {y+5}L{x+dx:.1f} {y+16}" stroke="#000000" stroke-width="1.9" stroke-linecap="round" opacity=".16"/>'
            out += (f'<path d="M{x-14.5} {y+3}C{x-19} {y-3} {x-12} {y-14} {x} {y-14}C{x+12} {y-14} {x+19} {y-3} {x+14.5} {y+3}Q{x} {y+8} {x-14.5} {y+3}z" fill="{c}"/>'
                    f'<path d="M{x-8} {y-8}Q{x-3} {y-11.5} {x+3} {y-10.5}" fill="none" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" opacity=".35"/>')
            for col, n, rad in ((s1, 5, 9), (s2, 4, 8)):
                if col:
                    for dx, dy, _ in spots(rng, n, rad, x, y - 4):
                        out += f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="1.6" fill="{col}"/>'
        return out
    if k == "quesadilla":  # two folded half-moons with melted filling spilling from the open edge
        _, c, char, f1, f2 = o
        out = ""
        for x, y, r in ((44, 36, -12), (56, 62, -12)):
            out += (f'<g transform="rotate({r} {x} {y})">'
                    f'<path d="M{x-27} {y+5}Q{x-23} {y+17} {x-15} {y+13}Q{x-8} {y+20} {x} {y+14}Q{x+8} {y+20} {x+15} {y+13}Q{x+23} {y+17} {x+27} {y+5}z" fill="{f1}"/>')
            if f2:
                for dx in (-17, -6, 6, 17):
                    out += f'<circle cx="{x+dx}" cy="{y+12}" r="2.6" fill="{f2}"/>'
            out += f'<path d="M{x-28} {y+6}A28 24 0 0 1 {x+28} {y+6}Q{x} {y+9} {x-28} {y+6}z" fill="{c}"/>'
            for dx, dy, a in spots(rng, 6, 14, x, y - 5):
                out += f'<ellipse cx="{dx:.1f}" cy="{dy:.1f}" rx="3.2" ry="1.9" fill="{char}" opacity=".75" transform="rotate({a:.0f} {dx:.1f} {dy:.1f})"/>'
            out += "</g>"
        return out
    if k == "burger":  # seen from the side; pass "" to leave a layer out
        patty = o[1]
        cheese = o[2] if len(o) > 2 else ""
        green = o[3] if len(o) > 3 else ""
        tomato = o[4] if len(o) > 4 else ""
        seed = o[5] if len(o) > 5 else ""
        bun = o[6] if len(o) > 6 else "#D9A45C"
        H = 11 + 12 + (3 if cheese else 0) + (4.5 if tomato else 0) + (5 if green else 0) + 22
        y = 50 + H / 2
        out = f'<path d="M26 {y-11}H74V{y-6}Q74 {y} 66 {y}H34Q26 {y} 26 {y-6}z" fill="{bun}"/>'
        y -= 11
        out += f'<rect x="21" y="{y-12}" width="58" height="12" rx="5.5" fill="{patty}"/><path d="M27 {y-9.4}H73" stroke="#FFFFFF" stroke-width="1.5" stroke-linecap="round" opacity=".2"/>'
        y -= 12
        if cheese:
            out += f'<path d="M24 {y-3}H76V{y+.5}H68L60 {y+7}L52 {y+.5}H42L34 {y+7}L28 {y+.5}H24z" fill="{cheese}" stroke="{cheese}" stroke-width="1" stroke-linejoin="round"/>'
            y -= 3
        if tomato:
            out += f'<rect x="25" y="{y-4.5}" width="50" height="4.8" rx="2.4" fill="{tomato}"/>'
            y -= 4.5
        if green:
            out += f'<path d="M21 {y-3}q5 -4 10 0t10 0t10 0t10 0t10 0t8 0" fill="none" stroke="{green}" stroke-width="5.5" stroke-linecap="round"/>'
            y -= 5
        out += (f'<path d="M24 {y}Q19 {y} 21 {y-5}Q26 {y-22} 50 {y-22}Q74 {y-22} 79 {y-5}Q81 {y} 76 {y}z" fill="{bun}"/>'
                f'<path d="M31 {y-11}Q37 {y-18} 48 {y-18.5}" fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" opacity=".32"/>')
        if seed:
            for dx, dy in ((-16, -8), (-6, -13), (6, -15), (17, -10), (0, -7), (12, -5), (-12, -4)):
                out += f'<ellipse cx="{50+dx}" cy="{y+dy}" rx="1.9" ry="1.1" fill="{seed}"/>'
        return out
    if k == "mushroom":  # sliced mushrooms from the side: brown cap, pale stem and gills
        c, n, sz, r = o[1:5]
        cx = o[5] if len(o) > 5 else 50; cy = o[6] if len(o) > 6 else 50
        out = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({(a-90)*.45:.0f}) scale({sz/7:.2f})">'
                    '<path d="M-3.2 -1Q-3.6 6.5 -3 7.4Q0 9 3 7.4Q3.6 6.5 3.2 -1z" fill="#F4E9D4"/>'
                    '<path d="M-8.4 .4Q0 3.6 8.4 .4L7 -1.6H-7z" fill="#D9C4A0"/>'
                    f'<path d="M-9 .4C-9.6 -5.4 -5.4 -8.4 0 -8.4C5.4 -8.4 9.6 -5.4 9 .4Q0 -2.6 -9 .4z" fill="{c}"/>'
                    '<path d="M-5 -4.2Q-2.6 -6.4 .6 -6.4" fill="none" stroke="#FFFFFF" stroke-width="1.3" stroke-linecap="round" opacity=".32"/></g>')
        return out
    if k == "broccoli":  # florets: pale stalk, bumpy dark green crown
        n, sz, r = o[1:4]
        cx = o[4] if len(o) > 4 else 50; cy = o[5] if len(o) > 5 else 50
        out = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({(a-90)*.9:.0f}) scale({sz/7:.2f})">'
                    '<path d="M-2.8 9Q0 10.6 2.8 9L3.2 2L7 -2H-7L-3.2 2z" fill="#B5D08A"/>'
                    '<circle cx="-6.4" cy="-1.8" r="4.4" fill="#3F7A34"/><circle cx="6.4" cy="-1.8" r="4.4" fill="#3F7A34"/>'
                    '<circle cx="-3.4" cy="-6" r="4.6" fill="#3F7A34"/><circle cx="3.4" cy="-6" r="4.6" fill="#3F7A34"/><circle cx="0" cy="-1.4" r="4.4" fill="#3F7A34"/>'
                    '<circle cx="-5.2" cy="-3.6" r="1.7" fill="#6FA04E"/><circle cx="-.6" cy="-7.6" r="1.9" fill="#6FA04E"/><circle cx="4.4" cy="-4.2" r="1.6" fill="#6FA04E"/></g>')
        return out
    if k in ("chip", "chipring"):  # tortilla or pita triangles; chipring leaves the centre clear for a dip
        if k == "chip":
            c, e, n, sz, r = o[1:6]
            cx = o[6] if len(o) > 6 else 50; cy = o[7] if len(o) > 7 else 50
            at = list(spots(rng, n, r, cx, cy))
        else:
            c, e, n, r = o[1:5]
            sz = 9
            at = [(50 + r * math.cos(6.283 * i / n), 50 + r * math.sin(6.283 * i / n), math.degrees(6.283 * i / n) - 90 + (20 if i % 2 else -20)) for i in range(n)]
        out = ""
        for x, y, a in at:
            d = f"M0 {-sz:.1f}L{sz*.92:.1f} {sz*.66:.1f}H{-sz*.92:.1f}z"
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><path d="{d}" transform="translate(0 1.3)" fill="{e}" stroke="{e}" stroke-width="2.4" stroke-linejoin="round"/>'
                    f'<path d="{d}" fill="{c}" stroke="{c}" stroke-width="2.4" stroke-linejoin="round"/>'
                    f'<circle cx="{-sz*.25:.1f}" cy="{sz*.2:.1f}" r=".9" fill="{e}"/><circle cx="{sz*.3:.1f}" cy="{sz*.3:.1f}" r=".9" fill="{e}"/><circle cx="0" cy="{-sz*.3:.1f}" r=".9" fill="{e}"/></g>')
        return out
    if k == "pretzel":  # one big twisted knot with coarse salt
        c = o[1] if len(o) > 1 else "#A9682A"
        shade = o[2] if len(o) > 2 else "#7A4A1E"
        salt = o[3] if len(o) > 3 else "#FFFFFF"
        d = "M30 72C40 60 46 53 57 37C63 25 79 23 81 41C83 60 66 80 50 80C34 80 17 60 19 41C21 23 37 25 43 37C54 53 60 60 70 72"
        out = (f'<g transform="rotate(-6 50 50)"><path d="{d}" fill="none" stroke="{shade}" stroke-width="11" stroke-linecap="round"/>'
               f'<path d="{d}" fill="none" stroke="{c}" stroke-width="8.6" stroke-linecap="round"/>'
               '<path d="M71 35Q77 36 76 44" fill="none" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" opacity=".3"/>')
        if salt:
            for x, y in ((28, 34), (20, 48), (26, 64), (38, 76), (52, 80), (66, 74), (76, 60), (80, 46), (68, 32), (37, 33), (47, 44), (56, 53), (61, 40)):
                out += f'<rect x="{x-1.3}" y="{y-1.1}" width="2.6" height="2.2" rx=".6" fill="{salt}" transform="rotate({x*7%90} {x} {y})"/>'
        return out + "</g>"
    if k == "waffle":  # a square waffle with a deep grid of pockets
        c = o[1] if len(o) > 1 else "#E8B45C"
        pocket = o[2] if len(o) > 2 else "#BE8434"
        out = (f'<g transform="rotate(-9 50 50)"><rect x="23" y="25" width="54" height="54" rx="10" fill="{pocket}"/>'
               f'<rect x="23" y="23" width="54" height="54" rx="10" fill="{c}"/>')
        for i in range(4):
            for j in range(4):
                if (i, j) in ((0, 0), (3, 0), (0, 3), (3, 3)):
                    continue
                out += f'<rect x="{26.5+i*12}" y="{26.5+j*12}" width="11" height="11" rx="2" fill="{pocket}"/>'
        for x, y in ((26.5, 26.5), (62.5, 26.5), (26.5, 62.5), (62.5, 62.5)):
            rx = 5 if True else 2
            out += f'<rect x="{x+1}" y="{y+1}" width="9" height="9" rx="{rx-1}" fill="{pocket}"/>'
        return out + "</g>"
    if k == "shell":  # open clams or mussels, "clam" or "mussel"
        style, n, sz, r = o[1:5]
        cx = o[5] if len(o) > 5 else 50; cy = o[6] if len(o) > 6 else 50
        out = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            out += f'<g transform="translate({x:.1f} {y:.1f}) rotate({a*2:.0f}) scale({sz/7:.2f})">'
            if style == "clam":
                out += ('<path d="M0 10L-4 10L-12 1Q-14 -3 -11.5 -6Q-10 -9.5 -6.5 -9.8Q-3.4 -12.4 0 -11.6Q3.4 -12.4 6.5 -9.8Q10 -9.5 11.5 -6Q14 -3 12 1L4 10z" fill="#8F8370"/>'
                        '<path d="M-2.4 7.4L-9.8 .4Q-11.4 -2.6 -9.4 -4.8Q-8 -7.4 -5.2 -7.6Q-2.6 -9.8 0 -9.2Q2.6 -9.8 5.2 -7.6Q8 -7.4 9.4 -4.8Q11.4 -2.6 9.8 .4L2.4 7.4z" fill="#F2E8D6"/>'
                        '<path d="M0 6V-6M-3 5.4L-7 -4M3 5.4L7 -4" stroke="#C9B89C" stroke-width="1.1" stroke-linecap="round"/>'
                        '<ellipse cx="0" cy="2.6" rx="3.8" ry="3.6" fill="#EDA979"/>')
            else:
                out += ('<path d="M-2 11C-8 9 -9 -8 -3 -12Q2 -14 6 -9C11 -2 7 9 -2 11z" fill="#2E3348"/>'
                        '<path d="M-2 8.4C-6 7 -6.8 -6.4 -2.4 -9.6Q1.6 -11 4 -7.4C8 -2 5 7 -2 8.4z" fill="#C7CEDD"/>'
                        '<path d="M-2 6.8C-4.6 6 -5 -5 -1.8 -7.6Q1.2 -8.6 2.8 -5.8C5.8 -1.6 3.4 5.8 -2 6.8z" fill="#EE9A5C"/>')
            out += "</g>"
        return out
    if k == "sando":  # two halves of a sandwich on sliced bread, from the side
        _, bread, crust, f1 = o[:4]
        f2 = o[4] if len(o) > 4 else ""
        out = ""
        for x, y, r in ((40, 39, -9), (60, 61, -9)):
            H = 12 if f2 else 15
            top = y - (H + (5 if f2 else 0) + 16) / 2
            out += f'<g transform="rotate({r} {x} {y}) translate({x} {y}) scale(.85) translate({-x} {-y})">'
            out += f'<rect x="{x-21}" y="{top+1.6}" width="42" height="8" rx="3.4" fill="{crust}"/><rect x="{x-21}" y="{top}" width="42" height="8" rx="3.4" fill="{bread}"/>'
            yy = top + 7.6
            out += f'<rect x="{x-22}" y="{yy}" width="44" height="{H}" rx="3.2" fill="{f1}"/>'
            yy += H
            if f2:
                out += f'<path d="M{x-21} {yy+2}q3.5 -3 7 0t7 0t7 0t7 0t7 0t7 0" fill="none" stroke="{f2}" stroke-width="4.4" stroke-linecap="round"/>'
                yy += 5
            out += f'<rect x="{x-21}" y="{yy+1.6}" width="42" height="8" rx="3.4" fill="{crust}"/><rect x="{x-21}" y="{yy}" width="42" height="8" rx="3.4" fill="{bread}"/></g>'
        return out
    if k == "onigiri":  # triangle rice balls with a nori band; optional filling dot
        c = o[1] if len(o) > 1 else "#FFFFFF"
        shade = o[2] if len(o) > 2 else "#DDD3BC"
        nori = o[3] if len(o) > 3 else "#1F2A22"
        fill = o[4] if len(o) > 4 else ""
        out = ""
        d = "M0 -16Q3.4 -16 5.6 -12L16.4 8Q19 14 12 14H-12Q-19 14 -16.4 8L-5.6 -12Q-3.4 -16 0 -16z"
        for x, y, r in ((50, 36, 6), (35, 61, -9), (65, 62, 7)):  # one on top, two below, so the nori never reads as a pair of eyes
            out += (f'<g transform="translate({x} {y}) rotate({r}) scale(.8)"><path d="{d}" transform="translate(0 1.8)" fill="{shade}"/><path d="{d}" fill="{c}"/>'
                    + (f'<circle cx="0" cy="-6" r="3.6" fill="{fill}"/>' if fill else "")
                    + f'<path d="M-10.5 3H10.5L14 9Q16 14 11 14H-11Q-16 14 -14 9z" fill="{nori}"/></g>')
        return out
    if k == "bao":  # round steamed buns with pleats gathered at the top
        c, pleat = o[1], o[2]
        out = ""
        for x, y in ((35, 39), (65, 41), (50, 66)):
            out += f'<circle cx="{x}" cy="{y}" r="15" fill="{pleat}"/><circle cx="{x}" cy="{y-.8}" r="14.2" fill="{c}"/>'
            for i in range(9):
                a = 6.283 * i / 9
                x1, y1 = x + 2.6 * math.cos(a), y - .8 + 2.6 * math.sin(a)
                x2, y2 = x + 10 * math.cos(a + .5), y - .8 + 10 * math.sin(a + .5)
                qx, qy = x + 7.6 * math.cos(a + .05), y - .8 + 7.6 * math.sin(a + .05)
                out += f'<path d="M{x1:.1f} {y1:.1f}Q{qx:.1f} {qy:.1f} {x2:.1f} {y2:.1f}" fill="none" stroke="{pleat}" stroke-width="1.5" stroke-linecap="round"/>'
            out += f'<circle cx="{x}" cy="{y-.8}" r="1.9" fill="{pleat}"/>'
        return out
    if k == "spiral":  # round buns with a spiral, for cinnamon rolls; glaze "" for none
        c, swirl = o[1], o[2]
        glaze = o[3] if len(o) > 3 else ""
        out = ""
        for x, y in ((35, 39), (65, 41), (50, 66)):
            out += f'<circle cx="{x}" cy="{y}" r="15.5" fill="{c}"/>'
            if glaze:
                out += f'<path d="{blob(rng,x,y,12.5,12,9,.1)}" fill="{glaze}"/>'
            pts = [(x + (1 + .46 * t) * math.cos(t * .5), y + (1 + .46 * t) * math.sin(t * .5)) for t in range(0, 28)]
            out += '<path d="M' + "L".join(f"{p[0]:.1f} {p[1]:.1f}" for p in pts) + f'" fill="none" stroke="{swirl}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
        return out
    if k == "ricecakes":  # short chewy cylinders, for tteokbokki
        _, c, shade, n, r = o[:5]
        cx = o[5] if len(o) > 5 else 50; cy = o[6] if len(o) > 6 else 50
        out = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><rect x="-10" y="-4.6" width="20" height="9.2" rx="4.6" fill="{c}"/>'
                    f'<ellipse cx="8" cy="0" rx="2.4" ry="3.8" fill="{shade}"/><path d="M-6 -2.2H3" stroke="#FFFFFF" stroke-width="1.3" stroke-linecap="round" opacity=".5"/></g>')
        return out
    if k == "sticks":  # a fan of long straight cut vegetable sticks, one colour pair per stick
        _, pairs, x, y, rot = o
        out = f'<g transform="translate({x} {y}) rotate({rot})">'
        n = len(pairs)
        for i, (c, shade) in enumerate(pairs):
            d = i - (n - 1) / 2
            out += (f'<g transform="rotate({d*5:.1f}) translate(0 {d*7.2:.1f})"><rect x="-24" y="-1.8" width="48" height="5.6" rx="2" fill="{shade}"/>'
                    f'<rect x="-24" y="-2.8" width="48" height="5.6" rx="2" fill="{c}"/></g>')
        return out + "</g>"
    if k == "ribs":  # a glazed rack of ribs with bone ends showing
        _, c, glaze = o[:3]
        bone = o[3] if len(o) > 3 else "#E6D2AC"
        out = '<g transform="rotate(-14 50 50)">'
        for x in (25, 35, 45, 55, 65, 75):
            out += f'<path d="M{x} 26V74" stroke="{bone}" stroke-width="6.4" stroke-linecap="round"/>'
        out += f'<path d="M20 37Q50 30 80 37Q84 50 80 63Q50 70 20 63Q16 50 20 37z" fill="{c}"/>'
        for x in (30, 40, 50, 60, 70):
            out += f'<path d="M{x} 34Q{x+1.6} 50 {x} 66" fill="none" stroke="{glaze}" stroke-width="2.2" stroke-linecap="round" opacity=".55"/>'
        return out + '<path d="M25 41Q50 35 75 40" fill="none" stroke="#FFFFFF" stroke-width="2.4" stroke-linecap="round" opacity=".3"/></g>'
    if k == "wholefish":  # one whole fish lying on the plate, scored skin, forked tail, no eye
        _, c, shade, x, y, rot, sc = o
        return (f'<g transform="translate({x} {y}) rotate({rot}) scale({sc})">'
                f'<path d="M-26 0Q-31 -8 -37 -11Q-33 0 -37 11Q-31 8 -26 0z" fill="{shade}"/>'
                f'<path d="M-28 0Q-14 -15 6 -14Q22 -12 33 0Q22 12 6 14Q-14 15 -28 0z" fill="{c}"/>'
                f'<path d="M-6 -13L-1 -19Q8 -19 13 -12z" fill="{shade}"/>'
                f'<path d="M20 -8.5Q15 0 20 8.5" fill="none" stroke="{shade}" stroke-width="1.8" stroke-linecap="round"/>'
                + "".join(f'<path d="M{dx} -7Q{dx-3} 0 {dx} 7" fill="none" stroke="{shade}" stroke-width="1.7" stroke-linecap="round"/>' for dx in (-12, -3, 6))
                + f'<path d="M-14 -7.5Q2 -11.5 18 -7" fill="none" stroke="#FFFFFF" stroke-opacity=".35" stroke-width="2" stroke-linecap="round"/></g>')
    if k == "smallfish":  # small whole fried fish laid side by side, no eyes; r is the gap between them
        _, c, belly, n, r = o
        out = '<g transform="rotate(-24 50 50)">'
        for i in range(n):
            y = 50 + (i - (n - 1) / 2) * r
            half = math.sqrt(max(34 ** 2 - (y - 50) ** 2, 1))
            sc = min(half / 24, 1.25)
            flip = -1 if i % 2 else 1
            out += (f'<g transform="translate(50 {y}) scale({sc*flip:.2f} {sc:.2f})"><path d="M-14 0L-23 -6Q-20 0 -23 6z" fill="{c}"/>'
                    f'<path d="M-15 0Q-4 -8 8 -5.2Q14 -3.4 19 0Q14 3.4 8 5.2Q-4 8 -15 0z" fill="{c}"/>'
                    f'<path d="M-10 1.6Q2 4.6 14 1.4" fill="none" stroke="{belly}" stroke-width="2.2" stroke-linecap="round"/></g>')
        return out + "</g>"
    if k == "pecans":  # halves with a centre groove and a wavy edge
        _, c, groove, n, r = o[:5]
        out = ""
        for x, y, a in spots(rng, n, r):
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a:.0f})"><path d="M-9 0Q-9 -5 -5 -4.6Q-3 -6.6 0 -5Q3 -6.6 5 -4.6Q9 -5 9 0Q9 5 5 4.6Q3 6.6 0 5Q-3 6.6 -5 4.6Q-9 5 -9 0z" fill="{c}"/>'
                    f'<path d="M-7 0H7" stroke="{groove}" stroke-width="1.5" stroke-linecap="round"/><path d="M-5 -2.6Q0 -1.4 5 -2.6M-5 2.6Q0 1.4 5 2.6" fill="none" stroke="{groove}" stroke-width="1" stroke-linecap="round" opacity=".7"/></g>')
        return out
    if k == "okra":  # whole baby okra pods, tapered, with a small stem cap
        _, n, r = o[:3]
        cx = o[3] if len(o) > 3 else 50; cy = o[4] if len(o) > 4 else 50
        out = ""
        for x, y, a in spots(rng, n, r, cx, cy):
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({a*2:.0f})"><path d="M-10 -3.6L-13 -2V2L-10 3.6z" fill="#C9C98A"/>'
                    '<path d="M-10.4 -4.2Q0 -5.4 13 0Q0 5.4 -10.4 4.2Q-11.4 0 -10.4 -4.2z" fill="#5F8A3A"/>'
                    '<path d="M-8 -1.5Q0 -2 9 0M-8 1.5Q0 2 9 0" fill="none" stroke="#A9C77A" stroke-width="1.1" stroke-linecap="round"/></g>')
        return out
    if k == "tamagoyaki":  # thick slices of rolled omelette showing their layers
        c = o[1] if len(o) > 1 else "#F2C94C"
        line = o[2] if len(o) > 2 else "#C98F2A"
        out = ""
        for x, y, r in ((31, 47, -7), (50, 53, -7), (69, 47, -7)):
            out += (f'<g transform="translate({x} {y}) rotate({r})"><rect x="-10" y="-17" width="20" height="36" rx="5" fill="{line}"/><rect x="-10" y="-19" width="20" height="36" rx="5" fill="{c}"/>'
                    f'<path d="M-6.4 12V-12Q-6.4 -15 -3 -15H3Q6.4 -15 6.4 -12V8Q6.4 11 3 11H0Q-2.8 11 -2.8 8V-7Q-2.8 -10 0 -10Q2.8 -10 2.8 -7V4" fill="none" stroke="{line}" stroke-width="1.5" stroke-linecap="round"/></g>')
        return out
    if k == "summerroll":  # rice paper rolls with shrimp and herbs showing through
        out = ""
        for x, y, r in ((38, 36, -24), (62, 45, -24), (46, 67, -24)):
            out += (f'<g transform="translate({x} {y}) rotate({r})"><rect x="-23" y="-10.5" width="46" height="21" rx="10.5" fill="#FBF7E9"/>'
                    '<path d="M-14 -3Q-7 -9 -1 -3" fill="none" stroke="#F0896A" stroke-width="4.6" stroke-linecap="round"/><path d="M1 -3Q8 -9 14 -3" fill="none" stroke="#F0896A" stroke-width="4.6" stroke-linecap="round"/>'
                    '<path d="M-16 3.4H16" stroke="#6E9A45" stroke-width="4" stroke-linecap="round" stroke-dasharray="7 3"/>'
                    '<rect x="-23" y="-10.5" width="46" height="21" rx="10.5" fill="#FFFFFF" opacity=".24"/></g>')
        return out
    if k == "madeleine":  # small shell-shaped cakes with ridges
        _, c, ridge, n, r = o
        out = ""
        for i in range(n):
            a = 6.283 * i / n + .3
            x, y = 50 + r * math.cos(a), 50 + r * math.sin(a)
            out += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({math.degrees(a)+90:.0f})"><path d="M0 13C-5 13 -7 11 -8 8C-13 3 -14 -10 0 -13C14 -10 13 3 8 8C7 11 5 13 0 13z" fill="{c}"/>')
            for t in (-30, -15, 0, 15, 30):
                ta = math.radians(t)
                out += f'<path d="M{3*math.sin(ta):.1f} {6-3*math.cos(ta):.1f}L{13.4*math.sin(ta):.1f} {6-14.6*math.cos(ta):.1f}" stroke="{ridge}" stroke-width="1.5" stroke-linecap="round"/>'
            out += "</g>"
        return out
    if k == "teaegg":  # whole peeled eggs marbled with dark tea cracks
        _, n, r = o
        out = ""
        for i in range(n):
            a = 6.283 * i / n + .5
            x, y = 50 + r * math.cos(a), 50 + r * math.sin(a)
            out += f'<g transform="translate({x:.1f} {y:.1f}) rotate({math.degrees(a)+60:.0f})"><ellipse rx="15" ry="12" fill="#E8C99A"/>'
            out += ('<path d="M-11 -4L-5 -2L-8 4L-1 6L1 -1L-3 -8M1 -1L7 -3L5 -9M7 -3L12 2L6 6L1 6M-8 4L-12 3M6 6L5 10" fill="none" stroke="#7A4A28" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
                    '<ellipse cx="-4" cy="-7" rx="5" ry="2" fill="#FFFFFF" opacity=".3"/></g>')
        return out
    return base_op(rng, o)



def plate_svg(name, parts):
    rng = random.Random(int(hashlib.sha1(name.encode()).hexdigest()[:8], 16))
    g = ['<circle cx="50" cy="50" r="48" fill="#FFFFFF" stroke="#E6E3DB"/><circle cx="50" cy="50" r="38" fill="none" stroke="#EFEAE0" stroke-width="1.4"/>']
    for o in parts:
        g.append(op(rng, o))
    return "".join(g)
