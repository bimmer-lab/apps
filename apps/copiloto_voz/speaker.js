'use strict';
/**
 * speaker.js - Copiloto de voz: plays one recorded phrase on the car's speakers, the way RideTube's service does it
 * (JustAnotherPU/ridetube, native/jap-ridetube-d/ytd.js, car-tested on an X5 G05): the Audio Manager connects the
 * apps' media source to the main sink (CommandControl.connect 1046 -> 1024), BMW's MediaPlayer (instance
 * OapGenericMedia) plays the file (playFile), and the connection is dropped again (STOP, disconnect). While it
 * speaks, the music pauses; the source that was on before (radio, CarPlay...) is connected again afterwards if the
 * Audio Manager did not bring it back by itself.
 *
 * The media player runs as its own user: it must be able to open the file. The phrase is tried where the app was
 * installed, then from a copy in the app's data folder made readable for it; what worked is remembered.
 * Node 8. Never throws out of say(): what went wrong is in status().
 */
const fs = require('fs');
const path = require('path');

const CC = 'org.genivi.am.commandinterface.CommandControl';
const MP = 'de.bmw.infotainment.entertainment.mediaplayer.MediaPlayer';
const SOURCE_ID = 1046, SINK_ID = 1024;     // the apps' media source and the main sink (as RideTube)
const LANG = 7695972;                       // the languageCode RideTube passes to playFile
const MAX_PHRASE_MS = 20000;                // a phrase that has not ended by then is stopped
const TMP_DIR = '/tmp/copiloto_voz';
const ROUTE_MS = 700;      // after the connection is up: the amplifier's ramp, so the first syllable is heard
// each folder on the way to f: its mode and owner, to see in the log which one stops BMW's media engine
function modes(f) {
    const out = [];
    for (let d = f; d && d !== path.dirname(d); d = path.dirname(d)) {
        try { const st = fs.statSync(d); out.unshift(path.basename(d) + ' ' + (st.mode & 0o777).toString(8) + ' ' + st.uid); } catch (e) { out.unshift(path.basename(d) + ' ?'); }
    }
    return out.join(' / ');
}
const EXT = ['.m4a', '.mp3', '.aac', '.wav'];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const errText = (e) => String(e && e.message || e).slice(0, 160);

class Speaker {
    constructor(opts) {
        this.signals = opts.signals;
        this.log = opts.log;
        this.dirs = opts.dirs;              // folders the phrases are looked for in, in order (or a function giving them:
                                            // the language and voice chosen in the settings)
        this.dataDir = opts.dataDir;
        this.cc = null; this.mp = null;
        this.ready = null;                  // the init promise
        this.queue = Promise.resolve();
        this.busy = false;
        this.lastError = null;
        this.lastSaid = null;
        this.state = null;                  // the player's state, as it says it
        this.time = null;                   // {elapsed, total}
        this.ended = null;                  // resolves the phrase playing now
        this.lastConnId = null;            // our last connection (listed a moment after its disconnect)
        this.useCopy = null;                // 'copy' | 'tmp': the place that worked where the installed file did not
        this.modesLogged = false;
    }

    /** The file for a phrase (bienvenida -> bienvenida.m4a ...), or null. */
    file(name) {
        // looked up once: a found file is kept, a missing one looked for again after a minute
        const c = this._files || (this._files = {});
        const hit = c[name];
        if (hit && (hit.path || Date.now() - hit.at < 60000)) return hit.path;
        const p = this._find(name);
        c[name] = { path: p, at: Date.now() };
        return p;
    }

    _find(name) {
        const dirs = typeof this.dirs === 'function' ? this.dirs() : this.dirs;
        for (let i = 0; i < dirs.length; i++) {
            for (let j = 0; j < EXT.length; j++) {
                const f = path.join(dirs[i], name + EXT[j]);
                if (fs.existsSync(f)) return f;
            }
        }
        return null;
    }

    /** Which phrases have a file: { name: true | false }. */
    found(names) {
        const out = {};
        names.forEach((n) => { out[n] = !!this.file(n); });
        return out;
    }

