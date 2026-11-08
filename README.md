# Middle-earth HUD — The War of the Ring

An interactive Python simulation that shows, on a **real map of Middle-earth**,
**where the groups of the Fellowship are at any point in the film trilogy** —
including how they split after the Fellowship breaks.

The map shows only the moving dots; the names live in the **legend** on the
right-hand panel.

## Install & run

```bash
cd ~/LotRSimulation
python3 -m pip install -r requirements.txt
python3 middle_earth_hud.py
```

## Controls

| Key / action            | Effect                                       |
|-------------------------|----------------------------------------------|
| Play/Stop button        | start / stop (bottom-left; ▶ when stopped, ■ when running) |
| `Space`                 | play / pause                                 |
| `Left` / `Right`        | skip back / forward (hold = faster)          |
| `+` / `-`               | change playback speed (1× … 1000×)           |
| drag the speed slider   | set the speed (bottom-right, top)            |
| drag the zoom slider    | zoom the map 1× … 100× (bottom-right, lower) |
| mouse wheel on the map  | zoom in/out toward the cursor                |
| drag on the map         | pan when zoomed in (the gauntlet cursor closes into a fist and grabs the map) |
| `0`                     | reset zoom to 1×                             |
| click/drag the timeline | jump to any moment (bottom-left)             |
| `R`                     | reset to the start                           |
| `Esc`                   | quit                                         |
| hover a route / legend  | highlight that group's route and name its stations |

## Real film runtime

The clock runs along the **extended-edition runtime** of the whole trilogy
(FotR 3:48 + TTT 3:55 + RotK 4:23 = **12:06 h**, 726 min). Events sit at their
approximate position within that runtime, so you could play the HUD alongside
the films.

- **Speed** is a factor of real time: **1×** = real time (a full run takes ~12 h),
  **1000×** = a full run in ~44 s. The slider is logarithmic, so 1 / 10 / 100 /
  1000 are evenly spaced.
- The HUD shows the current film and a timecode, e.g. `The Two Towers · 1:23:45 / 3:55:00`.

## What you see

- **Real Middle-earth map** as the background, covering the key locations
  (Shire → Rivendell → Moria → Lothlórien → Rohan → Gondor → Mordor).
- **Moving dots** for the groups that split when the Fellowship breaks:
  *The Fellowship* → *Frodo & Sam*, *Aragorn/Legolas/Gimli*, *Merry & Pippin*
  and *Gandalf the White*. No text on the map — see the legend.
- **Curved routes** drawn as smooth (centripetal Catmull-Rom) splines through the
  waypoints, following the terrain (Anduin, mountains, the Paths of the Dead) like
  a hand-drawn paths map. A keyframe in `data.py` may be a place name *or* a raw
  `(x, y)` waypoint that bends the route along a river or mountain range.
- **Fallen characters** get a memorial symbol where they fall. *Boromir* is
  marked with the **Horn of Gondor** at Amon Hen from the breaking of the
  Fellowship on, and the legend lists him with a † dagger. (Add more in
  `data.py` → `FALLEN`; each entry picks a `symbol` — `"horn"` or `"sword"`.)
- **HUD panel** on the right: date, film + timecode, current event, and a
  **legend** with a colour swatch and name for each group (plus where it is).
- **Bottom bar**: a **timeline** (left) with event markers and colour-coded film
  segments (FotR / TTT / RotK), and on the right a **speed slider** and a
  **zoom slider** (1× … 100×).

## Routes

Routes are drawn like in a navigation app, but in ink on parchment: the part a
group has already travelled is a solid line with an ink border and direction
arrows, the road still ahead is a dashed quill line, and the current position is
the group's seal with its emblem: an Elven star for the Fellowship, the One Ring
for Frodo & Sam, Anduril for Aragorn, Legolas & Gimli, a Lorien leaf brooch for
Merry & Pippin and a wizard's hat for Gandalf (set via `"icon"` in `data.py`).
Seals of groups at the same place are fanned out so none is hidden. Every station on a route is marked: filled in the
group's colour once reached, hollow before. **Hover** a route on the map or its
entry in the legend to highlight it (the other routes fade) and show the names
of its stations.

## Zoom

Use the **zoom slider** (bottom-right, 1× … 100×, logarithmic) or the **mouse
wheel** over the map to zoom toward the cursor. When zoomed in, **drag the map**
to pan around; press `0` to reset to the full map. The dots, routes and the
Boromir medallion keep a constant screen size so they stay readable at any zoom.
When zoomed in, the map is re-drawn from the vector original
(`assets/middle_earth.svg`), so it stays sharp at every zoom level. Rendering
happens in the background and covers a little more than the visible area, so
panning stays sharp; right after a zoom step the previous image is briefly
scaled until the new one is ready (a fraction of a second).

## Fine-tuning place positions (calibration)

The default place coordinates were measured against the map but may not sit
perfectly. To fine-tune them:

```bash
python3 middle_earth_hud.py --calibrate
```

Click each place as it is named (`N` = skip, `Backspace` = back, `Esc` = save).
The result is written to `locations.json` and from then on overrides the
defaults automatically.

## Files

- `middle_earth_hud.py` — main program (rendering, controls, HUD, calibration).
- `data.py` — places, group routes and events. **Edit here** to change locations,
  dates or event texts.
- `assets/middle_earth_bg.png` — background map (composited onto parchment).
- `assets/middle_earth.svg` — vector original of the map, used for sharp zooming
  (optional: without it, zooming falls back to the PNG).
- `assets/MAP_LICENSE.txt` — source & licence of the map.
- `locations.json` — created only by calibration (optional).

## Map licence

The map is from **Wikimedia Commons** ("Map of Middle-Earth", author: *k1tesurfen*)
and is licensed under **CC BY-SA 4.0**. It is embedded here for personal use;
any redistribution requires attribution and the same licence. See
`assets/MAP_LICENSE.txt`.

## Timeline

The chronology follows Tolkien's dates for the War of the Ring (T.A. 3018–3019).
Day 0 is Frodo's departure from Bag End (Sep 23, 3018); the end is Aragorn's
coronation (May 1, 3019).
# LotRSimulation
