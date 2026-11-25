'use strict';
// Copiloto de voz off the car: the notices (engine.js) on a clock of their own, the queue (announcer.js: priority, one
// at a time, calls, stale ones), what the signals mean (decode.js), the welcome by the time of day,
// and the speaker's SOME/IP sequence against a stand-in Audio Manager and media player.
//   node tools/test_copiloto_voz.js
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

const APP = path.join(__dirname, '..', 'apps', 'copiloto_voz');
// the app requires './tz' (shared/, copied next to it by the build)
const load = Module._load;
Module._load = function (req, parent, isMain) {
    if (req === './tz') return load.call(this, path.join(__dirname, '..', 'shared', 'tz.js'), parent, isMain);
    return load.call(this, req, parent, isMain);
};
const { Engine, EVENTS, PRIORITY, KEEP } = require(path.join(APP, 'engine'));
const { Announcer, KEEP_MS } = require(path.join(APP, 'announcer'));
const { phraseFor } = require(path.join(APP, 'phrases'));
const { Speaker } = require(path.join(APP, 'speaker'));
const D = require(path.join(APP, 'decode'));

let checks = 0, fails = 0;
const ok = (cond, msg) => { checks++; if (!cond) { fails++; console.log('FAIL ' + msg); } };
const MIN = 60000;

function rig(enabled, cfg) {
    const r = { t: 0, said: [] };
    r.e = new Engine({ now: () => r.t, say: (ev) => r.said.push(ev), enabled: enabled || (() => true), cfg: cfg });
    r.at = (min) => { while (r.t < min * MIN) { r.t = Math.min(min * MIN, r.t + 5000); r.e.tick(); } };
    r.start = () => { r.e.condition(5); r.e.condition(7); r.e.condition(8); r.e.condition(10); };
    r.stop = () => { r.e.condition(12); r.e.condition(5); };
    return r;
}
const count = (r, ev) => r.said.filter((x) => x === ev).length;

// ---------- the notices ----------
// welcome at the engine start, not at a door, not when the app starts on the road, not on a quick restart
let r = rig();
r.e.condition(5); r.e.openings([]);
ok(r.said.length === 0, 'nothing before the engine starts');
r.e.condition(7); r.e.condition(8); r.e.condition(10);
ok(r.said.join() === 'bienvenida', 'welcome at the start, got ' + r.said);
r.at(20); r.stop(); r.t += MIN; r.start();
ok(count(r, 'bienvenida') === 1, 'no welcome on a restart within 3 min, got ' + r.said);
r.at(40); r.stop(); r.at(60); r.start();
ok(count(r, 'bienvenida') === 2, 'welcome again later, got ' + r.said);
r = rig(); r.e.condition(10);
ok(r.said.length === 0, 'no welcome when the app starts on the road');

// end of the trip, and the windows left open
r = rig(); r.start(); r.at(10); r.e.openings(['ventanilla del conductor']); r.stop();
ok(r.said.join() === 'bienvenida,ventanillas,fin_viaje', 'windows and end at the switch-off, got ' + r.said);
r = rig(); r.start(); r.at(10); r.stop(); r.t += 5000; r.e.openings(['techo']);
ok(r.said.join() === 'bienvenida,fin_viaje,ventanillas', 'a window reported just after the switch-off, got ' + r.said);
r = rig(); r.start(); r.t += 30000; r.stop();
ok(r.said.join() === 'bienvenida', 'no end for a 30 s move, got ' + r.said);

// fuel: once, again only after a refuel (kept across a restart of the head unit)
r = rig(); r.start(); r.e.fuel(30, 400); r.e.fuel(6, 50); r.e.fuel(6, 45);
ok(count(r, 'combustible') === 1, 'fuel once, got ' + r.said);
r.stop(); r.e.fuel(50, 700); r.start(); r.e.fuel(6, 40);
ok(count(r, 'combustible') === 2, 'fuel again after a refuel, got ' + r.said);
const r2 = rig(); r2.e.restore({ minLitres: 6, fuelArmed: false }); r2.start(); r2.e.fuel(6, 40);
ok(count(r2, 'combustible') === 0, 'no repeat after a restart without refuel');
r2.e.fuel(52, 700); r2.e.fuel(52, 45);
ok(count(r2, 'combustible') === 1, 'refuel seen after a restart, got ' + r2.said);

