'use strict';
/**
 * The E36 theme's needle and economy maths (Cluster Themes "e36", widget e36dash in hud.js).
 *
 * The E36 328i cluster's needles read off printed scales, some of them non-linear (the economy gauge), so every dial
 * carries a table of [value, fraction of the sweep] pairs traced off the photos (tools/gen_e36.py) and frac() reads
 * the needle's place off it. The car has no fuel-flow signal, so the economy gauge is estimated from the engine's
 * power: flow = power x 250 g/kWh (a petrol engine's brake-specific fuel consumption near its best) / 745 g/L, never
 * under an idle floor of 0.8 L/h.
 *
 * Plain Node 8 (the car's 8.16.2): no optional chaining, no ??, no dependencies.
 */

const BSFC_G_PER_KWH = 250;      // grams of fuel per kWh, a petrol engine near its best
const PETROL_G_PER_L = 745;      // grams in a litre of petrol
const IDLE_LPH = 0.8;            // a 3-litre six at idle, warm
const STILL_KMH = 3;             // under this the car stands: the economy needle reads the thirsty end
const L100_PER_MPG = 235.215;    // L/100km x MPG (US) = 235.215
const L100_MAX = 30;             // the EU face's thirsty end: kept finite so the needle eases in and out of it

function isNum(v) { return typeof v === 'number' && !isNaN(v); }

/** Where the needle sits on a scale, 0..1, from `table` ([[value, frac], ...], values rising or falling); linear
 *  between the points and clamped to the scale's ends. No data (null, NaN) gives null: the caller parks the needle. */
function frac(value, table) {
    if (!isNum(value) || !table || !table.length) return null;
    const rising = table[table.length - 1][0] >= table[0][0];
    const at = (i) => table[rising ? i : table.length - 1 - i];
    if (value <= at(0)[0]) return at(0)[1];
    if (value >= at(table.length - 1)[0]) return at(table.length - 1)[1];
    for (let i = 1; i < table.length; i++) {
        const a = at(i - 1), b = at(i);
        if (value <= b[0]) return b[0] === a[0] ? b[1] : a[1] + (b[1] - a[1]) * (value - a[0]) / (b[0] - a[0]);
    }
    return at(table.length - 1)[1];
}

/** The engine's fuel flow in L/h from its power in kW (estimated, see the header); null without power. */
function flowLph(powerKw) {
    if (!isNum(powerKw)) return null;
    return Math.max(IDLE_LPH, Math.max(0, powerKw) * BSFC_G_PER_KWH / PETROL_G_PER_L);
}

/** What the economy gauge reads: L/100km (us false, at most 30, the face's end) or US MPG (us true). Standing still
 *  (under 3 km/h) it reads the thirsty end: 30 L/100km, 0 MPG. Null without data. */
function economy(speedKmh, flow, us) {
    if (!isNum(speedKmh) || !isNum(flow)) return null;
    if (speedKmh < STILL_KMH) return us ? 0 : L100_MAX;
    const l100 = flow / speedKmh * 100;
    return us ? L100_PER_MPG / l100 : Math.min(L100_MAX, l100);
}

/** Exponential smoothing with time constant `ms`. The first value (and the first after no data or an infinite one) is
 *  taken as is; no data (null) drops the value, so the needle parks on its pin. */
class Smooth {
    constructor(ms) { this.ms = ms; this.v = null; this.at = 0; }

    step(v, now) {
        if (!isNum(v)) { this.v = null; return null; }
        if (!isNum(this.v) || !isFinite(this.v) || !isFinite(v)) {
            this.v = v;
        } else {
            const k = 1 - Math.exp(-Math.max(0, now - this.at) / this.ms);
            this.v += (v - this.v) * k;
        }
        this.at = now;
        return this.v;
    }
}

/** A needle with inertia: a critically damped follower (no overshoot) of its target angle, worked out exactly for the
 *  time since the last step, so any frame rate gives the same motion. The car's samples arrive in steps; this turns
 *  them into one glide with a smooth speed, as a real needle's. `w` (1/s): how quick, a steady sweep trails by 2 / w
 *  seconds. The first target is taken as is. */
class Needle {
    constructor(w) { this.w = w; this.a = null; this.v = 0; this.at = 0; }

