'use strict';
/**
 * Small line icons, drawn here (no image files): straight RGBA for an image layer, w x h, in one colour.
 * Each icon is a distance function in a 0..1 box; coverage is anti-aliased over one pixel.
 *   gauge (engine), shock (chassis), wheel (steering), cog (gearbox), stack (the gear's shift program
 *   lines), therm (coolant), oilcan (oil), fuel (pump), tyre (a wheel seen from the side: ring, hub, tread notches),
 *   e36cool (the E36 coolant gauge's thermometer over waves), e36oil (the E36 M3's oil temperature mark: a thermometer
 *   over an oil can), e36range (the range: an arrow into the fuel pump, as the E90's LCD shows it).
 * LOGOS are drawn in their own colours (the icon's colour is not used), in their own units at their own aspect:
 *   e36m (the E36 M3's /M badge: three slanted stripes and the M, traced off design/photos/e36/m3-kmh-jpn-day-face.png).
 */
function seg(px, py, ax, ay, bx, by) {
    const pax = px - ax, pay = py - ay, bax = bx - ax, bay = by - ay;
    const h = Math.max(0, Math.min(1, (pax * bax + pay * bay) / (bax * bax + bay * bay)));
    return Math.hypot(pax - bax * h, pay - bay * h);
}
const ring = (px, py, cx, cy, r) => Math.abs(Math.hypot(px - cx, py - cy) - r);
const disc = (px, py, cx, cy, r) => Math.hypot(px - cx, py - cy) - r;
/** Signed distance to a polygon (negative inside), its points [[x, y], ...]. */
function poly(px, py, pts) {
    let d = Infinity, inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
        const [ax, ay] = pts[j], [bx, by] = pts[i];
        d = Math.min(d, seg(px, py, ax, ay, bx, by));
        if ((ay > py) !== (by > py) && px < ax + (bx - ax) * (py - ay) / (by - ay)) inside = !inside;
    }
    return inside ? -d : d;
}
function arc(px, py, cx, cy, r, a0, a1) {          // clockwise from 12 o'clock, radians, a0 < a1
    let a = Math.atan2(px - cx, -(py - cy)); if (a < a0) a += Math.PI * 2;
    if (a <= a1) return Math.abs(Math.hypot(px - cx, py - cy) - r);
    const e = (b) => Math.hypot(px - (cx + r * Math.sin(b)), py - (cy - r * Math.cos(b)));
    return Math.min(e(a0), e(a1));
}

