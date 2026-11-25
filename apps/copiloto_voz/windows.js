'use strict';
/**
 * windows.js - Copiloto de voz: which windows and the roof are open, learned from the car itself.
 *
 * No public app reads the windows, so the fields of DoorsWindowsRoofMirrors.CentralLockingSystemAndPanelStatus that say
 * "open" are not known. The driver teaches them (Diagnóstico, «Aprender ventanillas»):
 *   1. everything closed: press -> the signal as it is now is kept as "closed"
 *   2. for each of STEPS (the driver's window, the passenger's): open it and press -> the fields
 *      that changed since "closed" are that opening's (a press with nothing changed skips it)
 * Learned: { fields: { <field>: { name, closed } } }, kept in the data folder. An opening is open while one of its
 * fields is not at its "closed" value (so a position in % works as well as an open / closed flag). Until something is
 * learned, the field names are guessed (decode.js openingsOf). Plain logic, no I/O. Node 8.
 */
// the owner's car: two doors, two windows, no roof
const STEPS = [
    ['conductor', 'ventanilla del conductor', 'Abra la ventanilla del conductor'],
    ['acompanante', 'ventanilla del acompañante', 'Abra la ventanilla del acompañante (y cierre la del conductor)'],
];
// the doors' own switches change when a door opens: never taken for a window
const IGNORE = /^statusDoorSwitch|DoorSwitch/i;

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

class Windows {
    constructor(learned) {
        this.fields = (learned && learned.fields) || {};
        this.base = null;       // the signal with everything closed (while learning)
        this.step = -1;         // -1: not learning; 0: waiting for "everything closed"; 1..STEPS.length: that opening
        this.last = null;       // the last value of the signal
    }

    learned() { return Object.keys(this.fields).length > 0; }

    /** The signal's new value. */
    feed(v) { if (v && typeof v === 'object') this.last = v; }

    /** Open now: the learned openings, or null when nothing is learned (the caller guesses). */
    open() {
        if (!this.learned() || !this.last) return this.learned() ? [] : null;
        const out = [];
        Object.keys(this.fields).forEach((k) => {
            const f = this.fields[k];
            if (k in this.last && !same(this.last[k], f.closed) && out.indexOf(f.name) < 0) out.push(f.name);
        });
        return out;
    }

    /** What the driver should do now (the page shows it), or null when not learning. */
    prompt() {
        if (this.step < 0) return null;
        if (this.step === 0) return 'Aprender ventanillas: cierre las dos ventanillas y pulse «Aprender ventanillas»';
        return 'Aprender ventanillas (' + this.step + '/' + STEPS.length + '): ' + STEPS[this.step - 1][2] +
            ' y pulse otra vez (sin cambiar nada, se salta)';
    }

    /**
     * The button: starts learning, or takes the step. -> { done, msg, save } (save: what to keep in the data folder,
     * when it changed).
     */
    press() {
        if (!this.last) return { msg: 'Todavía no llega la señal de puertas y ventanillas: dé el contacto y espere unos segundos' };
        if (this.step < 0) {
            this.step = 0;
            this.fresh = {};
            return { msg: this.prompt() };
        }
        if (this.step === 0) {
            this.base = JSON.parse(JSON.stringify(this.last));
            this.step = 1;
            return { msg: 'Guardado como «todo cerrado». ' + this.prompt() };
        }
        const s = STEPS[this.step - 1];
        // a field already learned for an earlier opening (left open) is not taken again
        const changed = Object.keys(this.last).filter((k) => !IGNORE.test(k) && !(k in this.fresh) && !same(this.last[k], this.base[k]));
        let msg;
        this.lastFields = [];
        if (changed.length) {
            changed.forEach((k) => { this.fresh[k] = { name: s[1], closed: this.base[k] }; });
            msg = s[1] + ': aprendida';
            this.lastFields = changed;
        } else msg = s[1] + ': sin cambios, saltada';
        this.step++;
        if (this.step > STEPS.length) {
            const n = Object.keys(this.fresh).length;
            this.step = -1;
            if (!n) return { done: true, msg: msg + '. No se aprendió nada: se sigue adivinando por el nombre de los campos' };
            this.fields = this.fresh;
            return { done: true, save: { fields: this.fields, at: Date.now() }, msg: msg + '. Listo: ' + this.summary() };
        }
        return { msg: msg + '. ' + this.prompt() };
    }

    /** The openings learned, for the page. */
    summary() {
        const names = [];
        Object.keys(this.fields).forEach((k) => { if (names.indexOf(this.fields[k].name) < 0) names.push(this.fields[k].name); });
        return names.length ? names.length + ' aprendida(s): ' + names.join(', ') : 'nada aprendido';
    }
}

module.exports = { Windows, STEPS };
