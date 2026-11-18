#!/usr/bin/env python3
"""gen_e36.py - writes the E36 cluster theme's layout (Cluster Themes "e36").

    python3 tools/gen_e36.py apps/cluster_themes/hud/layouts/e36.json

The BMW E36 328i cluster of the 90s, traced off face-on photos kept next to the repo in ../design/photos/e36/:
eu-kmh-day-face.png (the metric face) and us-mph-day-face.png (the mph face); for the redlines on the 0 .. 8 rev
counter (7000, 7500) the M3's: m3-kmh-jpn-day-face.png and m3-us-mph-day-face.png (red needles, a faster
speedometer, the /M badge, the oil temperature gauge where the 328i has its economy gauge). Every measurement below is in that
photo's pixels, levelled (pairs that sit at one height on the car, like the two big hubs, take their average);
place() scales a face to the cluster at true proportions, the E36's warning-light strips in BMW's bottom row
(y 660..720), from 8 px above it: black, with holes where BMW's own drive mode and gear letter, and electric range show through.
The rest of BMW's row (its clock, messages, outside temperature) is behind the strips; the clock and outside
temperature show on the LCD's second row instead.

Angles are degrees clockwise from 12 o'clock, round the needle's pivot. A dial's "map" is [value, angle] pairs
read off its printed scale (shared/e36.js frac()); "rest" is where the needle lies with no data (on its pin).
The figures and labels are text widgets on top (fonts e_num, e_sm, e_lcd: tools/bake_fonts.py).

Edit this file, not the JSON.
"""
import copy
import json
import math
import os
import sys

W, H, ROW = 1920, 720, 660           # the cluster; BMW's bottom row is y 660..720, the E36's strips over it

# The warning-light strips (target px: x, y, w, h), black windows from the top of BMW's bottom row down past the
# screen's edge; their dividers moved from the E36's (photo x 750 and 1235) so each hole below lies inside a strip,
# the three symmetric about the middle. Black as BMW's own row (the "strip" role), so the holes don't show.
# They start LIFT px above BMW's row: BMW sets a two-line drive mode (SPORT / INDIVIDUAL) from a few px above y 660 down
# to the screen's foot, and the face cut its top off (car photo, Nov 17 2026). The fuel and coolant gauges and the
# ASC / M window go up as much (SMALL_Y, EU 'windows'), so they keep their gap to the strips.
LIFT = 8
TOP = ROW - LIFT
STRIPS = [[24, TOP, 382, H - TOP + 20], [424, TOP, 1072, H - TOP + 20], [1514, TOP, 382, H - TOP + 20]]
STRIP_R = 14
# BMW's own items that show through the strips (x0, x1; the layer pieces skip them), from the car photo of Nov 17
# 2026 (the theme over BMW's row): the drive mode at 1223..1314 (room for SPORT PLUS / COMFORT either side) and the
# gear letter (BMW blacks out its gear slot while anything covers x 1380..1460), the electric range at 1696..1842. The hole ends just past the gear slot: the outside
# temperature has been seen from x 1475 (the Nov 13 2026 photo the bench's backdrop is drawn from; 1541 on Nov 17).
# The hole starts just before the drive mode (its left edge 1223..1226 on both Nov 17 photos, left-aligned: SPORT
# INDIVIDUAL runs on to 1378): the navigation's street name, centred in BMW's row, ends as far right as 1197 and stays
# covered. BMW's range (its pump icon and figure at 107..299) stays behind the left strip: the LCD shows it, with its own icon.
HOLES = [(1214, 1462), (1680, 1880)]


def lerp_ang(table, v):
    """The angle at value v on a [(value, angle)] scale (rising values)."""
    for (a, x), (b, y) in zip(table, table[1:]):
        if a <= v <= b:
            return x + (y - x) * (v - a) / (b - a)
    raise ValueError(v)


FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'apps', 'cluster_themes', 'hud', 'fonts')
_fonts = {}


def font(name):
    """A baked font's metrics (tools/bake_fonts.py): cell height and each glyph's advance."""
    if name not in _fonts:
        with open(os.path.join(FONTS, name + '.json')) as f:
            _fonts[name] = json.load(f)
    return _fonts[name]


def digit_ink(fname):
    """The rows of a font's digit ink in its cell (an 8, from the baked atlas): (top, bottom + 1), px from the cell's top."""
    import base64
    f = font(fname)
    a, W8, g = base64.b64decode(f['alpha']), f['width'], f['glyphs']['8']
    rows = [y for y in range(g['h']) if any(a[(g['y'] + y) * W8 + g['x'] + x] > 100 for x in range(g['w']))]
    return rows[0], rows[-1] + 1


GAP = 7          # target px between a dial's figure (its ink) and the nearest tick


FONT_PAD = 2     # tools/bake_fonts.py PAD: the blank round each glyph's cell


def ink(text, fname):
    """A text's ink box about its widget's centre (centred on its advances): (half width, half height, dx, dy of the
    ink's middle). Its sides from the glyphs' cells (the digits are tabular: a "1" has far less ink than its advance),
    its top at cap height (Barlow's 0.7 em) above the baseline."""
    f = font(fname)
    g = [f['glyphs'][c] for c in text]
    pen = -sum(c['advance'] for c in g) / 2
    left = pen + g[0]['ox'] + FONT_PAD
    right = pen + sum(c['advance'] for c in g[:-1]) + g[-1]['ox'] + g[-1]['w'] - FONT_PAD
    top = f['baseline'] - 0.7 * f['size']
    return ((right - left) / 2, (f['baseline'] - top) / 2, (left + right) / 2,
            (top + f['baseline']) / 2 - f['cellHeight'] / 2)


def tick_segs(groups, dial, pivot, L):
    """A dial's ticks as segments in target px: (x0, y0, x1, y1, half width), from the tick groups (photo px)."""
    px, py = pivot
    out = []
    for g in groups:
        if g[0] != dial:
            continue
        n = g[3]
        for i in range(n):
            a = math.radians(g[1] + (g[2] - g[1]) * i / (n - 1) if n > 1 else g[1])
            ro, ri = L(g[4]), L(g[4] - g[5])
            out.append((px + ri * math.sin(a), py - ri * math.cos(a), px + ro * math.sin(a), py - ro * math.cos(a), L(g[6]) / 2))
    return out


def tick_points(segs):
    """The ticks sampled every half px along their middle line: (x, y, half width)."""
    out = []
    for x0, y0, x1, y1, hwid in segs:
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) * 2))
        out += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, hwid) for i in range(n + 1)]
    return out


