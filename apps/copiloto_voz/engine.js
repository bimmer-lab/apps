'use strict';
/**
 * engine.js - Copiloto de voz: when something happens in the car, which notice to give. Plain logic, no I/O, so it
 * can be tested off the car (tools/test_copiloto_voz.js) with a clock of its own. Node 8 (the car's 8.16.2).
 *
 * Fed with what the car sends (main.js decodes it):
 *   condition(n)      VehicleCondition.statusConditionVehicle as a number (kit/runtime/car.js conditionOf):
 *                     1-3 parked / nobody in the car, 5 living, 7 ignition on, 8 drive readiness starting,
 *                     10 drive-ready (the engine started), 12 drive readiness ending
 *   fuel(litres, km)  KombiIF statusRangeTankStatus
 *   outside(c)        EnvironmentalInformation.ExteriorTemperature, deg C
 *   engine(rpm, oilC) CDS statusEngineSpeed2 / statusEngineTemperature
 *   tyreWarning(on)   BMW's own tyre pressure Check Control message is on
 *   service(items)    [{ name, km, days, colour }] the car's service requirements (CBS)
 *   openings(list)    windows / sunroof open now (names)
 * and tick() every few seconds. It calls say(event, info) with one of EVENTS; announcer.js queues them by PRIORITY.
 */

// in order of priority: the first one is said first when two come together (announcer.js)
const EVENTS = ['combustible', 'neumaticos', 'hielo', 'motor_frio', 'bienvenida', 'mantenimiento',
    'ventanillas', 'fin_viaje', 'descanso', 'motor_caliente'];
const PRIORITY = {};
EVENTS.forEach((e, i) => { PRIORITY[e] = i + 1; });
// never dropped from the queue however long they wait (the others go after announcer.js KEEP_MS: no longer to the point)
const KEEP = ['combustible', 'neumaticos'];

const DEFAULTS = {
    fuelKm: 50,             // range at or under which "combustible" is said, once until a refuel
    fuelRearmKm: 30,        // ... a refuel: the range this much above fuelKm again,
    refuelLitres: 8,        // ... or this many litres more than the lowest seen
    iceC: 3,                // outside at or under this while driving: "hielo", once a trip
    iceAfterMs: 60000,      // ... not in the first minute (the sensor warms up in a garage)
    coldOilC: 80,           // oil under this is cold: "motor_frio" over coldRpm, "motor_caliente" when it gets there
    coldRpm: 2500,
    serviceKm: 2000,        // a service item due within this many km or serviceDays: "mantenimiento"
    serviceDays: 60,
    serviceEveryMs: 7 * 24 * 3600000,   // ... at most once a week
    breakMs: 2 * 3600000,   // driving this long: "descanso", again after as long
    stopResetsBreakMs: 15 * 60000,      // a stop this long starts the count again
    minTripMs: 60000,       // a trip shorter than this: no "fin_viaje" (moving the car)
    restartMs: 3 * 60000,   // the engine started again this soon after the end of a trip: no new welcome
};

const isNum = (v) => typeof v === 'number' && isFinite(v);
const DRIVING = 10;

class Engine {
    constructor(opts) {
        opts = opts || {};
        this.now = opts.now || Date.now;
        this.say = opts.say || (() => {});
        this.enabled = opts.enabled || (() => true);       // enabled(event) -> bool (the settings)
        this.cfg = Object.assign({}, DEFAULTS, opts.cfg || {});
        this.log = opts.log || (() => {});
        // freshStart() -> true while the head unit has only just started (main.js: os.uptime()): the app then came up
        // after the engine was started, and the welcome is still given, late, instead of not at all
        this.freshStart = opts.freshStart || (() => false);
        this.cond = null;
        this.trip = null;           // this trip: { start, ice, cold, warm, tyres, oilWasCold }
        this.tripEndAt = null;
        this.endCheckUntil = 0;
        this.stoppedAt = null;
        this.driveMs = 0;           // driving time counted for "descanso"
        this.breakSaid = 0;
        this.lastTick = null;
        this.fuelArmed = true;
        this.litres = null; this.range = null; this.minLitres = null;
        this.outsideC = null;
        this.rpm = null; this.oilC = null;
        this.tyres = false;
        this.items = [];            // the service items
        this.lastServiceAt = null;  // when "mantenimiento" was last said (kept across starts)
        this.open = [];             // windows / sunroof open now
        this.history = [];          // [{event, at, why}] the last few, for the page
        this.lastAt = {};           // event -> when it was last given
    }