    init() {
        if (!this.ready) {
            this.ready = (async () => {
                if (!this.signals.hasNative()) throw new Error('sin conexión con el coche (SOME/IP)');
                this.cc = await this.signals.proxy(CC, '1');
                this.mp = await this.signals.proxy(MP, 'OapGenericMedia');
                const watch = (name, fn) => this.signals.on(this.mp, name, fn, { warn: (m) => this.log.warn(m) }).catch((e) => this.log.warn('subscribe ' + name + ': ' + errText(e)));
                await watch('playerState', (v) => {
                    this.state = typeof v === 'object' && v ? (v.playerState || JSON.stringify(v)) : v;
                    if (this.ended && /STOP/.test(String(this.state)) && this.started && Date.now() - this.playAt > 700) this.ended('player stopped');
                    if (/PLAY/.test(String(this.state))) this.started = true;
                });
                await watch('currentItemTime', (v) => {
                    this.time = v;
                    // the end of THIS phrase: the player playing it (PLAY seen since playFile) for a moment at least; the
                    // last item's time, still sent right after playFile, is not taken for it
                    if (this.ended && this.started && Date.now() - this.playAt > 700 && v && v.total > 0 && v.elapsed >= v.total - 150) this.ended('end of the phrase');
                });
                await watch('playbackErrorEvent', (v) => {
                    const ev = v && (v.event !== undefined ? v.event : v);
                    if (String(ev) !== 'NO_ERROR' && String(ev) !== '0' && this.ended) this.ended('error ' + ev);
                });
                this.log.info('speaker: CommandControl and MediaPlayer ready');
            })();
            this.ready.catch((e) => { this.lastError = 'audio: ' + errText(e); this.ready = null; });
        }
        return this.ready;
    }

    /** Say a phrase (queued: one at a time). Resolves when it is done or failed; never rejects. */
    say(name) {
        const p = this.queue.then(() => this._say(name)).catch((e) => {
            this.lastError = name + ': ' + errText(e);
            this.log.warn('speaker: ' + this.lastError);
        });
        this.queue = p;
        return p;
    }

    async _say(name) {
        const file = this.file(name);
        if (!file) throw new Error('falta la grabación ' + name);
        await this.init();
        this.busy = true;
        let connId = null, before = null, onShared = false;
        try {
            const list = await this.signals.get(this.cc, 'listOfMainConnections').catch(() => null);
            const conns = Array.isArray(list) ? list : [];
            // the apps' media source already on the speakers (RideTube open): the phrase is said on that connection, which
            // is left as it was. A video playing is stopped first: RideTube holds it as paused where it was (its
            // service treats a STOP it did not ask for mid-file so), and the driver resumes it with Play
            let shared = conns.filter((c) => c && c.sourceID === SOURCE_ID)[0] || null;
            // our own connection of the last phrase, still listed for a moment after its disconnect: not RideTube's
            if (shared && this.lastConnId !== null && shared.mainConnectionID === this.lastConnId) {
                shared = (await this._gone(this.lastConnId)) ? null : shared;
                if (!shared) conns.splice(0, conns.length, ...((await this.signals.get(this.cc, 'listOfMainConnections').catch(() => null)) || []));
            }
            if (shared) {
                onShared = true;
                const st = await this.signals.get(this.mp, 'playerState').catch(() => this.state);
                const s0 = String(st && typeof st === 'object' ? st.playerState : st);
                if (/PLAY/.test(s0) && !/PAUSE|STOP/.test(s0)) {
                    await this.signals.call(this.mp, 'sendButtonEvent', { button: 'STOP', action: 'PRESS' }, 3000).catch(() => {});
                    await sleep(500);
                    this.log.info('speaker: RideTube was playing: stopped for the phrase (it stays paused)');
                    this.interrupted = true;
                } else this.log.info('speaker: the media source is already connected (RideTube open, not playing): said on it');
            } else {
                before = conns.filter((c) => c && c.sinkID === SINK_ID)[0] || null;
                const r = await this.signals.call(this.cc, 'connect', { sourceID: SOURCE_ID, sinkID: SINK_ID }, 5000);
                if (!r || (r.amError !== 'E_OK' && r.amError !== 'E_ALREADY_EXISTS')) throw new Error('el coche no conectó el audio (' + (r && r.amError) + ')');
                connId = r.mainConnectionID;
                await this._routed(connId);
            }
            // BMW's media engine opens the file as its own user: every folder on the way must let it through
            // (RideTube's cache is 0711 under /var/opt). Installed, then a copy in the data folder, then one in /tmp
            const tries = this.useCopy ? [this.useCopy] : ['installed', 'copy', 'tmp'];
            let reason = null;
            const why = [];
            for (let i = 0; i < tries.length; i++) {
                const f = tries[i] === 'copy' ? this._copy(file) : tries[i] === 'tmp' ? this._copy(file, TMP_DIR) : file;
                if (tries[i] === 'installed') this._openUp(path.dirname(f));
                reason = await this._play(f);
                if (!/^error/.test(reason)) { if (tries[i] !== 'installed') this.useCopy = tries[i]; break; }
                why.push({ installed: 'instalado', copy: 'copia', tmp: '/tmp' }[tries[i]] + ': ' + reason.replace(/^error /, ''));
                this.log.warn('speaker: ' + f + ' (' + tries[i] + '): ' + reason);
                if (!this.modesLogged) this.log.info('speaker: folders: ' + modes(f));
                if (i === tries.length - 1) this.modesLogged = true;
            }
            this._audioState('after ' + name).catch(() => {});
            if (/^error/.test(reason)) throw new Error('el reproductor no pudo con ' + path.basename(file) + ' (' + why.join('; ') + ')');
            this.lastSaid = { name: name, at: Date.now() };
            this.lastError = null;
            this.log.info('speaker: said ' + name + ' (' + reason + (this.time && this.time.total ? ', ' + this.time.elapsed + ' of ' + this.time.total + ' ms' : '') + ', player ' + this.state + ')');
        } finally {
            if (onShared) await this.signals.call(this.mp, 'sendButtonEvent', { button: 'STOP', action: 'PRESS' }, 3000).catch(() => {});
            if (connId !== null) {
                await this.signals.call(this.mp, 'sendButtonEvent', { button: 'STOP', action: 'PRESS' }, 3000).catch(() => {});
                const d = await this.signals.call(this.cc, 'disconnect', { mainConnectionID: connId }, 5000).catch((e) => errText(e));
                this.log.info('speaker: disconnect ' + JSON.stringify(d));
                this.lastConnId = connId;
                await this._gone(connId);
                if (before) await this._restore(before);
            }
            this.busy = false;
        }
    }

