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

import os
import sys
import json
import math
import pygame

from data import LOCATIONS, TRACKS, EVENTS, MAX_DAY, FILMS, TOTAL_RUNTIME, FALLEN

# --------------------------------------------------------------------------- Paths
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BG_PATH = os.path.join(ASSET_DIR, "middle_earth_bg.png")
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


def draw_map_background(surf, bg):
    """Blit the (zoomed) visible portion of the map into the map area."""
    if ZOOM <= 1.0001:
        surf.blit(bg, (0, 0))
        return
    sw, sh = MAP_W / ZOOM, MAP_H / ZOOM
    sx = int(min(max(CAM_X - sw / 2, 0), MAP_W - sw))
    sy = int(min(max(CAM_Y - sh / 2, 0), MAP_H - sh))
    sw, sh = max(1, int(sw)), max(1, int(sh))
    view = bg.subsurface(pygame.Rect(sx, sy, sw, sh))
    surf.blit(pygame.transform.smoothscale(view, (MAP_W, MAP_H)), (0, 0))


# --------------------------------------------------------------------------- Map markers
def draw_waypoints(surf):
    """Subtle dots for all journey stations (the map labels them itself)."""
    for _, p in LOCATIONS.items():
        x, y = world(p)
        pygame.draw.circle(surf, (60, 48, 36), (x, y), 3)


def draw_tracks(surf, day):
    # Faintly hint at the full routes (smooth spline curves)
    for track in TRACKS:
        full = [world(p) for p in full_spline(track)]
        if len(full) > 1:
            faint = tuple(int(c * 0.45 + 110 * 0.55) for c in track["color"])
            pygame.draw.lines(surf, faint, False, full, 2)

    # Travelled route + moving dot (no text label on the map)
    for track in TRACKS:
        if not track_active(track, day):
            continue
        pts = [world(p) for p in traveled_spline(track, day)]
        if len(pts) > 1:
            pygame.draw.lines(surf, track["color"], False, pts, 4)
        x, y = pts[-1]
        pygame.draw.circle(surf, (12, 10, 14), (x, y), 9)
        pygame.draw.circle(surf, track["color"], (x, y), 7)
        pygame.draw.circle(surf, (255, 255, 255), (x, y), 3)


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


# Symbols a fallen character can be marked with.
SYMBOLS = {"horn": draw_horn}


def draw_symbol(surf, cx, cy, color, kind, scale=1.0):
    SYMBOLS.get(kind, draw_horn)(surf, cx, cy, color, scale)


def draw_fallen(surf, day):
    """Memorial markers for characters who have fallen by the given day.
    Drawn as a heraldic medallion (silver-on-sable, in Gondor's colours)."""
    for f in FALLEN:
        if day >= f["day"]:
            x, y = world(LOCATIONS[f["location"]])
            pygame.draw.circle(surf, (26, 24, 30), (x, y), 15)        # dark field (sable)
            pygame.draw.circle(surf, (170, 175, 188), (x, y), 15, 2)  # silver rim
            draw_symbol(surf, x, y, f["color"], f.get("symbol", "horn"), scale=1.1)


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
    for track in TRACKS:
        active = track_active(track, day)
        col = track["color"] if active else (74, 70, 66)
        cx, cy = px + pad + 7, y + 8
        pygame.draw.circle(surf, (12, 10, 14), (cx, cy), 8)
        pygame.draw.circle(surf, col, (cx, cy), 6)
        if active:
            pygame.draw.circle(surf, (255, 255, 255), (cx, cy), 2)
        name_col = HUD_TEXT if active else (104, 98, 92)
        surf.blit(fonts["panel_med"].render(track["name"], True, name_col), (px + pad + 22, y)); y += 23
        if active:
            sub = "at " + nearest_location(track, day)
        elif day < track["keyframes"][0][0]:
            sub = "- yet to set out -"
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
            note = "with the Fellowship"
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
        if keys[pygame.K_LEFT]:
            minute = max(0, minute - speed / 60 * dt * 3); playing = False
        if keys[pygame.K_RIGHT]:
            minute = min(TOTAL_RUNTIME, minute + speed / 60 * dt * 3); playing = False

        if playing:
            minute += speed / 60.0 * dt      # speed = film-seconds per real second
            if minute >= TOTAL_RUNTIME:
                minute = TOTAL_RUNTIME; playing = False

        day = minute_to_day(minute)
        draw_map_background(screen, bg)
        screen.set_clip(pygame.Rect(0, 0, MAP_W, MAP_H))   # keep markers inside the map
        draw_waypoints(screen)
        draw_tracks(screen, day)
        draw_fallen(screen, day)
        screen.set_clip(None)
        draw_panel(screen, fonts, minute, day, playing)
        draw_bottom_bar(screen, fonts, minute, speed, ZOOM, playing)
        pygame.display.flip()


if __name__ == "__main__":
    main()
