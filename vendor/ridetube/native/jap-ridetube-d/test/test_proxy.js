'use strict';
// proxy.js: the Store's parse rules, its conf rules, the env carrier, and HTTP CONNECT / SOCKS5 tunnels (with and
// without a password) against fake proxies on this machine. Node 8 and 22: node test/test_proxy.js
const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const proxy = require('../proxy.js');
const ytd = require('../ytd.js');

let failed = 0;
const tests = [];
const test = (name, f) => tests.push([name, f]);

// assert.rejects is Node 10+
const rejects = (pr, re) => pr.then(() => { throw new Error('resolved, expected ' + re); }, (e) => assert.ok(re.test(String(e)), String(e) + ' !~ ' + re));
const p = (t) => { try { return proxy.parse(t); } catch (e) { return e.message; } };

// the Store's own vectors (mgu-store native/mgu-updater/test/test_proxy.js testParse)
test('parse: the Store\'s vectors', () => {
    const a = p('socks5://user:p%40ss@192.168.8.1:7891');
    assert.deepStrictEqual([a.kind, a.host, a.port, a.user, a.pass, a.label], ['socks5', '192.168.8.1', 7891, 'user', 'p@ss', '192.168.8.1:7891']);
    const b = p('http://proxy.lan:7890');
    assert.deepStrictEqual([b.kind, b.host, b.port, b.user], ['http', 'proxy.lan', 7890, '']);
    assert.strictEqual(p('socks5h://10.0.0.1').port, 1080);
    assert.strictEqual(p('socks5h://10.0.0.1').kind, 'socks5');
    assert.strictEqual(p('http://10.0.0.1').port, 80);
    assert.strictEqual(p('http://[fd00::1]:3128').label, '[fd00::1]:3128');
    assert.strictEqual(p('http://[fd00::1]:3128').host, 'fd00::1');
    assert.strictEqual(p('socks5://u:p%zz@h:1'), 'a % in a user name or password is written %25');
    const bad = ['', 'ftp://h:1', 'https://h:1', 'socks4://h:1', 'http://h:1/path', 'http://h:1?x', 'http://h:99999', 'h:1', 'http://h :1', 'http://h:1\nx = y'];
    assert.deepStrictEqual(bad.map((t) => [t, p(t)]).filter((x) => typeof x[1] !== 'string'), []);
    assert.strictEqual(p('socks5://' + 'u'.repeat(256) + ':x@h:1'), 'a SOCKS5 user name or password is at most 255 bytes');
});

test('readSetting: the Store\'s conf rules, fail closed', () => {
    const d = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-proxy-'));
    const f = path.join(d, 'updater.local.conf');
    assert.strictEqual(proxy.readSetting(f), null, 'no file: direct');
    fs.writeFileSync(f, 'channel = main\n');
    assert.strictEqual(proxy.readSetting(f), null, 'no proxy line: direct');
    fs.writeFileSync(f, 'proxy = \n');
    assert.strictEqual(proxy.readSetting(f), null, 'empty: direct');
    fs.writeFileSync(f, 'proxy = http://a:1\n# proxy = http://b:2\n  proxy=socks5://u:pw@10.1.2.3:1081  \n');
    const s = proxy.readSetting(f);
    assert.deepStrictEqual([s.proxy.kind, s.proxy.label, s.proxy.user, s.proxy.pass], ['socks5', '10.1.2.3:1081', 'u', 'pw'], 'last line wins, comments skipped');
    fs.writeFileSync(f, 'proxy = socks5://u:s3cret@h:1/x\n');
    const e = proxy.readSetting(f);
    assert.ok(e.error && /host and port only/.test(e.error) && e.error.indexOf('s3cret') < 0, 'wrong: an error without the text');
    const l = path.join(d, 'link.conf');
    fs.symlinkSync(f, l);
    assert.ok(proxy.readSetting(l).error, 'a symlink: an error, not direct');
    fs.unlinkSync(l);
    fs.mkdirSync(l);
    assert.ok(proxy.readSetting(l).error, 'a folder: an error');
    fs.writeFileSync(f, 'proxy = ' + 'x'.repeat(70000));
    assert.ok(proxy.readSetting(f).error, 'too big: an error');
});

test('env carrier: round trip, errors and garbage', () => {
    const s = { proxy: p('socks5://u:p%40ss@[fd00::2]:1081') };
    const back = proxy.fromEnv(proxy.toEnv(s));
    assert.deepStrictEqual([back.kind, back.host, back.port, back.user, back.pass], ['socks5', 'fd00::2', 1081, 'u', 'p@ss']);
    assert.deepStrictEqual(proxy.toEnv(null), {});
    assert.strictEqual(proxy.fromEnv({}), null);
    assert.strictEqual(proxy.fromEnv(proxy.toEnv({ error: 'x wrong' })).error, 'x wrong');
    assert.ok(proxy.fromEnv({ RT_PROXY: '{"kind":"ftp","host":"h","port":1}' }).error);
    assert.ok(proxy.fromEnv({ RT_PROXY: 'not json' }).error);
});

