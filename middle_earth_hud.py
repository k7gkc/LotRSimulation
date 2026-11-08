"""
Middle-earth HUD - a small interactive simulation of the War of the Ring.

On a real map of Middle-earth (Wikimedia Commons, CC BY-SA 4.0, author: k1tesurfen)
it shows where the groups of the Fellowship are at a given point in the film
trilogy. The clock runs along the EXTENDED EDITION runtime of all three films,
so at 1x speed a full run lasts as long as watching the extended trilogy.
The map shows only moving dots; the names live in the legend (right panel).

Controls:
    Play/Stop btn  click the button at the bottom-left to start / stop
    Space          play / pause
    Left arrow     skip back        (hold: faster)
    Right arrow    skip forward     (hold: faster)
    + / -          change playback speed (1x .. 1000x)
    Speed slider   drag in the bottom-right to set the speed
    Timeline       click/drag in the bottom-left to jump to a moment
    R              reset to the start
    Esc / window   quit

Calibrate place positions:
    python3 middle_earth_hud.py --calibrate
"""

import io
import os
import re
import sys
import json
import math
import time
import threading
import pygame

from data import LOCATIONS, TRACKS, EVENTS, MAX_DAY, FILMS, TOTAL_RUNTIME, FALLEN

# --------------------------------------------------------------------------- Paths
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BG_PATH = os.path.join(ASSET_DIR, "middle_earth_bg.png")
SVG_PATH = os.path.join(ASSET_DIR, "middle_earth.svg")   # vector original, sharp when zoomed
CALIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locations.json")

# --------------------------------------------------------------------------- Layout
MAP_W = 1000                      # target width of the map area (height follows the image)
MAP_H = 750                       # adjusted to the image aspect ratio on load
PANEL_W = 360                     # right-hand HUD panel
BAR_H = 100                       # bottom bar (timeline left, speed + zoom sliders right)

# --------------------------------------------------------------------------- Speed / zoom
SPEED_MIN = 1.0
SPEED_MAX = 1000.0
ZOOM_MIN = 1.0
ZOOM_MAX = 100.0

# Camera (set up in main once MAP_H is known): zoom factor and centre in base pixels.
ZOOM = 1.0
CAM_X = MAP_W / 2
CAM_Y = MAP_H / 2

# --------------------------------------------------------------------------- Colours
PANEL_BG  = (24, 22, 28)
PANEL_BG2 = (34, 31, 40)
HUD_TEXT  = (224, 218, 206)
HUD_DIM   = (150, 144, 132)
GOLD      = (214, 178, 96)
PARCHMENT = (244, 238, 222)

FILM_COLORS = {
    "The Fellowship of the Ring":   (196, 158, 96),
    "The Two Towers":               (120, 150, 110),
    "The Return of the King":       (196, 120, 90),
}

# Event anchors that map film-minutes <-> story-days (both monotonic).
EV_MIN = [ev[1] for ev in EVENTS]
EV_DAY = [ev[0] for ev in EVENTS]


# --------------------------------------------------------------------------- Maths / data
def lerp(a, b, t):
    return a + (b - a) * t


def world(p):
    """Normalised (0..1) map coordinate -> screen pixel, through the zoom camera."""
    bx, by = p[0] * MAP_W, p[1] * MAP_H
    return (int((bx - CAM_X) * ZOOM + MAP_W / 2),
            int((by - CAM_Y) * ZOOM + MAP_H / 2))


def screen_to_base(sx, sy):
    """Screen pixel in the map area -> base map pixel (0..MAP_W, 0..MAP_H)."""
    return ((sx - MAP_W / 2) / ZOOM + CAM_X, (sy - MAP_H / 2) / ZOOM + CAM_Y)


def clamp_camera():
    """Keep the viewport inside the map; auto-centres when fully zoomed out."""
    global CAM_X, CAM_Y
    hw, hh = MAP_W / (2 * ZOOM), MAP_H / (2 * ZOOM)
    CAM_X = min(max(CAM_X, hw), MAP_W - hw)
    CAM_Y = min(max(CAM_Y, hh), MAP_H - hh)


def set_zoom(z, focus=None):
    """Set the zoom factor, keeping the point under `focus` (screen px) fixed."""
    global ZOOM, CAM_X, CAM_Y
    z = max(ZOOM_MIN, min(ZOOM_MAX, z))
    if focus is not None:
        bx, by = screen_to_base(*focus)
        ZOOM = z
        CAM_X = bx - (focus[0] - MAP_W / 2) / ZOOM
        CAM_Y = by - (focus[1] - MAP_H / 2) / ZOOM
    else:
        ZOOM = z
    clamp_camera()


def zoom_to_pos(z):
    return math.log10(z / ZOOM_MIN) / math.log10(ZOOM_MAX / ZOOM_MIN)


def pos_to_zoom(pos):
    pos = max(0.0, min(1.0, pos))
    return max(ZOOM_MIN, min(ZOOM_MAX, ZOOM_MIN * (ZOOM_MAX / ZOOM_MIN) ** pos))


def minute_to_day(minute):
    """Map a film-runtime minute to the equivalent story day via the event anchors."""
    if minute <= EV_MIN[0]:
        return EV_DAY[0]
    if minute >= EV_MIN[-1]:
        return EV_DAY[-1]
    for i in range(len(EV_MIN) - 1):
        if EV_MIN[i] <= minute <= EV_MIN[i + 1]:
            f = (minute - EV_MIN[i]) / (EV_MIN[i + 1] - EV_MIN[i])
            return EV_DAY[i] + f * (EV_DAY[i + 1] - EV_DAY[i])
    return EV_DAY[-1]


def kf_xy(loc):
    """A keyframe target is either a place name or a raw (x, y) waypoint."""
    return LOCATIONS[loc] if isinstance(loc, str) else loc


def track_active(track, day):
    kfs = track["keyframes"]
    return kfs[0][0] <= day <= kfs[-1][0]


SEG_SAMPLES = 16          # line segments drawn per route leg (smoothness of the curve)


def _cr_segment(p0, p1, p2, p3, t, alpha=0.5):
    """Point on a centripetal Catmull-Rom spline segment p1->p2 (t in 0..1)."""
    def tnext(ti, a, b):
        return ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** (alpha * 0.5) + ti

    def at(a, b, ta, tb, tt):
        if tb == ta:
            return a
        w = (tb - tt) / (tb - ta)
        return (a[0] * w + b[0] * (1 - w), a[1] * w + b[1] * (1 - w))

    t0 = 0.0
    t1 = tnext(t0, p0, p1)
    t2 = tnext(t1, p1, p2)
    t3 = tnext(t2, p2, p3)
    tt = t1 + (t2 - t1) * t
    a1 = at(p0, p1, t0, t1, tt)
    a2 = at(p1, p2, t1, t2, tt)
    a3 = at(p2, p3, t2, t3, tt)
    b1 = at(a1, a2, t0, t2, tt)
    b2 = at(a2, a3, t1, t3, tt)
    return at(b1, b2, t1, t2, tt)


def _control_points(track):
    return [kf_xy(loc) for _, loc in track["keyframes"]]


def cr_eval(pts, seg, t):
    """Evaluate the spline on leg `seg` (between pts[seg] and pts[seg+1])."""
    n = len(pts)
    p0 = pts[max(0, seg - 1)]
    p1 = pts[seg]
    p2 = pts[seg + 1]
    p3 = pts[min(n - 1, seg + 2)]
    return _cr_segment(p0, p1, p2, p3, t)


def track_position(track, day):
    """Smooth (spline) normalised (x, y) position of a group on the given story day."""
    kfs = track["keyframes"]
    pts = _control_points(track)
    if day <= kfs[0][0]:
        return pts[0]
    if day >= kfs[-1][0]:
        return pts[-1]
    for i in range(len(kfs) - 1):
        d0, d1 = kfs[i][0], kfs[i + 1][0]
        if d0 <= day <= d1:
            t = 0 if d1 == d0 else (day - d0) / (d1 - d0)
            return cr_eval(pts, i, t)
    return pts[-1]


def full_spline(track):
    """Dense polyline (normalised) for the entire route."""
    pts = _control_points(track)
    out = [pts[0]]
    for i in range(len(pts) - 1):
        for s in range(1, SEG_SAMPLES + 1):
            out.append(cr_eval(pts, i, s / SEG_SAMPLES))
    return out


def traveled_spline(track, day):
    """Dense polyline (normalised) of the route already travelled by `day`."""
    kfs = track["keyframes"]
    pts = _control_points(track)
    out = [pts[0]]
    for i in range(len(kfs) - 1):
        d0, d1 = kfs[i][0], kfs[i + 1][0]
        if day >= d1:
            for s in range(1, SEG_SAMPLES + 1):
                out.append(cr_eval(pts, i, s / SEG_SAMPLES))
        elif day > d0:
            t = (day - d0) / (d1 - d0)
            for s in range(1, SEG_SAMPLES + 1):
                out.append(cr_eval(pts, i, t * s / SEG_SAMPLES))
            break
        else:
            break
    return out


