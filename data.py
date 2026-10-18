"""
Data for the Middle-earth simulation: places, group routes and events.

The timeline follows Tolkien's chronology of the War of the Ring (T.A. 3018-3019).
Day 0 = Frodo leaves Bag End (September 23, 3018). Every 'day' value is a running
day number from that point; the 'date' strings are only for display in the HUD.
"""

# ---------------------------------------------------------------------------
# Places on the map. Coordinates are NORMALISED (0..1) relative to the map image
# (x = east, y = south). This way they fit any window size and match the real
# background image (assets/middle_earth_bg.png).
#
# The values were carefully measured against the Wikimedia map. To fine-tune them
# pixel-perfectly:  python3 middle_earth_hud.py --calibrate
# That writes a locations.json which overrides these defaults.
# ---------------------------------------------------------------------------
LOCATIONS = {
    "Grey Havens":      (0.080, 0.275),
    "Hobbiton":         (0.267, 0.302),
    "Bree":             (0.345, 0.285),
    "Weathertop":       (0.400, 0.235),
    "Rivendell":        (0.485, 0.250),
    "Caradhras":        (0.475, 0.355),
    "Moria":            (0.485, 0.370),
    "Lothlorien":       (0.505, 0.395),
    "Argonath":         (0.550, 0.540),
    "Amon Hen":         (0.555, 0.565),
    "Emyn Muil":        (0.590, 0.585),
    "Dead Marshes":     (0.600, 0.605),
    "Black Gate":       (0.640, 0.610),
    "Ithilien":         (0.615, 0.640),
    "Minas Morgul":     (0.650, 0.665),
    "Cirith Ungol":     (0.650, 0.655),
    "Mount Doom":       (0.680, 0.645),
    "Barad-dur":        (0.715, 0.645),
    "Fangorn":          (0.460, 0.500),
    "Isengard":         (0.430, 0.510),
    "Edoras":           (0.480, 0.625),
    "Helm's Deep":      (0.460, 0.605),
    "Dunharrow":        (0.485, 0.650),
    "Pelargir":         (0.620, 0.770),
    "Minas Tirith":     (0.600, 0.660),
}