test('ipv6Bytes expands ::', () => {
    assert.strictEqual(proxy.ipv6Bytes('fd00::1').toString('hex'), 'fd000000000000000000000000000001');
    assert.strictEqual(proxy.ipv6Bytes('::1').toString('hex'), '00000000000000000000000000000001');
    assert.strictEqual(proxy.ipv6Bytes('1:2:3:4:5:6:7:8').toString('hex'), '00010002000300040005000600070008');
});

// ---- fake proxies: each records what it was asked and joins the tunnel to `target` (whatever name it was given)
function listen(srv) { return new Promise((r) => srv.listen(0, '127.0.0.1', () => r(srv.address().port))); }
function httpProxy(target, auth) {
    const srv = net.createServer((c) => {
        let buf = Buffer.alloc(0);
        const onData = (d) => {
            buf = Buffer.concat([buf, d]);
            const end = buf.indexOf('\r\n\r\n');
            if (end < 0) return;
            c.removeListener('data', onData);
            const head = buf.slice(0, end).toString();
            srv.asked.push(head.split('\r\n')[0]);
            if (auth && head.indexOf('Proxy-Authorization: Basic ' + Buffer.from(auth).toString('base64')) < 0) { c.end('HTTP/1.1 407 No\r\n\r\n'); return; }
            const t = net.connect(target(), '127.0.0.1', () => { c.write('HTTP/1.1 200 OK\r\n\r\n'); t.write(buf.slice(end + 4)); c.pipe(t).pipe(c); });
        };
        c.on('data', onData);
        c.on('error', () => {});
    });
    srv.asked = [];
    return srv;
}
function socksProxy(target, user, pass) {
    const srv = net.createServer((c) => {
        let buf = Buffer.alloc(0), stage = 0;
        const onData = (d) => {
            buf = Buffer.concat([buf, d]);
            if (stage === 0 && buf.length >= 2 + buf[1]) {
                const methods = Array.from(buf.slice(2, 2 + buf[1]));
                buf = buf.slice(2 + methods.length);
                const want = user ? 2 : 0;
                if (methods.indexOf(want) < 0) { c.end(Buffer.from([5, 0xff])); return; }
                c.write(Buffer.from([5, want]));
                stage = user ? 1 : 2;
            }
            if (stage === 1 && buf.length >= 2 && buf.length >= 3 + buf[1] && buf.length >= 3 + buf[1] + buf[2 + buf[1]]) {
                const u = buf.slice(2, 2 + buf[1]).toString(), pw = buf.slice(3 + buf[1], 3 + buf[1] + buf[2 + buf[1]]).toString();
                buf = buf.slice(3 + buf[1] + buf[2 + buf[1]]);
                if (u !== user || pw !== pass) { c.end(Buffer.from([1, 1])); return; }
                c.write(Buffer.from([1, 0]));
                stage = 2;
            }
            if (stage === 2 && buf.length >= 5) {
                const atyp = buf[3];
                const alen = atyp === 1 ? 4 : atyp === 4 ? 16 : 1 + buf[4];
                if (buf.length < 4 + alen + 2) return;
                srv.asked.push(atyp === 3 ? buf.slice(5, 5 + buf[4]).toString() : atyp === 1 ? Array.from(buf.slice(4, 8)).join('.') : 'v6:' + buf.slice(4, 20).toString('hex'));
                const rest = buf.slice(4 + alen + 2);
                stage = 3;
                c.removeListener('data', onData);
                const t = net.connect(target(), '127.0.0.1', () => {
                    c.write(Buffer.from([5, 0, 0, 1, 127, 0, 0, 1, 0, 0]));
                    if (rest.length) t.write(rest);
                    c.pipe(t).pipe(c);
                });
            }
        };
        c.on('data', onData);
        c.on('error', () => {});
    });
    srv.asked = [];
    return srv;
}
// the target: answers "pong <what it got>"
function echo() {
    return net.createServer((c) => { c.on('data', (d) => c.end('pong ' + d)); c.on('error', () => {}); });
}
const through = (px, host) => new Promise((resolve, reject) => {
    proxy.connect(px, host, 443, { timeoutMs: 2000 }, (e, s) => {
        if (e) return reject(e);
        let got = '';
        s.on('data', (d) => { got += d; });
        s.on('end', () => resolve(got));
        s.write('ping');
        return undefined;
    });
});