def current_event(minute):
    """Latest event whose film_minute <= current film time."""
    cur = EVENTS[0]
    for ev in EVENTS:
        if ev[1] <= minute:
            cur = ev
        else:
            break
    return cur


def track_label(track, day):
    """Legend name of a group on `day` (a group can be renamed, e.g. when
    Merry & Pippin part ways and only Pippin goes on with this route)."""
    name = track["name"]
    for d, label in track.get("label", []):
        if day >= d:
            name = label
    return name


def nearest_location(track, day):
    # Only named keyframes count as "places" (skip raw (x, y) curve waypoints).
    named = [kf for kf in track["keyframes"] if isinstance(kf[1], str)]
    best = named[0]
    for kf in named:
        if abs(kf[0] - day) < abs(best[0] - day):
            best = kf
    return best[1]


def split_minute(minute):
    """Which film are we in? Returns (name, short, minute_into_film, film_runtime)."""
    off = 0
    for name, short, rt in FILMS:
        if minute < off + rt:
            return name, short, minute - off, rt
        off += rt
    name, short, rt = FILMS[-1]
    return name, short, rt, rt


def fmt_tc(minutes):
    """Minutes (float) -> H:MM:SS timecode."""
    s = int(round(minutes * 60))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# --------------------------------------------------------------------------- Speed (log scale)
def speed_to_pos(speed):
    return math.log10(speed / SPEED_MIN) / math.log10(SPEED_MAX / SPEED_MIN)


def pos_to_speed(pos):
    pos = max(0.0, min(1.0, pos))
    return max(SPEED_MIN, min(SPEED_MAX, SPEED_MIN * (SPEED_MAX / SPEED_MIN) ** pos))


def fmt_speed(speed):
    return f"{speed:.0f}x" if speed >= 10 else f"{speed:.1f}x"


# --------------------------------------------------------------------------- Calibration I/O
def load_calibration():
    """If a locations.json exists, it overrides the default place positions."""
    if os.path.exists(CALIB_PATH):
        try:
            with open(CALIB_PATH, encoding="utf-8") as f:
                data = json.load(f)
            for name, xy in data.items():
                if name in LOCATIONS:
                    LOCATIONS[name] = (float(xy[0]), float(xy[1]))
            return True
        except (ValueError, OSError) as exc:
            print("Could not load locations.json:", exc)
    return False


# --------------------------------------------------------------------------- Background
def load_background_raw():
    """Load the map image (still unscaled) and adjust MAP_H to its aspect ratio.
    Must be called before setting the display mode (no .convert() here)."""
    global MAP_H
    if not os.path.exists(BG_PATH):
        return None
    img = pygame.image.load(BG_PATH)
    iw, ih = img.get_size()
    MAP_H = int(MAP_W * ih / iw)
    return img


def scale_background(raw):
    """Scale the loaded image onto the map area (after the display mode is set)."""
    return pygame.transform.smoothscale(raw.convert(), (MAP_W, MAP_H))


class SharpMap:
    """Renders the map straight from the SVG original, so it stays sharp at any
    zoom level (the PNG only has ~1000 px to stretch).

    Rendering runs in a background thread and covers a 3x3-screen area around the
    view, so panning stays sharp and zooming re-uses the last sharp image as a
    preview until the new one is ready. A whole-map render at OVERVIEW_SCALE x the
    screen resolution (made once at start) bridges the gaps, e.g. right after a
    fast zoom-out."""

    PARCHMENT = (244, 238, 222)        # background colour of the PNG version
    MARGIN = 3                         # rendered area = MARGIN x MARGIN screens
    SETTLE_S = 0.06                    # view must be still this long before re-rendering
    OVERVIEW_SCALE = 3                 # resolution of the whole-map fallback render
    DOT = b"@DOT@"                     # placeholder for the road-dot length
    DOT_PX = 0.6                       # road-dot length in screen px (shorter ones vanish)

    def __init__(self, svg_bytes):
        # Roads are drawn as zero-length dashes with round caps ("0,9"), which the
        # SVG renderer drops. Give the dots a length per render (see _worker).
        svg_bytes = re.sub(rb'stroke-dasharray="0,([0-9.]+)"',
                           lambda m: b'stroke-dasharray="' + self.DOT + b',' + m.group(1) + b'"',
                           svg_bytes)
        head_end = svg_bytes.index(b">", svg_bytes.index(b"<svg"))
        self.head, self.body = svg_bytes[:head_end], svg_bytes[head_end:]
        vb = re.search(rb'viewBox="([^"]*)"', self.head).group(1).replace(b",", b" ").split()
        self.vb_x, self.vb_y, self.vb_w, self.vb_h = (float(v) for v in vb)
        self.best = None                # (surface, (bx, by, bw, bh)) of the latest render
        self.ready = None               # finished render handed over by the worker thread
        self.busy = False
        self.lock = threading.Lock()
        self.wanted_key = None          # view we are waiting to settle
        self.wanted_since = 0.0
        full = (0.0, 0.0, float(MAP_W), float(MAP_H))
        self._worker(full, (MAP_W * self.OVERVIEW_SCALE, MAP_H * self.OVERVIEW_SCALE))
        self.overview, self.ready = self.ready, None    # whole map, sharp up to ~3x

    @classmethod
    def load(cls):
        if not os.path.exists(SVG_PATH):
            return None
        try:
            with open(SVG_PATH, "rb") as f:
                return cls(f.read())
        except (OSError, ValueError, AttributeError) as exc:
            print("Could not load map SVG, zoom stays blurry:", exc)
            return None

    # -- background rendering ------------------------------------------------
    def _worker(self, rect, size):
        bx, by, bw, bh = rect
        kx, ky = self.vb_w / MAP_W, self.vb_h / MAP_H     # base pixels -> SVG units
        px_per_unit = size[0] / (bw * kx)
        dot = b"%.4f" % max(0.01, self.DOT_PX / px_per_unit)
        view = b'viewBox="%.3f %.3f %.3f %.3f" width="%d" height="%d" preserveAspectRatio="none"' % (
            self.vb_x + bx * kx, self.vb_y + by * ky, bw * kx, bh * ky, size[0], size[1])
        try:
            svg = pygame.image.load(io.BytesIO(re.sub(rb'viewBox="[^"]*"', view, self.head)
                                               + self.body.replace(self.DOT, dot)), "map.svg")
            img = pygame.Surface(svg.get_size())          # opaque, parchment behind the ink
            img.fill(self.PARCHMENT)
            img.blit(svg, (0, 0))
        except (pygame.error, ValueError) as exc:
            print("SVG render failed:", exc)
            img = None
        with self.lock:
            self.ready = (img, rect)
            self.busy = False

    def _start(self, view):
        """Render a MARGIN x MARGIN area around `view` (clamped to the map)."""
        vx, vy, vw, vh = view
        bw, bh = min(MAP_W, vw * self.MARGIN), min(MAP_H, vh * self.MARGIN)
        bx = min(max(vx + vw / 2 - bw / 2, 0), MAP_W - bw)
        by = min(max(vy + vh / 2 - bh / 2, 0), MAP_H - bh)
        scale = MAP_W / vw                                # screen px per base px
        size = (max(1, round(bw * scale)), max(1, round(bh * scale)))
        self.busy = True
        threading.Thread(target=self._worker, args=((bx, by, bw, bh), size), daemon=True).start()

    def _needs_render(self, view):
        if self.best is None:
            return True
        img, (bx, by, bw, bh) = self.best
        vx, vy, vw, vh = view
        if abs((img.get_width() / bw) / (MAP_W / vw) - 1) > 0.01:     # zoom changed
            return True
        return not (bx - 0.5 <= vx and vx + vw <= bx + bw + 0.5 and
                    by - 0.5 <= vy and vy + vh <= by + bh + 0.5)

    def update(self, view):
        """Call once per frame with the visible base rect (bx, by, bw, bh)."""
        with self.lock:
            ready, self.ready = self.ready, None
        if ready is not None and ready[0] is not None:
            self.best = ready
        if self.busy or not self._needs_render(view):
            self.wanted_key = None
            return
        key = tuple(round(v, 2) for v in view)
        now = time.monotonic()
        if key != self.wanted_key:                         # still moving: wait a moment
            self.wanted_key, self.wanted_since = key, now
        elif now - self.wanted_since >= self.SETTLE_S:
            self.wanted_key = None
            self._start(view)

    # -- drawing -------------------------------------------------------------
    def draw(self, surf, view):
        """Draw the map for `view`: the latest sharp render where it covers the
        view, the whole-map overview underneath. Returns False if neither exists."""
        if self.best is not None and _covers(self.best[1], view):
            return self._blit_part(surf, self.best, view)
        ok = self.overview is not None and self.overview[0] is not None
        if ok:
            self._blit_part(surf, self.overview, view)
        if self.best is not None:
            self._blit_part(surf, self.best, view)          # sharp part on top
            ok = True
        return ok

    @staticmethod
    def _blit_part(surf, render, view):
        """Blit the part of `render` = (image, base rect) that lies inside `view`."""
        img, (bx, by, bw, bh) = render
        vx, vy, vw, vh = view
        # intersection of the view and the rendered area, in base pixels
        ix0, iy0 = max(vx, bx), max(vy, by)
        ix1, iy1 = min(vx + vw, bx + bw), min(vy + vh, by + bh)
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        sx, sy = img.get_width() / bw, img.get_height() / bh   # image px per base px
        dx, dy = MAP_W / vw, MAP_H / vh                         # screen px per base px
        src = pygame.Rect(int((ix0 - bx) * sx), int((iy0 - by) * sy),
                          max(1, int((ix1 - ix0) * sx)), max(1, int((iy1 - iy0) * sy)))
        src = src.clip(img.get_rect())
        dst = pygame.Rect(int((ix0 - vx) * dx), int((iy0 - vy) * dy),
                          max(1, round((ix1 - ix0) * dx)), max(1, round((iy1 - iy0) * dy)))
        if src.w <= 0 or src.h <= 0:
            return False
        if abs(src.w - dst.w) <= 1 and abs(src.h - dst.h) <= 1:
            surf.blit(img, dst.topleft, src)                    # 1:1 - fully sharp
        else:
            surf.blit(pygame.transform.smoothscale(img.subsurface(src), dst.size), dst.topleft)
        return True


