'use strict';
/**
 * Copiloto de voz: el coche habla. Frases grabadas (voces/<aviso>.m4a), de usted, cuando pasa algo; por prioridad
 * (engine.js EVENTS), de una en una, nunca durante una llamada (announcer.js):
 *
 *    1 combustible     autonomía de 50 km o menos (ajuste), una vez hasta repostar
 *    2 neumaticos      el aviso de presión de BMW (Check Control), una vez por viaje
 *    3 hielo           3 °C o menos fuera, conduciendo, una vez por viaje
 *    4 motor_frio      aceite por debajo de 80 °C y más de 2.500 rpm, una vez por viaje
 *    5 bienvenida      al arrancar el motor (bienvenida_manana / _tarde / _noche, o bienvenida)
 *    6 mantenimiento   al arrancar, si faltan 2.000 km o 60 días para algo (aceite, frenos, inspección), una vez por semana
 *    7 ventanillas     al apagar, una ventanilla o el techo abierto
 *    8 fin_viaje       al apagar después de conducir
 *    9 descanso        cada 2 horas conduciendo
 *   10 motor_caliente  el aceite llega a 80 °C (en un viaje que empezó en frío), siempre el último
 *
 * Las frases, en español con la voz Elvira, las instala la herramienta del Store copiloto-voz-voces en /var/opt
 * (voices.js; las genera scripts/generar_voces.py a partir de su frases.json).
 *
 * Arranca con el coche (app.json "autostart"): todo corre en background(), con o sin la pantalla abierta. Solo lee
 * del coche; lo único que hace es reproducir sonido. background() vuelve enseguida y se suscribe a las señales
 * después, así la pantalla se abre sin esperar al coche.
 * La pantalla (start), tres páginas: Avisos (cada aviso con su icono, si está activo y cuándo sonó), Diagnóstico (cada
 * señal del coche, la voz y el resultado de la demo, con ✓ / ✗) y Registro (las últimas líneas del registro).
 * Demo: un viaje simulado en el que pasan los 10 avisos (demo.js), por la cola y el altavoz de verdad, solo aparcado.
 * Node 8 (el del coche, 8.16.2).
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const mgu = require('./kit/mgu');
const Car = require('./kit/car');
const G = require('./kit/gauges');
const { Engine, EVENTS, PRIORITY, KEEP } = require('./engine');
const { Announcer } = require('./announcer');
const { Speaker } = require('./speaker');
const { phraseFor } = require('./phrases');
const D = require('./decode');
const V = require('./voices');
const { Demo } = require('./demo');
const { Windows } = require('./windows');

const PHRASES = ['bienvenida', 'bienvenida_manana', 'bienvenida_tarde', 'bienvenida_noche'].concat(EVENTS.filter((e) => e !== 'bienvenida'));
const TITLES = { combustible: 'Combustible', neumaticos: 'Neumáticos', hielo: 'Posible hielo', motor_frio: 'Motor frío',
    bienvenida: 'Bienvenida', mantenimiento: 'Mantenimiento', ventanillas: 'Ventanillas',
    fin_viaje: 'Fin del viaje', descanso: 'Pausa', motor_caliente: 'Motor caliente' };
const WHEN = { combustible: 'autonomía baja', neumaticos: 'aviso de presión BMW', hielo: '3 °C o menos', motor_frio: 'aceite < 80 °C y > 2.500 rpm',
    bienvenida: 'al arrancar', mantenimiento: '2.000 km o 60 días', ventanillas: 'abiertas al apagar',
    fin_viaje: 'al apagar', descanso: 'cada 2 horas', motor_caliente: 'aceite a 80 °C' };
const COND_TEXT = { 1: 'aparcado', 2: 'aparcado', 3: 'aparcado', 5: 'sin contacto', 7: 'contacto', 8: 'arrancando',
    10: 'en marcha', 12: 'apagando' };

mgu.run({
    async background(bg) {
        const log = bg.log, settings = bg.settings;
        // the log's last lines, for the Registro page (what fails, word for word)
        const ring = [];
        ['info', 'warn', 'error'].forEach((level) => {
            const orig = log[level].bind(log);
            log[level] = function () {
                const msg = Array.prototype.slice.call(arguments).map((x) => (x && x.stack) || String(x)).join(' ');
                ring.push({ at: Date.now(), level: level, msg: msg });
                if (ring.length > 300) ring.shift();
                return orig.apply(null, arguments);
            };
        });
        // one voice: Spanish, Elvira (the folders looked up once, not at every phrase or redraw)
        const lang = () => 'es';
        const voice = () => V.DEFAULT.es;
        const voiceDirs = V.dirsFor(V.root(path.join(__dirname, 'voces')), 'es', V.DEFAULT.es);
        const speaker = new Speaker({ signals: bg.signals, log, dataDir: bg.dataDir, dirs: voiceDirs });
        // the checklist: each signal the app uses, when it last came and what it said (or the error subscribing)
        const SIGNALS = [['estado', 'Estado del coche'], ['rpm', 'Revoluciones'], ['aceite', 'Temperatura del aceite'], ['combustible', 'Combustible y autonomía'],
            ['exterior', 'Temperatura exterior'], ['llamadas', 'Llamadas'], ['checkcontrol', 'Avisos de BMW (Check Control)'], ['mantenimiento', 'Mantenimiento (CBS)'],
            ['ventanillas', 'Puertas y ventanillas']];
        const sig = {};
        SIGNALS.forEach((x) => { sig[x[0]] = { label: x[1], at: 0, value: '', error: '' }; });
        const mark = (k, value) => { const x = sig[k]; if (x) { x.at = Date.now(); x.value = String(value).slice(0, 100); x.error = ''; } };
        const lastResult = {};            // event -> 'ok' | error, the last time it was said
        let demo = null;
        const memoFile = path.join(bg.dataDir, 'memo.json');
        const listeners = [];
        const changed = () => listeners.forEach((fn) => { try { fn(); } catch (e) { /* the page */ } });
        let call = null, ccActive = [], ccName = {};

        const announcer = new Announcer({
            priority: PRIORITY, keep: KEEP, log: (m) => log.info('voice: ' + m),
            inCall: () => call !== null && call > 0,
            speak: (ev) => {
                changed();
                return speaker.say(phraseFor(ev, settings, speaker, Date.now())).then(() => {
                    const err = speaker.lastError && speaker.lastError.replace(/^[a-z_]+: /, '');     // without the phrase's name
                    lastResult[ev] = err || 'ok';
                    // the car's audio not ready yet (just after the head unit started): the same notice once or twice more
                    if (err && !/falta la grabación/.test(err) && Date.now() - appStart < 4 * 60000 && (retries[ev] || 0) < 3 && !(demo && demo.running)) {
                        retries[ev] = (retries[ev] || 0) + 1;
                        log.info('voice: ' + ev + ' again in 6 s (' + retries[ev] + '/3): ' + err);
                        setTimeout(() => announcer.add(ev), 6000);
                    } else if (!err) delete retries[ev];
                    if (demo) demo.said(ev, err);
                    changed();
                });
            },
        });
        const appStart = Date.now(), retries = {};
        const engine = new Engine({
            freshStart: () => os.uptime() < 6 * 60,
            log: (m) => log.info(m),
            enabled: (ev) => settings.get('on_' + ev) !== false,
            cfg: { fuelKm: settings.get('fuelKm') || 50 },
            say: (ev, info) => {
                announcer.add(ev);
                changed();
            },
        });
        try { engine.restore(JSON.parse(fs.readFileSync(memoFile, 'utf8'))); } catch (e) { /* first start */ }
        let memoSaved = '';
        const saveMemo = () => {
            const s = JSON.stringify(engine.memo());
            if (s === memoSaved) return;
            memoSaved = s;
            try { fs.writeFileSync(memoFile, s); } catch (e) { log.warn('memo: ' + e.message); }
        };
        settings.onChange((key) => { if (key === 'fuelKm') engine.cfg.fuelKm = settings.get('fuelKm') || 50; });

        const recordings = () => {
            const missing = PHRASES.filter((p) => !speaker.file(p));
            log.info('recordings (' + lang() + ', ' + voice() + '): ' + (PHRASES.length - missing.length) + ' of ' + PHRASES.length + (missing.length ? ', missing ' + missing.join(' ') : ''));
        };
        recordings();
        demo = new Demo({ add: (ev) => announcer.add(ev), log: (m) => log.info(m) });
        const winFile = path.join(bg.dataDir, 'ventanillas.json');
        let winSaved = null;
        try { winSaved = JSON.parse(fs.readFileSync(winFile, 'utf8')); } catch (e) { /* nothing learned yet */ }
        const windows = new Windows(winSaved);
        // the two windows (WindowsRoofMaster: statusWindowsRoofMasterDRD / PSD, each { positionWindowRegulator...,
        // statusPositionWindowRegulator..., statusDriveWindowRegulator..., statusErrorWindowRegulator... }) merged with
        // the doors' signal into one object, which windows.js learns from
        let doorsLast = {};
        const winLast = {};
        const winView = () => Object.assign({}, doorsLast, winLast);
        const winText = () => ['DRD', 'PSD'].map((d) => {
            const p = winLast['W' + d + '.positionWindowRegulator' + d], st = winLast['W' + d + '.statusPositionWindowRegulator' + d];
            return (d === 'DRD' ? 'cond.' : 'acomp.') + ' ' + (p === undefined ? '?' : p) + (st !== undefined ? ' (' + st + ')' : '');
        }).join(' · ');
        const winFeed = (d, v) => {
            if (!v || typeof v !== 'object') return;
            Object.keys(v).forEach((k) => { winLast['W' + d + '.' + k] = v[k]; });
            if (!winLogged[d]) { winLogged[d] = true; log.info('window ' + d + ': ' + JSON.stringify(v)); }
            windows.feed(winView());
            const o = windows.open() || D.openingsOf(winView());
            mark('ventanillas', (o.length ? 'abierto: ' + o.join(', ') : 'cerrado') + ' · ' + winText() + (windows.learned() ? '' : ' (sin aprender)'));
            engine.openings(o);
            changed();
        };
        const winLogged = {};
        log.info('windows: ' + (windows.learned() ? windows.summary() : 'not learned yet: guessed by field name'));
        // nothing learned: where else could this car say its windows? The unit's SOME/IP catalog (5 MB, read once, only
        // then) is searched for names about windows, and what it finds is logged with the service around it
        const searchCatalog = () => {
            const f = '/etc/oap/someip/someip-interfaces.json';
            let text;
            try { text = fs.readFileSync(f, 'utf8'); } catch (e) { log.warn('catalog: ' + e.message); return; }
            const seen = {}, out = [];
            const re = /"([A-Za-z0-9_.]*(?:[Ww]indow|Fenster|FENSTER|Scheibe|SCHEIBE|FensterHeber|WindowLifter|FH_|Fh[A-Z])[A-Za-z0-9_.]*)"/g;
            let m;
            while ((m = re.exec(text)) && out.length < 40) {
                if (seen[m[1]]) continue;
                seen[m[1]] = true;
                const before = text.slice(Math.max(0, m.index - 4000), m.index);
                const svc = (before.match(/"(de\.bmw\.[A-Za-z0-9_.]+)"/g) || []).pop() || '?';
                out.push(m[1] + ' @ ' + svc.replace(/"/g, ''));
            }
            log.info('catalog: ' + text.length + ' bytes, ' + out.length + ' names about windows' + (out.length ? ': ' + out.join(' | ') : ''));
            // the windows' fields (statusPositionWindowRegulator..., statusDriveWindowRegulator...): which struct holds
            // them, and which attribute of which service carries that struct (the catalog walked as JSON)
            try {
                const db = JSON.parse(text);
                const hits = [], holders = {};
                const label = (o, k) => (o && typeof o === 'object' && (o.name || o.id || o.fqn)) ? String(o.name || o.id || o.fqn) : String(k);
                const walk = (o, chain, k) => {
                    if (!o || typeof o !== 'object' || hits.length > 40) return;
                    const here = chain.concat([label(o, k)]);
                    if (typeof o.name === 'string' && /WindowRegulator/.test(o.name) && !/^de\.bmw/.test(o.name)) {
                        hits.push(here.slice(-5).join(' > '));
                        holders[here[here.length - 3] || '?'] = 1;
                        holders[here[here.length - 2] || '?'] = 1;
                    }
                    Object.keys(o).forEach((key) => walk(o[key], here, key));
                };
                walk(db, [], 'catalog');
                // which attribute (of which service) has one of the windows' wrapper types: its type names it exactly
                const wrappers = Object.keys(holders).filter((h) => /Window.*Wrapper$/.test(h));
                const refs = [];
                const walk2 = (o, chain, k) => {
                    if (!o || typeof o !== 'object' || refs.length > 12) return;
                    const here = chain.concat([label(o, k)]);
                    Object.keys(o).forEach((key) => {
                        const v = o[key];
                        if (typeof v === 'string' && key !== 'name' && wrappers.some((w) => v === w || v.slice(-(w.length + 1)) === '.' + w)) {
                            refs.push(here.slice(-4).join(' > ') + ' [' + key + '=' + v.split('.').pop() + ']');
                        } else walk2(v, here, key);
                    });
                };
                walk2(db, [], 'catalog');
                const ids = Object.keys(db.interfaces || {}).filter((k) => /WindowsRoofMaster/.test(k));
                log.info('WindowsRoofMaster in the catalog as: ' + (ids.join(', ') || 'none') + '; instances ' +
                    ids.map((k) => JSON.stringify((db.instanceIDs || {})[k] || null)).join(', '));
                log.info('windows attributes: ' + (refs.length ? refs.join(' || ') : 'none found for ' + wrappers.join(', ')));
            } catch (e) { log.warn('catalog walk: ' + e.message); }
            // this app's own config of DoorsWindowsRoofMirrors (made by MainApp at install): its attributes
            try {
                const own = fs.readFileSync(path.join(__dirname, '..', '..', 'someipInterfaces', 'de_bmw_body_DoorsWindowsRoofMirrors.json'), 'utf8');
                const names = {}, re2 = /"([A-Za-z][A-Za-z0-9_]{3,})"/g;
                let k;
                while ((k = re2.exec(own))) if (/window|fenster|regulator|panel|status/i.test(k[1])) names[k[1]] = 1;
                log.info('own DoorsWindowsRoofMirrors (' + own.length + ' bytes): ' + Object.keys(names).slice(0, 60).join(' '));
            } catch (e) { log.warn('own DoorsWindowsRoofMirrors config: ' + e.message); }
            changed();
        };
        // each press reads the signal afresh (a car may not send it again when only a window moves) and logs every field
        let doorProxy = null, winProxy = null;
        const learnWindows = async () => {
            try {
                if (!doorProxy) doorProxy = await bg.signals.proxy('de.bmw.body.DoorsWindowsRoofMirrors', '1').catch(() => null);
                const v = doorProxy ? await bg.signals.get(doorProxy, 'CentralLockingSystemAndPanelStatus', 3000).catch(() => null) : null;
                if (v && typeof v === 'object') doorsLast = v;
                if (!winProxy) winProxy = await bg.signals.proxy('de.bmw.body.doorsandwindows.WindowsRoofMaster', '1').catch(() => null);
                for (const d of ['DRD', 'PSD']) {
                    const w = winProxy ? await bg.signals.get(winProxy, 'statusWindowsRoofMaster' + d, 3000).catch(() => null) : null;
                    if (w && typeof w === 'object') Object.keys(w).forEach((k) => { winLast['W' + d + '.' + k] = w[k]; });
                }
                windows.feed(winView());
                log.info('windows read: ' + winText());
            } catch (e) { log.warn('windows read: ' + (e && e.message || e)); }
            const first = windows.step < 0;
            const r = windows.press();
            if (first || (r.done && !r.save)) setTimeout(searchCatalog, 100);
            log.info('windows learn: ' + r.msg + (windows.lastFields && windows.lastFields.length ? ' [' + windows.lastFields.map((k) => k + '=' + JSON.stringify(windows.last[k])).join(', ') + ']' : ''));
            if (r.save) {
                try { fs.writeFileSync(winFile, JSON.stringify(r.save)); } catch (e) { log.warn('windows: ' + e.message); }
            }
            if (windows.last) {
                const o = windows.open() || D.openingsOf(windows.last);
                mark('ventanillas', (o.length ? 'abierto: ' + o.join(', ') : 'todo cerrado') + (windows.learned() ? '' : ' (sin aprender)'));
                engine.openings(o);
            }
            changed();
            return r.msg;
        };
        const startDemo = () => {
            if (engine.state().cond === 10) { log.warn('demo: only parked (the car is drive-ready)'); return 'Solo con el coche parado'; }
            if (demo.running) return 'La demo ya está en marcha';
            log.info('demo: a simulated trip, ' + lang() + ' / ' + voice());
            demo.run();
            changed();
            return null;
        };

        // what the car says: each signal on its own, so one the car lacks leaves the others working
        const W = (iface, keys, handlers, opts) => bg.signals.watch(iface, '1', handlers, log, opts).then((r) => {
            if (r.error) { log.warn(iface + ': ' + r.error); keys.forEach((k) => { sig[k].error = r.error; }); }
            return r;
        });
        // subscribed after background() has returned: the page opens at once, the signals come in as they are set up
        const subscribe = async () => {
        await W('de.bmw.infrastructure.VehicleCondition', ['estado'], { VehicleCondition: (v) => {
            const n = Car.conditionOf(v && v.statusConditionVehicle);
            mark('estado', n === undefined ? JSON.stringify(v && v.statusConditionVehicle) : (COND_TEXT[n] || n));
            engine.condition(n);
            changed();
        } });
        await W('de.bmw.infotainment.vehicle.CDS', ['rpm', 'aceite'], {
            statusEngineSpeed2: (v) => { const r = D.rpmOf(v); if (r !== null) mark('rpm', Math.round(r) + ' rpm'); engine.engine(r, null); },
            statusEngineTemperature: (v) => { const o = D.oilOf(v); if (o !== null) mark('aceite', Math.round(o) + ' °C'); engine.engine(null, o); },
        });
        await W('de.bmw.infotainment.KombiIF', ['combustible'], { statusRangeTankStatus: (v) => {
            const f = v && v.fluidLevelFuelTank, r = v && v.range;
            mark('combustible', f + ' L · ' + r + ' km');
            engine.fuel(typeof f === 'number' && f >= 0 && f < 255 ? f : null, typeof r === 'number' && r >= 0 && r < 4095 ? r : null);
            saveMemo();
            changed();
        } });
        await W('de.bmw.infrastructure.EnvironmentalInformation', ['exterior'], { ExteriorTemperature: (v) => {
            const t = v && v.temperatureExterior;
            if (typeof t === 'number' && t !== 255) { mark('exterior', (t * 0.5 - 40).toFixed(1) + ' °C'); engine.outside(t * 0.5 - 40); }
        } });
        await W('de.bmw.infotainment.connectivity.telephony.ConnectivityAggregator', ['llamadas'], { aggregatedCallState: (v) => {
            const was = call;
            call = D.callOf(v);
            mark('llamadas', call === null ? JSON.stringify(v) : ['sin llamada', 'sonando', 'en llamada', 'marcando', 'en espera'][call]);
            if (was > 0 && !(call > 0)) announcer.pump();       // the call ended: what waited is said now
            changed();
        } });
        // BMW's tyre pressure message (Check Control): the active ids, named by the cluster's list
        const tyreCheck = () => engine.tyreWarning(ccActive.some((id) => D.isTyreCc(ccName[id])));
        await W('de.bmw.infotainment.checkcontrol.CheckControlMaster', ['checkcontrol'], { basicList: (v) => {
            ccActive = D.activeCc(v);
            mark('checkcontrol', ccActive.length ? ccActive.length + ' activo(s): ' + ccActive.map((id) => ccName[id] || id).join(', ') : 'ninguno activo');
            if (ccActive.length) log.info('check control active: ' + ccActive.map((id) => id + (ccName[id] ? ' ' + ccName[id] : '')).join(' | '));
            tyreCheck();
        } });
        await W('de.bmw.infotainment.KombiInterface_2', ['mantenimiento'], {
            ccList: (v) => { D.ccNames(v).forEach((p) => { ccName[p[0]] = p[1]; }); tyreCheck(); },
            newCCMessage: (v) => { D.ccNames(v).forEach((p) => { ccName[p[0]] = p[1]; }); tyreCheck(); },
            cbsDataServiceList: (v) => {
                const items = D.serviceOf(v);
                mark('mantenimiento', items.length + ' elemento(s)' + (items.length ? ': ' + items.map((i) => i.name + ' ' + (i.km !== null ? i.km + ' km' : '') ).join(', ') : ''));
                log.info('service: ' + items.map((i) => i.name + ' ' + i.km + ' km ' + i.days + ' d ' + i.colour).join(' | '));
                engine.service(items);
            },
        }, { pollMs: 10 * 60000 });
        // windows and the roof: learned from the car (windows.js, «Aprender ventanillas»), else guessed by field name;
        // the fields this car sends are logged once
        let doorKeys = false;
        await W('de.bmw.body.DoorsWindowsRoofMirrors', ['ventanillas'], { CentralLockingSystemAndPanelStatus: (v) => {
            if (!doorKeys && v && typeof v === 'object') { doorKeys = true; log.info('doors/windows fields: ' + JSON.stringify(v).slice(0, 900)); }
            doorsLast = v && typeof v === 'object' ? v : {};
            windows.feed(winView());
            const o = windows.open() || D.openingsOf(winView());
            mark('ventanillas', (o.length ? 'abierto: ' + o.join(', ') : 'todo cerrado') + (windows.learned() ? '' : ' (sin aprender)'));
            engine.openings(o);
            changed();
        } });
        await W('de.bmw.body.doorsandwindows.WindowsRoofMaster', ['ventanillas'], {
            statusWindowsRoofMasterDRD: (v) => winFeed('DRD', v),
            statusWindowsRoofMasterPSD: (v) => winFeed('PSD', v),
        });
        // the audio side ready before the first phrase
        speaker.init().catch(() => {});
        };
        setTimeout(() => { subscribe().catch((e) => log.error('subscribe', e)); }, 0);
        setInterval(() => { engine.tick(); announcer.pump(); demo.expire(); saveMemo(); }, 5000);
        return { engine, speaker, announcer, demo, startDemo, windows, learnWindows, sig, SIGNALS, lastResult, ring, lang, voice, inCall: () => call > 0, onChange: (fn) => listeners.push(fn) };
    },

    async start(ctx) {
        const bgs = ctx.background;
        const W = 1600, H = 520;
        // BMW: dark, the brand blue, the M colours in a thin stripe
        const C = { bg: '#0a0d12', tile: '#141a23', tileOff: '#0f131a', line: '#232c3a', text: '#f2f5f9', dim: '#8a95a6', faint: '#4a5466',
            blue: '#1c69d4', light: '#4fa3ff', mLight: '#0066b1', mDark: '#003d78', mRed: '#e22718', green: '#2fd27a', amber: '#ffb000', red: '#ff5a4f', ice: '#8fd3ff' };
        if (!bgs) {
            const cv = ctx.canvas(W, H);
            cv.clear(C.bg);
            cv.text('El copiloto no ha podido arrancar: mire el registro de la app (copiloto_voz.log).', 40, 80, 30, C.red, { literal: true });
            ctx.ui.image('MAIN_GFX', cv);
            return;
        }
        const { engine, speaker, announcer } = bgs;
        let testIdx = 0, note = '';
        const ago = (t) => { const m = Math.round((Date.now() - t) / 60000); return m < 1 ? 'ahora' : m < 120 ? 'hace ' + m + ' min' : m < 2880 ? 'hace ' + Math.round(m / 60) + ' h' : 'hace ' + Math.round(m / 1440) + ' días'; };
        const fit = (cv, s, size, w, o) => { s = String(s); o = Object.assign({ literal: true }, o || {}); if (cv.textWidth(s, size, o) <= w) return s; while (s.length > 1 && cv.textWidth(s + '…', size, o) > w) s = s.slice(0, -1); return s + '…'; };

        // an icon per notice, drawn with lines: (cv, cx, cy, colour)
        const ICONS = {
            combustible: (cv, x, y, c) => { cv.roundRect(x - 14, y - 16, 20, 32, 3, c); cv.fillRect(x - 10, y - 11, 12, 9, C.tile); cv.line(x + 6, y - 8, x + 14, y - 2, 3, c); cv.line(x + 14, y - 2, x + 14, y + 10, 3, c); },
            neumaticos: (cv, x, y, c) => { cv.arc(x, y, 11, 17, 0, 360, c); cv.fillRect(x - 2, y - 7, 4, 9, c); cv.circle(x, y + 6, 2, c); },
            hielo: (cv, x, y, c) => { for (let a = 0; a < 180; a += 60) { const r = a * Math.PI / 180; cv.line(x - 16 * Math.cos(r), y - 16 * Math.sin(r), x + 16 * Math.cos(r), y + 16 * Math.sin(r), 3, c); } },
            motor_frio: (cv, x, y, c) => { cv.line(x, y - 16, x, y + 6, 6, c); cv.circle(x, y + 10, 7, c); cv.line(x + 8, y - 10, x + 14, y - 10, 2, c); cv.line(x + 8, y - 3, x + 14, y - 3, 2, c); },
            bienvenida: (cv, x, y, c) => { cv.arc(x - 7, y, 6, 10, 0, 360, c); cv.line(x + 3, y, x + 18, y, 4, c); cv.line(x + 13, y, x + 13, y + 7, 3, c); cv.line(x + 18, y, x + 18, y + 7, 3, c); },
            mantenimiento: (cv, x, y, c) => { cv.line(x - 12, y + 12, x + 6, y - 6, 5, c); cv.arc(x + 9, y - 9, 5, 10, 200, 520, c); },
            ventanillas: (cv, x, y, c) => { cv.quad([[x - 16, y + 12], [x - 16, y - 2], [x + 2, y - 14], [x + 16, y + 12]], c); cv.quad([[x + 2, y - 14], [x + 16, y - 14], [x + 16, y + 12], [x - 16, y + 12]], c); cv.quad([[x - 11, y + 7], [x - 11, y], [x + 1, y - 9], [x + 11, y + 7]], C.tile); cv.quad([[x + 1, y - 9], [x + 11, y - 9], [x + 11, y + 7], [x - 11, y + 7]], C.tile); cv.line(x - 11, y + 2, x + 11, y + 2, 2, c); },
            fin_viaje: (cv, x, y, c) => { cv.line(x - 12, y - 16, x - 12, y + 16, 3, c); for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) cv.fillRect(x - 10 + i * 8 + (j ? 0 : 0), y - 15 + j * 7, 8, 7, (i + j) % 2 ? C.tile : c); cv.strokeRoundRect(x - 10, y - 15, 24, 14, 0, 2, c); },
            descanso: (cv, x, y, c) => { cv.roundRect(x - 14, y - 8, 22, 20, 4, c); cv.arc(x + 9, y + 1, 4, 8, 270, 450, c); cv.line(x - 8, y - 16, x - 8, y - 11, 2, c); cv.line(x - 2, y - 16, x - 2, y - 11, 2, c); },
            motor_caliente: (cv, x, y, c) => { cv.line(x, y - 16, x, y + 6, 6, c); cv.circle(x, y + 10, 7, c); cv.line(x - 10, y - 12, x - 6, y - 6, 2, c); cv.line(x + 10, y - 12, x + 6, y - 6, 2, c); },
        };
        const TONE = { combustible: C.amber, neumaticos: C.amber, hielo: C.ice, motor_frio: C.ice, bienvenida: C.light,
            mantenimiento: C.light, ventanillas: C.light, fin_viaje: C.light, descanso: C.light, motor_caliente: C.red };

        const CY = 48;     // the chips' row, under the kit's header (its tabs: y < 38)
        const chip = (cv, x, label, ok, warn) => {
            const w = cv.textWidth(label, 19, { literal: true }) + 40;
            cv.roundRect(x - w, CY, w, 32, 16, C.tile);
            cv.circle(x - w + 17, CY + 16, 6, ok ? C.green : warn ? C.amber : C.red);
            cv.text(label, x - w + 30, CY + 23, 19, C.text, { literal: true });
            return x - w - 10;
        };

        const PAGES = ['Avisos', 'Diagnóstico', 'Registro'];
        let page = 0;
        const dot = (cv, x, y, state) => cv.circle(x, y, 8, state === 'ok' ? C.green : state === 'wait' ? C.faint : state === 'pending' ? C.blue : C.red);

        const header = (cv, s, a, found, have) => {
            // the kit's header: the app's name and the page tabs (on the car, in the HMI's title strip, with its Settings tab)
            G.frame(cv, ctx.theme(), PAGES, page, []);
            cv.fillRect(24, CY + 3, 8, 26, C.mLight); cv.fillRect(34, CY + 3, 8, 26, C.mDark); cv.fillRect(44, CY + 3, 8, 26, C.mRed);
            cv.text('Copiloto de voz', 66, CY + 25, 24, C.text, { font: 'bold', literal: true });
            let x = 1576;
            x = chip(cv, x, a.call ? 'En llamada: espera' : a.speaking ? 'Hablando: ' + TITLES[a.speaking] : 'Voz: ' + (speaker.lastError ? 'error' : have + '/' + PHRASES.length + ' frases'),
                !speaker.lastError && have === PHRASES.length && !a.call, !speaker.lastError);
            x = chip(cv, x, V.LANG_NAME[bgs.lang()] + ' · ' + V.voiceName(bgs.voice()), have > 0, false);
            chip(cv, x, 'Coche: ' + (s.cond === null ? 'esperando' : COND_TEXT[s.cond] || s.cond), s.cond !== null, true);
        };

        const showAvisos = (cv, s, a, found) => {
            // the notices: 4 x 3 tiles, in their order of priority
            const TW = 380, TH = 116, GX = 24, GY = 92, GAP = 8;
            EVENTS.forEach((ev, i) => {
                const col = i % 4, row = Math.floor(i / 4), tx = GX + col * (TW + GAP), ty = GY + row * (TH + GAP);
                const on = ctx.settings.get('on_' + ev) !== false, speaking = a.speaking === ev, waiting = a.waiting.indexOf(ev) >= 0;
                cv.roundRect(tx, ty, TW, TH, 12, on ? C.tile : C.tileOff);
                if (speaking) cv.strokeRoundRect(tx, ty, TW, TH, 12, 3, C.blue);
                cv.fillRect(tx, ty + 14, 4, TH - 28, on ? TONE[ev] : C.faint);
                ICONS[ev](cv, tx + 46, ty + 50, on ? TONE[ev] : C.faint);
                cv.text(TITLES[ev], tx + 84, ty + 40, 25, on ? C.text : C.faint, { font: 'bold', literal: true });
                cv.text(String(i + 1), tx + TW - 30, ty + 34, 18, C.faint, { literal: true });
                const last = s.lastAt[ev];
                const line = !on ? 'Desactivado' : speaking ? 'Hablando…' : waiting ? 'En cola' : last ? 'Último: ' + ago(last) : WHEN[ev];
                cv.text(fit(cv, line, 19, TW - 100), tx + 84, ty + 78, 19, speaking ? C.light : C.dim, { literal: true });
                if (!found[ev === 'bienvenida' ? 'bienvenida' : ev] && !(ev === 'bienvenida' && (found.bienvenida_manana || found.bienvenida_tarde || found.bienvenida_noche))) {
                    cv.text('sin grabación', tx + 84, ty + 104, 15, C.amber, { literal: true });
                }
            });
            // the twelfth tile: what the app reads from the car
            const f = (v, u, d) => (v === null || v === undefined ? '--' : (d ? v.toFixed(d).replace('.', ',') : Math.round(v)) + u);
            const tx = GX + 3 * (TW + GAP), ty = GY + 2 * (TH + GAP);
            cv.roundRect(tx, ty, TW, TH, 12, C.tile);
            cv.text('El coche', tx + 20, ty + 30, 18, C.dim, { literal: true });
            cv.text(fit(cv, 'Combustible ' + f(s.litres, ' L') + ' · ' + f(s.range, ' km'), 18, TW - 40), tx + 20, ty + 56, 18, C.text, { literal: true });
            cv.text(fit(cv, 'Aceite ' + f(s.oil, ' °C') + ' · fuera ' + f(s.outside, ' °C', 1), 18, TW - 40), tx + 20, ty + 80, 18, C.text, { literal: true });
            cv.text(fit(cv, s.open.length ? 'Abierto: ' + s.open.join(', ') : 'Ventanillas y techo cerrados', 18, TW - 40), tx + 20, ty + 104, 18, C.text, { literal: true });
        };

        // the checklist: the car's signals on the left, the voice and the demo on the right
        const showDiag = (cv, s, a, found, have) => {
            const sig = bgs.sig, now = Date.now();
            cv.text('Señales del coche', 30, 112, 22, C.dim, { font: 'bold', literal: true });
            bgs.SIGNALS.forEach((x, k) => {
                const g = sig[x[0]], y = 146 + k * 35;
                const st = g.error ? 'bad' : g.at ? 'ok' : 'wait';
                dot(cv, 40, y - 7, st);
                cv.text(x[1], 60, y, 19, C.text, { literal: true });
                const t = g.error ? 'Error: ' + g.error : g.at ? g.value + ' · ' + ago(g.at) : 'sin datos todavía';
                cv.text(fit(cv, t, 17, 420), 380, y, 17, g.error ? C.red : g.at ? C.dim : C.faint, { literal: true });
            });
            const X = 830;
            cv.text('Voz', X, 112, 22, C.dim, { font: 'bold', literal: true });
            const missing = PHRASES.filter((p) => !found[p]);
            const voiceRows = [
                [have === PHRASES.length ? 'ok' : have ? 'bad' : 'bad', 'Grabaciones: ' + have + ' de ' + PHRASES.length + (missing.length ? ' · faltan ' + missing.join(', ') : '')],
                [speaker.lastError ? 'bad' : speaker.cc ? 'ok' : 'wait', 'Audio del coche: ' + speaker.status()],
                [a.call ? 'pending' : 'ok', a.call ? 'En llamada: la voz espera' : 'Sin llamada' + (a.waiting.length ? ' · en cola: ' + a.waiting.map((e) => TITLES[e]).join(', ') : '')],
            ];
            voiceRows.forEach((r, k) => { const y = 146 + k * 35; dot(cv, X + 10, y - 7, r[0]); cv.text(fit(cv, r[1], 18, 730), X + 30, y, 18, r[0] === 'bad' ? C.red : C.text, { literal: true }); });
            const d = bgs.demo.summary();
            cv.text('Demo' + (d.running ? ' en marcha: ' + (d.ok + d.failed) + ' de ' + d.total : Object.keys(d.results).length ? ': ' + d.ok + ' de ' + d.total + ' bien' : ': pulse «Demo» en el menú (solo aparcado)'),
                X, 272, 22, C.dim, { font: 'bold', literal: true });
            EVENTS.forEach((ev, k) => {
                const col = k < 6 ? 0 : 1, row = k % 6, x = X + col * 380, y = 304 + row * 33;
                const r = d.results[ev] !== undefined ? d.results[ev] : bgs.lastResult[ev];
                const st = r === undefined ? 'wait' : r === 'ok' ? 'ok' : r === 'pending' ? 'pending' : 'bad';
                dot(cv, x + 10, y - 7, st);
                cv.text(fit(cv, TITLES[ev] + (st === 'bad' ? ': ' + r : st === 'pending' ? ': en cola' : ''), 17, 340), x + 30, y, 17, st === 'bad' ? C.red : C.text, { literal: true });
            });
        };

        // the log's last lines, newest at the bottom
        const showLog = (cv) => {
            const LW = 1460, rows = [];
            // the last lines, newest at the bottom; a long one wraps onto the next rows (the whole path shows)
            // the kit's own routine lines (frame rates, screen on/off) left out: they push what matters off the page
            const NOISE = /^(display: |renderd: |entry point |state MAIN |image MAIN_GFX|click BTN_PAGE)/;
            bgs.ring.filter((l) => !NOISE.test(l.msg)).slice(-16).forEach((l) => {
                const d = new Date(l.at), hh = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2) + ':' + ('0' + d.getSeconds()).slice(-2);
                let msg = l.msg.replace(/\s+/g, ' '), first = true;
                while (msg) {
                    let n = msg.length;
                    while (n > 1 && cv.textWidth(msg.slice(0, n), 16, { literal: true }) > LW) n = Math.floor(n * 0.9);
                    rows.push({ hh: first ? hh : '', text: msg.slice(0, n), level: l.level });
                    msg = msg.slice(n); first = false;
                }
            });
            const shown = rows.slice(-16);
            cv.text('Últimas líneas del registro (también en el archivo copiloto_voz.log)', 30, 112, 19, C.dim, { literal: true });
            if (!shown.length) cv.text('Todavía no hay nada.', 30, 146, 20, C.faint, { literal: true });
            shown.forEach((r, k) => {
                const y = 142 + k * 21;
                if (r.hh) cv.text(r.hh, 30, y, 16, C.faint, { literal: true });
                cv.text(r.text, 112, y, 16, r.level === 'error' ? C.red : r.level === 'warn' ? C.amber : C.text, { literal: true });
            });
        };

        const show = () => {
            const cv = ctx.canvas(W, H), s = engine.state(), a = announcer.state();
            const found = speaker.found(PHRASES), have = PHRASES.filter((p) => found[p]).length;
            cv.clear(C.bg);
            header(cv, s, a, found, have);
            if (page === 0) showAvisos(cv, s, a, found);
            else if (page === 1) showDiag(cv, s, a, found, have);
            else showLog(cv);
            if (note) cv.text(fit(cv, note, 18, 1550), 26, 506, 18, C.amber, { literal: true });
            ctx.ui.image('MAIN_GFX', cv);
        };
        // a burst of changes (signals, the queue) draws once; at most every 400 ms
        let drawT = null, drawnAt = 0;
        const redraw = () => {
            if (drawT) return;
            drawT = setTimeout(() => { drawT = null; drawnAt = Date.now(); show(); }, Math.max(0, 400 - (Date.now() - drawnAt)));
        };
        bgs.onChange(redraw);
        if (ctx.ui.onClick) {
            ctx.ui.onClick('BTN_PAGE', () => { page = (page + 1) % PAGES.length; show(); });
            if (ctx.onTab) ctx.onTab((i) => { if (i >= 0 && i < PAGES.length && i !== page) { page = i; show(); } });
            ctx.ui.onClick('BTN_LEARN', () => {
                note = 'Leyendo puertas y ventanillas…';
                page = 1;
                show();
                Promise.resolve(bgs.learnWindows()).then((m) => { note = m; show(); });
            });
            ctx.ui.onClick('BTN_DEMO', () => {
                const why = bgs.startDemo();
                note = why || 'Demo: un viaje simulado con los 10 avisos, uno tras otro (unos dos minutos)';
                page = 1;
                show();
            });
            ctx.ui.onClick('BTN_TEST', () => {
                const p = PHRASES[testIdx++ % PHRASES.length];
                note = 'Probando: ' + p;
                show();
                speaker.say(p).then(() => { note = speaker.lastError ? 'Error: ' + speaker.lastError : ''; show(); });
            });
        }
        setInterval(redraw, 30000);
        show();
    },
});