# ---------------------------------------------------------------------------
# Group routes. Each group has keyframes (day, place name).
# Positions are linearly interpolated between two keyframes.
# A group is only visible between its first and last day.
# ---------------------------------------------------------------------------
TRACKS = [
    {
        "name": "The Fellowship",
        "color": (232, 198, 92),
        "members": "Frodo, Sam, Merry, Pippin, Aragorn, Legolas, Gimli, Boromir, Gandalf",
        # A keyframe target is a place name OR a raw (x, y) curve waypoint that
        # bends the route along the terrain (river/mountains) like the reference map.
        "keyframes": [
            (0,   "Hobbiton"),
            (6,   "Bree"),
            (13,  "Weathertop"),
            (27,  "Rivendell"),
            (93,  "Rivendell"),
            (100, (0.470, 0.300)),       # Hollin / Eregion, west of the Misty Mts
            (104, "Caradhras"),
            (112, "Moria"),
            (116, "Lothlorien"),
            (146, "Lothlorien"),
            (149, (0.520, 0.468)),       # down the Anduin
            (152, "Argonath"),
            (156, "Amon Hen"),
        ],
    },
    {
        "name": "Frodo & Sam",
        "color": (214, 74, 62),
        "members": "Frodo, Sam (and Gollum)",
        "keyframes": [
            (156, "Amon Hen"),
            (159, "Emyn Muil"),
            (163, "Dead Marshes"),
            (165, "Black Gate"),
            (166, (0.628, 0.628)),       # turn south below the Morannon
            (167, "Ithilien"),
            (172, "Minas Morgul"),
            (173, "Cirith Ungol"),
            (181, "Cirith Ungol"),
            (183, (0.666, 0.651)),       # across the plain of Gorgoroth
            (185, "Mount Doom"),
            (190, "Minas Tirith"),
            (222, "Minas Tirith"),
        ],
    },
    {
        "name": "Aragorn, Legolas & Gimli",
        "color": (86, 150, 222),
        "members": "Aragorn, Legolas, Gimli",
        "keyframes": [
            (156, "Amon Hen"),
            (157, (0.515, 0.535)),       # pursuit across the Emnet of Rohan
            (159, "Fangorn"),
            (162, "Edoras"),
            (163, "Helm's Deep"),
            (165, "Helm's Deep"),
            (166, "Edoras"),
            (167, "Dunharrow"),
            (168, (0.550, 0.730)),       # Paths of the Dead, under the mountains
            (170, "Pelargir"),
            (173, (0.628, 0.712)),       # up the Anduin with the corsair ships
            (175, "Minas Tirith"),
            (185, "Black Gate"),
            (190, "Minas Tirith"),
            (222, "Minas Tirith"),
        ],
    },
    {
        "name": "Merry & Pippin",
        "color": (108, 190, 110),
        "members": "Merry, Pippin (split from day 165: Pippin -> Minas Tirith, Merry with Rohan)",
        "keyframes": [
            (156, "Amon Hen"),
            (157, (0.515, 0.535)),       # carried off across Rohan by the Uruk-hai
            (159, "Fangorn"),
            (163, "Isengard"),
            (165, "Isengard"),
            (167, (0.520, 0.585)),       # east across Rohan toward Gondor
            (169, "Minas Tirith"),
            (175, "Minas Tirith"),
            (222, "Minas Tirith"),
        ],
    },
    {
        "name": "Gandalf the White",
        "color": (236, 236, 246),
        "members": "Gandalf (returned after his fall in Moria)",
        "keyframes": [
            (159, "Fangorn"),
            (162, "Edoras"),
            (165, "Edoras"),
            (167, (0.540, 0.640)),       # riding east to Gondor with Pippin
            (169, "Minas Tirith"),
            (175, "Minas Tirith"),
            (185, "Black Gate"),
            (190, "Minas Tirith"),
            (222, "Minas Tirith"),
        ],
    },
]

# ---------------------------------------------------------------------------
# Characters who fall during the journey. They have no onward route, so instead
# of a moving dot they get a memorial marker (chosen via "symbol", e.g. a horn)
# at the place where they fall, shown from that day on, plus a legend entry.
# Available symbols: "horn".
# ---------------------------------------------------------------------------
FALLEN = [
    {
        "name": "Boromir",
        "symbol": "horn",                  # the Horn of Gondor
        "color": (238, 240, 247),          # Gondor silver-white (shown on a dark medallion)
        "location": "Amon Hen",
        "day": 156,                        # falls at the breaking of the Fellowship
        "note": "fell defending Merry & Pippin",
    },
]


# ---------------------------------------------------------------------------
# The three films and their EXTENDED EDITION runtimes (in minutes). The master
# clock runs along this total runtime, so at 1x speed a full run lasts as long
# as watching all three extended editions back to back.
# ---------------------------------------------------------------------------
FILMS = [
    ("The Fellowship of the Ring", "FotR", 228),
    ("The Two Towers",             "TTT",  235),
    ("The Return of the King",     "RotK", 263),
]
TOTAL_RUNTIME = sum(rt for _, _, rt in FILMS)   # minutes (= 726)