    // the connection actually on the speakers before the phrase: connect answers at once, but the Audio Manager ramps
    // the amplifier up after; a phrase of 2 s played meanwhile is not heard. Waits for CS_CONNECTED (at most 3 s), then
    // a little more for the ramp; the connection's fields are logged once, to see what this car sends
    async _routed(id) {
        const t0 = Date.now();
        let c = null;
        for (let i = 0; i < 15; i++) {
            const list = await this.signals.get(this.cc, 'listOfMainConnections').catch(() => null);
            c = Array.isArray(list) ? list.filter((x) => x && x.mainConnectionID === id)[0] : null;
            const st = c && c.connectionState;
            if (st === 'CS_CONNECTED' || st === 2) break;
            await sleep(200);
        }
        if (!this.connLogged) { this.connLogged = true; this.log.info('speaker: connection ' + JSON.stringify(c)); }
        await sleep(ROUTE_MS);
        this.log.info('speaker: routed in ' + (Date.now() - t0) + ' ms (' + (c ? c.connectionState : 'not listed') + ')');
    }

    // what the Audio Manager says while a phrase plays: the connections (with their state: connected or suspended),
    // the main sink's volume and mute, and our source; logged to find why a phrase played without sound
    async _audioState(when) {
        const conns = await this.signals.get(this.cc, 'listOfMainConnections').catch((e) => 'error ' + errText(e));
        const sinks = await this.signals.call(this.cc, 'getListMainSinks', {}, 3000).catch((e) => ({ e: errText(e) }));
        const srcs = await this.signals.call(this.cc, 'getListMainSources', {}, 3000).catch((e) => ({ e: errText(e) }));
        const sink = ((sinks && sinks.listMainSinks) || []).filter((k) => k && k.sinkID === SINK_ID)[0];
        const src = ((srcs && srcs.listMainSources) || []).filter((k) => k && k.sourceID === SOURCE_ID)[0];
        this.log.info('audio ' + when + ': connections ' + JSON.stringify(conns).slice(0, 400) + ' | sink ' + SINK_ID + ' ' +
            (sink ? JSON.stringify(sink).slice(0, 200) : JSON.stringify(sinks).slice(0, 120)) + ' | source ' + SOURCE_ID + ' ' +
            (src ? JSON.stringify(src).slice(0, 200) : JSON.stringify(srcs).slice(0, 120)) + ' | player ' + JSON.stringify(this.state));
    }