def draw_map_background(surf, bg, sharp=None):
    """Blit the (zoomed) visible portion of the map into the map area."""
    if ZOOM <= 1.0001:
        surf.blit(bg, (0, 0))
        return
    vw, vh = MAP_W / ZOOM, MAP_H / ZOOM
    view = (CAM_X - vw / 2, CAM_Y - vh / 2, vw, vh)         # same view as world() uses
    if sharp is not None:
        sharp.update(view)
        if sharp.draw(surf, view):
            return
    _draw_png_crop(surf, bg)


def _covers(rect, view):
    bx, by, bw, bh = rect
    vx, vy, vw, vh = view
    return (bx - 0.5 <= vx and by - 0.5 <= vy and
            vx + vw <= bx + bw + 0.5 and vy + vh <= by + bh + 0.5)


def _draw_png_crop(surf, bg):
    """Quick but blurry: stretch the matching crop of the 1000 px PNG."""
    sw, sh = MAP_W / ZOOM, MAP_H / ZOOM
    sx = int(min(max(CAM_X - sw / 2, 0), MAP_W - sw))
    sy = int(min(max(CAM_Y - sh / 2, 0), MAP_H - sh))
    sw, sh = max(1, int(sw)), max(1, int(sh))
    view = bg.subsurface(pygame.Rect(sx, sy, sw, sh))
    surf.blit(pygame.transform.smoothscale(view, (MAP_W, MAP_H)), (0, 0))


# --------------------------------------------------------------------------- Map markers
# Routes. Look: like a modern navigation app (outlined route, direction chevrons, a
# "you are here" puck) but drawn as ink on parchment: sepia casing, a dashed
# quill line for the road still ahead, stations as small seals.
INK = (52, 36, 24)                 # sepia ink for casings and outlines
INK_SOFT = (52, 36, 24, 150)
ROUTE_W = 5                        # core width of the travelled route (px)
CASING = 2                         # ink border on each side (px)
HOVER_TRACK = None                 # name of the route under the mouse (or None)
LEGEND_ROWS = []                   # [(pygame.Rect, track name)] filled by draw_panel
_SPLINES = {}
_SCREEN_CACHE = {"key": None}      # routes in screen px + the dashed layer, per view
_LABEL_FONT = None


def _route(track):
    """Cached dense polyline of a whole route (normalised)."""
    if track["name"] not in _SPLINES:
        _SPLINES[track["name"]] = full_spline(track)
    return _SPLINES[track["name"]]


def _view_key():
    return (round(ZOOM, 4), round(CAM_X, 2), round(CAM_Y, 2))


def _screen_routes():
    """Every route as screen points; recomputed only when the view changes."""
    key = _view_key()
    if _SCREEN_CACHE.get("routes_key") != key:
        _SCREEN_CACHE["routes"] = {t["name"]: [world(p) for p in _route(t)] for t in TRACKS}
        _SCREEN_CACHE["routes_key"] = key
    return _SCREEN_CACHE["routes"]


def _traveled_px(track, day):
    """Screen polyline of the part already travelled (prefix of the cached route
    plus the current, partly travelled leg)."""
    kfs = track["keyframes"]
    full = _screen_routes()[track["name"]]
    if day >= kfs[-1][0]:
        return full
    pts = _control_points(track)
    for i in range(len(kfs) - 1):
        d0, d1 = kfs[i][0], kfs[i + 1][0]
        if d0 <= day < d1:
            done = full[:i * SEG_SAMPLES + 1]
            t = (day - d0) / (d1 - d0)
            done += [world(cr_eval(pts, i, t * s / SEG_SAMPLES)) for s in range(1, SEG_SAMPLES + 1)]
            return done
    return full[:1]


def _stations(track):
    """Named places of a route in travel order: [(day of arrival, name)], no repeats."""
    out = []
    for day, loc in track["keyframes"]:
        if isinstance(loc, str) and (not out or out[-1][1] != loc):
            out.append((day, loc))
    return out


def _mix(c, other, t):
    return tuple(int(a * (1 - t) + b * t) for a, b in zip(c, other))


# Route graphics are drawn at SS x resolution and scaled down smoothly: pygame
# draws thick lines and circles without anti-aliasing, so this keeps the ink
# outlines from looking pixelated. All functions below take screen (1x) points.
SS = 2


def _ss(pts):
    return [(x * SS, y * SS) for x, y in pts]


def _thick_line(surf, color, pts, width):
    """Polyline with round joins and caps (pygame's own thick lines have gaps)."""
    if len(pts) < 2:
        return
    # Drop points closer than ~2 px to the previous one (the spline is dense).
    kept = [pts[0]]
    for p in pts[1:-1]:
        if abs(p[0] - kept[-1][0]) + abs(p[1] - kept[-1][1]) >= 2.5:
            kept.append(p)
    kept.append(pts[-1])
    pts = _ss(kept)
    h = width * SS / 2
    lo_x, lo_y, hi_x, hi_y = -h, -h, MAP_W * SS + h, MAP_H * SS + h
    # One filled quad per segment: clean edges (pygame's wide lines are built
    # from parallel 1 px lines and look ragged), round joins and caps.
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        if (max(ax, bx) < lo_x or min(ax, bx) > hi_x or
                max(ay, by) < lo_y or min(ay, by) > hi_y):
            continue                                   # off screen
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy)
        if ln < 1e-6:
            continue
        nx, ny = -dy / ln * h, dx / ln * h
        pygame.draw.polygon(surf, color, ((ax + nx, ay + ny), (bx + nx, by + ny),
                                          (bx - nx, by - ny), (ax - nx, ay - ny)))
        pygame.draw.circle(surf, color, (bx, by), h)
    if lo_x <= pts[0][0] <= hi_x and lo_y <= pts[0][1] <= hi_y:
        pygame.draw.circle(surf, color, pts[0], h)


def _dashes(pts, on, off):
    """Split a polyline into dash pieces of `on` px separated by `off` px."""
    pieces, cur, pos, drawing = [], [pts[0]], 0.0, True
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        t0 = 0.0
        while seg > 0 and t0 < seg:
            left = (on if drawing else off) - pos
            step = min(left, seg - t0)
            t0 += step
            pos += step
            p = (a[0] + (b[0] - a[0]) * t0 / seg, a[1] + (b[1] - a[1]) * t0 / seg)
            if drawing:
                cur.append(p)
            if pos >= (on if drawing else off) - 1e-6:
                if drawing and len(cur) > 1:
                    pieces.append(cur)
                drawing, pos, cur = not drawing, 0.0, [p]
    if drawing and len(cur) > 1:
        pieces.append(cur)
    return pieces


def _chevrons(surf, pts, color, every=64):
    """Small direction arrows along a polyline, like a navigation app."""
    dist_to_next = every * 0.6
    s = 3.2
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        while seg > 0 and dist_to_next <= seg:
            t = dist_to_next / seg
            cx, cy = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            dx, dy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
            tip = (cx + dx * s, cy + dy * s)
            l = (cx - dx * s - dy * s, cy - dy * s + dx * s)
            r = (cx - dx * s + dy * s, cy - dy * s - dx * s)
            pygame.draw.lines(surf, color, False, _ss([l, tip, r]), 2 * SS)
            dist_to_next += every
        dist_to_next -= seg


