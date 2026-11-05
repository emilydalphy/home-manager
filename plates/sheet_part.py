"""Draw every plate in a part as numbered pages of 30. Usage: python3 plates/sheet_part.py 03"""
import json, os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import plate_parts as pp
n = sys.argv[1]
specs = json.load(open(f"{H}/specs/part-{n}.json"))
names = sorted(specs)
for p in range(0, len(names), 30):
    W, CW, CH = 1220, 200, 200
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}" font-family="Helvetica, Arial, sans-serif"><rect width="{W}" height="{W}" fill="#F7F7F4"/>']
    for i, nm in enumerate(names[p:p + 30]):
        x, y = 10 + (i % 6) * CW, 10 + (i // 6) * CH
        s.append(f'<g transform="translate({x+22} {y}) scale(1.56)">{pp.plate_svg(nm, specs[nm])}</g>')
        s.append(f'<text x="{x+100}" y="{y+174}" text-anchor="middle" font-size="13" font-weight="600" fill="#163025">{nm[:27].replace("&", "and")}</text>')
    out = f"/tmp/plates-{n}-page{p//30+1}.svg"
    open(out, "w").write("".join(s + ["</svg>"]))
    subprocess.run(["qlmanage", "-t", "-s", "1220", "-o", "/tmp", out], capture_output=True)
    print(out + ".png")
