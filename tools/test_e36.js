'use strict';
/**
 * Tests for the E36 cluster theme (Cluster Themes "e36"): the needle and economy maths in shared/e36.js, the
 * night colours, and the generated layout. Plain Node with assert, no dependencies:
 *
 *     node tools/test_e36.js
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

let failed = 0, passed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
    } catch (e) {
        failed++;
        console.log('FAIL ' + name + '\n  ' + (e && e.message || e));
    }
}
const near = (a, b, eps) => Math.abs(a - b) <= eps;

const e36 = require('../shared/e36');

// --- needle maths -------------------------------------------------------------------------------------------------
test('Needle: the first target as is, then a glide to a new one with no overshoot', () => {
    const n = new e36.Needle(22);
    assert.strictEqual(n.step(-117, 0), -117);
    let last = -117;
    for (let t = 16; t <= 600; t += 16) {
        const a = n.step(60, t);
        assert.ok(a >= last - 1e-9 && a <= 60 + 1e-9, 't ' + t + ': ' + a);
        last = a;
    }
    assert.ok(near(last, 60, 0.05) && n.still(60), 'settled: ' + last);
});
test('Needle: the same motion at any frame rate (worked out exactly over each step)', () => {
    const a = new e36.Needle(22), b = new e36.Needle(22);
    a.step(0, 0); b.step(0, 0);
    let x, y;
    for (let t = 10; t <= 200; t += 10) x = a.step(100, t);
    for (let t = 50; t <= 200; t += 50) y = b.step(100, t);
    assert.ok(near(x, y, 1e-6), x + ' / ' + y);
});
test('Needle: a steady sweep trails by 2 / w seconds; 20 Hz steps come out as one smooth glide', () => {
    const n = new e36.Needle(22);
    n.step(0, 0);
    let a = 0;
    for (let t = 1; t <= 2000; t++) a = n.step(t * 0.1, t);       // 100 degrees a second
    assert.ok(near(200 - a, 100 * 2 / 22, 0.05), 'lag ' + (200 - a));
    // the car's samples in 50 ms steps, drawn every 16 ms: the needle never stops or goes back while they climb
    const m = new e36.Needle(22);
    m.step(0, 0);
    let prev = 0, prevV = 0, minV = Infinity;
    for (let t = 16; t <= 1000; t += 16) {
        const v = m.step(Math.floor(t / 50) * 5, t) - prev;        // 5 degrees each sample
        if (t > 200) minV = Math.min(minV, v);
        prev += v; prevV = v;
    }
    assert.ok(minV > 0.5, 'always moving on: the slowest frame ' + minV.toFixed(2) + ' deg (the 20 Hz tick: 0 for two frames of three)');
    assert.ok(prevV > 0);
});
test('Needle.ahead: where step() will be, without moving it', () => {
    const n = new e36.Needle(28);
    n.step(0, 0); n.step(50, 30);
    const want = n.ahead(50, 80), a = n.a;
    assert.strictEqual(n.a, a);
    assert.ok(near(n.step(50, 110), want, 1e-9));
});
// the car's side of a path: cluster-render takes a uniform up at its next 30 Hz tick (here a random phase and up to
// 8 ms on the socket), the display draws at 60 Hz and places the needle from the path it has then
function drive(sendMs, sampleMs, rpmAt, mode) {
    const W = 28, KEEP = 0.06, AHEAD = 0.07, n = new e36.Needle(W);
    const queue = [];
    let have = null, prev = null, angle = null, sampleAt = -1, sample = 0;
    const frames = [];
    let seed = 7;
    const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    const tickPhase = rnd() * 33.3;
    for (let ms = 0; ms <= 3000; ms++) {
        if (ms - sampleAt >= sampleMs) { sampleAt = ms; sample = rpmAt(ms); }       // the car's samples, in steps
        if (ms % sendMs === 0) {
            n.step(sample, ms);
            if (mode === 'path') {
                prev = e36.nextPath(prev, ms / 1000, KEEP, AHEAD, n.ahead(sample, (KEEP + AHEAD) * 1000));
                queue.push({ at: ms + rnd() * 8, v: prev });
            } else queue.push({ at: ms + rnd() * 8, v: n.a });
        }
        if ((ms - tickPhase) % 33.333 < 1 && ms >= tickPhase) {          // the service's tick: what has arrived goes
            while (queue.length && queue[0].at <= ms) { const q = queue.shift(); if (mode === 'path') have = q.v; else angle = q.v; }
        }
        if (ms % 16.667 < 1 && ms > 500) frames.push(mode === 'path' ? (have ? e36.pathAt(have, ms / 1000) : 0) : angle);
    }
    return frames;
}
test('needle paths: the renderer\'s frames move evenly, where angles sent at 60 Hz step (cluster-render\'s 30 Hz tick)', () => {
    const rev = (ms) => 1000 + 2500 * Math.max(0, Math.min(1, (ms - 400) / 2000));     // a steady pull, 75 rpm a tick
    const steps = (f) => f.slice(1).map((v, i) => v - f[i]).slice(5, -60);              // per display frame, mid-pull
    const evenness = (d) => { const m = d.reduce((a, b) => a + b, 0) / d.length; return Math.max.apply(null, d.map((x) => Math.abs(x - m))) / m; };
    const path = evenness(steps(drive(50, 20, rev, 'path'))), angles = evenness(steps(drive(16, 20, rev, 'angle')));
    assert.ok(path < 0.25, 'paths: a frame\'s step off the mean by ' + (path * 100).toFixed(0) + ' %');
    assert.ok(angles > 0.9, 'angles at 60 Hz still step (a frame with none, then two): ' + (angles * 100).toFixed(0) + ' %');
});
test('nextPath: the new path is the one before over the kept stretch (a late one joins on)', () => {
    let P = null;
    const n = new e36.Needle(28);
    for (let k = 0; k < 40; k++) {
        const t = k * 0.05 + (k % 3) * 0.004, to = 30 * Math.sin(k / 4);
        n.step(to, t * 1000);
        const Q = e36.nextPath(P, t, 0.06, 0.07, n.ahead(to, 130));
        if (P) for (let s = -0.02; s <= 0.06; s += 0.005) assert.ok(near(e36.pathAt(Q, t + s), e36.pathAt(P, t + s), 1e-9), k + ' at ' + s);
        assert.strictEqual(Q.a[3], n.ahead(to, 130));
        P = Q;
    }
});
test('pathAt: held after the last key, at most 0.25 s back before the first', () => {
    const P = { t: [10, 0.06, 0.13, 0.13], a: [5, 6, 20, 20] };
    assert.strictEqual(e36.pathAt(P, 99), 20);
    assert.ok(near(e36.pathAt(P, 9.8), 5 - (1 / 0.06) * 0.2, 1e-9), 'back along the first leg');
    assert.strictEqual(e36.pathAt(P, 9), 5, 'clocks further apart: the first key, never off along the line');
    assert.strictEqual(e36.pathAt(P, -500), 5);
    assert.ok(near(e36.pathAt(P, 10.03), 5.5, 1e-9));
});
test('frac: linear between the ends', () => {
    const t = [[20, 0], [240, 1]];
    assert.strictEqual(e36.frac(130, t), 0.5);
});
test('frac: clamps below and above the scale', () => {
    const t = [[20, 0], [240, 1]];
    assert.strictEqual(e36.frac(5, t), 0);
    assert.strictEqual(e36.frac(300, t), 1);
    assert.strictEqual(e36.frac(-12, t), 0);
});
test('frac: no data gives null (the needle parks)', () => {
    const t = [[20, 0], [240, 1]];
    assert.strictEqual(e36.frac(null, t), null);
    assert.strictEqual(e36.frac(undefined, t), null);
    assert.strictEqual(e36.frac(NaN, t), null);
});
test('frac: a descending table (the MPG face runs 40 -> 8)', () => {
    assert.strictEqual(e36.frac(10, [[40, 0], [8, 1]]), 0.9375);
    assert.strictEqual(e36.frac(Infinity, [[40, 0], [8, 1]]), 0);
});
test('frac: piecewise, a non-linear scale', () => {
    const t = [[50, 0], [90, 0.5], [120, 1]];
    assert.strictEqual(e36.frac(70, t), 0.25);
    assert.strictEqual(e36.frac(105, t), 0.75);
    assert.strictEqual(e36.frac(135, t), 1);
});

// --- economy -------------------------------------------------------------------------------------------------------
test('flowLph: idle floor and full power', () => {
    assert.strictEqual(e36.flowLph(0), 0.8);
    assert.ok(near(e36.flowLph(100), 33.56, 0.01), String(e36.flowLph(100)));
    assert.strictEqual(e36.flowLph(null), null);
});
test('economy: L/100km and MPG', () => {
    assert.strictEqual(e36.economy(100, 8, false), 8);
    assert.ok(near(e36.economy(100, 8, true), 29.4, 0.01), String(e36.economy(100, 8, true)));
});
test('economy: standing still reads the thirsty end (the EU face\'s 30, finite so the needle eases)', () => {
    assert.strictEqual(e36.economy(1, 0.8, false), 30);
    assert.strictEqual(e36.economy(2, 0.8, true), 0);
    assert.strictEqual(e36.economy(0, 0.8, false), 30);
    assert.strictEqual(e36.economy(4, 2, false), 30);          // 50 L/100km: past the face's end
});
test('economy: creeping at 2..4 km/h moves the needle smoothly, no 15 degree jumps', () => {
    const s = new e36.Smooth(1000);
    const vals = [2, 3.2, 2.5, 3.5, 2.8].map((kmh, i) => s.step(e36.economy(kmh, 0.8, false), i * 50));
    vals.forEach((v) => assert.ok(isFinite(v) && v > 26 && v <= 30, String(v)));
});
test('economy: no data gives null', () => {
    assert.strictEqual(e36.economy(null, 8, false), null);
    assert.strictEqual(e36.economy(50, null, true), null);
});

// --- smoothing and the trip readout ------------------------------------------------------------------------------------------
test('Smooth: first value taken as is, then eases', () => {
    const s = new e36.Smooth(1000);
    assert.strictEqual(s.step(10, 0), 10);
    const v = s.step(20, 1000);
    assert.ok(v > 15 && v < 20, String(v));
});
test('Smooth: no data drops the value (the needle parks), and the next value starts fresh', () => {
    const s = new e36.Smooth(1000);
    s.step(10, 0);
    assert.strictEqual(s.step(null, 500), null);
    assert.strictEqual(s.step(25, 1000), 25);
});
test('Smooth: Infinity in or out does not poison it', () => {
    const s = new e36.Smooth(1000);
    s.step(10, 0);
    const v = s.step(Infinity, 500);
    assert.strictEqual(v, Infinity);
    const w = s.step(12, 1000);
    assert.ok(isFinite(w), String(w));
});
test('tripText: the E36 trip reads 000.0, in km or miles, and rolls over at 1000', () => {
    assert.strictEqual(e36.tripText(0, false), '000.0');
    assert.strictEqual(e36.tripText(12.34, false), '012.3');
    assert.strictEqual(e36.tripText(1.609344, true), '001.0');
    assert.strictEqual(e36.tripText(1000.5, false), '000.5');
    assert.strictEqual(e36.tripText(null, false), '000.0');
});

// --- night colours ------------------------------------------------------------------------------------------------
const themes = require('../shared/hud_themes');
test('nightTheme: a theme with night roles takes them', () => {
    const t = themes.nightTheme(themes.THEMES.e36, 0.6);
    assert.deepStrictEqual(t.mark, themes.THEMES.e36.night.mark);
    assert.deepStrictEqual(t.lcd, themes.THEMES.e36.night.lcd);
    assert.strictEqual(t.night, undefined);
    assert.strictEqual(t.name, 'E36');
});
test('nightTheme: other themes still dim their whites and greys to 60 %', () => {
    const t = themes.nightTheme(themes.THEMES.classic, 0.6);
    assert.deepStrictEqual(t.fg, [146, 148, 150, 255]);
    assert.deepStrictEqual(t.bar, themes.THEMES.classic.bar);
});
test('E36 palette: every role the layout uses exists by day and by night', () => {
    const roles = ['bg', 'bezel', 'face', 'dial', 'mark', 'needle', 'hub', 'red', 'blue', 'orange', 'well', 'lcd',
        'strip', 'fg', 'dim'];
    roles.forEach((r) => {
        assert.ok(Array.isArray(themes.THEMES.e36[r]), 'day ' + r);
        assert.ok(Array.isArray(themes.nightTheme(themes.THEMES.e36, 0.6)[r]), 'night ' + r);
    });
    assert.strictEqual(themes.THEMES.e36.noGarland, true);
});

// --- shipping ------------------------------------------------------------------------------------------------------
test('shared/hud.js: every shared file it requires at load is in the "shared" list of each app that ships it', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'shared', 'hud.js'), 'utf8');
    const need = [];
    // the top-level requires (a lazy one inside a method, like the nav widget's, loads only where it is used)
    src.replace(/^const [^=]+= require\('\.\/([a-z_0-9]+)(\.js)?'\)/gm, (m, n) => { need.push(n + '.js'); return m; });
    ['cluster_themes', 'idrive_hud_widgets'].forEach((app) => {
        const spec = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'apps', app, 'app.json'), 'utf8'));
        if (spec.shared.indexOf('hud.js') < 0) return;
        const missing = need.filter((f) => spec.shared.indexOf(f) < 0);
        assert.deepStrictEqual(missing, [], app);
    });
});

// --- the generated layout ------------------------------------------------------------------------------------------
const os = require('os');
const LAYOUT = path.join(__dirname, '..', 'apps', 'cluster_themes', 'hud', 'layouts', 'e36.json');
const loadLayout = () => JSON.parse(fs.readFileSync(LAYOUT, 'utf8'));
const overlaps = (w, r) => w.x < r[2] && w.x + w.w > r[0] && w.y < r[3] && w.y + w.h > r[1];
test('layout: valid for the 1920x720 cluster', () => {
    // shared/hud_data.js opens the build's packed hud/ (kit/pack, only there in a build); validation never reads it
    const hd = path.join(__dirname, '..', 'shared', 'hud_data.js');
    require.cache[hd] = { id: hd, filename: hd, loaded: true, exports: { json: () => ({}) } };
    const Layouts = require('../shared/layouts');
    const L = new Layouts(fs.mkdtempSync(path.join(os.tmpdir(), 'e36-')), { warn() {}, info() {}, error() {} }, [1920, 720]);
    L._validate(loadLayout(), 'e36');
});
test('layout: only the backdrop pieces reach under the hood or the belt light', () => {
    const warns = require('../shared/visible').check(loadLayout().widgets, 'panel');
    const bad = warns.filter((s) => !/^(dash|strip_|needles_)/.test(s));     // the needles' layer: see-through
    assert.deepStrictEqual(bad, []);
});
test('layout: the strips cover BMW\'s bottom row but for its drive mode, gear letter and electric range', () => {
    const ws = loadLayout().widgets;
    // the strips from 652, 8 px over the row (a two-line drive mode starts above it); the rest ends there
    ws.filter((w) => overlaps(w, [0, 652, 1920, 720])).forEach((w) => assert.ok(w.type === 'e36dash' && w.stripRow && w.y === 652 && w.y + w.h === 720, w.id + ': only the strip pieces'));
    ws.filter((w) => w.type === 'e36dash' && w.y === 0).forEach((d) => {
        const mine = ws.filter((w) => w.stripRow && w.toggle === d.toggle);
        // BMW's range 107..299, drive mode 1223..1378 (SPORT / INDIVIDUAL on two lines from just above the row) and gear
        // letter 1380..1460, electric range 1696..1842 (car photos Nov 17 2026) stay open from 652; its clock 330..448,
        // messages, the street name (to 1197), outside temperature from 1475 (bench/page.html backdrop, Nov 13; 1541 on
        // Nov 17) are covered
        const all = ws.filter((w) => w.toggle === d.toggle);
        [[1223, 1378], [1380, 1460], [1696, 1842]].forEach(([x0, x1]) =>
            assert.deepStrictEqual(all.filter((w) => overlaps(w, [x0, 652, x1, 720])).map((w) => w.id), [], d.id + ' ' + x0));
        [[107, 299], [330, 448], [700, 1200], [1462, 1674]].forEach(([x0, x1]) => {
            for (let x = x0; x < x1; x++) assert.ok(mine.some((w) => x >= w.x && x < w.x + w.w), d.id + ': x ' + x + ' covered');
        });
        // each hole inside a strip, clear of its rounded corners
        const spans = mine.map((w) => [w.x, w.x + w.w]).sort((a, b) => a[0] - b[0]);
        spans.slice(1).forEach((g, i) => {
            const h0 = spans[i][1], h1 = g[0];
            assert.ok(d.strips.some((s) => h0 >= s[0] + d.stripR && h1 <= s[0] + s[2] - d.stripR), d.id + ': hole ' + h0 + '..' + h1);
        });
        assert.ok(d.strips.every((s) => s[1] === 652 && s[1] + s[3] >= 720), d.id + ': the strips fill the row');
        // the speedometer's flat bottom (4 px under the LCD) level with the rev counter's (its cut); the fuel and
        // coolant gauges' wells (4 px round them) and the ASC / M window clear of the strips' 2 px rim
        const [lcd] = d.windows, [fuel, , tach, cool] = d.dials;
        assert.ok(Math.abs(lcd[1] + lcd[3] + 4 - tach.cut) < 0.2, d.id + ': ' + (lcd[1] + lcd[3] + 4) + ' vs ' + tach.cut);
        [fuel, cool].forEach((g) => assert.ok(g.c[1] + g.r + 4 <= 650.2, d.id + ': small dial bottom ' + (g.c[1] + g.r)));
        assert.ok(d.windows[2][1] + d.windows[2][3] <= 645, d.id + ': ASC window ' + (d.windows[2][1] + d.windows[2][3]));
    });
});
test('layout: the LCD: the range and the trip, under them the clock and the outside temperature', () => {
    ['eu', 'us', 'eum3', 'usm3'].forEach((t) => {
        const lcd = loadLayout().widgets.filter((w) => w.id.indexOf('l' + t + '_') === 0);
        const row = (ids) => ids.map((n) => lcd.find((w) => w.id === 'l' + t + '_' + n));
        const [rng, trip] = row(['range', 'trip']), [time, out] = row(['time', 'out']);
        assert.ok(/range/.test(rng.text) && /trip/.test(trip.text) && /time/.test(time.text) && /ambient/.test(out.text), t);
        assert.ok(time.y > rng.y + rng.h / 2 && out.y === time.y, t + ': the second row under the first');
        // the icon just before the figure's first digit (hud.js text "lead"), room for it and four digits
        assert.ok(rng.lead && rng.lead.icon === 'e36range' && rng.align === 'right', t + ': the range\'s icon leads its figure');
        assert.ok(rng.w >= 4 * 24 + rng.lead.w + rng.lead.gap, t + ': room for the icon and four digits');
        // 80 % of the digits' ink (the e_lcd 8: rows 2 .. 31 of its 34 px cell) and level with it, inside the box
        assert.ok(rng.lead.h === 24 && rng.lead.w === 34 && rng.lead.cy === 17 && rng.lead.cy - rng.lead.h / 2 >= 0 && rng.lead.cy + rng.lead.h / 2 <= rng.h, t + ': ' + JSON.stringify(rng.lead));
    });
});
test('layout: each face has its e36dash, on its own units and redlines (the M3 on the 0 .. 8 rev counter)', () => {
    const ws = loadLayout().widgets.filter((w) => w.type === 'e36dash' && w.y === 0);
    const M3 = 'redline=7000|7500';
    const want = { 'units=metric&!': [false, false], 'units=us&!': [true, false], 'units=metric&': [false, true], 'units=us&': [true, true] };
    Object.keys(want).forEach((k) => {
        const [mph, m3] = want[k], w = ws.find((d) => d.toggle === k + M3);
        assert.ok(w, k + M3);
        assert.strictEqual(!!w.mph, mph, w.id);
        const nd = loadLayout().widgets.find((e) => e.type === 'e36needles' && e.toggle === w.toggle);
        assert.strictEqual(nd.needleColor, m3 ? 'mneedle' : undefined, w.id + ': red needles on the M3 only');
        assert.strictEqual(nd.econ.signal, m3 ? 'oil' : undefined, w.id + ': the oil gauge on the M3, economy on the 328i');
    });
    assert.strictEqual(ws.length, 4);
});
test('layout: the needles lie on top: over the print, the icons and the economy gauge (one layer a face, last)', () => {
    const ws = loadLayout().widgets;
    ws.filter((w) => w.type === 'e36dash' && w.y === 0).forEach((d) => {
        const mine = ws.filter((w) => w.toggle === d.toggle);
        const nd = mine.filter((w) => w.type === 'e36needles');
        assert.strictEqual(nd.length, 1, d.id);
        const top = Math.max.apply(null, mine.filter((w) => w !== nd[0]).map((w) => w.z || 0));
        assert.ok(nd[0].z > top, d.id + ': needles z ' + nd[0].z + ' over ' + top);
        assert.ok(nd[0].y + nd[0].h <= 660, d.id + ': above BMW\'s bottom row');
        // every needle within the layer at its full reach
        nd[0].dials.forEach((n) => {
            const k = Math.max(n.needle[0], n.tail, n.hub);
            assert.ok(n.pivot[0] - k >= nd[0].x && n.pivot[0] + k <= nd[0].x + nd[0].w && n.pivot[1] - k >= nd[0].y, d.id + ' ' + n.signal);
        });
        assert.ok(!d.dials.some((x) => x.needle), d.id + ': the face draws no needles');
        assert.strictEqual(ws.find((w) => w.type === 'e36econ' && w.toggle === d.toggle).econ.map, undefined, d.id + ': nor the economy layer');
    });
});
test('layout: the EU economy gauge is symmetric under the rev counter (window, ticks, needle pivot, print)', () => {
    const ws = loadLayout().widgets, ec = ws.find((w) => w.id === 'econ_eu'), nd = ws.find((w) => w.id === 'needles_eu');
    const ax = ec.tach[0], E = ec.econ;
    assert.ok(near(ax - E.cham[0], E.cham[2] - ax, 0.15) && E.cham[1] === E.cham[3], 'window corners and slopes: ' + E.cham);
    const T = E.ticks;
    T.forEach((t, i) => {
        const m = T[T.length - 1 - i];
        assert.ok(near(t[0] - ax, ax - m[0], 0.15) && near(t[1], m[1], 0.15), 'tick ' + i + ' mirrors ' + (T.length - 1 - i));
    });
    assert.ok(near(nd.econ.pivot[0], ax, 0.15), 'needle pivot on the axis');
    const t = (txt) => ws.find((w) => w.id.indexOf('teu_') === 0 && w.text === txt && w.x > ax - 300 && w.x < ax + 300);
    [['0', '30'], ['10', '20']].forEach(([a, b]) => {
        const A = t(a), B = t(b);
        assert.ok(near(ax - (A.x + A.w / 2), B.x + B.w / 2 - ax, 1) && A.y === B.y, a + ' / ' + b);
    });
});
test('layout: no trip knob or light sensor (redundant on a screen)', () => {
    loadLayout().widgets.filter((w) => w.type === 'e36dash').forEach((w) => {
        assert.strictEqual(w.knob, undefined, w.id);
        assert.strictEqual(w.sensor, undefined, w.id);
    });
});
test('layout: the coolant needle holds the middle from 85 to 105 C, like the car', () => {
    loadLayout().widgets.filter((w) => w.type === 'e36needles').forEach((w) => {
        const m = w.dials.find((d) => d.signal === 'coolant').map;
        [85, 90, 95, 105].forEach((c) => assert.strictEqual(e36.frac(c, m), 0, w.id + ' ' + c + ' C'));
        assert.ok(e36.frac(80, m) < 0 && e36.frac(110, m) > 0, w.id + ': moves outside the band');
        assert.strictEqual(e36.frac(120, m), 35, w.id + ': 120 C still on the red block');
    });
});
test('layout: the M3 faces: 280 km/h, 170 MPH, the oil scale, the /M badge', () => {
    const ws = loadLayout().widgets, by = (id) => ws.find((w) => w.id === id);
    assert.deepStrictEqual(by('needles_eum3').dials[1].map.slice(-1)[0][0], 280);
    assert.deepStrictEqual(by('needles_usm3').dials[1].map.slice(-1)[0][0], 170);
    const oil = (id) => by(id).econ.map.map((e) => e[0]);
    assert.deepStrictEqual(oil('needles_eum3'), [40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150]);
    assert.deepStrictEqual(oil('needles_usm3'), [104, 120, 138, 156, 174, 192, 210, 228, 246, 264, 282, 300]);
    ['eum3', 'usm3'].forEach((t) => {
        const angs = by('needles_' + t).econ.map.map((e) => e[1]);
        angs.slice(1).forEach((a, i) => assert.ok(a < angs[i], t + ': the needle turns one way up the scale'));
        assert.ok(by('i' + t + '_e36m'), t + ': /M badge');
        assert.ok(by('i' + t + '_e36oil'), t + ': oil mark');
    });
    const figs = (t) => ws.filter((w) => w.id.indexOf('f' + t + '_') === 0).map((w) => w.text);
    assert.deepStrictEqual(figs('eum3'), Array.from({ length: 14 }, (_, k) => String(20 * (k + 1))));
    assert.ok(!ws.some((w) => /^t(eu|us)m3_/.test(w.id) && /L\/100km|MPG/.test(w.text)), 'no economy print on the M3');
});
test('toggles: "a&b" holds only while both parts do (hud.js _toggleOn)', () => {
    const hd = path.join(__dirname, '..', 'shared', 'hud_data.js');
    require.cache[hd] = require.cache[hd] || { id: hd, filename: hd, loaded: true, exports: { json: () => ({}) } };
    // hud.js loads the kit's modules, only there in a build: stand-ins (only _toggleOn is called)
    const Module = require('module'), load = Module._load;
    Module._load = function (req, parent) { return /^\.\/kit\//.test(req) ? {} : load.apply(this, arguments); };
    let Hud;
    try { Hud = require('../shared/hud'); } finally { Module._load = load; }
    const h = Object.create(Hud.prototype);
    const set = { units: 'us', redline: 7000, mapUp: false };
    h.setting = (k) => set[k];
    assert.strictEqual(h._toggleOn('units=us&redline=7000|7500'), true);
    assert.strictEqual(h._toggleOn('units=us&!redline=7000|7500'), false);
    assert.strictEqual(h._toggleOn('units=metric&redline=7000|7500'), false);
    set.redline = 6500;
    assert.strictEqual(h._toggleOn('units=us&!redline=7000|7500'), true);
});
test('layout: a rev counter for every Redline step, and one set of its figures shown for each', () => {
    const steps = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'apps', 'cluster_themes', 'app.json'), 'utf8'))
        .settings.find((s) => s.key === 'redline').steps;
    const ws = loadLayout().widgets;
    const dashes = ws.filter((w) => w.type === 'e36dash' && w.y === 0);
    steps.forEach((r) => {
        // per units, exactly one face carries this redline's rev counter
        ['units=metric', 'units=us'].forEach((u) => {
            const has = dashes.filter((w) => w.toggle.indexOf(u + '&') === 0 && w.revs[r]);
            assert.strictEqual(has.length, 1, u + ' ' + r);
            const R = has[0].revs[r], N = ws.find((w) => w.type === 'e36needles' && w.toggle === has[0].toggle).revs[r];
            const top = N.map[N.map.length - 2][0];
            assert.ok(top > r, has[0].id + ' ' + r + ': the scale (' + top + ') runs past the redline');
            assert.ok(R.zones.some((z) => z[5] === 'red' && !z[6]), has[0].id + ' ' + r + ': a solid red zone');
        });
    });
    steps.forEach((r) => {
        const shown = ws.filter((w) => /^trev/.test(w.id) && w.toggle.slice(8).split('|').indexOf(String(r)) >= 0);
        const tops = Array.from(new Set(shown.map((w) => w.id.split('_')[0])));
        assert.strictEqual(tops.length, 1, r + ': one scale of figures, got ' + tops);
        const top = Number(tops[0].slice(4));
        assert.deepStrictEqual(shown.map((w) => w.text), Array.from({ length: top + 1 }, (_, k) => String(k)), String(r));
        const d = ws.find((w) => w.type === 'e36needles' && w.toggle.indexOf('units=metric&') === 0 && w.revs[r]);
        assert.strictEqual(top * 1000, d.revs[r].map.slice(-2)[0][0], r + ': figures match the needle\'s scale');
    });
});

if (failed) {
    console.log('e36: ' + failed + ' failed, ' + passed + ' passed');
    process.exit(1);
}
console.log('e36: all tests passed (' + passed + ')');