def box_clearance(cx, cy, hw, hh, pts):
    """How far the box (centre, half sizes) is from the nearest tick's edge, px (negative: they overlap)."""
    return min(math.hypot(max(abs(x - cx) - hw, 0.0), max(abs(y - cy) - hh, 0.0)) - hwid for x, y, hwid in pts)


def figure_at(text, fname, ang, r_in, pivot, segs, placed, r_lo=None):
    """A dial's figure as printed: on its tick's line (angle `ang` round the pivot), its ink GAP px in from the nearest
    tick of its dial, so every figure keeps the same gap to the ticks and they follow the scale's arc. Starts at the
    ring's inner end `r_in` (target px) and moves in. With `r_lo` (another ring's outer end further in) the figure stays
    in the band between the two rings: GAP px in from the outer ring where the band allows, else as clear of both as
    it can be (the mph face's MPH figures; the photo's are as tight). Returns the widget's centre; fails if it would
    touch a figure already `placed` (ink boxes, added to)."""
    hw, hh, dx, dy = ink(text, fname)
    px, py = pivot
    ux, uy = math.sin(math.radians(ang)), -math.cos(math.radians(ang))
    pts = [p for p in tick_points(segs) if abs(math.hypot(p[0] - px, p[1] - py) - r_in) < hw + hh + 80]
    clear = lambda r: box_clearance(px + ux * r, py + uy * r, hw, hh, pts)
    if r_lo is not None:
        rs = [r_in - i * 0.25 for i in range(int((r_in - r_lo) * 4) + 1)]
        want = min(GAP, max(clear(r) for r in rs))
        if want < 0:
            raise SystemExit('no room for the figure %s at %.1f degrees between the rings' % (text, ang))
        r = next(r for r in rs if clear(r) >= want - 0.05)
    else:
        r = r_in
        while clear(r) < GAP:
            r -= 0.25
            if r < r_in / 2:
                raise SystemExit('no room for the figure %s at %.1f degrees' % (text, ang))
    bx, by = px + ux * r, py + uy * r
    for ox, oy, ow, oh in placed:
        if abs(bx - ox) < hw + ow + 2 and abs(by - oy) < hh + oh + 2:
            raise SystemExit('the figure %s at %.1f degrees runs into another' % (text, ang))
    placed.append((bx, by, hw, hh))
    return bx - dx, by - dy


def text_widget(wid, text, fname, cx, cy, toggle, color='mark'):
    """A text widget centred on (cx, cy), just wide enough for its text."""
    f = font(fname)
    w = sum(f['glyphs'][c]['advance'] for c in text) + 4
    h = f['cellHeight']
    return {'id': wid, 'type': 'text', 'class': 'content', 'z': 5, 'x': round(cx - w / 2), 'y': round(cy - h / 2),
            'w': w, 'h': h, 'text': text, 'font': fname, 'color': color, 'align': 'center', 'toggle': toggle}


# --- the rev counter: one face per redline ------------------------------------------------------------------------
# BMW printed the E36's rev counter in three scales, on the same sweep: 0 .. 6 (the diesels), 0 .. 7 (the 328i and
# under) and 0 .. 8 (the M3). The theme takes the scale from the "Redline" setting (apps/cluster_themes/app.json, one
# face per step): the first that runs past it. The red as the faces print it (REV_RED): on 0 .. 7 and 0 .. 8 solid from
# the redline to the scale's end, three red stripes over the 280 rpm under it (the photo's 328i face: stripes
# 6020 .. 6260, solid from 6300); on the diesel's 0 .. 6 (5500 limit) solid from the 5, no stripes. The figures sit on
# their thousands' ticks (figure_at()); hud.js picks the face (widget e36dash "revs").
REV_ZERO, REV_ONE, REV_END = -116.6, -92.0, 116.6    # 0 and 1000 rpm, and the scale's end (7 or 8): both faces
REV_TOPS = (6, 7, 8)
REV_RED = {6: (500, 0), 7: (0, 3), 8: (0, 3)}       # scale: (solid red from this far under the redline, stripes)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'apps', 'cluster_themes', 'app.json')) as _f:
    _REDLINE = next(s for s in json.load(_f)['settings'] if s['key'] == 'redline')
REDLINES = _REDLINE['steps']
REV_TICK = (234, 26)     # the thousands' ticks: outer radius, length (photo px)


def rev_top(redline):
    """The rev counter's scale, in thousands, for a redline: the first that runs past it."""
    return next((t for t in REV_TOPS if t * 1000 > redline), REV_TOPS[-1])


def tach_ang(top):
    """The rev counter's [(rpm, angle)] scale, 0 .. top thousand: 0 to 1000 a short step (the idle), then even."""
    step = (REV_END - REV_ONE) / (top - 1)
    return ([(0, REV_ZERO), (1000, REV_ONE)] + [(1000 * k, REV_ONE + step * (k - 1)) for k in range(2, top)]
            + [(1000 * top, REV_END)])


def rev_face(redline):
    """The rev counter for a redline: its needle map, ticks and red zones (the dial is 2)."""
    top = rev_top(redline)
    a = tach_ang(top)
    at = lambda v: lerp_ang(a, v)
    return {'top': top, 'map': a + [(1000 * top + 600, 125)],     # the needle's stop just past the end
            'ticks': [[2, REV_ZERO, REV_ZERO, 1] + list(REV_TICK) + [8],            # 0
                      [2, REV_ONE, REV_END, top] + list(REV_TICK) + [8],            # 1 .. top
                      [2, at(500), at(500), 1, 234, 22, 3],                       # 500
                      [2, at(1500), at(1000 * top - 500), top - 1, 234, 22, 3]],  # 1500 .. top - 500
            'zones': [[2, at(redline - REV_RED[top][0]), REV_END, 199, 226, 'red']]
            + ([[2, at(redline - 280), at(redline - 40), 205, 226, 'red', REV_RED[top][1]]] if REV_RED[top][1] else [])}


