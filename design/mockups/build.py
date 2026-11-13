"""Build the TaskBeavo HTML mockups.

Expands {{...}} tokens in screens/*.html (icons, status bar, tab bar, beaver mascot,
stars, admin sidebar) and writes self-contained pages plus an index to dist/.

    python3 design/mockups/build.py            # all screens
    python3 design/mockups/build.py 06-checkout
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).parent
CSS = (ROOT / "base.css").read_text()

I = {
 "home": '<path d="M3 10l9-7 9 7v10a2 2 0 0 1-2 2h-4v-7H9v7H5a2 2 0 0 1-2-2z"/>',
 "tasks": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 12h6M9 16h6"/>',
 "plus": '<path d="M12 5v14M5 12h14"/>',
 "msg": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
 "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
 "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
 "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
 "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
 "pin": '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
 "down": '<path d="m6 9 6 6 6-6"/>',
 "right": '<path d="m9 18 6-6-6-6"/>',
 "back": '<path d="m15 18-6-6 6-6"/>',
 "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
 "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
 "check": '<path d="M20 6 9 17l-5-5"/>',
 "x": '<path d="M18 6 6 18M6 6l12 12"/>',
 "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>',
 "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
 "cal": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
 "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
 "euro": '<path d="M4 10h12M4 14h9M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12c0 4.4 3.5 8 7.8 8 2 0 3.8-.8 5.2-2"/>',
 "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
 "phone": '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/>',
 "send": '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
 "clip": '<path d="m21.4 11-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
 "more": '<circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/>',
 "leaf": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.5 19 2c1 2 2 4.2 2 8 0 5.5-4.8 10-10 10Z"/><path d="M2 21c0-3 1.9-5.4 5.1-6"/>',
 "cart": '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2 2h3l2.7 12.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/>',
 "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
 "spark": '<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>',
 "heart": '<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z"/>',
 "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
 "paw": '<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/><path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.8 1.2 4 4 0 0 0-2.6-2.6A3.5 3.5 0 0 1 5.8 10z"/>',
 "globe": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/>',
 "case": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
 "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
 "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
 "map": '<path d="M9 3 3 6v15l6-3 6 3 6-3V3l-6 3z"/><path d="M9 3v15M15 6v15"/>',
 "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
 "nav": '<path d="m3 11 19-9-9 19-2-8z"/>',
 "receipt": '<path d="M4 2v20l3-2 3 2 3-2 3 2 3-2 3 2V2l-3 2-3-2-3 2-3-2-3 2z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
 "alert": '<path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3z"/><path d="M12 9v4M12 17h.01"/>',
 "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
 "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 "scale": '<path d="M12 3v18M5 21h14M3 7h18M6 7l-3 7a3 3 0 0 0 6 0zM18 7l-3 7a3 3 0 0 0 6 0z"/>',
 "door": '<path d="M13 4h3a2 2 0 0 1 2 2v14M2 20h3M13 20h9M10 12v.01M13 4.6v16.8a.6.6 0 0 1-.8.6L5 20V5.6a1 1 0 0 1 .8-1L12.2 3.4a.6.6 0 0 1 .8.6z"/>',
 "thumb": '<path d="M7 10v12M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.8a2 2 0 0 0 1.8-1.1L12 2a3.1 3.1 0 0 1 3 3.9z"/>',
 "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
 "edit": '<path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/>',
 "filter": '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
 "card": '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
 "refresh": '<path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5"/>',
 "chart": '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
 "id": '<rect x="2" y="5" width="20" height="14" rx="2"/><circle cx="8" cy="12" r="2.5"/><path d="M14 10h5M14 14h3"/>',
 "ban": '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
 "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
 "eyeoff": '<path d="M9.9 4.2A10 10 0 0 1 12 4c6.5 0 10 8 10 8a17 17 0 0 1-2.2 3.2M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2M2 2l20 20"/>',
 "trash": '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
 "life": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/><path d="m4.9 4.9 4.3 4.3M14.8 14.8l4.3 4.3M14.8 9.2l4.3-4.3M4.9 19.1l4.3-4.3"/>',
 "archive": '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
 "phonedev": '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
 "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
 "up": '<path d="m18 15-6-6-6 6"/>',
 "bank": '<path d="M3 21h18M5 21V10M19 21V10M9 21V10M15 21V10M2 10l10-7 10 7z"/>',
 "star": None,
}

def icon(name, size="24", color="#17211C", sw="2"):
    if name == "star":
        return (f'<svg width="{size}" height="{size}" style="width:{size}px;height:{size}px;flex:none" viewBox="0 0 24 24" fill="{color}">'
                '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>')
    return (f'<svg width="{size}" height="{size}" style="width:{size}px;height:{size}px;flex:none" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{I[name]}</svg>')

def beaver(size="160", pose="stand"):
    arm = {
        "wave": '<ellipse cx="152" cy="98" rx="11" ry="26" fill="#C9772B" transform="rotate(38 152 98)"/><circle cx="166" cy="76" r="11" fill="#A85F22"/>',
        "thumb": '<ellipse cx="150" cy="112" rx="11" ry="22" fill="#C9772B" transform="rotate(30 150 112)"/><circle cx="160" cy="94" r="11" fill="#A85F22"/><rect x="156" y="74" width="8" height="16" rx="4" fill="#A85F22"/>',
        "cheer": '<ellipse cx="150" cy="96" rx="11" ry="26" fill="#C9772B" transform="rotate(30 150 96)"/><ellipse cx="50" cy="96" rx="11" ry="26" fill="#C9772B" transform="rotate(-30 50 96)"/><circle cx="162" cy="74" r="11" fill="#A85F22"/><circle cx="38" cy="74" r="11" fill="#A85F22"/>',
        "stand": '<ellipse cx="146" cy="138" rx="10" ry="22" fill="#B86A24" transform="rotate(-12 146 138)"/>',
    }[pose]
    left = '' if pose == "cheer" else '<ellipse cx="54" cy="138" rx="10" ry="22" fill="#B86A24" transform="rotate(12 54 138)"/>'
    return (f'<svg width="{size}" height="{size}" style="width:{size}px;height:{size}px;flex:none" viewBox="0 0 200 200">'
            '<ellipse cx="156" cy="168" rx="38" ry="16" fill="#6B3E1E" transform="rotate(-22 156 168)"/>'
            '<ellipse cx="100" cy="138" rx="52" ry="54" fill="#C9772B"/>'
            '<ellipse cx="100" cy="148" rx="32" ry="38" fill="#F3C98E"/>'
            '<ellipse cx="78" cy="190" rx="16" ry="7" fill="#6B3E1E"/><ellipse cx="122" cy="190" rx="16" ry="7" fill="#6B3E1E"/>'
            f'{left}{arm}'
            '<circle cx="64" cy="42" r="11" fill="#A85F22"/><circle cx="136" cy="42" r="11" fill="#A85F22"/>'
            '<circle cx="100" cy="74" r="44" fill="#D9862F"/>'
            '<path d="M58 54a42 42 0 0 1 84 0z" fill="#1F5C46"/><rect x="52" y="50" width="96" height="9" rx="4.5" fill="#174A38"/>'
            '<ellipse cx="100" cy="90" rx="24" ry="17" fill="#F3C98E"/>'
            '<circle cx="84" cy="72" r="5" fill="#17211C"/><circle cx="116" cy="72" r="5" fill="#17211C"/>'
            '<circle cx="86" cy="70" r="1.6" fill="#fff"/><circle cx="118" cy="70" r="1.6" fill="#fff"/>'
            '<circle cx="72" cy="88" r="6" fill="#F2A33A" opacity=".55"/><circle cx="128" cy="88" r="6" fill="#F2A33A" opacity=".55"/>'
            '<ellipse cx="100" cy="84" rx="8" ry="5.5" fill="#3B2312"/>'
            '<rect x="92.5" y="94" width="15" height="14" rx="2" fill="#fff" stroke="#D8D2C2"/><path d="M100 94v14" stroke="#D8D2C2"/>'
            '</svg>')

SB = ('<div class="sb"><span>9:41</span><span class="r">'
      '<svg width="18" height="12" viewBox="0 0 18 12"><rect x="0" y="8" width="3" height="4" rx="1" fill="#17211C"/><rect x="5" y="5" width="3" height="7" rx="1" fill="#17211C"/><rect x="10" y="2" width="3" height="10" rx="1" fill="#17211C"/><rect x="15" y="0" width="3" height="12" rx="1" fill="#17211C"/></svg>'
      '<svg width="26" height="12" viewBox="0 0 26 12"><rect x=".5" y=".5" width="22" height="11" rx="3" fill="none" stroke="#17211C"/><rect x="2" y="2" width="17" height="8" rx="2" fill="#17211C"/><rect x="23.5" y="4" width="2" height="4" rx="1" fill="#17211C"/></svg>'
      '</span></div>')

def tabbar(active="home"):
    def t(key, ic, label):
        on = key == active
        c = "#1F5C46" if on else "#5E6661"
        return f'<div class="tab{" on" if on else ""}">{icon(ic, "24", c)}<span>{label}</span></div>'
    return ('<div class="tabbar">' + t("home", "home", "Home") + t("tasks", "tasks", "My tasks")
            + f'<div class="tab"><div class="plus">{icon("plus", "28", "#fff", "2.5")}</div><span>Post</span></div>'
            + t("msg", "msg", "Messages") + t("profile", "user", "Profile") + '</div>')

def stars(n="5", size="14"):
    n = int(n)
    return '<span class="stars">' + "".join(icon("star", size, "#F2A33A" if i < n else "#E3E1D8") for i in range(5)) + '</span>'

ADMIN = [("grid","Overview"),("tasks","Tasks"),("cal","Bookings"),("euro","Payments"),("scale","Disputes"),
         ("id","Users &amp; ID checks"),("users","Helper status &amp; DAC7"),("receipt","Fiscal receipts"),("star","Reviews"),("flag","Reports &amp; appeals"),("lock","Data requests"),("gear","Settings")]

def admin(active):
    rows = []
    for ic, label in ADMIN:
        on = label.startswith(active)
        style = "height:44px;padding:0 12px" + (";background:#1F5C46;color:#fff;border-radius:10px;font-weight:700" if on else "")
        extra = '<span class="badge" style="margin-left:auto;background:#F2A33A;color:#17211C">4</span>' if label == "Disputes" else ""
        rows.append(f'<div class="row s" style="{style}">{icon(ic, "18", "#fff" if on else "#C9D3CE")} {label}{extra}</div>')
    return ('<div class="col" style="width:248px;background:#17211C;color:#C9D3CE;padding:20px 12px;gap:4px;flex:none">'
            f'<div class="row" style="padding:0 8px 20px;gap:8px">{beaver("32")}<span class="p" style="color:#fff;font-weight:800">TaskBeavo Admin</span></div>'
            + "".join(rows) + '</div>')

def expand(m):
    parts = m.group(1).split(":")
    k, a = parts[0], parts[1:]
    if k == "i": return icon(*a)
    if k == "sb": return SB
    if k == "tab": return tabbar(*a)
    if k == "beaver": return beaver(*a)
    if k == "stars": return stars(*a)
    if k == "admin": return admin(*a)
    raise ValueError(m.group(0))

DIST = ROOT / "dist"
HEAD = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{}</title>'

def title(name):
    num, _, rest = name.partition("-")
    return f"{int(num)} · {rest.replace('-', ' ').capitalize()}"

def build(name):
    src = (ROOT / "screens" / f"{name}.html").read_text()
    html = re.sub(r"\{\{([^}]+)\}\}", expand, src)
    out = DIST / f"{name}.html"
    out.write_text(HEAD.format(f"TaskBeavo · {title(name)}") + f'<style>{CSS}</style></head><body>{html}</body></html>')
    screens = html.count('class="screen')
    print(f"{out.relative_to(ROOT)}  {screens} screens")

def index(names):
    links = "".join(f'<a href="{n}.html">{title(n)}</a>' for n in names)
    (DIST / "index.html").write_text(
        HEAD.format("TaskBeavo mockups")
        + "<style>body{font-family:Manrope,sans-serif;background:#F5F4EF;color:#17211C;padding:40px}"
          "a{display:block;padding:14px 18px;margin:8px 0;background:#fff;border-radius:12px;color:#1F5C46;"
          "font-weight:700;text-decoration:none;max-width:420px}</style></head>"
        f"<body><h1>TaskBeavo mockups</h1>{links}</body></html>")

if __name__ == "__main__":
    DIST.mkdir(exist_ok=True)
    names = sys.argv[1:] or sorted(p.stem for p in (ROOT / "screens").glob("*.html"))
    for n in names:
        build(n)
    index(sorted(p.stem for p in (ROOT / "screens").glob("*.html")))
