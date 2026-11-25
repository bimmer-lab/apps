'use strict';
/**
 * priority.js - runs the app below the car's own processes (CarPlay, the HMI, navigation, media).
 *
 * The head unit's CPU and disk are shared: when an app draws or writes, CarPlay's video and its link to the phone
 * have to wait for it, and the phone drops CarPlay when they wait too long. A lower CPU priority (nice) and the idle
 * I/O class make the scheduler give the car's own work the CPU and the flash first; the app only gets what is left.
 * Nothing changes while the unit has CPU to spare.
 *
 *   app.json  "config": { "nice": 19 }     0..19, default 10; 0 leaves the app as it is
 *             "config": { "ioIdle": false } keeps the normal I/O class (default: idle)
 *
 * Linux keeps the nice value per thread, so every thread in /proc/self/task is changed; threads made later inherit
 * it. Node 8 on the head unit has no os.setPriority, so renice / ionice are used where the unit has them. It only
 * lowers the priority (allowed without root), never raises it; a failure is logged and the app goes on as before.
 *
 * Plain Node 8.
 */
const fs = require('fs');
const os = require('os');
const { execFile } = require('child_process');

function lower(log, cfg) {
    cfg = cfg || {};
    const nice = Math.max(0, Math.min(19, cfg.nice == null ? 10 : Number(cfg.nice) || 0));
    const ioIdle = cfg.ioIdle !== false;
    if (!nice && !ioIdle) return;
    let tids = [String(process.pid)];
    try { tids = fs.readdirSync('/proc/self/task'); } catch (e) { /* not Linux (host preview) */ }
    const run = (cmd, args, done) => {
        try { execFile(cmd, args, { timeout: 3000 }, (e) => done(!e)); } catch (e) { done(false); }
    };
    let niced = 0, ioniced = 0, left = tids.length * 2;
    const end = () => {
        if (--left > 0) return;
        log.info('priority: nice ' + nice + ' on ' + niced + '/' + tids.length + ' threads' + (ioIdle ? ', idle I/O on ' + ioniced : ''));
    };
    tids.forEach((tid) => {
        if (!nice) end();
        else if (typeof os.setPriority === 'function') {
            try { os.setPriority(Number(tid), nice); niced++; } catch (e) { /* left as it is */ }
            end();
        } else run('renice', ['-n', String(nice), '-p', tid], (ok) => { if (ok) niced++; end(); });
        if (!ioIdle) end();
        else run('ionice', ['-c', '3', '-p', tid], (ok) => { if (ok) ioniced++; end(); });
    });
}

module.exports = { lower };