# --- the metric (EU) face, eu-kmh-day-face.png (2000 x 1500) ----------------------------------------------------
# The EU economy scale, 0 .. 30 L/100km every 2 (the 0, 10, 20, 30 marks wide), measured off the photo's ticks
# (each one's line fitted to its pixels): all point at one centre, evenly spaced (linear in L/100km, to 0.75 degrees);
# their inner ends on one circle (to 0.5 px), their outer ends on another (to 4 px) and level along the bottom.
# The photo is taken at an angle: it puts the gauge 21 px left of the rev counter's centre, its 0 .. 30 axis 1 degree
# off the vertical and its circles off that axis. On the car it is symmetric under the rev counter's centre (the
# face-on US photo, tach-econ-closeup.png): so the fit's sizes, on the rev counter's vertical (EU_AXIS).
EU_AXIS = 1270                           # the rev counter's centre x (EU_PIVOT['tach'])
ECON_C = (EU_AXIS, 830.6)                # the ticks' centre
ECON_ANG = (-118.3, -241.7)              # 0 and 30 L/100km round it, mirrored about straight down
ECON_IN = ((EU_AXIS, 765.1), 186.9)      # the inner ends' circle (centre, radius)
ECON_OUT = ((EU_AXIS, 755.3), 228.7)     # the outer ends' circle, and their level along the bottom
ECON_FLOOR = 964
ECON_W = (7.2, 2.4)                      # the marks' widths: the tens, the rest


def _ray_circle(c, u, centre, r):
    """How far along the ray c + t u (u a unit vector, c inside the circle) it leaves the circle."""
    dx, dy = c[0] - centre[0], c[1] - centre[1]
    b = dx * u[0] + dy * u[1]
    return -b + math.sqrt(b * b - (dx * dx + dy * dy - r * r))


def eu_econ_ticks():
    """[(outer end, inner end, width)] in photo px, 0 .. 30 L/100km."""
    out = []
    for v in range(0, 31, 2):
        a = math.radians(ECON_ANG[0] + (ECON_ANG[1] - ECON_ANG[0]) * v / 30)
        u = (math.sin(a), -math.cos(a))
        t_in = _ray_circle(ECON_C, u, *ECON_IN)
        t_out = min(_ray_circle(ECON_C, u, *ECON_OUT), (ECON_FLOOR - ECON_C[1]) / u[1])
        at = lambda t: (round(ECON_C[0] + u[0] * t, 1), round(ECON_C[1] + u[1] * t, 1))
        out.append((at(t_out), at(t_in), ECON_W[0] if v % 10 == 0 else ECON_W[1]))
    return out
# The fuel and coolant gauges are the same instrument: one circle, the pivot under its centre, the same sweep, tick
# radii, colour blocks and icon height. Measured on the US photo (face-on) from its hubs, checked against a flat
# print of the dial face (design/photos/e36/dial-print.png); the needle's tail (its tab past the hub) off the EU photo's fuel
# hub (42 px from the pivot, the hub 27). The EU photo is seen at an angle and its raised hubs
# project ~30 px off, so it gives only the circles' size. Lengths in photo px of the EU frame.
SMALL = {'r': 132, 'drop': 62, 'hub': 28, 'tail': 41, 'needle': [130, 7], 'sweep': 44, 'rest': -47,
         'tick': [142, 25, 3.5], 'block': [117, 142, 12, 3],
         # (dy, box size): both glyphs 34 tall, centred 82 above the pivot (the pump fills 0.66 of its box, low;
         # the thermometer 0.94)
         'icons': {'fuel': (-88, 52), 'e36cool': (-82, 36)}}
# the photo-to-cluster scale (place(): the small dials' outer edges at the screen's), for what moves in target px
SCALE = (W - 12) / (2 * (SMALL['r'] + 4) + 1672 - 347)
# the circles' centres: level, symmetric about the speedometer and tach (x 1009.5); the photo's 856, up LIFT target px
SMALL_X, SMALL_Y = (347, 1672), round(856 - LIFT / SCALE, 2)
FUEL_TEXTS = [('0', 'e_lab', -92, -123), ('½', 'e_frac', 0, -158), ('⅟', 'e_frac', 92, -123)]   # (text, font, dx, dy)


def small_dial(signal, cx, cy, mapping):
    return {'signal': signal, 'c': [cx, cy], 'r': SMALL['r'], 'pivot': [cx, cy + SMALL['drop']], 'hub': SMALL['hub'],
            'tail': SMALL['tail'], 'needle': SMALL['needle'], 'map': mapping, 'rest': SMALL['rest']}


def small_ticks(dial, count):
    return [dial, -SMALL['sweep'], SMALL['sweep'], count] + SMALL['tick']


def small_block(dial, side, colour):
    """A colour block just inside an end tick (side -1 the low end, 1 the high end)."""
    r0, r1, span, gap = SMALL['block']
    a0, a1 = sorted((side * (SMALL['sweep'] - gap), side * (SMALL['sweep'] - gap - span)))
    return [dial, a0, a1, r0, r1, colour]


def small_print(face):
    """The fuel gauge's figures and both gauges' icons, placed from the pivots: (texts, icons)."""
    (fx, fy), (kx, ky) = face['dials'][0]['pivot'], face['dials'][3]['pivot']
    ic = SMALL['icons']
    return ([(t, f, fx + dx, fy + dy) for t, f, dx, dy in FUEL_TEXTS],
            [('fuel', fx, fy + ic['fuel'][0], ic['fuel'][1]), ('e36cool', kx, ky + ic['e36cool'][0], ic['e36cool'][1])])