# ---------------------------------------------------------------------------
# Events for the HUD. Each event has:
#   (day, film_minute, date, film, title, text)
# - day .......... story day (drives the group routes, see TRACKS)
# - film_minute .. approximate position within the trilogy's extended runtime
#                  (cumulative minutes from the start of film 1). Monotonic.
# The displayed event is the latest one whose film_minute <= current time.
# ---------------------------------------------------------------------------
EVENTS = [
    (0,    40, "Sep 23, 3018", "The Fellowship of the Ring",
     "Departure from Bag End",
     "Frodo leaves the Shire with the One Ring, heading for Bree."),
    (6,    66, "Sep 29, 3018", "The Fellowship of the Ring",
     "At the Prancing Pony",
     "In Bree the hobbits meet Strider (Aragorn)."),
    (13,   85, "Oct 6, 3018", "The Fellowship of the Ring",
     "Weathertop",
     "Frodo is wounded by the Witch-king with a Morgul blade."),
    (27,  104, "Oct 20, 3018", "The Fellowship of the Ring",
     "Arrival in Rivendell",
     "Elrond heals Frodo. The free peoples debate the fate of the Ring."),
    (93,  130, "Dec 25, 3018", "The Fellowship of the Ring",
     "The Fellowship sets out",
     "Nine companions leave Rivendell heading south."),
    (104, 142, "Jan 11, 3019", "The Fellowship of the Ring",
     "Caradhras",
     "Snow forces the Fellowship to take the road through Moria."),
    (112, 152, "Jan 13, 3019", "The Fellowship of the Ring",
     "The Mines of Moria",
     "The Fellowship enters the ancient dwarven halls."),
    (114, 170, "Jan 15, 3019", "The Fellowship of the Ring",
     "Gandalf falls",
     "On the Bridge of Khazad-dum, Gandalf falls into the abyss with the Balrog."),
    (116, 184, "Jan 17, 3019", "The Fellowship of the Ring",
     "Lothlorien",
     "Galadriel welcomes the Fellowship into the Golden Wood."),
    (146, 200, "Feb 16, 3019", "The Fellowship of the Ring",
     "Farewell to Lorien",
     "They travel down the Anduin by boat."),
    (156, 222, "Feb 26, 3019", "The Fellowship of the Ring",
     "The Fellowship breaks",
     "Boromir falls. Frodo & Sam head east, Merry & Pippin are captured, "
     "Aragorn, Legolas & Gimli take up the pursuit."),
    (159, 255, "Feb 29, 3019", "The Two Towers",
     "Fangorn & Gandalf's return",
     "Merry & Pippin meet Treebeard. Gandalf returns as the White."),
    (162, 285, "Mar 2, 3019", "The Two Towers",
     "Edoras",
     "Gandalf frees King Theoden from Saruman's grip."),
    (163, 300, "Mar 3, 3019", "The Two Towers",
     "Frodo & Sam in the Dead Marshes",
     "Gollum leads them through the marshes toward the Black Gate."),
    (164, 425, "Mar 4, 3019", "The Two Towers",
     "Battle of Helm's Deep",
     "Rohan holds the fortress; the Ents overrun Isengard."),
    (165, 440, "Mar 5, 3019", "The Two Towers",
     "At the Black Gate",
     "The gate is shut - Gollum knows another way. Pippin looks into the palantir."),
    (167, 458, "Mar 7, 3019", "The Two Towers",
     "Ithilien & Faramir",
     "Frodo & Sam are caught by Faramir's rangers."),
    (169, 500, "Mar 9, 3019", "The Return of the King",
     "Gandalf & Pippin in Minas Tirith",
     "The white city of Gondor prepares against Mordor."),
    (172, 560, "Mar 12, 3019", "The Return of the King",
     "Cirith Ungol",
     "Shelob lurks in the pass. Aragorn takes the Paths of the Dead."),
    (175, 640, "Mar 15, 3019", "The Return of the King",
     "Battle of the Pelennor Fields",
     "Theoden falls, the Witch-king is slain, Aragorn arrives with the fleet."),
    (185, 690, "Mar 25, 3019", "The Return of the King",
     "The Ring is destroyed",
     "The host at the Black Gate draws Sauron's eye - at Mount Doom the Ring falls. Sauron is defeated."),
    (222, 720, "May 1, 3019", "The Return of the King",
     "The Coronation",
     "Aragorn is crowned King Elessar. A new age begins."),
]

# Last day of the timeline (used for normalisation of the routes).
MAX_DAY = EVENTS[-1][0]