test('connect: CONNECT and SOCKS5, with and without a password, the name to the proxy', async () => {
    const tgt = echo(), tport = await listen(tgt);
    const T = () => tport;
    const hp = httpProxy(T), hpa = httpProxy(T, 'u:pw'), sp = socksProxy(T), spa = socksProxy(T, 'u', 'p@ss');
    const ports = await Promise.all([hp, hpa, sp, spa].map(listen));
    const px = (kind, port, user, pass) => ({ kind: kind, host: '127.0.0.1', port: port, user: user || '', pass: pass || '', label: '127.0.0.1:' + port });
    assert.strictEqual(await through(px('http', ports[0]), 'www.youtube.com'), 'pong ping');
    assert.deepStrictEqual(hp.asked, ['CONNECT www.youtube.com:443 HTTP/1.1']);
    assert.strictEqual(await through(px('http', ports[1], 'u', 'pw'), 'i.ytimg.com'), 'pong ping');
    await rejects(through(px('http', ports[1], 'u', 'bad'), 'i.ytimg.com'), /proxy 127\.0\.0\.1:\d+: user name or password refused/);
    assert.strictEqual(await through(px('socks5', ports[2]), 'rr1.googlevideo.com'), 'pong ping');
    assert.deepStrictEqual(sp.asked, ['rr1.googlevideo.com']);
    assert.strictEqual(await through(px('socks5', ports[3], 'u', 'p@ss'), 'www.youtube.com'), 'pong ping');
    await rejects(through(px('socks5', ports[3], 'u', 'nope'), 'www.youtube.com'), /user name or password refused/);
    await rejects(through(px('socks5', ports[3]), 'www.youtube.com'), /wants a user name and password/);
    await rejects(through(px('socks5', 1), 'www.youtube.com'), /^Error: proxy 127\.0\.0\.1:1: /);
    await rejects(through({ error: 'the Store\'s proxy setting: x' }, 'www.youtube.com'), /the Store's proxy setting: x/);
    [tgt, hp, hpa, sp, spa].forEach((s) => s.close());
});

test('ytRequest: a wrong setting fails, never direct; a proxy that does not answer is named', async () => {
    await rejects(ytd.ytRequest('GET', 'https://www.youtube.com/', null, null, 2000, undefined, { error: 'the Store\'s proxy setting: bad' }),
        /the Store's proxy setting: bad/);
    const dead = net.createServer(() => {});   // takes the connection, never answers
    const port = await listen(dead);
    await rejects(ytd.ytRequest('GET', 'https://www.youtube.com/', null, null, 700, undefined,
        { kind: 'socks5', host: '127.0.0.1', port: port, user: '', pass: '', label: '127.0.0.1:' + port }), /proxy 127\.0\.0\.1:\d+: no answer|timeout/);
    dead.close();
});

test('the daemon: the Store setting goes to every fetch-uid child as env, read at each spawn', () => {
    const d = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-proxyd-'));
    const conf = path.join(d, 'updater.local.conf');
    const logs = [];
    const self = Object.assign(Object.create(ytd.Daemon.prototype), { opts: { storeConf: conf, stateDir: path.join(d, 'state') }, log: (l) => logs.push(l) });
    const env = () => ytd.Daemon.prototype.proxyEnv.call(self);
    assert.deepStrictEqual(env(), {});
    fs.writeFileSync(conf, 'proxy = socks5://u:pw@10.0.0.9:1080\n');
    const e = env();
    assert.strictEqual(JSON.parse(e.RT_PROXY).pass, 'pw');
    fs.writeFileSync(conf, 'proxy = gopher://x\n');
    assert.ok(env().RT_PROXY_ERROR);
    assert.ok(logs.every((l) => l.indexOf('pw') < 0 && l.indexOf('u:') < 0), 'the log names host:port only: ' + JSON.stringify(logs));
    assert.deepStrictEqual(logs.map((l) => l.replace(/ \(.*$/, '')), ['network: direct', 'network: through socks5 10.0.0.9:1080',
        'network: the Store\'s proxy setting: a proxy address starts with http:// or socks5://']);
    fs.writeFileSync(conf, 'proxy = socks5://u:pw@10.0.0.9:1080\n');
    env();
    const n = logs.length;
    fs.writeFileSync(conf, 'proxy = socks5://u:pw2@10.0.0.9:1080\n');
    assert.strictEqual(JSON.parse(env().RT_PROXY).pass, 'pw2');
    assert.strictEqual(logs.length, n + 1, 'a new password is a change (the spare goes)');
});

test('reports: the proxy host by context at any length, its user and password in every URL form', () => {
    const r = ytd.redactLog('network: through socks5 vp:1080 (the Store\'s proxy setting)\nerr proxy [fd00::1]:3128: no answer\nx p%21w%28 y', { secrets: ["p!w("].concat([encodeURIComponent("p!w("), "p%21w%28"]) }, 's', null);
    assert.ok(r.indexOf('vp:1080') < 0 && r.indexOf('fd00') < 0 && r.indexOf('p%21w%28') < 0, r);
    assert.ok(/through socks5 \[proxy\]/.test(r) && /proxy \[proxy\]: no answer/.test(r), r);
});

test('fetchRequest refuses to run without the proxy env (never direct by mistake)', async () => {
    await rejects(ytd.fetchRequest({ fetchCmd: ['/bin/false'] }, 0, {}), /no childEnv/);
});

test('no spare while the Store\'s proxy setting is wrong (serve would stop at once, every tick)', () => {
    const d = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-proxys-'));
    const conf = path.join(d, 'c');
    fs.writeFileSync(conf, 'proxy = gopher://x\n');
    let spawned = 0;
    const self = Object.assign(Object.create(ytd.Daemon.prototype), { opts: { storeConf: conf, stateDir: path.join(d, 'state'), spare: true, warmMs: 60000 }, client: {}, usedAt: Date.now(),
        log: () => {}, ytc: () => d, pyzArgs: () => [], spawnFetchUid: () => { spawned++; throw new Error('stop'); } });
    self.keepWarm();
    self.keepWarm();
    assert.strictEqual(spawned, 0);
    fs.writeFileSync(conf, 'proxy = socks5://10.0.0.1:1080\n');
    self.keepWarm();
    assert.strictEqual(spawned, 1, 'a good setting: a spare again');
});

test('the app\'s switch: off goes direct while the Store keeps its proxy, kept across restarts, only store or off', () => {
    const d = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-proxyt-'));
    const conf = path.join(d, 'updater.local.conf');
    fs.writeFileSync(conf, 'proxy = socks5://u:pw@10.0.0.9:1080\n');
    const mk = () => { const o = { logs: [], out: [], dropped: 0 }; return Object.assign(Object.create(ytd.Daemon.prototype), { opts: { storeConf: conf, stateDir: path.join(d, 'state') },
        log: (l) => o.logs.push(l), emit: (m) => o.out.push(m), dropSpare: () => o.dropped++, o: o }); };
    const a = mk();
    assert.ok(JSON.parse(a.proxyEnv().RT_PROXY), 'the default: the Store\'s proxy');
    a.proxy('off');
    assert.deepStrictEqual(a.o.out.pop(), { ev: 'proxy', mode: 'off', store: true }, 'the Store still has its proxy: told, for the switch');
    assert.deepStrictEqual(a.proxyEnv(), {}, 'off: direct');
    assert.strictEqual(a.o.dropped, 1, 'the spare made through the proxy goes');
    assert.ok(/network: direct \(the Store's proxy is off in RideTube\)/.test(a.o.logs.pop()));
    assert.strictEqual(a.proxySetting(), null, 'health sees direct');
    const b = mk();
    assert.deepStrictEqual(b.proxyEnv(), {}, 'off kept in state/proxy.json');
    b.proxy('on'); b.proxy(undefined); b.proxy('http://evil:1');
    assert.deepStrictEqual(b.o.out, [0, 1, 2].map(() => ({ ev: 'error', cmd: 'proxy', msg: 'bad proxy mode' })));
    assert.deepStrictEqual(b.proxyEnv(), {}, 'a bad mode changes nothing');
    b.proxy('store');
    assert.ok(JSON.parse(b.proxyEnv().RT_PROXY));
    fs.writeFileSync(path.join(d, 'state', 'proxy.json'), 'junk');
    assert.ok(JSON.parse(mk().proxyEnv().RT_PROXY), 'an unreadable state file: the default (store), never a silent off');
    // the app's switch follows the Store: told when its proxy is cleared or set, once per change, also while off
    const c = mk();
    c.proxy('store');
    assert.deepStrictEqual(c.o.out.pop(), { ev: 'proxy', mode: 'store', store: true });
    fs.writeFileSync(conf, '');
    c.proxyEnv(); c.proxyEnv();
    assert.deepStrictEqual(c.o.out, [{ ev: 'proxy', mode: 'store', store: false }], 'cleared in the Store: told once');
    c.proxy('off'); c.o.out.length = 0;
    fs.writeFileSync(conf, 'proxy = gopher://x\n');
    assert.deepStrictEqual(c.proxyEnv(), {}, 'off: direct, even with a wrong Store setting');
    assert.deepStrictEqual(c.o.out, [{ ev: 'proxy', mode: 'off', store: true }], 'a wrong setting counts as set (on: fails closed)');
});

(async () => {
    for (const [name, f] of tests) {
        try { await f(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + (e && e.stack || e)); }
    }
    console.log(failed ? failed + ' failed' : 'all ok');
    process.exit(failed ? 1 : 0);
})();
