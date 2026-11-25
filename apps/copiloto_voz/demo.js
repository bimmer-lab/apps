'use strict';
/**
 * demo.js - Copiloto de voz: a simulated trip in which every notice happens, to check in the car that each one is said.
 * The car's signals are not touched: a second Engine (engine.js, the same rules) is fed a scripted trip on a clock of
 * its own, and what it gives goes through the real queue (announcer.js: by priority, one at a time, waiting during a
 * call) and the real speaker. Only parked (the car not drive-ready). The result of each notice is kept for the page's
 * checklist: 'pending' | 'ok' | the error.
 *
 * The trip: a service due before the start, BMW's tyre warning on; the engine starts (welcome,
 * service, tyres); two minutes on, 1 °C outside (ice) and 3,000 rpm with the oil at 40 °C (cold engine); the
 * range down to 45 km (fuel); two hours of driving (break); the oil at 85 °C (warm engine); a window left open and the
 * switch-off (windows, end of the trip). Ten notices. Node 8.
 */
const { Engine, EVENTS } = require('./engine');

class Demo {
    constructor(opts) {
        this.add = opts.add;            // the queue's add(event)
        this.log = opts.log || (() => {});
        this.results = {};              // event -> 'pending' | 'ok' | error text
        this.running = false;
        this.startedAt = 0;
    }

    /** Run the trip, then queue what it gave in the order of priority. -> the events given. */
    run() {
        let t = 0;
        const given = [];
        const e = new Engine({ now: () => t, say: (ev) => { given.push(ev); this.results[ev] = 'pending'; }, enabled: () => true, log: (m) => this.log('demo: ' + m) });
        this.results = {};
        this.running = true;
        this.startedAt = Date.now();
        e.condition(5);
        e.service([{ name: 'aceite', km: 1500, days: 200, colour: 'GREEN' }]);
        e.engine(800, 20);
        e.tyreWarning(true);
        e.fuel(30, 400);
        e.condition(7); e.condition(8); e.condition(10);
        e.tick();
        t += 2 * 60000; e.tick();
        e.outside(1);
        e.engine(3000, 40);
        e.fuel(6, 45);
        for (let i = 0; i < 2 * 60 * 12 + 2; i++) { t += 5000; e.tick(); }      // two hours of driving, a tick every 5 s
        e.engine(2000, 85);
        e.openings(['ventanilla del conductor']);
        e.condition(12); e.condition(5);
        // all queued together, in the order of priority (the queue would say the first one given at once)
        EVENTS.filter((ev) => given.indexOf(ev) >= 0).forEach((ev) => this.add(ev));
        const missing = EVENTS.filter((ev) => given.indexOf(ev) < 0);
        if (missing.length) this.log('demo: not given: ' + missing.join(' '));
        return given;
    }

    /** What a notice gave when it was said (the speaker's error, or null). */
    said(event, error) {
        if (!this.running || this.results[event] === undefined) return;
        this.results[event] = error ? String(error) : 'ok';
        if (EVENTS.every((ev) => this.results[ev] !== undefined && this.results[ev] !== 'pending')) {
            this.running = false;
            const bad = EVENTS.filter((ev) => this.results[ev] !== 'ok');
            this.log('demo: done in ' + Math.round((Date.now() - this.startedAt) / 1000) + ' s, ' + (EVENTS.length - bad.length) + ' of ' + EVENTS.length + ' said'
                + (bad.length ? '; failed: ' + bad.map((ev) => ev + ' (' + this.results[ev] + ')').join(', ') : ''));
        }
    }

    /** Notices still pending after 5 minutes never got said (dropped from the queue, or the speaker hung). */
    expire(now) {
        if (!this.running || (now || Date.now()) - this.startedAt < 5 * 60000) return;
        EVENTS.forEach((ev) => { if (this.results[ev] === 'pending') this.said(ev, 'no sonó en 5 minutos (se quedó en la cola o el altavoz no respondió)'); });
    }

    summary() {
        const r = this.results, done = EVENTS.filter((ev) => r[ev] === 'ok').length, bad = EVENTS.filter((ev) => r[ev] && r[ev] !== 'ok' && r[ev] !== 'pending').length;
        return { running: this.running, ok: done, failed: bad, total: EVENTS.length, results: Object.assign({}, r) };
    }
}

module.exports = { Demo };
