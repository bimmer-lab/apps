'use strict';
/**
 * Fuel Link: the fuel left and the range, as JSON, for your own phone over your tailnet. Read-only: it only listens to
 * the cluster's fuel and range (KombiIF statusRangeTankStatus, the same signal Cluster Themes reads) and never writes
 * anything to the car.
 *
 *   GET /fuel         ->  { litres, rangeKm, l100km, at, now }
 *   GET /go?to=<url>  ->  302 to <url>#car=<litres>,<l100km>,<at>,<now>   (<url> must start with config.allowOrigin)
 *
 * It listens on the head unit's loopback and on its tailnet address (100.64.0.0/10, looked for every 30 s until the
 * tailnet is up, then every 5 min), never on the car's Wi-Fi or hotspot. A tailnet without "tailscale serve" reaches
 * it over plain http, which an https page cannot fetch; /go hands the reading to the page through a redirect instead.
 * l100km is litres over range: BMW works the range out from the recent consumption, so this is the consumption it
 * uses. The last reading is kept in the data folder, so the page gets one right after a restart, with its time.
 *
 * No page and no tile in the apps menu (0.4.0, app.json "hidden"): it starts with the car (autostart) and runs only in
 * background(). Light on the head unit: CarPlay, the HMI and BMW's own services share the same CPU and memory.
 *   - nothing is drawn, ever
 *   - a reading equal to the last one only refreshes its time: nothing is saved
 *   - the reading is saved at most once a minute, and only when it changed
 *   - the process runs at the lowest CPU and I/O priority (app.json config.nice 19, kit/runtime/priority.js), so the
 *     car's own work (CarPlay first) always goes first
 */
const fs = require('fs');
const http = require('http');
const path = require('path');
const os = require('os');
const mgu = require('./kit/mgu');

mgu.run({
    async background(bg) {
        const log = bg.log, cfg = bg.spec.config || {};
        const port = cfg.port || 8473, origin = cfg.allowOrigin || '*';
        const memo = path.join(bg.dataDir, 'last.json');
        let last = { litres: null, rangeKm: null, l100km: null, at: null };
        try { last = Object.assign(last, JSON.parse(fs.readFileSync(memo, 'utf8'))); } catch (e) { /* first run */ }

        let saved = 0, savedAt = null, tailnet = null;
        const save = () => {
            if (last.at === savedAt || Date.now() - saved < 60000) return;
            saved = Date.now(); savedAt = last.at;
            try { fs.writeFileSync(memo, JSON.stringify(last)); } catch (e) { log.warn('cannot save ' + memo + ': ' + e.message); }
        };

        const handler = (req, res) => {
            const head = { 'Access-Control-Allow-Origin': origin, 'Vary': 'Origin', 'Cache-Control': 'no-store' };
            if (req.method === 'OPTIONS') {
                res.writeHead(204, Object.assign(head, { 'Access-Control-Allow-Methods': 'GET', 'Access-Control-Allow-Private-Network': 'true' }));
                return res.end();
            }
            const p = req.url.split('?')[0];
            if (req.method === 'GET' && p === '/go') {
                // back to the page with the reading in the URL's hash; only to the allowed page, never anywhere else
                let to = '';
                try { to = decodeURIComponent((req.url.split('?')[1] || '').replace(/^.*?\bto=([^&]*).*$/, '$1')); } catch (e) { to = ''; }
                if (origin === '*' || to.indexOf(origin + '/') !== 0) { res.writeHead(400, head); return res.end('to must be a page of ' + origin); }
                const v = [last.litres, last.l100km, last.at, Date.now()].map((x) => (x == null ? '' : x)).join(',');
                res.writeHead(302, Object.assign(head, { Location: to.split('#')[0] + '#car=' + v }));
                return res.end();
            }
            if (req.method !== 'GET' || p !== '/fuel') { res.writeHead(404, head); return res.end(); }
            res.writeHead(200, Object.assign(head, { 'Content-Type': 'application/json' }));
            res.end(JSON.stringify(Object.assign({}, last, { now: Date.now() })));
        };

        // loopback always; the tailnet address once it is up (CGNAT 100.64.0.0/10, the range Tailscale gives out)
        const bound = {};
        const listen = (host) => {
            if (bound[host]) return;
            bound[host] = true;
            const srv = http.createServer(handler);
            // idle connections from the phone are closed soon: no sockets kept open on the head unit
            srv.keepAliveTimeout = 2000;
            srv.timeout = 10000;
            srv.on('error', (e) => {
                delete bound[host];
                log.error('cannot listen on ' + host + ':' + port + ': ' + e.message);
                if (tailnet === host) tailnet = null;
            });
            srv.listen(port, host, () => {
                log.info('fuel at http://' + host + ':' + port + '/fuel');
                if (host !== '127.0.0.1') tailnet = host;
            });
        };
        const tailnetAddr = () => {
            const all = os.networkInterfaces();
            for (const name of Object.keys(all)) {
                for (const a of all[name] || []) {
                    if (a.family !== 'IPv4' && a.family !== 4) continue;
                    const o = a.address.split('.').map(Number);
                    if (o[0] === 100 && o[1] >= 64 && o[1] <= 127) return a.address;
                }
            }
            return null;
        };
        listen('127.0.0.1');
        const look = () => {
            try { const a = tailnetAddr(); if (a) listen(a); } catch (e) { log.warn('tailnet address: ' + e.message); }
        };
        // every 30 s until the tailnet is up, then every 5 min (it may come back with a new address)
        const lookLater = () => setTimeout(() => { look(); lookLater(); }, tailnet ? 300000 : 30000);
        look();
        lookLater();

        // the cluster's fuel and range: litres (255: none) and km (4095: none)
        bg.signals.watch('de.bmw.infotainment.KombiIF', '1', {
            statusRangeTankStatus: (v) => {
                const f = v && v.fluidLevelFuelTank, r = v && v.range;
                const litres = typeof f === 'number' && f >= 0 && f < 255 ? f : null;
                const rangeKm = typeof r === 'number' && r >= 0 && r < 4095 ? r : null;
                if (litres == null) return;
                // the signal comes often while driving, mostly with the same values: then only its time is new
                if (litres === last.litres && rangeKm === last.rangeKm) { last.at = Date.now(); return; }
                last = { litres, rangeKm, l100km: rangeKm > 20 ? Math.round(litres / rangeKm * 1000) / 10 : null, at: Date.now() };
                save();
            },
        }, log).then((r) => { if (r && r.error) log.warn('fuel signal: ' + r.error); }, (e) => log.warn('fuel signal: ' + e));
        setInterval(save, 60000);
        return null;
    },
});
