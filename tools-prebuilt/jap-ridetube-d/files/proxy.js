'use strict';
// The proxy the MGU Store's updater downloads through (its `proxy` line in updater.local.conf, set in the Store's
// Settings or on its phone page) carries RideTube's traffic too. Node 8 (the daemon, its --fetch child) and Node 22
// (thumb.js).
// - readSetting: the root daemon reads the line; absent or empty = direct, anything wrong = an error, never direct.
// - parse: the Store's own rules (mgu-store native/mgu-updater/js/tunnel.js, Robert Nisipeanu, GPL-3.0), so a setting
//   the Store takes is one RideTube takes, with the same defaults.
// - connect: an HTTP CONNECT or SOCKS5 tunnel to host:port, the name looked up by the proxy. From istt1899's
//   RideTube fork (github.com/istt1899/ridetube, proxy.js, GPL-3.0), IPv6 literals fixed.
// - toEnv / fromEnv: the parsed setting to the fetch-uid children as RT_PROXY (or RT_PROXY_ERROR). A process's
//   environment is readable by its own uid and root only (/proc/<pid>/environ is 0400), the uid that already gets
//   the account's token on stdin.
const fs = require('fs');
const net = require('net');
const URL = require('url').URL;   // Node 8 has no global URL

const STORE_CONF = '/var/opt/mgu-updater/updater.local.conf';
const CONF_MAX = 64 << 10;
const KINDS = { 'http:': 'http', 'socks5:': 'socks5', 'socks5h:': 'socks5' };
const DEFAULT_PORT = { http: 80, socks5: 1080 };

// -> { kind, host, port, user, pass, label: "host:port" }; throws an Error saying what is wrong (never the text itself)
function parse(text) {
    let u;
    try {
        if (/[\s\x00-\x1f\x7f]/.test(String(text).trim())) throw new Error('spaces');
        u = new URL(String(text).trim());
    } catch (e) {
        throw new Error('not a proxy address (http://host:port or socks5://host:port)');
    }
    const kind = KINDS[u.protocol];
    if (!kind) throw new Error('a proxy address starts with http:// or socks5://');
    const host = u.hostname.replace(/^\[(.*)\]$/, '$1');
    if (!host || /[\s/@]/.test(host)) throw new Error('a proxy address names a host');
    if ((u.pathname && u.pathname !== '/') || u.search || u.hash) throw new Error('a proxy address is host and port only');
    const port = u.port ? Number(u.port) : DEFAULT_PORT[kind];
    if (!(port > 0 && port < 65536)) throw new Error('a proxy port is 1 to 65535');
    let user, pass;
    try {
        user = decodeURIComponent(u.username);
        pass = decodeURIComponent(u.password);
    } catch (e) {
        throw new Error('a % in a user name or password is written %25');
    }
    if (kind === 'socks5' && (Buffer.byteLength(user) > 255 || Buffer.byteLength(pass) > 255)) {
        throw new Error('a SOCKS5 user name or password is at most 255 bytes');
    }
    return { kind: kind, host: host, port: port, user: user, pass: pass, label: (net.isIPv6(host) ? '[' + host + ']' : host) + ':' + port };
}

