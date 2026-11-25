'use strict';
/**
 * decode.js - Copiloto de voz: what the car's signals mean, as plain functions (tested in tools/test_copiloto_voz.js).
 * Where each comes from, and the app it was learnt from (all car-tested there, cauliflowermossgrape/idrive7-apps):
 *   oil / rpm     CDS statusEngineTemperature.temperatureEngineOilDrive (deg C + 48), statusEngineSpeed2
 *                 .actualValueRPMEngineCrankshaft (x 0.25)                                     (kit/runtime/car.js)
 *   phone call    ConnectivityAggregator.aggregatedCallState: 0 none, 1 ringing, 2 active, 3 dialing, 4 held  (Redline)
 *   Check Control CheckControlMaster.basicList: { ccmId, activeState }; names from KombiInterface_2 ccList /
 *                 newCCMessage { cCIdentifer, cCShortName }                                     (Fault Scanner)
 *   service       KombiInterface_2.cbsDataServiceList: { cBSIdentifier, cBSServcieName, cBSServiceWay, cBSServiceDate,
 *                 cBSColor }, texts in the car's language and units                                     (Service)
 *   12 V battery  the engine unit's control module voltage, UDS 22 F442 (1 mV) or OBD 01 42                (eDrive)
 *   windows       DoorsWindowsRoofMirrors.CentralLockingSystemAndPanelStatus: no app reads them yet; any field that
 *                 names a window or the roof, open by a 1 / ..._OFFEN / ...OPEN / a position over 0. To be confirmed
 *                 on the car: main.js logs the fields it receives once.
 * Node 8.
 */
const isNum = (v) => typeof v === 'number' && isFinite(v);

const oilOf = (v) => { const r = v && v.temperatureEngineOilDrive; return isNum(r) && r !== 255 ? r - 48 : null; };
const rpmOf = (v) => { const r = v && v.actualValueRPMEngineCrankshaft; return isNum(r) && r !== 65535 ? r * 0.25 : null; };

const CALL = { DISCONNECTED: 0, INCOMING: 1, ACTIVE: 2, DIALING: 3, HELD: 4 };
/** The call state as 0-4 (a name or number, bare or in a one-field object), else null. */
function callOf(v) {
    if (v && typeof v === 'object') { const k = Object.keys(v); if (k.length !== 1) return null; v = v[k[0]]; }
    if (isNum(v)) return v >= 0 && v <= 4 ? v : null;
    if (typeof v === 'string') { const n = CALL[v.toUpperCase()]; return n === undefined ? null : n; }
    return null;
}

/** Every object inside v that has `key` (lists and unions arrive nested). */
function walk(v, key, out) {
    out = out || [];
    if (!v || typeof v !== 'object') return out;
    if (key in v) out.push(v);
    Object.keys(v).forEach((k) => { if (v[k] && typeof v[k] === 'object') walk(v[k], key, out); });
    return out;
}

/** CheckControlMaster.basicList -> the active Check Control ids. */
function activeCc(v) {
    return walk(v, 'ccmId').filter((o) => o.activeState === 'AKTIV' || o.activeState === 0 || o.activeState === 'ACTIVE').map((o) => o.ccmId);
}
/** ccList / newCCMessage -> [[id, name]] */
function ccNames(v) {
    return walk(v, 'cCShortName').concat(walk(v, 'cCName')).map((o) => [o.cCIdentifer !== undefined ? o.cCIdentifer : o.cCIdentifier, String(o.cCShortName || o.cCName || '')])
        .filter((p) => p[0] !== undefined && p[1]);
}
// BMW's tyre pressure messages, in the languages an iDrive may be set to
const TYRE_RE = /reifen|tyre|tire|neum[aá]tic|presi[oó]n de (los )?neum|presi[oó]n de inflado|pneu|gomm|RDC|RPA/i;
const isTyreCc = (name) => TYRE_RE.test(String(name || ''));

const CBS_NAMES = { 0x01: 'aceite', 0x02: 'frenos delanteros', 0x03: 'líquido de frenos', 0x06: 'frenos traseros',
    0x07: 'filtro de partículas', 0x11: 'revisión del vehículo', 0x20: 'inspección técnica', 0x21: 'control de emisiones', 0x64: 'revisión del vehículo' };
const COLOURS = { 0: 'DEFAULT', 1: 'RED', 2: 'YELLOW', 3: 'GREEN', 4: 'ORANGE' };