FADE = 0.68                        # how far non-hovered routes fade into the parchment


def _fade(color, fade):
    """Colour washed towards the parchment (used for routes that step back)."""
    if not fade:
        return color
    rgb = _mix(color[:3], PARCHMENT, FADE)
    return (*rgb, color[3]) if len(color) == 4 else rgb


def _circle(surf, color, p, r):
    pygame.draw.circle(surf, color, (p[0] * SS, p[1] * SS), r * SS)


def _big_layer(slot):
    """Reusable transparent SS x layer (one per slot), cleared."""
    size = (MAP_W * SS, MAP_H * SS)
    layer = _SCREEN_CACHE.get(slot)
    if layer is None or layer.get_size() != size:
        layer = _SCREEN_CACHE[slot] = pygame.Surface(size, pygame.SRCALPHA)
    layer.fill((0, 0, 0, 0))
    return layer


def _bbox(tracks):
    """Screen rect around the given routes (plus room for seals and halos)."""
    key = ("bbox", _view_key(), tuple(t["name"] for t in tracks))
    if key not in _SCREEN_CACHE:
        pts = [p for t in tracks for p in _screen_routes()[t["name"]]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        r = pygame.Rect(min(xs) - 24, min(ys) - 24, max(xs) - min(xs) + 48, max(ys) - min(ys) + 48)
        _SCREEN_CACHE[key] = r.clip(pygame.Rect(0, 0, MAP_W, MAP_H))
    return _SCREEN_CACHE[key]


def _blit_down(surf, layer, rect, alpha=None):
    """Scale the `rect` part of an SS x layer down onto the map (the anti-aliasing)."""
    if rect.w <= 0 or rect.h <= 0:
        return
    src = pygame.Rect(rect.x * SS, rect.y * SS, rect.w * SS, rect.h * SS)
    img = pygame.transform.smoothscale(layer.subsurface(src), rect.size)
    if alpha is not None:
        img.set_alpha(alpha)
    surf.blit(img, rect.topleft)


def _draw_ahead(layer, track, highlight, fade=False):
    """The whole route as a dashed quill line (the road still ahead shows through)."""
    col = track["color"]
    ahead_col = _fade(col if highlight else _mix(col, INK, 0.35), fade)
    casing = _fade(INK if highlight else INK_SOFT, fade)
    for piece in _dashes(_screen_routes()[track["name"]], 9, 7):
        _thick_line(layer, casing, piece, 6 if highlight else 4)
        _thick_line(layer, ahead_col, piece, 3 if highlight else 2)


def _ahead_layer(hovered):
    """Dashed lines of all non-hovered routes, cached until view or hover change."""
    key = (_view_key(), hovered["name"] if hovered else None)
    if _SCREEN_CACHE.get("ahead_key") != key:
        big = _big_layer("big_ahead")
        for track in TRACKS:
            if track is not hovered:
                _draw_ahead(big, track, False, fade=hovered is not None)
        if hovered is not None:
            _draw_ahead(big, hovered, True)
        layer = pygame.Surface((MAP_W, MAP_H), pygame.SRCALPHA)
        _blit_down(layer, big, _bbox(TRACKS))
        _SCREEN_CACHE["ahead"], _SCREEN_CACHE["ahead_key"] = layer, key
    return _SCREEN_CACHE["ahead"]


def _draw_route(layer, track, day, highlight, fade=False):
    """Draw the travelled part of one route (ink casing, coloured core, chevrons)."""
    col = _fade(track["color"], fade)
    w = ROUTE_W + (2 if highlight else 0)
    if day < track["keyframes"][0][0]:
        return None
    done = _traveled_px(track, day)
    if len(done) > 1:
        _thick_line(layer, _fade(INK, fade), done, w + 2 * CASING)   # ink casing
        _thick_line(layer, col, done, w)                          # coloured core
        _chevrons(layer, done, _mix(col, (255, 250, 236), 0.75))  # direction
    return done[-1]


def _draw_station(surf, p, fill, ring, r=5, fade=False):
    fill, ring = _fade(fill, fade), _fade(ring, fade)
    _circle(surf, ring, p, r + 2)
    _circle(surf, fill, p, r)
    _circle(surf, ring, p, max(1, r - 3))                      # seal centre


# Group emblems drawn on the position seal and in the legend. Shapes use
# normalised coordinates (-1..1, y down) around the seal centre.
def _xf(pts, cx, cy, s, rot=0.0):
    c, sn = math.cos(rot), math.sin(rot)
    return [(cx + (x * c - y * sn) * s, cy + (x * sn + y * c) * s) for x, y in pts]


def _icon_star(surf, cx, cy, s, fg, bg):
    """Eight-pointed Elven star (as on the Doors of Durin): the Fellowship."""
    pts = [((0.82 if i % 2 == 0 else 0.34) * math.sin(i * math.pi / 8),
            -(0.82 if i % 2 == 0 else 0.34) * math.cos(i * math.pi / 8)) for i in range(16)]
    pygame.draw.polygon(surf, fg, _xf(pts, cx, cy, s))
    pygame.draw.circle(surf, bg, (cx, cy), 0.14 * s)


def _icon_ring(surf, cx, cy, s, fg, bg):
    """The One Ring: Frodo & Sam."""
    pygame.draw.circle(surf, fg, (cx, cy), 0.66 * s)
    pygame.draw.circle(surf, bg, (cx, cy), 0.40 * s)
    pygame.draw.circle(surf, _mix(fg, (255, 255, 255), 0.55),     # glint
                       (cx - 0.30 * s, cy - 0.40 * s), 0.10 * s)


def _icon_sword(surf, cx, cy, s, fg, bg):
    """Anduril, the sword that was broken: Aragorn, Legolas & Gimli."""
    rot = math.radians(45)
    blade = [(0, -0.92), (0.13, -0.70), (0.13, 0.30), (-0.13, 0.30), (-0.13, -0.70)]
    guard = [(-0.48, 0.28), (0.48, 0.28), (0.48, 0.42), (-0.48, 0.42)]
    grip = [(-0.08, 0.42), (0.08, 0.42), (0.08, 0.72), (-0.08, 0.72)]
    for shape in (blade, guard, grip):
        pygame.draw.polygon(surf, fg, _xf(shape, cx, cy, s, rot))
    pygame.draw.circle(surf, fg, _xf([(0, 0.80)], cx, cy, s, rot)[0], 0.13 * s)


def _icon_leaf(surf, cx, cy, s, fg, bg):
    """Leaf brooch of Lorien (Pippin dropped his as a sign): Merry & Pippin."""
    rot = math.radians(35)
    n = 12
    right = [(0.46 * math.sin(math.pi * i / n), -0.86 + 1.5 * i / n) for i in range(n + 1)]
    left = [(-x, y) for x, y in reversed(right)]
    pygame.draw.polygon(surf, fg, _xf(right + left, cx, cy, s, rot))
    vein = _xf([(0, -0.62), (0, 0.84)], cx, cy, s, rot)            # midrib + stem
    pygame.draw.line(surf, bg, vein[0], _xf([(0, 0.60)], cx, cy, s, rot)[0], max(1, int(0.10 * s)))
    pygame.draw.line(surf, fg, _xf([(0, 0.60)], cx, cy, s, rot)[0], vein[1], max(1, int(0.12 * s)))


def _icon_hat(surf, cx, cy, s, fg, bg):
    """Pointed wizard's hat: Gandalf."""
    cone = [(-0.40, 0.38), (0.36, 0.38), (0.20, -0.30), (0.38, -0.86), (-0.02, -0.40)]
    pygame.draw.polygon(surf, fg, _xf(cone, cx, cy, s))
    pygame.draw.ellipse(surf, fg, pygame.Rect(cx - 0.80 * s, cy + 0.28 * s, 1.60 * s, 0.34 * s))
    pygame.draw.line(surf, bg, (cx - 0.34 * s, cy + 0.22 * s), (cx + 0.31 * s, cy + 0.22 * s),
                     max(1, int(0.10 * s)))                          # hat band


ICONS = {"star": _icon_star, "ring": _icon_ring, "sword": _icon_sword,
         "leaf": _icon_leaf, "hat": _icon_hat}


def _glyph_colour(color):
    """Light glyph on dark seals, ink glyph on light ones."""
    lum = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
    return INK if lum > 170 else (250, 244, 228)


def _seal(surf, cx, cy, r, color, icon, ring=INK):
    """A seal: ink ring, disc in the group's colour, emblem on top (target px)."""
    pygame.draw.circle(surf, ring, (cx, cy), r + max(2, r * 0.18))
    pygame.draw.circle(surf, color, (cx, cy), r)
    ICONS.get(icon, _icon_star)(surf, cx, cy, r * 0.82, _glyph_colour(color), color)


def _draw_puck(surf, p, color, highlight, fade=False, icon="star"):
    """Current position: the group's seal with its emblem (no halo, so the line
    runs cleanly into the seal where the solid route turns into the dashed one)."""
    r = (12 if highlight else 10) * SS
    _seal(surf, p[0] * SS, p[1] * SS, r, _fade(color, fade), icon, _fade(INK, fade))


def legend_seal(surf, center, color, icon, r=9):
    """Small anti-aliased seal for the legend (drawn at SS x and scaled down)."""
    size = int((r + 4) * 2)
    big = pygame.Surface((size * SS, size * SS), pygame.SRCALPHA)
    _seal(big, size * SS / 2, size * SS / 2, r * SS, color, icon)
    small = pygame.transform.smoothscale(big, (size, size))
    surf.blit(small, (center[0] - size // 2, center[1] - size // 2))


def _draw_label(surf, p, text, placed):
    """Small parchment tag with the station name (shown for the hovered route).
    Tries above / below / right / left of the station and skips the label if all
    spots overlap tags in `placed` or leave the map."""
    global _LABEL_FONT
    if _LABEL_FONT is None:
        _LABEL_FONT = pygame.font.SysFont("Palatino,Georgia,serif", 14, bold=True)
    img = _LABEL_FONT.render(text, True, INK)
    box = img.get_rect()
    box.inflate_ip(10, 4)
    x, y = int(p[0]), int(p[1])
    area = pygame.Rect(0, 0, MAP_W, MAP_H)
    for attr, pos in (("midbottom", (x, y - 9)), ("midtop", (x, y + 9)),
                      ("midleft", (x + 11, y)), ("midright", (x - 11, y))):
        setattr(box, attr, pos)
        if area.contains(box) and box.collidelist(placed) < 0:
            break
    else:
        return
    placed.append(box.copy())
    pygame.draw.rect(surf, (250, 244, 228), box, border_radius=4)
    pygame.draw.rect(surf, INK, box, 1, border_radius=4)
    surf.blit(img, img.get_rect(center=box.center))


def draw_waypoints(surf):
    """Places that are on no route (e.g. the Grey Havens): small ink dots."""
    on_route = {loc for t in TRACKS for _, loc in t["keyframes"] if isinstance(loc, str)}
    on_route |= {f["location"] for f in FALLEN}      # those get a memorial instead
    for name, p in LOCATIONS.items():
        if name not in on_route:
            pygame.draw.circle(surf, INK, world(p), 3)


def _fan_out(pucks, near=14, radius=11):
    """Groups at the same spot (e.g. all waiting in Minas Tirith) would hide each
    other's seals: spread such clusters evenly around their centre."""
    out, done = [], set()
    for i, (p, t) in enumerate(pucks):
        if i in done:
            continue
        group = [j for j, (q, _) in enumerate(pucks)
                 if j not in done and math.dist(p, q) < near]
        done.update(group)
        if len(group) == 1:
            out.append((p, t))
            continue
        cx = sum(pucks[j][0][0] for j in group) / len(group)
        cy = sum(pucks[j][0][1] for j in group) / len(group)
        for k, j in enumerate(group):
            a = -math.pi / 2 + 2 * math.pi * k / len(group)
            out.append(((cx + radius * math.cos(a), cy + radius * math.sin(a)), pucks[j][1]))
    return out


def draw_tracks(surf, day):
    """All routes, their stations and current positions; the hovered route on top
    (the others fade into the parchment). Everything is drawn on one SS x layer
    and scaled down once."""
    hovered = next((t for t in TRACKS if t["name"] == HOVER_TRACK), None)
    fade = hovered is not None
    surf.blit(_ahead_layer(hovered), (0, 0))
    big = _big_layer("big")
    pucks = []
    for track in TRACKS:
        if track is hovered:
            continue
        pos = _draw_route(big, track, day, False, fade)
        if pos is not None and track_active(track, day):
            pucks.append((pos, track))
    # Stations: one seal per place, filled in the colour of the group that got
    # there last (hollow while nobody has reached it yet).
    latest = {}
    for track in TRACKS:
        if track is hovered:
            continue
        for d, loc in _stations(track):
            prev = latest.get(loc)
            if prev is None or (d <= day and (prev[0] > day or d >= prev[0])):
                latest[loc] = (d, track["color"])
    for loc, (d, color) in latest.items():
        _draw_station(big, world(LOCATIONS[loc]), color if d <= day else (250, 244, 228), INK,
                      fade=fade)
    for pos, track in _fan_out(pucks):
        _draw_puck(big, pos, track["color"], False, fade, track.get("icon", "star"))

    first = {}
    if hovered is not None:
        pos = _draw_route(big, hovered, day, True)
        for d, loc in _stations(hovered):               # first arrival per place
            first.setdefault(loc, d)
        for loc, d in first.items():
            _draw_station(big, world(LOCATIONS[loc]),
                          hovered["color"] if day >= d else (250, 244, 228), INK, 6)
        if pos is not None and track_active(hovered, day):
            _draw_puck(big, pos, hovered["color"], True, icon=hovered.get("icon", "star"))
    _blit_down(surf, big, _bbox(TRACKS))
    placed = []
    for loc in first:
        _draw_label(surf, world(LOCATIONS[loc]), loc, placed)


def _seg_dist(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0 if l2 == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


# --------------------------------------------------------------------------- Mouse cursor
# Over the map the pointer is an armoured gauntlet: open while hovering, a
# closed fist that "grabs" the map while you drag it. Drawn in a 32-unit grid
# at 4x and scaled down, so the edges stay smooth.
STEEL = (104, 92, 82)               # dark, greyish-brown blackened iron
STEEL_HI = (150, 138, 124)          # worn edges catching the light
STEEL_DK = (70, 61, 54)             # cuff
CURSOR_INK = (20, 16, 14)
CURSOR_RIM = (236, 226, 204)      # light parchment rim around the silhouette
CURSOR_PX = 32
_CURSORS = {}
_CURSOR_STATE = None


def _capsule(surf, color, a, b, r, k):
    """Thick segment a->b with round ends (grid units, k px per unit)."""
    ax, ay, bx, by = a[0] * k, a[1] * k, b[0] * k, b[1] * k
    dx, dy = bx - ax, by - ay
    ln = math.hypot(dx, dy) or 1
    nx, ny = -dy / ln * r * k, dx / ln * r * k
    pygame.draw.polygon(surf, color, ((ax + nx, ay + ny), (bx + nx, by + ny),
                                      (bx - nx, by - ny), (ax - nx, ay - ny)))
    pygame.draw.circle(surf, color, (ax, ay), r * k)
    pygame.draw.circle(surf, color, (bx, by), r * k)


def _rrect_pts(x, y, w, h, r, steps=5):
    """Outline points of a rounded rectangle (grid units)."""
    pts = []
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0),
                       (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _gauntlet(closed):
    """Cursor surface + hotspot. Open: a pointing gauntlet seen from the back,
    index finger stretched diagonally up-left like an arrow pointer, thumb out to
    the left, the other fingers curled. Closed: a fist from the back (the thumb
    is hidden in front), grabbing the map."""
    k = 8                                                   # drawn at 8x, scaled down
    surf = pygame.Surface((CURSOR_PX * k, CURSOR_PX * k), pygame.SRCALPHA)
    if closed:
        rot, shift = math.radians(-12), (0.0, -1.6)
    else:
        rot, shift = math.radians(-30), (-1.6, -1.2)
    c, sn = math.cos(rot), math.sin(rot)

    def T(p):                                               # rotate around the centre + shift
        x, y = p[0] - 16, p[1] - 16
        return (16 + x * c - y * sn + shift[0], 16 + x * sn + y * c + shift[1])

    def caps(color, a, b, r):
        _capsule(surf, color, T(a), T(b), r, k)

    def poly(color, pts):
        pygame.draw.polygon(surf, color, [(x * k, y * k) for x, y in map(T, pts)])

    o = 0.85                                                # ink outline (units)
    hand = (10.5, 13.0, 12.5, 11.0, 3.4)                   # back of the hand
    cuff = (10.0, 23.2, 13.5, 6.8, 1.8)
    if closed:
        hand = (9.0, 13.0, 14.5, 11.0, 3.4)
        cuff = (9.4, 23.2, 13.8, 6.8, 1.8)
        index = None
        thumb = ((10.0, 21.6), (7.9, 16.2), 1.7)           # side of the thumb, left of the fist
        curled = [((10.9, 11.4), (10.9, 14.5), 1.85), ((14.5, 10.9), (14.5, 14.5), 1.9),
                  ((18.1, 10.9), (18.1, 14.5), 1.9), ((21.6, 11.6), (21.6, 14.8), 1.75)]
    else:
        index = ((13.0, 15.0), (13.0, 3.2), 1.95)
        thumb = ((12.0, 20.5), (5.6, 15.4), 1.95)
        curled = [((17.2, 13.6), (17.2, 16.8), 1.85), ((20.3, 14.0), (20.3, 17.2), 1.8),
                  ((22.9, 15.2), (22.7, 18.2), 1.6)]
    fingers = ([index] if index else []) + ([thumb] if thumb else []) + curled

    # 0) thin light rim so the dark gauntlet stays visible on dark map areas
    rim = o + 0.45
    for a, b, r in fingers:
        caps(CURSOR_RIM, a, b, r + rim)
    poly(CURSOR_RIM, _rrect_pts(hand[0] - rim, hand[1] - rim, hand[2] + 2 * rim, hand[3] + 2 * rim, hand[4] + rim))
    poly(CURSOR_RIM, _rrect_pts(cuff[0] - rim, cuff[1] - rim, cuff[2] + 2 * rim, cuff[3] + 2 * rim, cuff[4] + rim))
    # 1) ink silhouette, one outline width larger
    for a, b, r in fingers:
        caps(CURSOR_INK, a, b, r + o)
    poly(CURSOR_INK, _rrect_pts(hand[0] - o, hand[1] - o, hand[2] + 2 * o, hand[3] + 2 * o, hand[4] + o))
    poly(CURSOR_INK, _rrect_pts(cuff[0] - o, cuff[1] - o, cuff[2] + 2 * o, cuff[3] + 2 * o, cuff[4] + o))
    # 2) steel plates (the fist's thumb sits behind the hand, only its side shows)
    poly(STEEL_DK, _rrect_pts(*cuff))
    if closed:
        caps(STEEL, *thumb[:2], thumb[2])
    poly(STEEL, _rrect_pts(*hand))
    if closed:                                              # edge where thumb meets hand
        caps(CURSOR_INK, (hand[0] + 0.15, hand[1] + 6.5), (hand[0] + 0.15, hand[1] + 10.6), 0.32)
    for a, b, r in fingers:
        if closed and (a, b, r) == thumb:
            continue
        caps(STEEL, a, b, r)
    # 3) plate edges (joints), knuckle line, highlights, cuff rivets
    for a, b, r in fingers:
        ts = (0.36, 0.68) if (a, b, r) == index else (0.55,)
        for t in ts:
            px, py = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            ln = math.hypot(b[0] - a[0], b[1] - a[1]) or 1
            nx, ny = -(b[1] - a[1]) / ln * r * 0.85, (b[0] - a[0]) / ln * r * 0.85
            caps(CURSOR_INK, (px + nx, py + ny), (px - nx, py - ny), 0.3)
        caps(STEEL_HI, (a[0] - r * 0.35, a[1] + (b[1] - a[1]) * 0.12),
             (b[0] - r * 0.35, b[1] - (b[1] - a[1]) * 0.12), 0.42)
    kx0, kx1 = hand[0] + 1.2, hand[0] + hand[2] - 1.2
    caps(CURSOR_INK, (kx0, hand[1] + 3.6), (kx1, hand[1] + 3.6), 0.3)       # knuckle plate edge
    caps(STEEL_HI, (kx0 + 0.6, hand[1] + 5.4), (kx1 - 1.2, hand[1] + 5.4), 0.55)
    caps(CURSOR_INK, (cuff[0] + 0.6, cuff[1] + 0.2), (cuff[0] + cuff[2] - 0.6, cuff[1] + 0.2), 0.45)
    for x in (cuff[0] + 3.0, cuff[0] + cuff[2] / 2, cuff[0] + cuff[2] - 3.0):
        cx, cy = T((x, cuff[1] + 3.8))
        pygame.draw.circle(surf, STEEL_HI, (cx * k, cy * k), 0.8 * k)

    img = pygame.transform.smoothscale(surf, (CURSOR_PX, CURSOR_PX))
    if index:                                               # click point = fingertip
        tip = T((index[1][0], index[1][1] - index[2] + 0.4))
        hot = (max(0, int(round(tip[0]))), max(0, int(round(tip[1]))))
    else:
        hot = (16, 16)
    return img, hot


def set_cursor(state):
    """state: 'arrow', 'point' (legend), 'open' (over the map) or 'grab' (dragging).
    The gauntlet is drawn by draw_cursor() into the frame instead of being a
    system cursor: macOS shows custom SDL cursors at half resolution on Retina
    screens (they look pixelated), the frame itself is scaled smoothly."""
    global _CURSOR_STATE
    if state == _CURSOR_STATE:
        return
    _CURSOR_STATE = state
    try:
        if state in ("open", "grab"):
            pygame.mouse.set_visible(False)
        else:
            pygame.mouse.set_visible(True)
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND if state == "point"
                                    else pygame.SYSTEM_CURSOR_ARROW)
    except (pygame.error, AttributeError, TypeError):
        pass


def draw_cursor(surf):
    """Draw the gauntlet at the mouse position (call last, right before flip)."""
    if _CURSOR_STATE not in ("open", "grab") or not pygame.mouse.get_focused():
        return
    if _CURSOR_STATE not in _CURSORS:
        _CURSORS[_CURSOR_STATE] = _gauntlet(_CURSOR_STATE == "grab")
    img, hot = _CURSORS[_CURSOR_STATE]
    mx, my = pygame.mouse.get_pos()
    surf.blit(img, (mx - hot[0], my - hot[1]))


def update_hover(mouse, drag=None):
    """Find the route under the mouse (a legend row, or a line on the map) and
    pick the mouse cursor: gauntlet over the map, a closed fist while panning."""
    global HOVER_TRACK
    name = None
    on_legend = False
    on_map = mouse[0] < MAP_W and mouse[1] < MAP_H
    if drag is None:
        for rect, track_name in LEGEND_ROWS:
            if rect.collidepoint(mouse):
                name, on_legend = track_name, True
        if name is None and on_map:
            best = 9.0                                       # hit radius in px
            for track in TRACKS:
                pts = _screen_routes()[track["name"]]
                for a, b in zip(pts, pts[1:]):
                    d = _seg_dist(mouse, a, b)
                    if d < best:
                        best, name = d, track["name"]
    HOVER_TRACK = name
    if drag == "pan":
        set_cursor("grab")
    elif drag is None and on_map:
        set_cursor("open")
    elif on_legend:
        set_cursor("point")
    else:
        set_cursor("arrow")


DARK = (16, 14, 18)


def _bez(p0, p1, p2, t):
    u = 1 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


def _bez_d(p0, p1, p2, t):
    return (2 * (1 - t) * (p1[0] - p0[0]) + 2 * t * (p2[0] - p1[0]),
            2 * (1 - t) * (p1[1] - p0[1]) + 2 * t * (p2[1] - p1[1]))


def draw_horn(surf, cx, cy, color, scale=1.0):
    """The Horn of Gondor: a tapered, curved horn with a flared bell and bands."""
    P0, P1, P2 = (-9, 7), (-1, -10), (12, -5)        # tip -> bell (upper right)
    N = 18
    top, bot = [], []
    for i in range(N + 1):
        t = i / N
        bx, by = _bez(P0, P1, P2, t)
        dx, dy = _bez_d(P0, P1, P2, t)
        ln = math.hypot(dx, dy) or 1
        nx, ny = -dy / ln, dx / ln                   # normal
        w = (0.7 + 4.6 * t ** 1.6) * scale
        top.append((cx + bx * scale + nx * w, cy + by * scale + ny * w))
        bot.append((cx + bx * scale - nx * w, cy + by * scale - ny * w))
    poly = top + bot[::-1]
    pygame.draw.polygon(surf, color, poly)
    pygame.draw.polygon(surf, DARK, poly, max(1, int(1.6 * scale)))
    bell = _bez(P0, P1, P2, 1.0)                      # open mouth of the bell
    pygame.draw.circle(surf, DARK, (int(cx + bell[0] * scale), int(cy + bell[1] * scale)),
                       max(1, int(2.2 * scale)))
    for tb in (0.42, 0.6):                            # silver bands
        bx, by = _bez(P0, P1, P2, tb)
        dx, dy = _bez_d(P0, P1, P2, tb)
        ln = math.hypot(dx, dy) or 1
        nx, ny = -dy / ln, dx / ln
        w = (0.7 + 4.6 * tb ** 1.6) * scale
        pygame.draw.line(surf, DARK,
                         (cx + bx * scale + nx * w, cy + by * scale + ny * w),
                         (cx + bx * scale - nx * w, cy + by * scale - ny * w),
                         max(1, int(1.2 * scale)))


def draw_grey_hat(surf, cx, cy, color, scale=1.0):
    """Gandalf the Grey's pointed hat (same emblem as on Gandalf's route seal)."""
    _icon_hat(surf, cx, cy, 10.5 * scale, color, (26, 24, 30))


# Symbols a fallen character can be marked with.
SYMBOLS = {"horn": draw_horn, "hat": draw_grey_hat}


def draw_symbol(surf, cx, cy, color, kind, scale=1.0):
    SYMBOLS.get(kind, draw_horn)(surf, cx, cy, color, scale)


def draw_fallen(surf, day):
    """Memorial markers for characters who have fallen by the given day.
    Drawn as a heraldic medallion (silver-on-sable, in Gondor's colours)."""
    for f in FALLEN:
        if day >= f["day"]:
            x, y = world(LOCATIONS[f["location"]])
            r = 12                                                     # drawn at SS x, scaled down
            size = (r + 4) * 2
            big = pygame.Surface((size * SS, size * SS), pygame.SRCALPHA)
            c = (size * SS // 2, size * SS // 2)
            pygame.draw.circle(big, INK, c, (r + 2) * SS)                      # ink ring
            pygame.draw.circle(big, (26, 24, 30), c, r * SS)                   # dark field (sable)
            pygame.draw.circle(big, (170, 175, 188), c, (r - 1) * SS, SS)      # silver rim
            draw_symbol(big, c[0], c[1], f["color"], f.get("symbol", "horn"), scale=0.85 * SS)
            surf.blit(pygame.transform.smoothscale(big, (size, size)), (x - size // 2, y - size // 2))


# --------------------------------------------------------------------------- HUD panel
def draw_panel(surf, fonts, minute, day, playing):
    px = MAP_W
    pygame.draw.rect(surf, PANEL_BG, (px, 0, PANEL_W, MAP_H))
    pygame.draw.rect(surf, GOLD, (px, 0, 3, MAP_H))

    _, _, date, film, title, text = current_event(minute)
    name, short, tif, rt = split_minute(minute)
    pad = 22
    y = 22

    surf.blit(fonts["panel_lbl"].render("DATE", True, HUD_DIM), (px + pad, y)); y += 22
    surf.blit(fonts["panel_big"].render(date, True, GOLD), (px + pad, y)); y += 42

    fc = FILM_COLORS.get(film, HUD_TEXT)
    surf.blit(fonts["panel_lbl"].render("FILM (EXTENDED)", True, HUD_DIM), (px + pad, y)); y += 22
    for line in wrap_text(film, fonts["panel_med"], PANEL_W - 2 * pad):
        surf.blit(fonts["panel_med"].render(line, True, fc), (px + pad, y)); y += 26
    surf.blit(fonts["panel_sm"].render(f"{fmt_tc(tif)} / {fmt_tc(rt)}", True, HUD_DIM),
              (px + pad, y)); y += 26

    surf.blit(fonts["panel_lbl"].render("EVENT", True, HUD_DIM), (px + pad, y)); y += 24
    for line in wrap_text(title, fonts["panel_med"], PANEL_W - 2 * pad):
        surf.blit(fonts["panel_med"].render(line, True, HUD_TEXT), (px + pad, y)); y += 26
    y += 4
    for line in wrap_text(text, fonts["panel_sm"], PANEL_W - 2 * pad):
        surf.blit(fonts["panel_sm"].render(line, True, HUD_DIM), (px + pad, y)); y += 21
    y += 14

    # Legend: every group's colour + name (the names live here, not on the map)
    pygame.draw.line(surf, (70, 64, 60), (px + pad, y), (px + PANEL_W - pad, y), 1); y += 12
    surf.blit(fonts["panel_lbl"].render("LEGEND", True, HUD_DIM), (px + pad, y)); y += 24
    LEGEND_ROWS.clear()
    for track in TRACKS:
        active = track_active(track, day)
        row = pygame.Rect(px + 8, y - 3, PANEL_W - 16, 46)
        LEGEND_ROWS.append((row, track["name"]))
        if track["name"] == HOVER_TRACK:                  # hovered: highlight the row
            pygame.draw.rect(surf, PANEL_BG2, row, border_radius=6)
            pygame.draw.rect(surf, track["color"], row, 1, border_radius=6)
        col = track["color"] if active else (74, 70, 66)
        legend_seal(surf, (px + pad + 7, y + 9), col, track.get("icon", "star"))
        name_col = HUD_TEXT if active else (104, 98, 92)
        surf.blit(fonts["panel_med"].render(track_label(track, day), True, name_col),
                  (px + pad + 22, y)); y += 23
        if active:
            sub = "at " + nearest_location(track, day)
        elif day < track["keyframes"][0][0]:
            sub = "- " + track.get("before", "yet to set out") + " -"
        else:
            sub = "- journey ended -"
        sub_col = HUD_DIM if active else (90, 86, 82)
        surf.blit(fonts["panel_sm"].render(sub, True, sub_col), (px + pad + 22, y)); y += 22

    # Fallen characters (e.g. Boromir): one compact line each
    for f in FALLEN:
        fallen = day >= f["day"]
        cx, cy = px + pad + 7, y + 9
        if fallen:
            pygame.draw.circle(surf, (40, 38, 46), (cx, cy), 11)        # medallion field
            pygame.draw.circle(surf, (170, 175, 188), (cx, cy), 11, 1)  # silver rim
            draw_symbol(surf, cx, cy, f["color"], f.get("symbol", "horn"), scale=0.95)
            name = f["name"]
            note = f["note"]
            name_col, note_col = HUD_TEXT, HUD_DIM
        else:
            pygame.draw.circle(surf, (74, 70, 66), (cx, cy), 5)
            name = f["name"]
            note = f.get("with", "with the Fellowship")
            name_col, note_col = (104, 98, 92), (90, 86, 82)
        surf.blit(fonts["panel_med"].render(name, True, name_col), (px + pad + 22, y)); y += 23
        surf.blit(fonts["panel_sm"].render(note, True, note_col), (px + pad + 22, y)); y += 22

    status = "PLAYING" if playing else "PAUSED"
    surf.blit(fonts["panel_sm"].render(status, True, GOLD), (px + pad, MAP_H - 26))


# --------------------------------------------------------------------------- Timeline + sliders
def timeline_geom():
    return 74, MAP_W - 24, MAP_H + 40             # x0, x1, y (room for the play button)


def play_button_geom():
    return 38, MAP_H + 40, 16                     # cx, cy, radius


def play_button_rect():
    cx, cy, r = play_button_geom()
    return pygame.Rect(cx - r - 2, cy - r - 2, 2 * (r + 2), 2 * (r + 2))


def draw_play_button(surf, playing):
    cx, cy, r = play_button_geom()
    pygame.draw.circle(surf, (52, 48, 58), (cx, cy), r)
    pygame.draw.circle(surf, GOLD, (cx, cy), r, 2)
    if playing:                                   # running -> Stop icon (square)
        pygame.draw.rect(surf, GOLD, (cx - 6, cy - 6, 12, 12))
    else:                                         # stopped -> Play icon (triangle)
        pygame.draw.polygon(surf, GOLD, [(cx - 5, cy - 7), (cx - 5, cy + 7), (cx + 8, cy)])


def speed_geom():
    return MAP_W + 24, MAP_W + PANEL_W - 24, MAP_H + 32   # x0, x1, y


def zoom_geom():
    return MAP_W + 24, MAP_W + PANEL_W - 24, MAP_H + 78   # x0, x1, y


def timeline_hit_rect():
    return pygame.Rect(0, MAP_H, MAP_W, BAR_H)


def slider_hit_rect():
    return pygame.Rect(MAP_W, MAP_H, PANEL_W, BAR_H)


def _draw_slider(surf, fonts, geom, label, value_text, pos):
    sx0, sx1, sy = geom
    surf.blit(fonts["panel_lbl"].render(label, True, HUD_DIM), (sx0, sy - 22))
    val = fonts["panel_med"].render(value_text, True, GOLD)
    surf.blit(val, (sx1 - val.get_width(), sy - 26))
    pygame.draw.line(surf, (90, 84, 80), (sx0, sy), (sx1, sy), 3)
    kx = sx0 + (sx1 - sx0) * pos
    pygame.draw.circle(surf, GOLD, (int(kx), sy), 7)
    pygame.draw.circle(surf, (255, 255, 255), (int(kx), sy), 3)


def draw_bottom_bar(surf, fonts, minute, speed, zoom, playing):
    pygame.draw.rect(surf, PANEL_BG2, pygame.Rect(0, MAP_H, MAP_W + PANEL_W, BAR_H))
    pygame.draw.line(surf, GOLD, (0, MAP_H), (MAP_W + PANEL_W, MAP_H), 2)
    pygame.draw.line(surf, (70, 64, 60), (MAP_W, MAP_H), (MAP_W, MAP_H + BAR_H), 1)

    # --- Play / Stop button + timeline (left) --------------------------------
    draw_play_button(surf, playing)
    x0, x1, ty = timeline_geom()
    pygame.draw.line(surf, (90, 84, 80), (x0, ty), (x1, ty), 3)

    off = 0
    for name, short, rt in FILMS:
        bx0 = x0 + (x1 - x0) * off / TOTAL_RUNTIME
        bx1 = x0 + (x1 - x0) * (off + rt) / TOTAL_RUNTIME
        pygame.draw.line(surf, FILM_COLORS[name], (bx0, ty), (bx1, ty), 3)
        lbl = fonts["panel_sm"].render(short, True, HUD_DIM)
        surf.blit(lbl, ((bx0 + bx1) / 2 - lbl.get_width() / 2, ty + 12))
        off += rt

    for ev in EVENTS:
        ex = x0 + (x1 - x0) * ev[1] / TOTAL_RUNTIME
        col = GOLD if ev[1] <= minute else (110, 104, 100)
        pygame.draw.circle(surf, col, (int(ex), ty), 4)

    hx = x0 + (x1 - x0) * minute / TOTAL_RUNTIME
    pygame.draw.line(surf, (255, 255, 255), (hx, ty - 13), (hx, ty + 13), 2)
    pygame.draw.circle(surf, (255, 255, 255), (int(hx), ty), 6)

    # --- Speed + zoom sliders (right) ---------------------------------------
    _draw_slider(surf, fonts, speed_geom(), "SPEED", fmt_speed(speed), speed_to_pos(speed))
    _draw_slider(surf, fonts, zoom_geom(), "ZOOM", fmt_speed(zoom), zoom_to_pos(zoom))


def time_from_x(mx):
    x0, x1, _ = timeline_geom()
    return max(0.0, min(TOTAL_RUNTIME, (mx - x0) / (x1 - x0) * TOTAL_RUNTIME))


def speed_from_x(mx):
    sx0, sx1, _ = speed_geom()
    return pos_to_speed((mx - sx0) / (sx1 - sx0))


def zoom_from_x(mx):
    sx0, sx1, _ = zoom_geom()
    return pos_to_zoom((mx - sx0) / (sx1 - sx0))


# --------------------------------------------------------------------------- Helpers
def wrap_text(text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        test = (cur + " " + w).strip()
        if font.size(test)[0] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def load_fonts():
    pygame.font.init()
    return {
        "place":     pygame.font.SysFont("Georgia,serif", 15),
        "panel_lbl": pygame.font.SysFont("Arial", 13, bold=True),
        "panel_big": pygame.font.SysFont("Georgia,serif", 30, bold=True),
        "panel_med": pygame.font.SysFont("Arial", 18, bold=True),
        "panel_sm":  pygame.font.SysFont("Arial", 15),
        "tiny":      pygame.font.SysFont("Arial", 12),
    }


def draw_fallback_map(surf, fonts):
    """Used when no map image was found."""
    surf.fill(PARCHMENT, rect=pygame.Rect(0, 0, MAP_W, MAP_H))
    msg = fonts["panel_med"].render("Map image missing (assets/middle_earth_bg.png)", True, (120, 60, 50))
    surf.blit(msg, (40, 40))


# --------------------------------------------------------------------------- Calibration mode
def run_calibration(screen, bg, fonts):
    """Measure place positions by clicking and save them to locations.json."""
    names = list(LOCATIONS.keys())
    coords = dict(LOCATIONS)
    idx = 0
    clock = pygame.time.Clock()
    big = pygame.font.SysFont("Arial", 20, bold=True)

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    save_calibration(coords); pygame.quit(); sys.exit()
                elif event.key == pygame.K_BACKSPACE:
                    idx = max(0, idx - 1)
                elif event.key in (pygame.K_n, pygame.K_RIGHT):
                    idx = min(len(names), idx + 1)
                elif event.key == pygame.K_LEFT:
                    idx = max(0, idx - 1)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if idx < len(names) and event.pos[1] < MAP_H:
                    coords[names[idx]] = (event.pos[0] / MAP_W, event.pos[1] / MAP_H)
                    idx += 1

        screen.blit(bg, (0, 0))
        for i, n in enumerate(names):
            x, y = world(coords[n])
            pygame.draw.circle(screen, (200, 40, 40) if i < idx else (120, 120, 120), (x, y), 4)

        banner = pygame.Surface((MAP_W, 40)); banner.set_alpha(220); banner.fill((20, 18, 24))
        screen.blit(banner, (0, 0))
        if idx < len(names):
            txt = f"[{idx + 1}/{len(names)}] Click on:  {names[idx]}    (N=skip, Backspace=back, Esc=save)"
        else:
            txt = "Done! Press Esc to save (locations.json)."
        screen.blit(big.render(txt, True, (240, 230, 210)), (16, 9))
        pygame.display.flip()
        clock.tick(60)


def save_calibration(coords):
    data = {n: [round(x, 4), round(y, 4)] for n, (x, y) in coords.items()}
    with open(CALIB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Saved:", CALIB_PATH)


# --------------------------------------------------------------------------- Main loop
def main():
    calibrate = "--calibrate" in sys.argv
    if not calibrate:
        load_calibration()

    global CAM_X, CAM_Y
    pygame.init()
    raw = load_background_raw()                  # adjusts MAP_H, still unscaled
    CAM_X, CAM_Y = MAP_W / 2, MAP_H / 2          # camera centred on the map
    win_w, win_h = MAP_W + PANEL_W, MAP_H + BAR_H
    screen = pygame.display.set_mode((win_w, win_h))
    pygame.display.set_caption("Middle-earth HUD - The War of the Ring")
    fonts = load_fonts()

    if raw is not None:
        bg = scale_background(raw)
    else:
        bg = pygame.Surface((MAP_W, MAP_H))
        draw_fallback_map(bg, fonts)

    if calibrate:
        run_calibration(screen, bg, fonts)
        return

    sharp = SharpMap.load()                      # None -> zoom falls back to the PNG

    clock = pygame.time.Clock()
    minute = 0.0                 # current position in the trilogy runtime
    speed = 60.0                 # playback factor (film-seconds per real second)
    playing = True
    drag = None                  # one of: "time", "speed", "zoom", "pan"

    while True:
        dt = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    pygame.quit(); sys.exit()
                elif event.key == pygame.K_SPACE:
                    playing = not playing
                elif event.key == pygame.K_r:
                    minute = 0.0
                elif event.key == pygame.K_0:
                    set_zoom(ZOOM_MIN)
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    speed = pos_to_speed(speed_to_pos(speed) + 0.05)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    speed = pos_to_speed(speed_to_pos(speed) - 0.05)
            elif event.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if mx < MAP_W and my < MAP_H:
                    set_zoom(ZOOM * (1.2 ** event.y), focus=(mx, my))
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if play_button_rect().collidepoint(event.pos):
                    if minute >= TOTAL_RUNTIME:     # restart if already at the end
                        minute = 0.0
                    playing = not playing
                elif slider_hit_rect().collidepoint(event.pos):
                    drag = "zoom" if event.pos[1] >= MAP_H + 54 else "speed"
                    if drag == "zoom":
                        set_zoom(zoom_from_x(event.pos[0]))
                    else:
                        speed = speed_from_x(event.pos[0])
                elif timeline_hit_rect().collidepoint(event.pos):
                    drag = "time"; playing = False
                    minute = time_from_x(event.pos[0])
                elif event.pos[1] < MAP_H:          # left-drag on the map pans
                    drag = "pan"
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                drag = None
            elif event.type == pygame.MOUSEMOTION:
                if drag == "speed":
                    speed = speed_from_x(event.pos[0])
                elif drag == "zoom":
                    set_zoom(zoom_from_x(event.pos[0]))
                elif drag == "time":
                    minute = time_from_x(event.pos[0])
                elif drag == "pan":
                    CAM_X -= event.rel[0] / ZOOM
                    CAM_Y -= event.rel[1] / ZOOM
                    clamp_camera()

        keys = pygame.key.get_pressed()
        update_hover(pygame.mouse.get_pos(), drag)
        if keys[pygame.K_LEFT]:
            minute = max(0, minute - speed / 60 * dt * 3); playing = False
        if keys[pygame.K_RIGHT]:
            minute = min(TOTAL_RUNTIME, minute + speed / 60 * dt * 3); playing = False

        if playing:
            minute += speed / 60.0 * dt      # speed = film-seconds per real second
            if minute >= TOTAL_RUNTIME:
                minute = TOTAL_RUNTIME; playing = False

        day = minute_to_day(minute)
        draw_map_background(screen, bg, sharp)
        screen.set_clip(pygame.Rect(0, 0, MAP_W, MAP_H))   # keep markers inside the map
        draw_waypoints(screen)
        draw_tracks(screen, day)
        draw_fallen(screen, day)
        screen.set_clip(None)
        draw_panel(screen, fonts, minute, day, playing)
        draw_bottom_bar(screen, fonts, minute, speed, ZOOM, playing)
        draw_cursor(screen)
        pygame.display.flip()


if __name__ == "__main__":
    main()