/** Distance (in box units) from (x, y) to the icon's ink; < 0 or < stroke half-width = ink. */
const ICONS = {
    gauge: (x, y) => Math.min(arc(x, y, 0.5, 0.55, 0.36, -0.75 * Math.PI, 0.75 * Math.PI) - 0.045, seg(x, y, 0.5, 0.55, 0.7, 0.36) - 0.045, disc(x, y, 0.5, 0.55, 0.07)),
    shock: (x, y) => Math.min(seg(x, y, 0.24, 0.78, 0.5, 0.52) - 0.11, seg(x, y, 0.5, 0.52, 0.78, 0.24) - 0.035, seg(x, y, 0.14, 0.88, 0.2, 0.82) - 0.05, seg(x, y, 0.8, 0.22, 0.86, 0.16) - 0.05),
    wheel: (x, y) => Math.min(ring(x, y, 0.5, 0.5, 0.38) - 0.055, disc(x, y, 0.5, 0.55, 0.1), seg(x, y, 0.14, 0.5, 0.42, 0.55) - 0.05, seg(x, y, 0.58, 0.55, 0.86, 0.5) - 0.05, seg(x, y, 0.5, 0.62, 0.5, 0.86) - 0.05),
    cog: (x, y) => {
        let d = Math.min(ring(x, y, 0.5, 0.5, 0.27) - 0.06, ring(x, y, 0.5, 0.5, 0.1) - 0.04);
        for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4; d = Math.min(d, seg(x, y, 0.5 + 0.3 * Math.sin(a), 0.5 - 0.3 * Math.cos(a), 0.5 + 0.42 * Math.sin(a), 0.5 - 0.42 * Math.cos(a)) - 0.06); }
        return d;
    },
    stack: (x, y) => Math.min(seg(x, y, 0.1, 0.22, 0.62, 0.22) - 0.055, seg(x, y, 0.1, 0.5, 0.78, 0.5) - 0.06, seg(x, y, 0.1, 0.8, 0.92, 0.8) - 0.075),
    therm: (x, y) => Math.min(seg(x, y, 0.42, 0.14, 0.42, 0.6) - 0.07, disc(x, y, 0.42, 0.74, 0.14), seg(x, y, 0.6, 0.24, 0.76, 0.24) - 0.035, seg(x, y, 0.6, 0.4, 0.76, 0.4) - 0.035),
    fuel: (x, y) => Math.min(Math.max(Math.abs(x - 0.38) - 0.18, Math.abs(y - 0.55) - 0.33, -Math.max(Math.abs(x - 0.38) - 0.1, Math.abs(y - 0.36) - 0.08)), seg(x, y, 0.12, 0.9, 0.64, 0.9) - 0.04, seg(x, y, 0.56, 0.36, 0.76, 0.48) - 0.04, seg(x, y, 0.76, 0.48, 0.76, 0.78) - 0.04, seg(x, y, 0.76, 0.78, 0.66, 0.84) - 0.04),
    tyre: (x, y) => {
        let d = Math.min(ring(x, y, 0.5, 0.5, 0.4) - 0.075, ring(x, y, 0.5, 0.5, 0.17) - 0.04);
        for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4 + Math.PI / 8; d = Math.min(d, seg(x, y, 0.5 + 0.42 * Math.sin(a), 0.5 - 0.42 * Math.cos(a), 0.5 + 0.49 * Math.sin(a), 0.5 - 0.49 * Math.cos(a)) - 0.04); }
        return d;
    },
    // E36: the coolant gauge's mark, a thermometer with three graduations over two waves
    e36cool: (x, y) => {
        const wave = (yc) => Math.max(Math.abs(y - (yc + 0.045 * Math.sin((x - 0.12) * Math.PI * 5))) - 0.035, Math.abs(x - 0.5) - 0.36);
        return Math.min(seg(x, y, 0.5, 0.1, 0.5, 0.5) - 0.06, disc(x, y, 0.5, 0.56, 0.1),
            seg(x, y, 0.6, 0.16, 0.72, 0.16) - 0.03, seg(x, y, 0.6, 0.27, 0.72, 0.27) - 0.03, seg(x, y, 0.6, 0.38, 0.72, 0.38) - 0.03,
            wave(0.76), wave(0.9));
    },
    // E36 M3: the oil temperature gauge's mark (design/photos/e36/m3-kmh-jpn-day-face.png): a thermometer with three
    // graduations over an oil can (handle loop, cap, body, spout and a drop)
    e36oil: (x, y) => Math.min(seg(x, y, 0.475, 0.1, 0.475, 0.46) - 0.038, disc(x, y, 0.475, 0.465, 0.07),
        seg(x, y, 0.5, 0.175, 0.62, 0.175) - 0.02, seg(x, y, 0.5, 0.275, 0.64, 0.275) - 0.02, seg(x, y, 0.5, 0.362, 0.64, 0.362) - 0.02,
        ring(x, y, 0.13, 0.69, 0.075) - 0.033, poly(x, y, [[0.31, 0.6], [0.45, 0.6], [0.45, 0.72], [0.31, 0.72]]),
        seg(x, y, 0.2, 0.725, 0.62, 0.725) - 0.038, seg(x, y, 0.6, 0.73, 0.9, 0.625) - 0.045,
        seg(x, y, 0.76, 0.76, 0.64, 0.89) - 0.04, seg(x, y, 0.21, 0.9, 0.66, 0.9) - 0.04, seg(x, y, 0.21, 0.72, 0.21, 0.9) - 0.04,
        disc(x, y, 0.94, 0.865, 0.045), seg(x, y, 0.94, 0.79, 0.94, 0.86) - 0.02),
    // drawn 1.4 wide to 1 high (x -0.2 .. 1.2 in its box): the arrow on the left, fuel's pump the box's full height
    // on the right (its ink, unit x 0.08 .. 0.8 and y 0.22 .. 0.94, scaled to x 0.2 .. 1.2, y 0 .. 1)
    e36range: (x, y) => Math.min(seg(x, y, -0.15, 0.5, 0.1, 0.5) - 0.055, seg(x, y, 0.1, 0.5, -0.02, 0.38) - 0.055,
        seg(x, y, 0.1, 0.5, -0.02, 0.62) - 0.055, ICONS.fuel(0.08 + (x - 0.2) * 0.72, 0.22 + y * 0.72) / 0.72),
    oilcan: (x, y) => Math.min(seg(x, y, 0.2, 0.62, 0.6, 0.62) - 0.12, seg(x, y, 0.6, 0.58, 0.9, 0.44) - 0.04, disc(x, y, 0.92, 0.66, 0.05), seg(x, y, 0.3, 0.46, 0.3, 0.34) - 0.035, seg(x, y, 0.22, 0.34, 0.38, 0.34) - 0.04),
};

