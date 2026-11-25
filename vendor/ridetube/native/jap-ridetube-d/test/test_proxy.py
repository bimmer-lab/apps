"""ytfetch.py and the Store's proxy (python3.12 -I test/test_proxy.py): RT_PROXY read, every stdlib connection through
fake CONNECT / SOCKS5 proxies on this machine (the name given to the proxy), no name looked up here but the proxy's,
a wrong setting failing every connection, yt-dlp's socks5h URL. Each test runs in a fresh child: install_proxy
patches the socket module for good."""
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def serve(handler):
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen(8)
    asked = []

    def loop():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(c, asked), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return s, asked


def http_target(c, asked):
    """an HTTP server: 200 'pong <path>'"""
    req = b''
    while b'\r\n\r\n' not in req:
        d = c.recv(4096)
        if not d:
            return
        req += d
    path = req.split(b' ')[1]
    body = b'pong ' + path
    c.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s' % (len(body), body))
    c.close()


def pipe(a, b):
    def one(x, y):
        try:
            while True:
                d = x.recv(65536)
                if not d:
                    break
                y.sendall(d)
        except OSError:
            pass
        finally:
            for z in (x, y):
                try:
                    z.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
    threading.Thread(target=one, args=(a, b), daemon=True).start()
    threading.Thread(target=one, args=(b, a), daemon=True).start()


def connect_proxy(target_port, auth=None):
    def h(c, asked):
        head = b''
        while b'\r\n\r\n' not in head:
            head += c.recv(1)
        asked.append(head.split(b'\r\n')[0].decode())
        if auth and auth.encode() not in head:
            c.sendall(b'HTTP/1.1 407 No\r\n\r\n')
            c.close()
            return
        t = socket.create_connection(('127.0.0.1', target_port))
        c.sendall(b'HTTP/1.1 200 OK\r\n\r\n')
        pipe(c, t)
    return serve(h)