// ice: not in the first minute, then once a trip
r = rig(); r.start(); r.e.outside(1.5);
ok(count(r, 'hielo') === 0, 'no ice in the first minute');
r.at(2); r.e.outside(1); r.at(5);
ok(count(r, 'hielo') === 1, 'ice once, got ' + r.said);

// cold engine over 2,500 rpm, once; warm at 80 °C on a trip that started cold, once
r = rig(); r.e.engine(800, 25); r.start(); r.e.engine(2400, 40);
ok(count(r, 'motor_frio') === 0, 'no cold warning at 2,400 rpm');
r.e.engine(2700, 45); r.e.engine(3000, 50);
ok(count(r, 'motor_frio') === 1, 'cold warning once, got ' + r.said);
r.e.engine(2000, 79); r.e.engine(2000, 80); r.e.engine(2000, 82);
ok(count(r, 'motor_caliente') === 1, 'warm once, got ' + r.said);
r = rig(); r.e.engine(800, 90); r.start(); r.e.engine(3000, 91);
ok(count(r, 'motor_frio') + count(r, 'motor_caliente') === 0, 'a warm start: neither, got ' + r.said);

// the app up after the engine started: welcome only when the head unit has just started (freshStart)
{
    const said = [];
    const e1 = new Engine({ now: () => 0, say: (ev) => said.push(ev), freshStart: () => true });
    e1.condition(10);
    const e2 = new Engine({ now: () => 0, say: (ev) => said.push('late:' + ev), freshStart: () => false });
    e2.condition(10);
    ok(said.join() === 'bienvenida', 'late start: welcome only on a fresh head unit, got ' + said);
}
// no battery notice any more (0.7.0)
ok(EVENTS.indexOf('bateria') < 0, 'no battery notice');

// tyres: BMW's warning, once a trip (also when it was already on at the start)
r = rig(); r.e.tyreWarning(true); r.start(); r.e.tyreWarning(true);
ok(count(r, 'neumaticos') === 1, 'tyres once, got ' + r.said);

// service: within 2,000 km or 60 days, at most once a week
const svc = [{ name: 'aceite', km: 1500, days: 200, colour: 'GREEN' }];
r = rig(); r.e.service(svc); r.start();
ok(count(r, 'mantenimiento') === 1, 'service due: said at the start, got ' + r.said);
r.at(30); r.stop(); r.at(60); r.start();
ok(count(r, 'mantenimiento') === 1, 'not again the same week');
r.t += 7 * 24 * 60 * MIN; r.stop(); r.t += 10 * MIN; r.start();
ok(count(r, 'mantenimiento') === 2, 'again a week later, got ' + count(r, 'mantenimiento'));
r = rig(); r.e.service([{ name: 'frenos', km: 9000, days: 300, colour: 'GREEN' }]); r.start();
ok(count(r, 'mantenimiento') === 0, 'nothing due: nothing');

// a break after 2 h, again at 4 h; a long stop starts the count again
r = rig(); r.start(); r.at(119);
ok(count(r, 'descanso') === 0, 'no break before 2 h');
r.at(121); r.at(241);
ok(count(r, 'descanso') === 2, 'break at 2 h and 4 h, got ' + count(r, 'descanso'));

// an event switched off is not said
r = rig((ev) => ev !== 'bienvenida'); r.start();
ok(r.said.length === 0, 'welcome off in the settings');