/** "12.000 km", "7,500 mi", "-200 km" -> km (a number), else null. */
function kmOf(s) {
    s = String(s || '').trim();
    const m = /^(-?)\s*([\d.,\s  ]+)\s*(km|mi|miles?)?/i.exec(s);
    if (!m) return null;
    const n = Number(m[2].replace(/[.,\s  ]/g, ''));
    if (!isNum(n)) return null;
    const km = /^mi/i.test(m[3] || '') ? n * 1.609 : n;
    return Math.round(m[1] ? -km : km);
}
/** "09/2027", "09.2027", "15.09.2027", "2027-09" -> days from now until then (the 1st of the month when no day), else null. */
function daysOf(s, now) {
    s = String(s || '').trim();
    let m, d = 1, mo, y;
    if ((m = /^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/.exec(s))) { d = +m[1]; mo = +m[2]; y = +m[3]; }
    else if ((m = /^(\d{1,2})[./-](\d{4})$/.exec(s))) { mo = +m[1]; y = +m[2]; }
    else if ((m = /^(\d{4})[./-](\d{1,2})(?:[./-](\d{1,2}))?$/.exec(s))) { y = +m[1]; mo = +m[2]; d = m[3] ? +m[3] : 1; }
    else return null;
    if (mo < 1 || mo > 12) return null;
    return Math.floor((Date.UTC(y, mo - 1, d) - (now || Date.now())) / 86400000);
}
/** cbsDataServiceList -> [{ id, name, km, days, colour }] */
function serviceOf(v, now) {
    return walk(v, 'cBSIdentifier').map((o) => {
        const c = o.cBSColor;
        return { id: o.cBSIdentifier, name: CBS_NAMES[o.cBSIdentifier] || String(o.cBSServcieName || o.cBSServiceName || 'servicio ' + o.cBSIdentifier),
            km: kmOf(o.cBSServiceWay), days: daysOf(o.cBSServiceDate, now), colour: isNum(c) ? COLOURS[c] : String(c || '') };
    });
}

/** The 12 V voltage from a reply: 62 F4 42 hh ll (1 mV) or 41 42 A B (OBD, 1 mV); 5-18 V, else null. */
function voltsOf(uds) {
    if (!uds || uds.length < 4) return null;
    let mv = null;
    if (uds[0] === 0x62 && uds[1] === 0xF4 && uds[2] === 0x42 && uds.length >= 5) mv = (uds[3] << 8) | uds[4];
    else if (uds[0] === 0x41 && uds[1] === 0x42) mv = (uds[2] << 8) | uds[3];
    if (mv === null) return null;
    const v = mv / 1000;
    return v >= 5 && v <= 18 ? v : null;
}

// windows and the roof: the field names that say so (English or German), never a door's
const OPENING_RE = /window|fenster|roof|dach|sunroof|schiebe|SHD|panorama/i;
const NAME_ES = [[/roof|dach|schiebe|SHD|panorama/i, 'techo'], [/DR(?!R)|driver|fahrer/i, 'ventanilla del conductor'],
    [/PS(?!R)|passenger|beifahrer/i, 'ventanilla del acompañante'], [/DRR|PSR|rear|hinten|fond/i, 'ventanilla trasera']];
/** CentralLockingSystemAndPanelStatus -> the windows / roof open (Spanish names, each once). */
function openingsOf(v) {
    const out = [];
    if (!v || typeof v !== 'object') return out;
    Object.keys(v).forEach((k) => {
        if (!OPENING_RE.test(k) || /door|tuer|tür/i.test(k)) return;
        const x = v[k];
        const open = x === 1 || /OFFEN|OPEN|GEOEFFNET/i.test(String(x)) || (/position|pos\b/i.test(k) && isNum(x) && x > 0);
        if (!open) return;
        const hit = NAME_ES.filter((p) => p[0].test(k))[0];
        const name = hit ? hit[1] : 'ventanilla';
        if (out.indexOf(name) < 0) out.push(name);
    });
    return out;
}

// ---- where the car is (as hexlay/idrive7-custom-apps speed_limit reads it, car-tested) ----
//   navigation  navigation.positioning.Positioning (Main_Instance) currentLocation: { latitude, longitude } degrees,
//               only with coordinatesValid (GNSS with the car's dead reckoning)
//   GNSS        GPSData GPSPositionCAN: { statusGPSLatitudeCAN, statusGPSLongitudeCAN }, deg = (0.001 x raw - 2147483.648) / 3600,
//               raw >= 4294967293 or 2147483647: none
function navPositionOf(v) {
    if (!v || !v.coordinatesValid) return null;
    const lat = v.latitude, lon = v.longitude;
    if (!isNum(lat) || !isNum(lon) || (lat === 0 && lon === 0) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { lat: lat, lon: lon };
}
function gnssPositionOf(v) {
    const la = v && v.statusGPSLatitudeCAN, lo = v && v.statusGPSLongitudeCAN;
    const bad = (x) => !isNum(x) || x >= 4294967293 || x === 2147483647;
    if (bad(la) || bad(lo)) return null;
    const deg = (raw) => (0.001 * raw - 2147483.648) / 3600;
    const lat = deg(la), lon = deg(lo);
    return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 && !(lat === 0 && lon === 0) ? { lat: lat, lon: lon } : null;
}

module.exports = { navPositionOf, gnssPositionOf,  oilOf, rpmOf, callOf, activeCc, ccNames, isTyreCc, kmOf, daysOf, serviceOf, voltsOf, openingsOf, walk };
