'use strict';
/**
 * Holiday colours on the HUD (HUD Widgets, Nov 13 2026). The "holiday" setting (as Cluster Themes': off, auto by the
 * date, or one holiday; windows in holidays.js) recolours the active HUD theme's ACCENTS while a holiday is on:
 * labels ("dim"), the arc, the shift bar zones, the rev bar ramp, cruise / retro headers and the M stripe. "fg" (the
 * big numbers) and "hot" (the shift red, the warnings) stay as they are: the HUD floats over the road, so the
 * readouts read first, and nothing here moves or falls (no snow on the HUD). Every colour keeps the HUD rule of
 * hud_themes.js (opaque, no lit colour under about 70 % brightness; dark blue is lifted for the glass).
 * Christmas also turns shift lights into a string of bulbs: "lightBulbs" [red, green, gold, blue] makes hud.js send
 * the lights shader u_round 2, which colours the lights in turn with its four existing colour uniforms (no new
 * shader, no new uniform); they still fill with the revs and all flash red at the shift point.
 * The greeting ("hudGreeting", role "greet"): hud.js {{greeting}}, shown only while the car stands (under 3 km/h); the
 * HUD layouts (design/gen_hud.py) carry a "greeting" text widget that stays empty otherwise.
 *
 *   theme(base, key)   a copy of theme `base` in holiday `key`'s colours (base itself is never changed)
 */
const RED = [255, 42, 42];          // the shift red (hud_themes.js "hot"): also the first bulb

const SETS = {
    xmas: {
        name: 'Christmas', greeting: 'MERRY CHRISTMAS', greet: [255, 84, 84],
        dim: [255, 214, 120], arc: [64, 226, 116], line: [255, 214, 120], head: [255, 84, 84], accent: [64, 226, 116],
        green: [64, 226, 116],
        bar: { low: [64, 226, 116], mid: [255, 206, 84] },
        rev: { from: [64, 226, 116], to: [255, 206, 84] },
        stripe: [[64, 226, 116], [255, 206, 84], [255, 64, 64]],
        lightBulbs: [RED, [64, 226, 116], [255, 206, 84], [96, 156, 255]],
    },
    thanks: {
        name: 'Thanksgiving', greeting: 'HAPPY THANKSGIVING', greet: [255, 150, 60],
        dim: [240, 196, 140], arc: [255, 150, 60], line: [240, 196, 140], head: [255, 150, 60], accent: [236, 186, 84],
        green: [236, 186, 84],
        bar: { low: [236, 186, 84], mid: [255, 150, 60] },
        rev: { from: [236, 186, 84], to: [255, 150, 60] },
        stripe: [[236, 186, 84], [255, 150, 60], [235, 100, 56]],
    },
    newyear: {
        name: 'New Year', greeting: 'HAPPY NEW YEAR', greet: [255, 210, 74],
        dim: [236, 218, 160], arc: [255, 210, 74], line: [236, 218, 160], head: [255, 210, 74], accent: [226, 230, 240],
        green: [255, 210, 74],
        bar: { low: [226, 230, 240], mid: [255, 210, 74] },
        rev: { from: [200, 206, 220], to: [255, 210, 74] },
        stripe: [[226, 230, 240], [255, 210, 74], [255, 244, 214]],
    },
    july4: {
        name: 'Fourth of July', greeting: 'HAPPY 4TH OF JULY', greet: [255, 80, 80],
        dim: [180, 200, 255], arc: [255, 80, 80], line: [180, 200, 255], head: [255, 80, 80], accent: [120, 166, 255],
        green: [120, 166, 255],
        bar: { low: [120, 166, 255], mid: [244, 246, 255] },
        rev: { from: [120, 166, 255], to: [244, 246, 255] },
        stripe: [[120, 166, 255], [244, 246, 255], [255, 64, 64]],
    },
};

const rgba = (c, a) => [c[0], c[1], c[2], a === undefined ? 255 : a];

/** Theme `base` in holiday `key`'s colours, or base itself for an unknown key. */
function theme(base, key) {
    const s = SETS[key];
    if (!s) return base;
    const t = Object.assign({}, base, {
        name: base.name + ' · ' + s.name,
        holiday: key,
        dim: rgba(s.dim), arc: rgba(s.arc),
        bar: Object.assign({}, base.bar, s.bar),             // the high zone keeps the theme's red
        rev: Object.assign({}, base.rev, s.rev),
        stripeColors: s.stripe.map((c) => rgba(c)),
        greet: rgba(s.greet), hudGreeting: s.greeting,
    });
    // the cluster-style roles only where the theme has them (Cruise, Retro on the HUD)
    ['line', 'head', 'accent', 'green'].forEach((r) => { if (base[r]) t[r] = rgba(s[r], base[r][3]); });
    if (s.lightBulbs) t.lightBulbs = s.lightBulbs;
    return t;
}

module.exports = { SETS, theme };