EU = {
    'photo': 'eu-kmh-day-face.png',
    'mph': False,
    'face': {'box': [100, 470, 1900, 1075], 'radius': 190, 'arch': [1000, 2290, 1820], 'bezel': 40},
    # LCD, top lamp, ASC. The LCD (photo y 862) moved down so the speedometer's flat bottom, 4 target px under it,
    # lines up with the rev counter's (its cut, 974): on the car the rev counter's read lower. The ASC window (930)
    # up LIFT target px with the strips.
    'windows': [[617, round(974 - 4 / SCALE - 95, 2), 268, 95, 2], [915, 498, 195, 62, 6], [957, round(930 - LIFT / SCALE, 2), 92, 55, 6]],
    'dials': [
        small_dial('fuel', SMALL_X[0], SMALL_Y, [(0, -SMALL['sweep']), (1, SMALL['sweep'])]),
        {'signal': 'speed', 'c': [745, 763], 'r': 238, 'cut': 860, 'pivot': [745, 763], 'hub': 33, 'tail': 50,
         'needle': [228, 9], 'map': [(0, -114), (20, -108), (250, 117.82)], 'rest': -114},
        # its map, ticks and zones: rev_face(), one per redline (the default redline's here)
        # cut flat just under the economy gauge's ticks, as its window's bottom (the photos: EU ~978, US 945 -> 969)
        {'signal': 'rpm', 'c': [1270, 763], 'r': 238, 'cut': 974, 'pivot': [1270, 763], 'hub': 33, 'tail': 50,
         'needle': [228, 9], 'map': rev_face(_REDLINE['default'])['map'], 'rest': -117},
        # 50 and 120 °C at the blue and red blocks' middles; like the car's own gauge it holds the middle across the
        # normal running band (85 .. 105 °C), so a warm engine reads dead centre and only a real rise moves it
        small_dial('coolant', SMALL_X[1], SMALL_Y, [(40, -44), (50, -35), (85, 0), (105, 0), (120, 35), (130, 44)]),
    ],
    # [dial, first angle, last angle, count, outer radius, length, width]
    'ticks': [
        [1, -108, 108, 12, 234, 32, 9],                 # 20 .. 240 km/h
        [1, -98.18, 117.82, 12, 234, 20, 3.5],          # 30 .. 250
        small_ticks(0, 5),                              # 0 1/4 1/2 3/4 1/1
        small_ticks(3, 4),
    ],
    # [dial, from angle, to angle, inner radius, outer radius, colour(, stripes)]
    'zones': [
        small_block(0, -1, 'red'),                      # the reserve
        small_block(3, -1, 'blue'),
        small_block(3, 1, 'red'),
    ],
    # the speedometer's figures: (text, font, angle, the inner end of their ring's ticks(, the outer end of a ring further
    # in: they sit between the two)), photo px (figure_at())
    'figures': [(str(v), 'e_spd', -108 + (v - 20) * 216 / 220, 234 - 32) for v in range(20, 241, 20)],
    # the printing: (text, font, photo centre x, y); '⅟' is the fuel gauge's "1/1" (tools/bake_fonts.py)
    'texts': [
        ('km/h', 'e_lab', 752, 675), ('EG', 'e_xs', 752, 696),
        ('1/min', 'e_lab', 1264, 665), ('x1000', 'e_lab', 1264, 690),
        # the economy print, mirrored about the gauge's axis (the photo's, 1249: 10 and 20 39 px out; 0 and 30 112 px
        # out at 888 / 896 high, here 108 out at 900: under the window's slopes, 6.6 px off the ticks)
        ('0', 'e_lab', EU_AXIS - 108, 900), ('10', 'e_lab', EU_AXIS - 39, 931.5), ('20', 'e_lab', EU_AXIS + 39, 931.5),
        ('30', 'e_lab', EU_AXIS + 108, 900), ('L/100km', 'e_sm', EU_AXIS, 904),
    ],
    'econ': {'pivot': [EU_AXIS, 815], 'top': 877, 'len': 150, 'width': 4.5, 'ticks': eu_econ_ticks(),
             'values': list(range(0, 31, 2)), 'rest': -118},
}
# The economy gauge's window: level along its top, its two top corners cut off, sloping down to the dial's rim:
# (left corner dx from the rev counter's centre, its slope, right corner dx, its slope), photo px of the EU frame.
# The US face: the left corner 118 US px left of the pivot, slope 0.47; the right corner 117.5 US px right of it,
# slope 0.64 (its photo, face-on).
ECON_CHAM = (-147.0, 0.46, 139.0, 0.64)
ECON_GAP = 5                             # photo px between the slopes and the end ticks or figures under them
ECON_SLOPE = 0.46                        # the EU slopes, as its photo's left one (0.45; the US face's: 0.47, 0.64)


def eu_econ_cham():
    """The EU window, symmetric about its axis as on the car: its corners toward the 5 and 25 L/100km marks (between
    their ticks' ends), as far in as the slope (ECON_SLOPE) still clears the ticks and the figures under it by ECON_GAP."""
    a = math.radians(ECON_ANG[0] + (ECON_ANG[1] - ECON_ANG[0]) * 5 / 30)
    u = (math.sin(a), -math.cos(a))
    dx = -u[0] * (_ray_circle(ECON_C, u, *ECON_IN) + _ray_circle(ECON_C, u, *ECON_OUT)) / 2
    top, k, s = EU['econ']['top'], ECON_SLOPE, place(EU)[0]
    pts = [(o, w / 2) for o, i, w in eu_econ_ticks()]             # the ticks' outer ends, their half width
    for t, fn, x, y in EU['texts']:
        if t in ('0', '30') and abs(x - EU_AXIS) < 200:            # the economy's, not the fuel gauge's 0
            hw, hh, ix, iy = ink(t, fn)                           # target px about the widget's centre
            pts += [((x + (ix + sx * hw) / s, y + (iy - hh) / s), 0) for sx in (-1, 1)]   # the ink's top corners
    for (x, y), r in pts:
        dx = max(dx, abs(x - EU_AXIS) - (y - r - ECON_GAP - top) / k)
    return (-round(dx, 1), k, round(dx, 1), k)


_t, EU['icons'] = small_print(EU)     # icons: (icon, photo centre x, y, size)
EU['texts'] += _t


# --- the mph (US) face, us-mph-day-face.png (1600 x 1200) --------------------------------------------------------
# The same housing as the EU face: only the speedometer's scales, the economy gauge (MPG), the printing and the
# fuel reserve block (orange) differ. Its own measurements are taken relative to their dial's pivot in the US photo
# and carried into the EU photo's frame (k: the two photos' scale, from the hubs' distance, 525 / 442 px).
US_K = 525 / 442
US_PIVOT = {'spd': (583, 762), 'tach': (1025, 771)}
EU_PIVOT = {'spd': (745, 763), 'tach': (1270, 763)}


def us2eu(dial, x, y):
    (ux, uy), (ex, ey) = US_PIVOT[dial], EU_PIVOT[dial]
    return ex + (x - ux) * US_K, ey + (y - uy) * US_K


def mph_ang(v):
    """The outer MPH scale: 16.2 degrees a 10 mph, 20 mph at -102.3."""
    return -102.3 + 1.6225 * (v - 20)