// Multi-colour marks: { w, h (their units), layers: [[r, g, b], distance (x, y) in those units], bottom first }
// the /M badge's slant: 15 units right over its 28 high
const mslant = (x0, x1) => [[x0, 28], [x0 + 15, 0], [x1 + 15, 0], [x1, 28]];
const LOGOS = {
    e36m: {
        w: 76, h: 28,
        layers: [
            [[70, 170, 240], (x, y) => poly(x, y, mslant(0, 12))],          // light blue
            [[92, 72, 160], (x, y) => poly(x, y, mslant(13, 21))],          // dark blue
            [[232, 52, 40], (x, y) => poly(x, y, mslant(21.5, 33))],        // red
            [[232, 232, 230], (x, y) => poly(x, y, [[34.5, 28], [49.5, 0], [59.5, 0], [59.5, 8.5],
                [65.5, 0], [75.5, 0], [75.5, 28], [64, 28], [63.5, 21], [60, 28], [49, 28], [48.5, 21], [45, 28]])],   // the M
        ],
    },
};

function paintLogo(L, w, h) {
    const out = Buffer.alloc(w * h * 4, 0);
    const k = Math.min(w / L.w, h / L.h), ox = (w - L.w * k) / 2, oy = (h - L.h * k) / 2;
    for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
            const lx = (x + 0.5 - ox) / k, ly = (y + 0.5 - oy) / k, o = (y * w + x) * 4;
            let r = 0, g = 0, b = 0, a = 0;      // straight alpha, layered bottom up
            L.layers.forEach(([c, f]) => {
                const t = Math.max(0, Math.min(1, 0.5 - f(lx, ly) * k));
                if (t <= 0) return;
                const na = t + a * (1 - t);
                r = (c[0] * t + r * a * (1 - t)) / na; g = (c[1] * t + g * a * (1 - t)) / na; b = (c[2] * t + b * a * (1 - t)) / na;
                a = na;
            });
            if (a <= 0) continue;
            out[o] = Math.round(r); out[o + 1] = Math.round(g); out[o + 2] = Math.round(b); out[o + 3] = Math.round(a * 255);
        }
    }
    return out;
}

function paintIcon(name, w, h, color) {
    if (LOGOS[name]) return paintLogo(LOGOS[name], w, h);
    const f = ICONS[name], out = Buffer.alloc(w * h * 4, 0);
    if (!f) return out;
    const s = Math.min(w, h), ox = (w - s) / 2, oy = (h - s) / 2, aa = 1 / s;
    for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
            const d = f((x + 0.5 - ox) / s, (y + 0.5 - oy) / s);
            const a = Math.max(0, Math.min(1, 0.5 - d / aa));
            if (a <= 0) continue;
            const o = (y * w + x) * 4;
            out[o] = color[0]; out[o + 1] = color[1]; out[o + 2] = color[2]; out[o + 3] = Math.round(a * (color[3] === undefined ? 255 : color[3]));
        }
    }
    return out;
}

module.exports = { paintIcon, ICONS, LOGOS };
