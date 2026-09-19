"""Build the Sparnod Rotation page.

Outputs
  index.html        fragment for the claude.ai artifact (the viewer wraps it)
  docs/index.html   full document for GitHub Pages (installable PWA)
  docs/manifest.webmanifest, docs/sw.js, docs/icon-192.png, docs/icon-512.png

Exercise animations come from free-exercise-db (yuhonas, Unlicense) — the
catalog Lyftr serves through open-exercise-db. Each exercise has two frames
(start / end position); the page flips between them. Frames are downscaled
and embedded as data URIs so both copies are self-contained.

Usage:  python build.py            # builds everything
        python build.py --refresh  # re-downloads frames even if cached
"""
import base64
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
CACHE = ROOT / "frames"
DOCS = ROOT / "docs"
RAW = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/{id}/{n}.jpg"
WIDTH = 320
QUALITY = 72

program = json.loads((ROOT / "program.json").read_text(encoding="utf-8"))
ids = {it["ex"] for w in program["workouts"].values() for it in w["items"]}
ids |= {b["ex"] for b in program.get("warmup", []) + program.get("cooldown", [])}
ids = sorted(ids)
refresh = "--refresh" in sys.argv
CACHE.mkdir(exist_ok=True)
DOCS.mkdir(exist_ok=True)


def fetch(ex_id: str, n: int) -> bytes:
    p = CACHE / f"{ex_id}.{n}.jpg"
    if p.exists() and not refresh:
        return p.read_bytes()
    req = urllib.request.Request(RAW.format(id=ex_id, n=n), headers={"User-Agent": "home-gym-build/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    p.write_bytes(data)
    return data


def shrink(data: bytes) -> str:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((WIDTH, WIDTH * 2), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


frames = {}
total = 0
for ex_id in ids:
    pair = [shrink(fetch(ex_id, n)) for n in (0, 1)]
    frames[ex_id] = pair
    total += sum(len(p) for p in pair)

html = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
# Encoding-agnostic output: entities in markup, \uXXXX escapes in the script,
# so the page reads correctly even from a server that sends no charset.
cut = html.index("<script>")
head, script = html[:cut], html[cut:]
for ch in sorted({c for c in html if ord(c) > 127}):
    head = head.replace(ch, "&#%d;" % ord(ch))
    script = script.replace(ch, chr(92) + "u%04x" % ord(ch))
html = head + script
html = html.replace("/*__PROGRAM__*/null", json.dumps(program))
html = html.replace("/*__FRAMES__*/null", json.dumps(frames))
(ROOT / "index.html").write_text(html, encoding="utf-8")

# ---- GitHub Pages copy: a full document with PWA hooks
build_id = hashlib.sha1(html.encode("utf-8")).hexdigest()[:10]
doc = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n"
    "<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
    "<meta name=\"color-scheme\" content=\"light dark\">\n"
    "<meta name=\"theme-color\" content=\"#F2F3F0\" media=\"(prefers-color-scheme: light)\">\n"
    "<meta name=\"theme-color\" content=\"#131619\" media=\"(prefers-color-scheme: dark)\">\n"
    "<meta name=\"apple-mobile-web-app-capable\" content=\"yes\">\n"
    "<meta name=\"apple-mobile-web-app-status-bar-style\" content=\"black-translucent\">\n"
    "<link rel=\"manifest\" href=\"manifest.webmanifest\">\n"
    "<link rel=\"icon\" href=\"icon-192.png\">\n"
    "<link rel=\"apple-touch-icon\" href=\"icon-192.png\">\n"
    "<style>html{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}img{max-width:100%}</style>\n"
    "</head>\n<body>\n" + html + "\n</body>\n</html>\n"
)
(DOCS / "index.html").write_text(doc, encoding="utf-8")

manifest = {
    "name": "Sparnod Rotation",
    "short_name": "Rotation",
    "description": "Full-body A/B/C rotation for the Sparnod SHG-10000 home gym.",
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#131619",
    "theme_color": "#131619",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}
(DOCS / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

sw = """// Sparnod Rotation service worker — build __BUILD__
const V = "sr-__BUILD__";
const ASSETS = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(V).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const nav = e.request.mode === "navigate" || e.request.destination === "document";
  if (nav) {
    // network first so a new build shows up; the cached page covers offline
    e.respondWith(fetch(e.request).then((r) => { const c = r.clone(); caches.open(V).then((k) => k.put("./index.html", c)); return r; }).catch(() => caches.match("./index.html")));
    return;
  }
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request).then((res) => { if (res.ok) { const c = res.clone(); caches.open(V).then((k) => k.put(e.request, c)); } return res; })));
});
""".replace("__BUILD__", build_id)
(DOCS / "sw.js").write_text(sw, encoding="utf-8")


def icon(size: int) -> None:
    """A weight stack with the pin in — the one thing this page is about."""
    im = Image.new("RGB", (size, size), "#131619")
    d = ImageDraw.Draw(im)
    u = size / 512
    plates = 6
    ph = 40 * u
    gap = 14 * u
    top = (size - (plates * ph + (plates - 1) * gap)) / 2
    x0, x1 = 112 * u, 400 * u
    for i in range(plates):
        y = top + i * (ph + gap)
        d.rounded_rectangle([x0, y, x1, y + ph], radius=10 * u, fill="#E6E8EA")
    # the guide rods
    for rx in (x0 + 52 * u, x1 - 52 * u):
        d.rectangle([rx - 6 * u, top - 28 * u, rx + 6 * u, top + plates * (ph + gap)], fill="#8D949C")
    # the pin, in plate 4
    py = top + 3 * (ph + gap) + ph / 2
    d.rounded_rectangle([x1 - 20 * u, py - 12 * u, x1 + 60 * u, py + 12 * u], radius=12 * u, fill="#E8794F")
    d.ellipse([x1 + 40 * u, py - 22 * u, x1 + 84 * u, py + 22 * u], fill="#E8794F")
    im.save(DOCS / f"icon-{size}.png", "PNG", optimize=True)


icon(192)
icon(512)
print(f"{len(ids)} exercises, {total // 1024} KB of frames -> index.html ({len(html.encode()) // 1024} KB), docs/ build {build_id}")