    // wait (at most 3 s) for a connection to leave the Audio Manager's list; -> true when it did
    async _gone(id) {
        for (let i = 0; i < 15; i++) {
            const list = await this.signals.get(this.cc, 'listOfMainConnections').catch(() => null);
            if (!Array.isArray(list) || !list.some((c) => c && c.mainConnectionID === id)) return true;
            await sleep(200);
        }
        this.log.warn('speaker: connection ' + id + ' still listed 3 s after its disconnect');
        return false;
    }

    // playFile and wait for its end; -> why it ended ('error ...' when it did not play)
    async _play(file) {
        this.started = false;
        this.playAt = Date.now();
        const done = new Promise((resolve) => { this.ended = resolve; });
        const r = await this.signals.call(this.mp, 'playFile', { path: file, languageCode: LANG }, 5000);
        if (!r || r.EntError !== 'OK') { this.ended = null; return 'error playFile ' + (r && r.EntError); }
        setTimeout(() => { this._audioState('1 s into ' + path.basename(file)).catch(() => {}); }, 1000);
        const why = await Promise.race([done, sleep(MAX_PHRASE_MS).then(() => 'time limit')]);
        this.ended = null;
        await sleep(250);     // the last syllable out of the amplifier before the source goes
        return why;
    }

    // the app's own folders on the way to dir (from oap_<uid> down) opened for passing through (o+x), as RideTube's
    _openUp(dir) {
        const uid = process.getuid ? process.getuid() : -1;
        for (let d = dir; d && d !== path.dirname(d) && !/^\/var\/opt\/public\/oap$/.test(d); d = path.dirname(d)) {
            try { const st = fs.statSync(d); if (st.uid === uid && !(st.mode & 0o001)) fs.chmodSync(d, (st.mode & 0o777) | 0o011); } catch (e) { /* not ours */ }
            if (/\/oap_\d+$/.test(d)) break;
        }
    }

    // the phrase copied into the data folder (or dir), readable by the media player's user
    _copy(file, dirIn) {
        if (dirIn) {
            try { fs.mkdirSync(dirIn); } catch (e) { /* there */ }
            try { fs.chmodSync(dirIn, 0o755); } catch (e) { /* not ours */ }
            const o = path.join(dirIn, path.basename(path.dirname(file)) + '_' + path.basename(file));
            try { if (!fs.existsSync(o) || fs.statSync(o).size !== fs.statSync(file).size) fs.writeFileSync(o, fs.readFileSync(file)); fs.chmodSync(o, 0o644); } catch (e) { this.log.warn('speaker: copy to ' + dirIn + ': ' + errText(e)); }
            return o;
        }
        this._openUp(this.dataDir);
        const dir = path.join(this.dataDir, 'voces');
        try { fs.mkdirSync(dir); } catch (e) { /* there */ }
        try { fs.chmodSync(this.dataDir, 0o711); fs.chmodSync(dir, 0o711); } catch (e) { /* not ours to change */ }
        const out = path.join(dir, path.basename(path.dirname(file)) + '_' + path.basename(file));   // one per voice
        try {
            if (!fs.existsSync(out) || fs.statSync(out).size !== fs.statSync(file).size) fs.writeFileSync(out, fs.readFileSync(file));
            fs.chmodSync(out, 0o644);
        } catch (e) { this.log.warn('speaker: copy ' + path.basename(file) + ': ' + errText(e)); }
        return out;
    }

    // the source that was on before (radio, CarPlay ...) back, if the Audio Manager did not bring it back itself
    async _restore(before) {
        await sleep(800);
        const list = await this.signals.get(this.cc, 'listOfMainConnections').catch(() => null);
        if (!Array.isArray(list)) return;
        if (list.some((c) => c && c.sourceID === before.sourceID && c.sinkID === SINK_ID)) return;
        const r = await this.signals.call(this.cc, 'connect', { sourceID: before.sourceID, sinkID: SINK_ID }, 5000).catch((e) => errText(e));
        this.log.info('speaker: source ' + before.sourceID + ' connected again: ' + JSON.stringify(r));
    }

    status() {
        if (this.busy) return 'hablando';
        if (this.lastError) return this.lastError;
        if (this.lastSaid) return 'última: ' + this.lastSaid.name;
        return this.cc ? 'listo' : 'sin probar todavía';
    }
}

module.exports = { Speaker, SOURCE_ID, SINK_ID };