US_ECON_TICKS = [   # (outer end, inner end) in the US photo, width, MPG (None: the wedges past 40, not on the map)
    ((885, 900), (912.5, 882.5), 10, None), ((905, 912.5), (927.5, 895), 10, None),
    ((931, 927.5), (952.5, 905), 4, 40), ((971, 930), (977.5, 914), 3.5, 25), ((992.5, 931), (996, 917.5), 3.5, 20),
    ((1025, 931), (1026, 919), 3.5, 15), ((1102.5, 930), (1082.5, 907.5), 4, 10), ((1155, 897.5), (1126, 878.5), 5, 8),
]
US = copy.deepcopy(EU)
US.update({
    'photo': 'us-mph-day-face.png',
    'mph': True,
})
US['dials'][1]['map'] = [(0, -121), (20, mph_ang(20)), (150, mph_ang(150))]
US['dials'][1]['rest'] = -121
US['ticks'] = [
    [1, mph_ang(10), mph_ang(150), 15, 228, 28.5, 8.3],                 # 10 .. 150 mph
    [1, mph_ang(15), mph_ang(145), 14, 228, 16.6, 3.5],                 # 15 .. 145
    [1, mph_ang(20 * 0.621371), mph_ang(240 * 0.621371), 23, 147, 10.7, 3],   # the inner km/h ring, 20 .. 240 every 10
] + [t for t in EU['ticks'] if t[0] != 1]
US['zones'] = [z if z[0] != 0 else small_block(0, -1, 'orange') for z in EU['zones']]
_ep = us2eu('tach', 1035.5, 818)
US['econ'] = {'pivot': [round(_ep[0], 1), round(_ep[1], 1)], 'top': round(us2eu('tach', 0, 861)[1], 1), 'len': 150,
              'width': 4.5, 'rest': -113, 'cham': ECON_CHAM,
              'ticks': [(us2eu('tach', *o), us2eu('tach', *i), w * US_K) for o, i, w, v in US_ECON_TICKS],
              'values': [v for o, i, w, v in US_ECON_TICKS]}
US['figures'] = ([(str(v), 'e_spd', mph_ang(v), 228 - 28.5, 147) for v in range(20, 141, 20)]     # MPH, between the rings
                 + [(str(v), 'e_lab', mph_ang(v * 0.621371), 147 - 10.7) for v in range(20, 241, 20)])   # km/h inside
_US_TEXTS = [   # (text, font, dial, US photo centre x, y)
    ('km/h', 'e_sm', 'spd', 587, 714), ('MPH', 'e_lab', 'spd', 589, 825), ('EG', 'e_xs', 'spd', 589, 842),
    ('MPG', 'e_sm', 'tach', 1017, 879.5), ('40', 'e_us', 'tach', 955, 891), ('25', 'e_us', 'tach', 979, 900),
    ('20', 'e_us', 'tach', 1002, 904), ('15', 'e_us', 'tach', 1026, 904), ('10', 'e_us', 'tach', 1076, 892),
    ('8', 'e_us', 'tach', 1112, 871),
]
_EU_TACH_TEXTS = [t for t in EU['texts'] if t[0] in ('1/min', 'x1000')]
_t, US['icons'] = small_print(US)     # the small gauges are the EU face's
US['texts'] = (_EU_TACH_TEXTS + [(t, f) + tuple(round(v, 1) for v in us2eu(d, x, y)) for t, f, d, x, y in _US_TEXTS]
               + _t)

# --- the M3 faces, for the redlines on the 0 .. 8 rev counter ----------------------------------------------------
# The E36 M3's cluster is the 328i's housing with its own print: red needles, a faster speedometer, the /M badge in the
# small window between the dials (the 328i's ASC lamp), and an oil temperature gauge where the 328i has its economy
# gauge. The rev counter is rev_face()'s 0 .. 8. Measured off m3-kmh-jpn-day-face.png (2000 x 1125, face-on: the km/h
# face; JPN, so its fuel gauge reads in litres: the EU face keeps its own) and m3-us-mph-day-face.png (1200 x 900, the
# mph face, shot from the left: read after mapping its tick rings' ellipse to a circle). Carried into the EU photo's
# frame about each dial's centre; KJ: the two photos' scale, from the dials' centres (544.5 px apart, the EU frame's
# 525).
M3_REDLINES = [r for r in REDLINES if rev_top(r) == 8]
KJ = 525 / 544.5
JPN_C = {'spd': (728.4, 534.1), 'tach': (1266.5, 533.5)}     # the dials' centres (the ticks' lines, the rims)


def j2e(dial, x, y):
    (jx, jy), (ex, ey) = JPN_C[dial], EU_PIVOT[dial]
    return ex + (x - jx) * KJ, ey + (y - jy) * KJ


def m3_kmh_ang(v):
    """The km/h face: linear (all 27 ticks to 1.5 degrees, the photo's own distortion), 20 at -110.2, 280 at 113.6."""
    return -127.37 + 0.86074 * v


def m3_mph_ang(v):
    """The mph face's MPH scale, its inner km/h ring the same (km/h x 0.621371): 10 at -121.7, 170 at 115.1."""
    return -136.46 + 1.47982 * v


# The oil temperature gauge, 50 .. 150 C every 10 (120 .. 300 F on the mph face, the same ticks): all point at one
# centre above the window, evenly spaced (to 0.2 degrees); their inner ends on one circle (to 0.3 px), their outer ends
# on another (the dial's rim, 16 px in) and level along the bottom. 50, 100 and 150 wide (100 a short block). JPN px.
OIL_C = (1258.6, 595.6)
OIL_ANG = lambda c: -68.609 - 1.11456 * c
OIL_IN = ((1258.4, 532.1), 193.7)
OIL_OUT = ((1258.5, 529.7), 235.4)
OIL_FLOOR = 737.4
OIL_W = {50: 8.3, 100: 9.0, 150: 8.3}, 3.1           # the wide ones, the rest (photo px, the ink as thresholded)
OIL_INK = 1.15                                       # thresholded widths to drawn ones (the 328i's: 7.8 px -> 9)
OIL_PIN = -118                                       # the needle's pin, under 50 C (the photo's cold needle)
OIL_LEN = 130                                        # the needle reaches the ticks' inner ends


def oil_ticks():
    """[(outer end, inner end, width)] in EU frame px, 50 .. 150 C."""
    out = []
    for v in range(50, 151, 10):
        a = math.radians(OIL_ANG(v))
        u = (math.sin(a), -math.cos(a))
        t_in = _ray_circle(OIL_C, u, *OIL_IN)
        t_out = min(_ray_circle(OIL_C, u, *OIL_OUT), (OIL_FLOOR - OIL_C[1]) / u[1])
        at = lambda t: tuple(round(c, 1) for c in j2e('tach', OIL_C[0] + u[0] * t, OIL_C[1] + u[1] * t))
        out.append((at(t_out), at(t_in), round(OIL_W[0].get(v, OIL_W[1]) * OIL_INK * KJ, 2)))
    return out