    _emit(event, why) {
        if (EVENTS.indexOf(event) < 0) return false;
        if (!this.enabled(event)) { this.log('event ' + event + ' (' + why + '): apagado en los ajustes'); return false; }
        const at = this.now();
        this.history.unshift({ event: event, at: at, why: why });
        if (this.history.length > 8) this.history.length = 8;
        this.lastAt[event] = at;
        this.log('event ' + event + ': ' + why);
        this.say(event, { why: why, litres: this.litres, range: this.range, outside: this.outsideC });
        return true;
    }

    condition(n) {
        if (!isNum(n)) return;
        const was = this.cond, t = this.now();
        if (n === was) return;
        this.cond = n;
        if (n === DRIVING && was !== DRIVING) this._tripStart(was, t);
        if (was === DRIVING && n !== DRIVING) this._tripEnd(t);
    }

    _tripStart(was, t) {
        const restart = this.tripEndAt !== null && t - this.tripEndAt < this.cfg.restartMs;
        this.trip = { start: t, ice: false, cold: false, warm: false, tyres: false,
            oilWasCold: isNum(this.oilC) ? this.oilC < this.cfg.coldOilC : null };
        if (this.stoppedAt !== null && t - this.stoppedAt >= this.cfg.stopResetsBreakMs) { this.driveMs = 0; this.breakSaid = 0; }
        this.stoppedAt = null;
        // the welcome when the engine starts; not when the app itself starts on the road (was unknown), not on a
        // restart right after a trip (a stall, moving the car)
        if (was !== null && !restart) this._emit('bienvenida', 'motor arrancado');
        else if (was === null && this.freshStart()) this._emit('bienvenida', 'la app arrancó con el motor ya en marcha (unidad recién encendida)');
        this._serviceCheck();
        this._tyres();
    }

    _tripEnd(t) {
        const trip = this.trip;
        this.trip = null;
        this.stoppedAt = t;
        if (!trip || t - trip.start < this.cfg.minTripMs) return;
        this.tripEndAt = t;
        if (this.open.length) this._emit('ventanillas', 'abierto al apagar: ' + this.open.join(', '));
        this._emit('fin_viaje', 'apagado tras ' + Math.round((t - trip.start) / 60000) + ' min de viaje');
        this.endCheckUntil = t + 15000;   // the windows' state can come a little after the switch-off
    }

    fuel(litres, range) {
        if (isNum(litres)) {
            if (this.minLitres === null || litres < this.minLitres) this.minLitres = litres;
            if (litres - this.minLitres >= this.cfg.refuelLitres) { this.minLitres = litres; this.fuelArmed = true; }
            this.litres = litres;
        }
        if (isNum(range)) {
            this.range = range;
            if (range > this.cfg.fuelKm + this.cfg.fuelRearmKm) this.fuelArmed = true;
            if (this.fuelArmed && range <= this.cfg.fuelKm && this.cond === DRIVING) {
                this.fuelArmed = false;
                this._emit('combustible', 'autonomía ' + range + ' km');
            }
        }
    }

    outside(c) {
        if (!isNum(c)) return;
        this.outsideC = c;
        this._ice();
    }

    _ice() {
        const tr = this.trip;
        if (!tr || tr.ice || !isNum(this.outsideC) || this.outsideC > this.cfg.iceC || this.now() - tr.start < this.cfg.iceAfterMs) return;
        tr.ice = true;
        this._emit('hielo', 'fuera ' + this.outsideC + ' °C');
    }