def socks_proxy(target_port, user=None, pw=None):
    def rx(c, n):
        b = b''
        while len(b) < n:
            b += c.recv(n - len(b))
        return b

    def h(c, asked):
        _, n = rx(c, 2)
        methods = rx(c, n)
        want = 2 if user else 0
        if want not in methods:
            c.sendall(b'\x05\xff')
            return c.close()
        c.sendall(bytes([5, want]))
        if user:
            _, ul = rx(c, 2)
            u = rx(c, ul).decode()
            p = rx(c, rx(c, 1)[0]).decode()
            if (u, p) != (user, pw):
                c.sendall(b'\x01\x01')
                return c.close()
            c.sendall(b'\x01\x00')
        _, _, _, atyp = rx(c, 4)
        host = rx(c, rx(c, 1)[0]).decode() if atyp == 3 else socket.inet_ntoa(rx(c, 4)) if atyp == 1 else 'v6'
        rx(c, 2)
        asked.append(host)
        t = socket.create_connection(('127.0.0.1', target_port))
        c.sendall(b'\x05\x00\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('>H', 0))
        pipe(c, t)
    return serve(h)


CHILD = r'''
import json, os, socket, sys, http.client, urllib.request
sys.path.insert(0, sys.argv[1])
import ytfetch
ytfetch.install_proxy(ytfetch.proxy_env())
out = {}
def run(name, f):
    try:
        out[name] = f()
    except Exception as e:
        out[name] = 'ERR ' + type(e).__name__ + ': ' + str(e)
port = int(sys.argv[2])
def get():
    c = http.client.HTTPConnection('www.youtube.com', port, timeout=3)
    c.request('GET', '/robots.txt')
    return c.getresponse().read().decode()
if ytfetch.PROXY is None:  # direct: nothing to reach here
    get = lambda: None  # noqa: E731
run('http.client', get)
run('urllib', (lambda: None) if ytfetch.PROXY is None else lambda: urllib.request.urlopen(f'http://rr1.googlevideo.com:{port}/videoplayback', timeout=3).read().decode())
run('dns', lambda: socket.getaddrinfo('www.youtube.com', 443)[0][4][0])
run('dns_local', lambda: socket.getaddrinfo('127.0.0.1', 443)[0][4][0])
run('ydl', lambda: ytfetch.ydl_proxy())
print(json.dumps(out))
'''


def child(env, port):
    r = subprocess.run([sys.executable, '-I', '-c', CHILD, ROOT, str(port)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def rt(kind, port, user='', pw=''):
    return {'RT_PROXY': json.dumps({'kind': kind, 'host': '127.0.0.1', 'port': port, 'user': user, 'pass': pw,
                                    'label': f'127.0.0.1:{port}'})}


class ProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target, _ = serve(http_target)
        cls.tport = cls.target.getsockname()[1]

    def test_direct_without_setting(self):
        o = child({}, self.tport)
        self.assertEqual(o['ydl'], {})
        self.assertEqual(o['dns_local'], '127.0.0.1')

    def test_socks5_every_connection_names_to_the_proxy(self):
        s, asked = socks_proxy(self.tport, 'u', 'p@ss')
        o = child(rt('socks5', s.getsockname()[1], 'u', 'p@ss'), self.tport)
        self.assertEqual(o['http.client'], 'pong /robots.txt')
        self.assertEqual(o['urllib'], 'pong /videoplayback')
        self.assertEqual(asked, ['www.youtube.com', 'rr1.googlevideo.com'])
        self.assertIn('looked up by the proxy only', o['dns'])
        self.assertEqual(o['dns_local'], '127.0.0.1')
        self.assertEqual(o['ydl'], {'proxy': f'socks5h://u:p%40ss@127.0.0.1:{s.getsockname()[1]}'})

    def test_socks5_wrong_password(self):
        s, _ = socks_proxy(self.tport, 'u', 'right')
        o = child(rt('socks5', s.getsockname()[1], 'u', 'wrong'), self.tport)
        self.assertIn('ProxyError', o['http.client'])
        self.assertIn('user name or password refused', o['http.client'])
        self.assertNotIn('wrong', o['http.client'])

    def test_http_connect_with_auth(self):
        import base64
        s, asked = connect_proxy(self.tport, 'Proxy-Authorization: Basic ' + base64.b64encode(b'u:pw').decode())
        o = child(rt('http', s.getsockname()[1], 'u', 'pw'), self.tport)
        self.assertEqual(o['http.client'], 'pong /robots.txt')
        self.assertEqual(asked[0], f'CONNECT www.youtube.com:{self.tport} HTTP/1.1')
        self.assertEqual(o['ydl'], {'proxy': f'http://u:pw@127.0.0.1:{s.getsockname()[1]}'})

    def test_proxy_down_is_named(self):
        o = child(rt('socks5', 1), self.tport)
        self.assertIn('ProxyError: proxy 127.0.0.1:1:', o['http.client'])

    def test_wrong_setting_fails_closed(self):
        o = child({'RT_PROXY_ERROR': "the Store's proxy setting: x"}, self.tport)
        self.assertIn("ProxyError: the Store's proxy setting: x", o['http.client'])
        self.assertIn('looked up by the proxy only', o['dns'])
        self.assertEqual(o['ydl'], {})
        o = child({'RT_PROXY': 'garbage'}, self.tport)
        self.assertIn('proxy setting unreadable', o['http.client'])

    def test_main_takes_it_once_and_drops_it_from_env(self):
        code = ('import os, sys; sys.path.insert(0, sys.argv[1]); import ytfetch\n'
                'ytfetch.main(["request", "--cache-dir", sys.argv[2]])\n'
                'ytfetch.main(["request", "--cache-dir", sys.argv[2]])\n'  # serve runs main() twice
                'print("\\n" + repr(("RT_PROXY" in os.environ, ytfetch.PROXY["host"])))')
        import tempfile
        r = subprocess.run([sys.executable, '-I', '-c', code, ROOT, tempfile.mkdtemp()], env=rt('socks5', 1), input='x',
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip().split('\n')[-1], "(False, '127.0.0.1')", r.stderr)

    def test_wrong_setting_stops_yt_dlp_commands(self):
        import tempfile
        d = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'ytfetch.py'), 'extract', 'dQw4w9WgXcQ', d + '/i', '--cache-dir', d],
                           env={'RT_PROXY_ERROR': "the Store's proxy setting: x"}, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("the Store's proxy setting: x", r.stdout)


if __name__ == '__main__':
    unittest.main()