def m3_oil(us):
    """The oil gauge as an 'econ' entry: its ticks, values (C, or F on the mph face), pin, window."""
    piv = j2e('tach', *OIL_C)
    top = j2e('tach', 0, 649.5)[1]       # the window's top: level, its corners cut off at 1115 and 1412, slope 0.6
    cham = ((1115 - JPN_C['tach'][0]) * KJ, 0.6, (1412 - JPN_C['tach'][0]) * KJ, 0.6)
    vals = [120 + 18 * i if us else 50 + 10 * i for i in range(11)]
    return {'signal': 'oil', 'pivot': [round(piv[0], 1), round(piv[1], 1)], 'top': round(top, 1), 'len': OIL_LEN * KJ,
            'width': 4.5, 'ticks': oil_ticks(), 'values': vals, 'rest': OIL_PIN,
            'pin': [(104 if us else 40), OIL_PIN], 'cham': cham}


# its print, ink centres in the JPN photo: the figures at the wide ticks, the mark between 50 and 150
OIL_FIGS = [(1149.6, 668.6), (1258.6, 709.5), (1366.5, 668.5)]
OIL_MARK = (1260, 668, 40)        # centre x, y, box


def m3_face(base, us):
    f = copy.deepcopy(base)
    f['photo'] = 'm3-us-mph-day-face.png' if us else 'm3-kmh-jpn-day-face.png'
    f['redlines'] = M3_REDLINES
    f['needleColor'] = 'mneedle'
    spd = f['dials'][1]
    if us:
        a, km = m3_mph_ang, lambda v: m3_mph_ang(v * 0.621371)
        spd['map'], spd['rest'] = [(0, -137), (10, a(10)), (170, a(170))], -137
        # the US photo's rings (after the ellipse) scaled to the 328i US face's outer ring (228)
        f['ticks'] = [
            [1, a(10), a(170), 17, 228, 27.6, 8.3],                 # 10 .. 170 MPH
            [1, a(15), a(165), 16, 228, 27.6, 2.5],                 # 15 .. 165
            [1, km(20), km(280), 14, 142.8, 15.4, 3],               # the inner km/h ring: 20 .. 280 long
            [1, km(30), km(270), 13, 142.8, 10.7, 3],               # 30 .. 270 short
        ] + [t for t in base['ticks'] if t[0] != 1]
        f['figures'] = ([(str(v), 'e_spd', a(v), 228 - 27.6, 142.8) for v in range(20, 161, 20)]
                        + [(str(v), 'e_lab', km(v), 142.8 - 15.4) for v in range(20, 281, 20)])
        labels = ('120', '210', '300')
    else:
        a = m3_kmh_ang
        spd['map'], spd['rest'] = [(0, -119.5), (20, a(20)), (280, a(280))], -119.5
        f['ticks'] = [
            [1, a(20), a(280), 14, 234, 28, 8],                      # 20 .. 280 km/h
            [1, a(10), a(270), 14, 234, 28, 2.5],                    # 10 .. 270
        ] + [t for t in base['ticks'] if t[0] != 1]
        f['figures'] = [(str(v), 'e_spd', a(v), 234 - 28) for v in range(20, 281, 20)]
        labels = ('50', '100', '150')
    # the economy gauge's print out, the oil gauge's in
    f['texts'] = [t for t in f['texts'] if not (1100 < t[2] < 1450 and t[3] > 850)]     # under the rev counter
    f['inks'] = [(t, 'e_lab') + j2e('tach', x, y) for t, (x, y) in zip(labels, OIL_FIGS)]
    mx, my = j2e('tach', OIL_MARK[0], OIL_MARK[1])
    f['icons'] = f['icons'] + [('e36oil', mx, my, OIL_MARK[2] * KJ)]
    f['econ'] = m3_oil(us)
    f['logo'] = 'e36m'
    return f


EU['redlines'] = US['redlines'] = [r for r in REDLINES if r not in M3_REDLINES]
EU_M3, US_M3 = m3_face(EU, False), m3_face(US, True)
# the /M badge in the small window: its marks 0.774 of the plate's width, 0.505 of its height, centred
# (m3-kmh-jpn-day-face.png: plate 952 .. 1049.5 x 689.5 .. 745, marks 962.5 .. 1038 x 703 .. 731)
LOGO_FILL = (0.774, 0.505)

STRIP_TOP = 993    # the E36's warning-light strips' top (photo px): the face ends there, at BMW's own bottom row


def place(face):
    """Scale and offset from the face photo to the cluster, true proportions: as big as the screen allows, the fuel and
    coolant dials reaching its left and right edges (the bezel and the hood's outline go off-screen; the grey face
    fills behind), and the E36's warning-light strips' top at BMW's bottom row (y 660). The strips (STRIPS) are
    drawn there, over BMW's row, with holes for its range, drive mode, gear letter and electric range (HOLES)."""
    fuel, cool = face['dials'][0], face['dials'][3]
    left, right = fuel['c'][0] - fuel['r'] - 4, cool['c'][0] + cool['r'] + 4      # their dark rims included
    s = (W - 12) / (right - left)
    assert abs(s - SCALE) < 1e-9, 'SCALE is place()\'s'
    ox = 6 - s * left
    oy = ROW - s * STRIP_TOP
    return s, ox, oy


# the E36 fonts are baked at this scale's sizes (tools/bake_fonts.py E36_SIZE: sizes for a 0.9 scale, times this / 0.9)
def check_fonts(s):
    want = round(32 * s / 0.9)
    got = font('e_spd')['size']
    if got != want:
        raise SystemExit('fonts baked for another scale: e_spd is %d px, this layout needs %d (bake_fonts.py E36_SCALE = %.4f)'
                         % (got, want, s / 0.9))