    step(target, now) {
        if (this.a === null || !isFinite(this.a)) { this.a = target; this.v = 0; this.at = now; return target; }
        const dt = Math.min(0.25, Math.max(0, now - this.at) / 1000), w = this.w;
        this.at = now;
        const e = this.a - target, k = Math.exp(-w * dt), b = this.v + w * e;
        this.a = target + (e + b * dt) * k;
        this.v = (this.v - w * b * dt) * k;
        return this.a;
    }

    /** Where it will be `ms` from its last step if the target holds (nothing changes). */
    ahead(target, ms) {
        if (this.a === null) return target;
        const dt = ms / 1000, w = this.w, e = this.a - target, b = this.v + w * e;
        return target + (e + b * dt) * Math.exp(-w * dt);
    }

    /** Still: on its target and not moving (to well under the 0.1 degree the layer is sent in). */
    still(target) { return this.a !== null && Math.abs(this.a - target) < 0.02 && Math.abs(this.v) < 0.5; }
}

/* A needle's path in the renderer's own clock (cluster-render layer "time" 1: u_time, seconds since the layer was
 * attached, worked out on every display frame). cluster-render applies uniforms only at its 30 Hz tick, out of step
 * with the display, so a needle sent as an angle moves in uneven steps however often it is sent. Sent as a path, the
 * renderer places it on every frame, and a path landing a tick late is still right.
 * A path: four keys, t ([the first's time in that clock (s), the others' after it]) and a (angles); straight between
 * keys, held after the last, and before the first back along the first leg (at most PATH_BACK s: our idea of the
 * renderer's clock may run a little ahead of it). Further back than that our clock is wrong (the layer attached again
 * unseen, a bench page with another clock): the first key, so the needle at worst steps as sent, never runs off along
 * a line. The shader's ang() reads it the same way. */
const PATH_BACK = 0.25;

/** A path's angle at t (s, our idea of the renderer's clock). */
function pathAt(P, t) {
    const T = P.t, A = P.a, s = t - T[0];
    if (s < -PATH_BACK) return A[0];
    if (s < T[1]) return A[0] + (A[1] - A[0]) * s / Math.max(T[1], 1e-4);
    if (s < T[2]) return A[1] + (A[2] - A[1]) * (s - T[1]) / Math.max(T[2] - T[1], 1e-4);
    return A[2] + (A[3] - A[2]) * Math.min(1, (s - T[2]) / Math.max(T[3] - T[2], 1e-4));
}

/** The next path, made at t (s): the one before (`prev`, the last sent) kept as it was for `keep` s, so whenever within
 *  that the renderer takes the new one up the needle goes on without a jump; then straight on to `to` in `ahead` s
 *  more. Without one before: still at `to` (the first, or after the layer was attached again). */
function nextPath(prev, t, keep, ahead, to) {
    if (!prev) return { t: [t, keep, keep + ahead, keep + ahead], a: [to, to, to, to] };
    const pts = [[0, pathAt(prev, t)]];
    let knot = null;          // the one before's own turn within the kept stretch, if any (the latest)
    for (let i = 1; i < 4; i++) {
        const k = prev.t[0] + prev.t[i] - t;
        if (k > 1e-3 && k < keep - 1e-3) knot = k;
    }
    if (knot !== null) pts.push([knot, pathAt(prev, t + knot)]);
    pts.push([keep, pathAt(prev, t + keep)], [keep + ahead, to]);
    while (pts.length < 4) pts.push(pts[pts.length - 1].slice());
    return { t: [t, pts[1][0], pts[2][0], pts[3][0]], a: pts.map((p) => p[1]) };
}

/** The trip as the E36's LCD shows it: 000.0, in miles (us) or km, rolling over at 1000. */
function tripText(km, us) {
    const v = isNum(km) ? Math.max(0, km) / (us ? 1.609344 : 1) : 0;
    const tenths = Math.floor(v * 10 + 1e-6) % 10000;
    const s = String(Math.floor(tenths / 10));
    return (s.length < 3 ? '000'.slice(s.length) + s : s) + '.' + (tenths % 10);
}

module.exports = { frac, flowLph, economy, Smooth, Needle, PATH_BACK, pathAt, nextPath, tripText };