// ---------- the queue ----------
(async () => {
    const tick = () => new Promise((res) => setImmediate(res));
    const q = { t: 0, said: [], call: false, release: null };
    const an = new Announcer({ gapMs: 0, priority: PRIORITY, keep: KEEP, now: () => q.t, inCall: () => q.call,
        speak: (ev) => new Promise((res) => { q.said.push(ev); q.release = res; }) });
    // two at once: the higher priority first, never together
    an.add('motor_caliente'); await tick(); an.add('bienvenida');
    ok(q.said.join() === 'motor_caliente', 'the first one starts at once (nothing playing)');
    an.add('combustible'); an.add('bienvenida');
    await tick();
    ok(q.said.length === 1, 'one voice at a time');
    q.release(); await tick(); await tick();
    ok(q.said.join() === 'motor_caliente,combustible', 'then the highest priority waiting, got ' + q.said);
    q.release(); await tick(); await tick();
    q.release(); await tick(); await tick();
    ok(q.said.join() === 'motor_caliente,combustible,bienvenida' && an.state().waiting.length === 0, 'the same one waiting twice is said once, got ' + q.said);
    // during a call: nothing; after it, what waited (stale ones dropped, fuel kept)
    q.call = true; q.said = [];
    an.add('descanso'); an.add('combustible');
    ok(q.said.length === 0, 'nothing during a call');
    q.t += KEEP_MS + 1000; q.call = false; an.pump(); await tick();
    ok(q.said.join() === 'combustible' && an.state().waiting.length === 0, 'after the call: fuel said, the stale break dropped, got ' + q.said);
    q.release(); await tick();
    ok(EVENTS.length === 10 && PRIORITY.combustible === 1 && PRIORITY.motor_caliente === 10, 'ten notices, fuel first, warm engine last');

    // ---------- what the signals mean ----------
    ok(D.oilOf({ temperatureEngineOilDrive: 128 }) === 80 && D.oilOf({ temperatureEngineOilDrive: 255 }) === null, 'oil temperature');
    ok(D.rpmOf({ actualValueRPMEngineCrankshaft: 10000 }) === 2500, 'rpm');
    ok(D.callOf('ACTIVE') === 2 && D.callOf({ aggregatedCallState: 0 }) === 0 && D.callOf('x') === null, 'call state');
    ok(D.voltsOf([0x62, 0xF4, 0x42, 0x2E, 0x18]) === 11.8 && D.voltsOf([0x41, 0x42, 0x30, 0xD4]) === 12.5 && D.voltsOf([0x7F, 0x22, 0x31]) === null, 'battery voltage replies');
    ok(D.kmOf('12.000 km') === 12000 && D.kmOf('1,500 mi') === 2414 && D.kmOf('-200 km') === -200 && D.kmOf('') === null, 'service distances');
    const now = Date.UTC(2026, 10, 24);
    ok(D.daysOf('01/2027', now) === 38 && D.daysOf('15.12.2026', now) === 21 && D.daysOf('x', now) === null, 'service dates');
    const items = D.serviceOf({ cbsDataServiceList: [{ cBSIdentifier: 1, cBSServcieName: 'Motoröl', cBSServiceWay: '1.800 km', cBSServiceDate: '05/2027', cBSColor: 'GREEN' }] }, now);
    ok(items.length === 1 && items[0].name === 'aceite' && items[0].km === 1800 && items[0].colour === 'GREEN', 'service list, got ' + JSON.stringify(items));
    ok(D.activeCc({ basicList: [{ ccmId: 141, activeState: 'AKTIV' }, { ccmId: 50, activeState: 'INAKTIV' }] }).join() === '141', 'active Check Control');
    ok(D.ccNames({ ccList: [{ cCIdentifer: 141, cCShortName: 'Presión de neumáticos baja' }] })[0][1] === 'Presión de neumáticos baja', 'Check Control names');
    ok(D.isTyreCc('Presión de neumáticos baja') && D.isTyreCc('Reifendruck niedrig') && D.isTyreCc('Low tyre pressure') && !D.isTyreCc('Aceite del motor'), 'tyre messages in Spanish, German, English');
    ok(D.openingsOf({ statusDoorSwitchDRD: 1, statusWindowDR: 'FENSTER_OFFEN', statusSunroof: 'GESCHLOSSEN', positionWindowPSR: 12 }).join() === 'ventanilla del conductor,ventanilla trasera', 'windows open, doors not counted, got ' + D.openingsOf({ statusDoorSwitchDRD: 1, statusWindowDR: 'FENSTER_OFFEN', statusSunroof: 'GESCHLOSSEN', positionWindowPSR: 12 }));

    // ---------- the welcome by the time of day; the page link ----------
    const settings = { get: (k) => (k === 'timeZone' ? 10012 : undefined) };
    const have = (names) => ({ file: (n) => (names.indexOf(n) >= 0 ? '/x/' + n + '.m4a' : null) });
    const jan = Date.UTC(2026, 0, 15);
    ok(phraseFor('bienvenida', settings, have(['bienvenida_manana']), jan + 7 * 3600000) === 'bienvenida_manana', 'morning welcome');
    ok(phraseFor('bienvenida', settings, have(['bienvenida_manana']), jan + 19 * 3600000) === 'bienvenida', 'no evening recording: the plain one');
    ok(phraseFor('motor_frio', settings, have([]), jan) === 'motor_frio', 'other notices: their own name');

    // ---------- the speaker: connect, play, wait for the end, stop, disconnect, the radio back ----------
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cv-'));
    const voces = path.join(dir, 'voces'); fs.mkdirSync(voces);
    fs.writeFileSync(path.join(voces, 'fin_viaje.m4a'), 'x');
    const calls = [];
    let conns = [{ mainConnectionID: 7, sourceID: 300, sinkID: 1024 }], handlers = {};
    let mpState = 'PLAYER_STATE_STOP';
    const signals = {
        hasNative: () => true,
        proxy: (iface) => Promise.resolve({ iface }),
        on: (p, name, fn) => { handlers[name] = fn; return Promise.resolve(); },
        get: (p, field) => Promise.resolve(field === 'playerState' ? { playerState: mpState } : conns.slice()),
        call: (p, m, args) => {
            if (/^getList/.test(m)) return Promise.resolve({});
            calls.push(m + (args && args.sourceID ? ' ' + args.sourceID : ''));
            if (m === 'connect') {
                if (args.sourceID === 1046) conns = [{ mainConnectionID: 9, sourceID: 1046, sinkID: 1024 }];
                else conns.push({ mainConnectionID: 10, sourceID: args.sourceID, sinkID: 1024 });
                return Promise.resolve({ mainConnectionID: args.sourceID === 1046 ? 9 : 10, amError: 'E_OK' });
            }
            if (m === 'disconnect') { conns = conns.filter((c) => c.mainConnectionID !== args.mainConnectionID); return Promise.resolve({ amError: 'E_OK' }); }
            if (m === 'playFile') {
                setTimeout(() => { handlers.playerState('PLAYER_STATE_PLAY'); handlers.currentItemTime({ elapsed: 900, total: 1000 }); }, 20);
                return Promise.resolve({ EntError: 'OK' });
            }
            return Promise.resolve({ EntError: 'OK' });
        },
    };
    const log = { info: () => {}, warn: () => {} };
    const sp = new Speaker({ signals, log, dataDir: dir, dirs: [voces] });
    await sp.say('fin_viaje');
    ok(calls.join(',') === 'connect 1046,playFile,sendButtonEvent,disconnect,connect 300', 'speaker sequence, got ' + calls);
    ok(sp.status() === 'última: fin_viaje', 'speaker status, got ' + sp.status());
    calls.length = 0;
    await sp.say('hielo');
    ok(calls.length === 0 && /falta la grabación hielo/.test(sp.status()), 'missing recording, got ' + sp.status());
    conns = [{ mainConnectionID: 3, sourceID: 1046, sinkID: 1024 }];
    calls.length = 0;
    await sp.say('fin_viaje');
    ok(calls.join(',') === 'playFile,sendButtonEvent' && sp.status() === 'última: fin_viaje', 'RideTube open, not playing: said on its connection, got ' + calls + ' ' + sp.status());
    mpState = 'PLAYER_STATE_PLAY';
    calls.length = 0;
    await sp.say('fin_viaje');
    ok(calls.join(',') === 'sendButtonEvent,playFile,sendButtonEvent' && sp.status() === 'última: fin_viaje', 'RideTube playing: stopped, then said over it, got ' + calls + ' ' + sp.status());
    mpState = 'PLAYER_STATE_STOP';

    // voices.js: the chosen voice first, then the language's other voices, then voces/ itself
    const V = require(path.join(APP, 'voices'));
    const vroot = fs.mkdtempSync(path.join(os.tmpdir(), 'cvv-'));
    ['es/es-ES-ElviraNeural', 'es/es-ES-AlvaroNeural', 'en/en-GB-SoniaNeural'].forEach((d) => fs.mkdirSync(path.join(vroot, d), { recursive: true }));
    fs.writeFileSync(path.join(vroot, 'es/es-ES-AlvaroNeural/hielo.mp3'), 'x');
    fs.writeFileSync(path.join(vroot, 'es/es-ES-ElviraNeural/fin_viaje.mp3'), 'x');
    const vd = V.dirsFor(vroot, 'es', 'es-ES-AlvaroNeural').map((d) => path.relative(vroot, d));
    ok(vd.join('|') === 'es/es-ES-AlvaroNeural|es/es-ES-ElviraNeural|', 'voice dirs, got ' + vd.join('|'));
    ok(V.dirsFor(vroot, 'xx', null)[0] === path.join(vroot, 'es', 'es-ES-ElviraNeural'), 'unknown language -> Spanish, Elvira');
    ok(V.voiceName('zh-CN-XiaoxiaoNeural') === 'Xiaoxiao', 'voice name');
    const sp2 = new Speaker({ signals, log, dataDir: dir, dirs: () => V.dirsFor(vroot, 'es', 'es-ES-AlvaroNeural') });
    ok(/AlvaroNeural.hielo\.mp3$/.test(sp2.file('hielo')) && /ElviraNeural.fin_viaje\.mp3$/.test(sp2.file('fin_viaje')) && !sp2.file('bateria'), 'speaker looks in the chosen voice, then the others');
    const VOC = path.join(__dirname, '..', 'tools-prebuilt', 'copiloto-voz-voces');
    const fr = JSON.parse(fs.readFileSync(path.join(VOC, 'frases.json'), 'utf8'));
    ok(Object.keys(fr.frases).length === 13 && Object.keys(fr.frases).every((k) => fr.frases[k].es && fs.existsSync(path.join(VOC, 'es', 'es-ES-ElviraNeural', k + '.m4a'))), 'frases.json: 13 Spanish phrases, each recorded with Elvira');
    const comp = JSON.parse(fs.readFileSync(path.join(VOC, 'component.json'), 'utf8'));
    const appj = JSON.parse(fs.readFileSync(path.join(APP, 'app.json'), 'utf8'));
    ok(comp.files.length === 13 && appj.requires['copiloto-voz-voces'] === comp.version && !appj.settings.some((x) => /^voz_|^idioma$|bateria/.test(x.key)), 'the voices tool: 13 files, the version the app asks for; no language, voice or battery settings')

    // demo.js: a simulated trip gives the 10 notices; through the real queue they are said in the order of priority
    const { Demo } = require(path.join(APP, 'demo'));
    const spoken = [];
    const dq = new Announcer({ gapMs: 0, priority: PRIORITY, keep: KEEP, inCall: () => false, speak: (ev) => { spoken.push(ev); return tick().then(() => demo.said(ev, ev === 'hielo' ? 'falta la grabación hielo' : null)); } });
    const demo = new Demo({ add: (ev) => dq.add(ev) });
    const given = demo.run();
    ok(EVENTS.every((ev) => given.indexOf(ev) >= 0), 'demo gives all 10, got ' + given.join(' '));
    for (let i = 0; i < 40 && demo.running; i++) await tick();
    const sm = demo.summary();
    ok(!sm.running && sm.ok === 9 && sm.failed === 1 && /hielo/.test(sm.results.hielo), 'demo results, got ' + JSON.stringify(sm));
    ok(spoken.join(' ') === EVENTS.join(' '), 'demo said in the order of priority, got ' + spoken.join(' '));
    const d2 = new Demo({ add: () => {} });
    d2.run(); d2.expire(Date.now() + 6 * 60000);
    ok(!d2.running && d2.summary().failed === 10, 'demo: never said -> failed after 5 minutes');

    // windows.js: learned from the car: closed, then each opening (a press with nothing changed skips it)
    const { Windows } = require(path.join(APP, 'windows'));
    const wv = { statusDoorSwitchDRD: 0, fhDR: 'ZU', fhPS: 'ZU', shd: 0, lock: 1 };
    const w = new Windows(null);
    ok(w.open() === null && /no llega/.test(w.press().msg), 'windows: nothing learned, no signal yet');
    w.feed(Object.assign({}, wv));
    w.press(); w.press();                                              // start, everything closed
    w.feed(Object.assign({}, wv, { fhDR: 'AUF', statusDoorSwitchDRD: 1 })); w.press();   // driver's window (+ the door: ignored)
    w.feed(Object.assign({}, wv, { fhPS: 'AUF' })); const wr = w.press();   // passenger's: the last step
    ok(wr.done && wr.save && Object.keys(wr.save.fields).sort().join() === 'fhDR,fhPS', 'windows learned: ' + JSON.stringify(wr.save && wr.save.fields));
    w.feed(Object.assign({}, wv, { fhDR: 'AUF' }));
    ok(w.open().join() === 'ventanilla del conductor', 'windows open from what was learned, got ' + w.open());
    const w2 = new Windows(wr.save); w2.feed(Object.assign({}, wv));
    ok(w2.learned() && w2.open().length === 0, 'windows: learned kept across starts, all closed');

    console.log('copiloto_voz: ' + checks + ' checks, ' + fails + ' failed');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.log('FAIL ' + (e && e.stack || e)); process.exit(1); });