def build(face, wid, toggle):
    s, ox, oy = place(face)
    check_fonts(s)
    X = lambda x: round(ox + s * x, 1)
    Y = lambda y: round(oy + s * y, 1)
    L = lambda v: round(s * v, 2)
    F = face['face']
    dials, needles = [], []
    for d in face['dials']:
        dials.append({'c': [X(d['c'][0]), Y(d['c'][1])], 'r': L(d['r']), 'cut': Y(d['cut']) if d.get('cut') else 0,
                      'pivot': [X(d['pivot'][0]), Y(d['pivot'][1])]})
        needles.append({'signal': d['signal'], 'pivot': [X(d['pivot'][0]), Y(d['pivot'][1])], 'hub': L(d['hub']),
                        'tail': L(d['tail']), 'needle': [L(d['needle'][0]), L(d['needle'][1])],
                        'map': [[v, round(a, 2)] for v, a in d['map']], 'rest': d['rest']})
    tk = lambda g: [g[0], round(g[1], 2), round(g[2], 2), g[3], L(g[4]), L(g[5]), L(g[6])]
    zn = lambda z: [z[0], round(z[1], 2), round(z[2], 2), L(z[3]), L(z[4]), z[5], z[6] if len(z) > 6 else 0]
    E = face['econ']
    px, py = E['pivot']
    # the ticks as printed, end to end (they don't all point at the needle's pivot: the right-hand ones lean over);
    # the needle's map from the angle of each tick's middle round the pivot
    eticks, emap, last = [], [], None
    for ((x0, y0), (x1, y1), w), v in zip(E['ticks'], E['values']):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        ang = math.degrees(math.atan2(mx - px, -(my - py)))
        if last is not None and ang - last > 180:     # the ticks point down past 6 o'clock: keep the angles continuous
            ang -= 360
        elif last is not None and last - ang > 180:
            ang += 360
        last = ang
        eticks.append([X(x0), Y(y0), X(x1), Y(y1), L(w)])
        if v is not None:
            emap.append([v, round(ang, 2)])
    if E.get('pin'):
        emap.append(list(E['pin']))       # the needle on its pin, under the scale
    emap.sort(key=lambda e: e[0])
    tach = face['dials'][2]
    dash = {
        'id': wid, 'type': 'e36dash', 'class': 'content', 'z': 0, 'x': 0, 'y': 0, 'w': W, 'h': TOP,
        'toggle': toggle, 'mph': face['mph'],
        'face': {'box': [X(F['box'][0]), Y(F['box'][1]), X(F['box'][2]), Y(F['box'][3])], 'radius': L(F['radius']),
                 'arch': [X(F['arch'][0]), Y(F['arch'][1]), L(F['arch'][2])], 'bezel': L(F['bezel'])},
        'dials': dials,
        'ticks': [tk(g) for g in face['ticks']],
        'zones': [zn(z) for z in face['zones']],
        # the rev counter (dial 2) for each "Redline" setting: its ticks and zones (rev_face())
        'revs': {str(red): {'dial': 2, 'ticks': [tk(g) for g in R['ticks']], 'zones': [zn(z) for z in R['zones']]}
                 for red, R in ((r, rev_face(r)) for r in face['redlines'])},
        'windows': [[X(x), Y(y), L(w), L(h), L(r)] for x, y, w, h, r in face['windows']],
        'strips': STRIPS, 'stripR': STRIP_R,
    }
    # the economy gauge's own layer: its window in the rev counter, from the top line down to the dial's flat cut
    cx, cy, r = X(tach['c'][0]), Y(tach['c'][1]), L(tach['r'])
    top, cut = Y(E['top']), Y(tach['cut'])
    half = math.sqrt(max(0.0, r * r - (top - cy) ** 2))
    ex0, ex1 = math.floor(cx - half) - 2, math.ceil(cx + half) + 2
    ey0, ey1 = math.floor(top) - 3, min(TOP, math.ceil(cut) + 2)
    cl, kl, cr, kr = E['cham']
    cham = [round(cx + L(cl), 1), kl, round(cx + L(cr), 1), kr]
    econ = {
        'id': wid.replace('dash', 'econ'), 'type': 'e36econ', 'class': 'content', 'z': 0,
        'x': ex0, 'y': ey0, 'w': ex1 - ex0, 'h': ey1 - ey0, 'toggle': toggle, 'mph': face['mph'],
        'tach': [cx, cy, r, cut],
        'econ': {'top': top, 'cham': cham, 'ticks': eticks},
    }
    # the needles' own layer on top of everything (the print, the icons, the economy gauge), as on the car: over the
    # needles' reach, down to BMW's bottom row
    reach = [(n['pivot'], max(n['needle'][0], n['tail'], n['hub']) + n['needle'][1] + 3) for n in needles]
    nx0 = max(0, math.floor(min(p[0] - k for p, k in reach)))
    nx1 = min(W, math.ceil(max(p[0] + k for p, k in reach)))
    ny0 = max(0, math.floor(min(p[1] - k for p, k in reach)))
    ndl = {
        'id': wid.replace('dash', 'needles'), 'type': 'e36needles', 'class': 'content', 'z': 6,
        'x': nx0, 'y': ny0, 'w': nx1 - nx0, 'h': TOP - ny0, 'toggle': toggle, 'mph': face['mph'],
        'dials': needles,
        # the rev counter's needle scale for each "Redline" setting (rev_face())
        'revs': {str(red): {'dial': 2, 'map': [[v, round(a, 2)] for v, a in rev_face(red)['map']]} for red in face['redlines']},
        'econ': {'pivot': [X(px), Y(py)], 'top': top, 'len': L(E['len']), 'width': L(E['width']), 'cham': cham,
                 'tach': [cx, cy, r, cut], 'map': emap, 'rest': E['rest']},
    }
    if E.get('signal'):
        ndl['econ']['signal'] = E['signal']
    if face.get('needleColor'):
        ndl['needleColor'] = face['needleColor']
    tag = wid.split('_')[1]
    # the speedometer's figures on their ticks' lines, all the same gap in from the ticks
    spd = [X(face['dials'][1]['pivot'][0]), Y(face['dials'][1]['pivot'][1])]
    segs, placed = tick_segs(face['ticks'], 1, spd, L), []
    texts = [text_widget('f%s_%d' % (tag, i), f[0], f[1],
                         *figure_at(f[0], f[1], f[2], L(f[3]), spd, segs, placed, L(f[4]) if len(f) > 4 else None),
                         toggle=toggle)
             for i, f in enumerate(face['figures'])]   # (text, font, angle, ring's inner end(, band's inner end))
    texts += [text_widget('t%s_%d' % (tag, i), t, fn, X(x), Y(y), toggle) for i, (t, fn, x, y) in enumerate(face['texts'])]
    # print placed by its ink's centre (photo px)
    for i, (t, fn, x, y) in enumerate(face.get('inks', [])):
        hw, hh, dx, dy = ink(t, fn)
        texts.append(text_widget('k%s_%d' % (tag, i), t, fn, X(x) - dx, Y(y) - dy, toggle))
    icons = [{'id': 'i%s_%s' % (tag, name), 'type': 'icon', 'icon': name, 'class': 'content', 'z': 5,
              'x': round(X(x) - L(sz) / 2), 'y': round(Y(y) - L(sz) / 2), 'w': round(L(sz)), 'h': round(L(sz)),
              'color': 'mark', 'toggle': toggle} for name, x, y, sz in face['icons']]
    if face.get('logo'):
        wx, wy, ww, wh = face['windows'][2][:4]
        lw, lh = L(ww * LOGO_FILL[0]), L(wh * LOGO_FILL[1])
        icons.append({'id': 'i%s_%s' % (tag, face['logo']), 'type': 'icon', 'icon': face['logo'], 'class': 'content', 'z': 5,
                      'x': round(X(wx + ww / 2) - lw / 2), 'y': round(Y(wy + wh / 2) - lh / 2), 'w': round(lw), 'h': round(lh),
                      'toggle': toggle})
    # the LCD under the speedometer (design/photos/e36/lcd-odometer.png): the odometer field (the range: the car has
    # no odometer signal), the unit, the trip; its second row (where the E36 put INSPECTION / OIL SERVICE) the clock
    # and the outside temperature, which the strips hide in BMW's row. Positions in the window, photo px of the EU frame.
    lx, ly, lw, lh = face['windows'][0][:4]
    unit = 'miles' if face['mph'] else 'km'

    def lcd(n, text, fname, x0, x1, ycen, align, color='lcd'):
        f = font(fname)
        h = f['cellHeight']
        return {'id': 'l%s_%s' % (tag, n), 'type': 'text', 'class': 'content', 'z': 5,
                'x': round(X(lx + x0)), 'y': round(Y(ly + ycen) - h / 2), 'w': round(L(x1 - x0)), 'h': h,
                'text': text, 'font': fname, 'color': color, 'align': align, 'toggle': toggle}
    r1, r2 = 18, 50
    # the range marked as the E90's LCD marks it: an arrow into the fuel pump just before the figure, wherever its
    # digits start (hud.js text "lead"), level with the digits' ink
    top, bot = digit_ink('e_lcd')
    rng = lcd('range', '{{range}}', 'e_lcd', 4, 122, r1, 'right')
    ih = round((bot - top) * 0.8)      # the pump 80 % of the digits' height (car photo, Nov 17 2026: as tall read too big)
    rng['lead'] = {'icon': 'e36range', 'w': round(ih * 1.4), 'h': ih, 'gap': 3, 'cy': (top + bot) / 2}
    lcds = [
        rng,
        lcd('unit', unit, 'e_sm', 126, 172, r1 + 2, 'left'),
        lcd('trip', '{{trip}}', 'e_lcd', 176, 262, r1, 'right'),
        lcd('time', '{{time}}', 'e_lcd2', 6, 96, r2, 'left'),
        lcd('ampm', '{{ampmLc}}', 'e_sm', 98, 124, r2 + 1, 'left'),
        lcd('out', '{{ambient}}', 'e_lcd2', 150, 236, r2, 'right'),
        lcd('outu', '{{u:temp}}', 'e_sm', 238, 262, r2 + 1, 'left'),
    ]
    # the strip row: the same picture in pieces round the holes, where BMW's own items show (a layer is a rectangle)
    edges = [0] + [x for h in HOLES for x in h] + [W]
    pieces = [dict(dash, id='%s_%s' % (wid, 'abcd'[i]), x=x0, y=TOP, w=x1 - x0, h=H - TOP, stripRow='abcd'[i])
              for i, (x0, x1) in enumerate(zip(edges[::2], edges[1::2]))]
    return [dash, econ] + pieces + texts + icons + lcds + [ndl]