// The Store's setting: null (none: direct), { proxy } or { error }. Its conf rules: `key = value` lines, # comments,
// the last line wins. A regular file only (no symlink), capped; a read error other than "no file" is an error.
function readSetting(file) {
    file = file || STORE_CONF;
    let text;
    try {
        const st = fs.lstatSync(file);
        if (!st.isFile()) return { error: 'the Store\'s settings file is not a file' };
        if (st.size > CONF_MAX) return { error: 'the Store\'s settings file is too big' };
        const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
        try {
            const fst = fs.fstatSync(fd);   // what was opened, not what lstat saw (a FIFO swapped in would block root)
            if (!fst.isFile() || fst.size > CONF_MAX) return { error: 'the Store\'s settings file is not a file' };
            text = fs.readFileSync(fd, 'utf8');
        } finally { fs.closeSync(fd); }
    } catch (e) {
        return e.code === 'ENOENT' ? null : { error: 'the Store\'s settings file cannot be read' };
    }
    let value = null;
    text.split('\n').forEach(function (l) {
        if (/^\s*#/.test(l)) return;
        const m = /^\s*([a-z_]+)\s*=\s*(.*?)\s*$/.exec(l);
        if (m && m[1] === 'proxy') value = m[2];
    });
    if (!value) return null;
    try { return { proxy: parse(value) }; } catch (e) { return { error: 'the Store\'s proxy setting: ' + e.message }; }
}

function toEnv(setting) {
    if (!setting) return {};
    if (setting.error) return { RT_PROXY_ERROR: setting.error };
    const p = setting.proxy;
    return { RT_PROXY: JSON.stringify({ kind: p.kind, host: p.host, port: p.port, user: p.user, pass: p.pass, label: p.label }) };
}
// -> null (direct), a proxy, or { error } (every connection then fails with it)
function fromEnv(env) {
    env = env || process.env;
    if (env.RT_PROXY_ERROR) return { error: String(env.RT_PROXY_ERROR) };
    if (!env.RT_PROXY) return null;
    try {
        const p = JSON.parse(env.RT_PROXY);
        if (!p || (p.kind !== 'http' && p.kind !== 'socks5') || typeof p.host !== 'string' || !(p.port > 0 && p.port < 65536)) throw new Error('bad');
        return p;
    } catch (e) {
        return { error: 'proxy setting unreadable' };
    }
}

// cb(err, socket): a net.Socket tunneled to targetHost:targetPort (TLS goes over it). Errors name the proxy by
// host:port only. options: timeoutMs, lookup (how the proxy's own name is looked up)
function connect(proxy, targetHost, targetPort, options, cb) {
    if (typeof options === 'function') { cb = options; options = {}; }
    if (proxy.error) return process.nextTick(function () { cb(new Error(proxy.error)); });
    const timeoutMs = options.timeoutMs || 10000;
    let done = false;
    const socket = net.connect({ host: proxy.host, port: proxy.port, lookup: options.lookup });
    const finish = function (err, sock) {
        if (done) { if (err && sock) sock.destroy(); return; }
        done = true;
        clearTimeout(timer);
        if (err) socket.destroy();
        cb(err ? new Error('proxy ' + proxy.label + ': ' + err.message) : null, sock);
    };
    const timer = setTimeout(function () { finish(new Error('no answer')); }, timeoutMs);
    if (timer.unref) timer.unref();
    socket.once('error', function (e) { finish(e); });
    socket.once('close', function () { finish(new Error('connection closed')); });   // a no-op once handed over
    socket.setTimeout(timeoutMs, function () { finish(new Error('no answer')); });
    if (proxy.kind === 'http') httpTunnel(socket, proxy, targetHost, targetPort, finish);
    else socks5Tunnel(socket, proxy, targetHost, targetPort, finish);
    return undefined;
}

// bytes until predicate(buf) says {hit, len} or {bad}; done(err, reply, rest)
function readReply(socket, predicate, cap, done) {
    let buf = Buffer.alloc(0), finished = false;
    const finish = function (err, reply, rest) {
        if (finished) return;
        finished = true;
        socket.removeListener('data', onData);   // EventEmitter.off is Node 10+
        done(err, reply, rest);
    };
    const onData = function (d) {
        buf = Buffer.concat([buf, d]);
        if (buf.length > cap) return finish(new Error('answer too long'));
        const r = predicate(buf);
        if (r.hit) return finish(null, buf.slice(0, r.len), buf.slice(r.len));
        if (r.bad) return finish(r.bad);
        return undefined;
    };
    socket.on('data', onData);
}

function httpTunnel(socket, proxy, host, port, finish) {
    socket.once('connect', function () {
        const target = (net.isIPv6(host) ? '[' + host + ']' : host) + ':' + port;
        const auth = (proxy.user || proxy.pass)
            ? 'Proxy-Authorization: Basic ' + Buffer.from(proxy.user + ':' + proxy.pass, 'utf8').toString('base64') + '\r\n' : '';
        socket.write('CONNECT ' + target + ' HTTP/1.1\r\nHost: ' + target + '\r\n' + auth + '\r\n');
        readReply(socket, function (buf) {
            const end = buf.indexOf('\r\n\r\n');
            if (end < 0) return { hit: false };
            const m = /^HTTP\/\d\.\d\s+(\d{3})\b/.exec(buf.slice(0, end).toString('latin1').split('\r\n')[0] || '');
            if (!m) return { bad: new Error('not an HTTP proxy') };
            if (m[1] === '407') return { bad: new Error('user name or password refused') };
            if (m[1] !== '200') return { bad: new Error('CONNECT refused (HTTP ' + m[1] + ')') };
            return { hit: true, len: end + 4 };
        }, 8192, function (err, reply, rest) {
            if (err) return finish(err);
            socket.setTimeout(0);
            if (rest.length) socket.unshift(rest);
            return finish(null, socket);
        });
    });
}

const SOCKS_REPLIES = ['', 'general failure', 'not allowed by its rules', 'network unreachable', 'host unreachable',
    'connection refused', 'TTL expired', 'command not supported', 'address type not supported'];

function socks5Tunnel(socket, proxy, host, port, finish) {
    socket.once('connect', function () {
        const auth = !!(proxy.user || proxy.pass);
        socket.write(Buffer.from(auth ? [0x05, 0x01, 0x02] : [0x05, 0x01, 0x00]));
        readReply(socket, function (buf) { return buf.length >= 2 ? { hit: true, len: 2 } : { hit: false }; }, 64, function (err, reply, rest) {
            if (err) return finish(err);
            if (reply[0] !== 0x05) return finish(new Error('not a SOCKS5 proxy'));
            if (reply[1] === 0x02 && auth) return socks5Auth(socket, proxy, rest, host, port, finish);
            if (reply[1] === 0x00) return socks5Ask(socket, rest, host, port, finish);
            return finish(new Error(auth ? 'it takes no user name and password' : 'it wants a user name and password'));
        });
    });
}

function socks5Auth(socket, proxy, rest, host, port, finish) {
    const u = Buffer.from(proxy.user || '', 'utf8'), p = Buffer.from(proxy.pass || '', 'utf8');
    if (rest.length) socket.unshift(rest);
    socket.write(Buffer.concat([Buffer.from([0x01, u.length]), u, Buffer.from([p.length]), p]));
    readReply(socket, function (buf) { return buf.length >= 2 ? { hit: true, len: 2 } : { hit: false }; }, 64, function (err, reply, rest2) {
        if (err) return finish(err);
        if (reply[1] !== 0x00) return finish(new Error('user name or password refused'));
        return socks5Ask(socket, rest2, host, port, finish);
    });
}

// an IPv6 literal's 16 bytes ("::" expanded)
function ipv6Bytes(h) {
    const halves = h.split('::');
    const part = function (s) { return s ? s.split(':') : []; };
    const head = part(halves[0]), tail = halves.length > 1 ? part(halves[1]) : [];
    const words = head.concat(new Array(8 - head.length - tail.length).fill('0'), tail);
    const b = Buffer.alloc(16);
    words.forEach(function (w, i) { b.writeUInt16BE(parseInt(w, 16) || 0, i * 2); });
    return b;
}

function socks5Ask(socket, rest, host, port, finish) {
    let dst;
    if (net.isIPv4(host)) dst = Buffer.concat([Buffer.from([0x01]), Buffer.from(host.split('.').map(Number))]);
    else if (net.isIPv6(host)) dst = Buffer.concat([Buffer.from([0x04]), ipv6Bytes(host)]);
    else {
        const h = Buffer.from(host, 'utf8');
        if (h.length > 255) return finish(new Error('host name over 255 bytes'));
        dst = Buffer.concat([Buffer.from([0x03, h.length]), h]);   // the proxy looks the name up
    }
    if (rest.length) socket.unshift(rest);
    socket.write(Buffer.concat([Buffer.from([0x05, 0x01, 0x00]), dst, Buffer.from([port >> 8 & 0xff, port & 0xff])]));
    return readReply(socket, function (buf) {
        if (buf.length < 4) return { hit: false };
        if (buf[0] !== 0x05) return { bad: new Error('not a SOCKS5 proxy') };
        if (buf[1] !== 0x00) return { bad: new Error(SOCKS_REPLIES[buf[1]] || 'refused (code ' + buf[1] + ')') };
        const need = buf[3] === 0x01 ? 10 : buf[3] === 0x04 ? 22 : buf[3] === 0x03 ? (buf.length < 5 ? Infinity : 7 + buf[4]) : -1;
        if (need < 0) return { bad: new Error('bad answer') };
        return buf.length >= need ? { hit: true, len: need } : { hit: false };
    }, 600, function (err, reply, rest2) {
        if (err) return finish(err);
        socket.setTimeout(0);
        if (rest2.length) socket.unshift(rest2);
        return finish(null, socket);
    });
}

module.exports = { STORE_CONF: STORE_CONF, parse: parse, readSetting: readSetting, toEnv: toEnv, fromEnv: fromEnv, connect: connect, ipv6Bytes: ipv6Bytes };