    engine(rpm, oilC) {
        if (isNum(rpm)) this.rpm = rpm;
        if (isNum(oilC)) this.oilC = oilC;
        const tr = this.trip;
        if (!tr || !isNum(this.oilC)) return;
        if (tr.oilWasCold === null) tr.oilWasCold = this.oilC < this.cfg.coldOilC;
        if (!tr.cold && this.oilC < this.cfg.coldOilC && isNum(this.rpm) && this.rpm > this.cfg.coldRpm) {
            tr.cold = true;
            this._emit('motor_frio', Math.round(this.rpm) + ' rpm con el aceite a ' + Math.round(this.oilC) + ' °C');
        }
        // warm: only on a trip that started cold, once
        if (!tr.warm && tr.oilWasCold && this.oilC >= this.cfg.coldOilC) {
            tr.warm = true;
            this._emit('motor_caliente', 'aceite a ' + Math.round(this.oilC) + ' °C');
        }
    }

    tyreWarning(on) {
        this.tyres = !!on;
        this._tyres();
    }

    _tyres() {
        const tr = this.trip;
        if (!tr || tr.tyres || !this.tyres) return;
        tr.tyres = true;
        this._emit('neumaticos', 'aviso de presión de BMW');
    }

    service(items) {
        this.items = Array.isArray(items) ? items : [];
    }

    _serviceCheck() {
        if (this.lastServiceAt !== null && this.now() - this.lastServiceAt < this.cfg.serviceEveryMs) return;
        const due = this.items.filter((i) => i && ((isNum(i.km) && i.km <= this.cfg.serviceKm) || (isNum(i.days) && i.days <= this.cfg.serviceDays)
            || i.colour === 'RED' || i.colour === 'YELLOW'));
        if (!due.length) return;
        const what = due.map((i) => i.name + (isNum(i.km) ? ' ' + i.km + ' km' : '') + (isNum(i.days) ? ' ' + i.days + ' días' : '')).join(', ');
        if (this._emit('mantenimiento', what)) this.lastServiceAt = this.now();
    }

    openings(list) {
        const was = this.open.length;
        this.open = Array.isArray(list) ? list.slice() : [];
        // reported just after a switch-off that found everything shut
        if (!was && this.open.length && this.now() < this.endCheckUntil && this.cond !== DRIVING && !this._recent('ventanillas', 60000)) {
            this._emit('ventanillas', 'abierto al apagar: ' + this.open.join(', '));
        }
    }

    _recent(event, ms) { return this.lastAt[event] !== undefined && this.now() - this.lastAt[event] < ms; }

    /** Every few seconds: the driving time for "descanso", the ice check. */
    tick() {
        const t = this.now();
        if (this.lastTick !== null && this.cond === DRIVING) this.driveMs += t - this.lastTick;
        this.lastTick = t;
        if (this.cond === DRIVING && this.driveMs >= this.cfg.breakMs * (this.breakSaid + 1)) {
            this.breakSaid++;
            this._emit('descanso', Math.round(this.driveMs / 60000) + ' min conduciendo');
        }
        this._ice();
    }

    /** What is kept across starts of the head unit (a refuel happens while it is off; the weekly service notice). */
    memo() { return { minLitres: this.minLitres, fuelArmed: this.fuelArmed, lastServiceAt: this.lastServiceAt }; }
    restore(m) {
        if (!m || typeof m !== 'object') return;
        if (isNum(m.minLitres)) this.minLitres = m.minLitres;
        if (typeof m.fuelArmed === 'boolean') this.fuelArmed = m.fuelArmed;
        if (isNum(m.lastServiceAt)) this.lastServiceAt = m.lastServiceAt;
    }

    /** What the page shows. */
    state() {
        return { cond: this.cond, litres: this.litres, range: this.range, outside: this.outsideC, rpm: this.rpm, oil: this.oilC,
            tyres: this.tyres, open: this.open.slice(), service: this.items.length, fuelArmed: this.fuelArmed,
            driveMin: Math.round(this.driveMs / 60000), history: this.history.slice(), lastAt: Object.assign({}, this.lastAt) };
    }
}

module.exports = { Engine, EVENTS, PRIORITY, KEEP, DEFAULTS };
