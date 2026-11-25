'use strict';
/**
 * announcer.js - Copiloto de voz: the queue of notices. One voice at a time, never two over each other:
 *   - the waiting notice with the highest priority (engine.js PRIORITY, 1 first) goes next;
 *   - nothing is said during a phone call: the notices wait for it to end;
 *   - a notice that has waited KEEP_MS is dropped (no longer to the point), except those in KEEP (fuel, tyres);
 *   - the same notice waiting twice is kept once.
 * speak(event) -> Promise (the speaker, done when the phrase has played or failed); inCall() -> bool.
 * pump() is called on every change and every few seconds (main.js). Node 8.
 */
const KEEP_MS = 2 * 60000;

class Announcer {
    constructor(opts) {
        this.speak = opts.speak;
        this.inCall = opts.inCall || (() => false);
        this.priority = opts.priority;
        this.keep = opts.keep || [];
        this.now = opts.now || Date.now;
        this.log = opts.log || (() => {});
        this.waiting = [];        // [{ event, at }]
        this.speaking = null;     // the event being said
        this.dropped = 0;
        this.gapMs = opts.gapMs === undefined ? 1500 : opts.gapMs;
    }

    add(event) {
        if (this.waiting.some((w) => w.event === event)) return;
        this.waiting.push({ event: event, at: this.now() });
        this.pump();
    }

    pump() {
        const t = this.now();
        this.waiting = this.waiting.filter((w) => {
            const stale = this.keep.indexOf(w.event) < 0 && t - w.at > KEEP_MS;
            if (stale) { this.dropped++; this.log('dropped ' + w.event + ': waited over ' + KEEP_MS / 60000 + ' min'); }
            return !stale;
        });
        if (this.speaking || !this.waiting.length || this.inCall()) return;
        this.waiting.sort((a, b) => (this.priority[a.event] - this.priority[b.event]) || (a.at - b.at));
        const next = this.waiting.shift();
        this.speaking = next.event;
        Promise.resolve().then(() => this.speak(next.event)).catch(() => {}).then(() => {
            // a pause between two phrases, so one does not run into the next
            const next2 = () => { this.speaking = null; this.pump(); };
            if (this.gapMs > 0) setTimeout(next2, this.gapMs); else next2();
        });
    }

    state() { return { speaking: this.speaking, waiting: this.waiting.map((w) => w.event), call: !!this.inCall() }; }
}

module.exports = { Announcer, KEEP_MS };