def rev_texts():
    """The rev counter's figures, one set per scale, shown for the redlines on it (both faces: one rev counter)."""
    s, ox, oy = place(EU)
    assert place(US) == (s, ox, oy), 'the faces are placed alike'
    X = lambda x: ox + s * x
    Y = lambda y: oy + s * y
    L = lambda v: round(s * v, 2)
    piv = [X(EU['dials'][2]['pivot'][0]), Y(EU['dials'][2]['pivot'][1])]
    out = []
    for top in REV_TOPS:
        reds = [r for r in REDLINES if rev_top(r) == top]
        if not reds:
            continue
        segs, placed = tick_segs(rev_face(reds[0])['ticks'], 2, piv, L), []
        for k, (_, ang) in enumerate(tach_ang(top)):
            cx, cy = figure_at(str(k), 'e_rpm', ang, L(REV_TICK[0] - REV_TICK[1]), piv, segs, placed)
            out.append(text_widget('trev%d_%d' % (top, k), str(k), 'e_rpm', cx, cy, 'redline=' + '|'.join(str(r) for r in reds)))
    return out


EU['econ']['cham'] = eu_econ_cham()      # it needs place()


def layout():
    m3 = 'redline=' + '|'.join(str(r) for r in M3_REDLINES)
    widgets = (build(EU, 'dash_eu', 'units=metric&!' + m3) + build(US, 'dash_us', 'units=us&!' + m3)
               + build(EU_M3, 'dash_eum3', 'units=metric&' + m3) + build(US_M3, 'dash_usm3', 'units=us&' + m3) + rev_texts())
    return {
        'schemaVersion': 2,
        'name': 'E36',
        'packWaste': 3,
        'theme': 'e36',
        '_about': 'E36 cluster, generated by tools/gen_e36.py (edit that and regenerate): the BMW E36 328i cluster of the 90s, '
                  'traced off face-on photos at true proportions. The EU face (km/h, L/100km) with metric units, the US face '
                  '(MPH, MPG) with US units; with the redline at 7000 or 7500 (the 0 .. 8 rev counter) the M3\'s faces: red needles, '
                  'a 280 km/h or 170 MPH speedometer, the /M badge, an oil temperature gauge for the economy gauge. One e36dash shader draws the face, dials, ticks, zones and needles; the printed '
                  'figures are text widgets. Lamps are never lit (the app cannot read them); BMW draws its own turn arrows over the cluster. The warning-light strips '
                  'cover BMW\'s bottom row but for holes where its range, drive mode, gear letter and electric range show; the clock '
                  'and outside temperature are on the LCD\'s second row. Widget reference: shared/hud.js header.',
        'widgets': widgets,
    }


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else 'apps/cluster_themes/hud/layouts/e36.json'
    data = layout()
    with open(out, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print('wrote', out)
