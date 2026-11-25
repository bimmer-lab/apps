#!/usr/bin/env python3
"""ytfetch.py's "stream the video through RAM" tests (WP8), offline: python3 -I test/test_stream.py [-v]

Window (the position feed), Source (Range requests that retry and refresh the URL), PacedReader, Prefixed,
parse_sidx, stream_layout + demux_video from a fragment (a real ffmpeg fMP4 with a sidx inserted), webm_layout +
demux_webm from a cluster (ffmpeg VP9 WebMs), fetch_stream over a local Range server (the first range, progress
before each request), Trimmer (hole punching) and plan_stream (RAM, flash window or the whole file). Only the ffmpeg
tests are skipped without ffmpeg; the punching ones without a file system that frees blocks.
"""
import http.server
import io
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, HERE]
import ytfetch  # noqa: E402

FF = shutil.which('ffmpeg')
TMP = tempfile.gettempdir()
FRAG = ['-movflags', 'frag_keyframe+empty_moov+default_base_moof']
WAIT = 10  # s: an upper bound for things that must happen, never slept for


def events(fn):
    out, old = io.StringIO(), ytfetch._out
    ytfetch._out = out
    try:
        fn()
    finally:
        ytfetch._out = old
    return [json.loads(x) for x in out.getvalue().splitlines()]


def in_thread(fn):
    """Run fn in a thread: (thread, result list holding ('ok', value) or ('err', exception))."""
    res = []

    def run():
        try:
            res.append(('ok', fn()))
        except BaseException as e:  # noqa: BLE001
            res.append(('err', e))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, res


def poke(w, **kw):
    """set Window attributes and wake whoever waits"""
    with w.cv:
        for k, v in kw.items():
            setattr(w, k, v)
        w.cv.notify_all()


# ---------------------------------------------------------------- Window

class WindowTests(unittest.TestCase):
    def test_listen_reads_pos_lines_and_ignores_junk(self):
        r, wfd = os.pipe()
        rf, wf = os.fdopen(r, 'r'), os.fdopen(wfd, 'w')
        self.addCleanup(rf.close)
        w = ytfetch.Window(1000, 1000)
        w.listen(rf)
        wf.write('pos 700\n')
        wf.flush()
        with w.cv:
            self.assertTrue(w.cv.wait_for(lambda: w.play == 700, WAIT))
        self.assertFalse(w.gone, 'the pipe is still open')
        # junk after the last good line must change nothing
        wf.write('\nhello\npos\npos abc\npos -5\npos 1 2\nposx 9\npos 1.5\nseek 9\n')
        wf.flush()
        wf.close()  # the feed closes: ytd.js is gone
        with w.cv:
            self.assertTrue(w.cv.wait_for(lambda: w.gone, WAIT))
        self.assertEqual(w.play, 700)
        with self.assertRaises(OSError):
            w.wait_room(lambda: True)
        with self.assertRaises(OSError):  # even while full
            w.wait_room(lambda: False)

    def test_wait_room_blocks_while_ahead_is_full_then_returns(self):
        w = ytfetch.Window(500, 100)
        w.fetched = 1000          # 1000 past play 0 > ahead 500
        t, res = in_thread(lambda: w.wait_room(lambda: True))
        t.join(0.4)
        self.assertTrue(t.is_alive(), 'must wait while fetched - play > ahead')
        poke(w, play=400)         # 600 > 500: still full
        t.join(0.3)
        self.assertTrue(t.is_alive())
        poke(w, play=500)         # exactly ahead: room
        t.join(WAIT)
        self.assertFalse(t.is_alive())
        self.assertEqual(res, [('ok', None)])
        self.assertGreater(w.waited, 0.3)

    def test_wait_room_blocks_while_ram_is_short(self):
        w = ytfetch.Window(500, 100)
        free = [False]
        t, res = in_thread(lambda: w.wait_room(lambda: free[0]))
        t.join(0.4)
        self.assertTrue(t.is_alive(), 'must wait while free_ok() is False')
        free[0] = True
        poke(w)
        t.join(WAIT)
        self.assertEqual(res, [('ok', None)])

    def test_wait_room_ticks_while_it_waits(self):
        w = ytfetch.Window(100, 0)
        w.fetched = 500
        ticks = threading.Event()
        t, res = in_thread(lambda: w.wait_room(lambda: True, ticks.set))
        self.assertTrue(ticks.wait(WAIT), 'tick: called while full')
        poke(w, play=450)
        t.join(WAIT)
        self.assertEqual(res, [('ok', None)])

    def test_no_wait_when_there_is_room(self):
        w = ytfetch.Window(500, 100, play=2000)
        self.assertEqual((w.play, w.fetched), (2000, 2000))
        w.wait_room(lambda: True)
        self.assertLess(w.waited, 0.5)


# ---------------------------------------------------------------- a Range server

class RangeServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *a):
        pass


class _H(http.server.BaseHTTPRequestHandler):
    """GET + Range -> 206. server.script: one entry per request, popped first: 'reset' (hang up without an answer),
    an int (that HTTP error) or None (serve). server.dead: paths that always answer 403."""

    def do_GET(self):  # noqa: N802
        srv = self.server
        srv.seen.append((self.path, self.headers.get('Range'), self.headers.get('X-T')))
        step = srv.script.pop(0) if srv.script else None
        if self.path in srv.dead:
            step = 403
        if step == 'reset':
            self.close_connection = True
            return
        if step:
            self.send_error(step)
            return
        s, e = (int(x) for x in self.headers['Range'].split('=')[1].split('-'))
        body = srv.data[s:e + 1]
        self.send_response(206)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionError:
            pass

    def log_message(self, *a):
        pass


DATA = random.Random(7).randbytes(60000)


class Served(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = RangeServer(('127.0.0.1', 0), _H)
        cls.srv.data = DATA
        cls.base = f'http://127.0.0.1:{cls.srv.server_address[1]}'
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.srv.seen, self.srv.script, self.srv.dead = [], [], set()
        self.addCleanup(setattr, ytfetch, 'NET_BACKOFF', ytfetch.NET_BACKOFF)
        ytfetch.NET_BACKOFF = (0.01,)
        self.w = ytfetch.Window(10 ** 9, 0)

    def source(self, path='/v', refresh=None, **kw):
        return ytfetch.Source(dict({'url': self.base + path, 'filesize': len(DATA), 'format_id': '137',
                                    'http_headers': {'X-T': 'old'}}, **kw), self.w, refresh, timeout=5)


class SourceTests(Served):
    def test_ranges_are_byte_identical(self):
        src = self.source()
        n = len(DATA)
        for s, e in ((0, 1), (0, 8), (10, 5000), (777, 59999), (n - 100, n), (0, n), (n - 1, n)):
            self.assertEqual(src.get(s, e), DATA[s:e], (s, e))
        self.assertEqual(self.srv.seen[1][1], 'bytes=0-7', 'bytes=s-(e-1)')
        self.assertEqual(self.srv.seen[0][2], 'old', 'the format\'s headers go along')
        self.assertEqual(events(lambda: src.get(0, 10)), [], 'a link that works says nothing')

    def test_retries_until_it_works_with_one_event_down_and_one_up(self):
        for mode in ('reset', 500):
            for n in (1, 3):
                with self.subTest(mode=mode, tries=n):
                    self.srv.script[:] = [mode] * n
                    self.srv.seen.clear()
                    src = self.source()
                    got = []
                    evs = events(lambda: got.append(src.get(100, 2100)))
                    self.assertEqual(got, [DATA[100:2100]])
                    self.assertEqual(len(self.srv.seen), n + 1)
                    self.assertEqual(len(evs), 2, evs)
                    self.assertEqual((evs[0]['ev'], evs[0]['ok']), ('net', False))
                    self.assertTrue(evs[0]['msg'] and isinstance(evs[0]['msg'], str))
                    self.assertEqual(evs[1], {'ev': 'net', 'ok': True, 'tries': n})
                    self.assertEqual(src.down, 0)
                    # healthy again: silent
                    self.assertEqual(events(lambda: src.get(0, 5)), [])

    def test_http_500_message_names_the_status(self):
        self.srv.script[:] = [500]
        evs = events(lambda: self.source().get(0, 10))
        self.assertIn('500', evs[0]['msg'])

    def test_backoff_steps_up_and_stays_on_the_last(self):
        waits = []
        ytfetch.NET_BACKOFF = (0.01, 0.02, 0.03)
        src = self.source()
        cv = self.w.cv

        class CV:
            def __enter__(s):
                return cv.__enter__()

            def __exit__(s, *a):
                return cv.__exit__(*a)

            def wait(s, t):
                waits.append(t)
                return True
        self.w.cv = CV()
        self.srv.script[:] = [500] * 5
        events(lambda: src.get(0, 10))
        self.assertEqual(waits, [0.01, 0.02, 0.03, 0.03, 0.03])

    def test_403_looks_the_url_up_again_and_carries_on(self):
        self.srv.dead = {'/old'}
        calls = []

        def refresh(fid):
            calls.append(fid)
            return {'url': self.base + '/new', 'filesize': len(DATA), 'http_headers': {'X-T': 'new'}}
        src = self.source('/old', refresh)
        got = []
        evs = events(lambda: got.append(src.get(5, 905)))
        self.assertEqual(got, [DATA[5:905]])
        self.assertEqual(calls, ['137'])
        self.assertEqual((src.url, src.headers['X-T'], src.refreshed), (self.base + '/new', 'new', 1))
        self.assertEqual([p for p, _, _ in self.srv.seen], ['/old', '/new'], 'at once with the new URL')
        self.assertEqual(evs, [], 'a refresh is not a down link')
        self.assertEqual(events(lambda: src.get(0, 10)), [])
        self.assertEqual(self.srv.seen[-1][0], '/new', 'and it stays on the new one')

    def test_refresh_without_filesize_or_headers_keeps_what_it_had(self):
        self.srv.dead = {'/old'}
        src = self.source('/old', lambda fid: {'url': self.base + '/new'})
        self.assertEqual(src.get(0, 10), DATA[:10])
        self.assertEqual((src.url, src.headers, src.size), (self.base + '/new', {'X-T': 'old'}, len(DATA)))

    def test_refresh_finding_another_size_does_not_switch(self):
        self.srv.dead = {'/old'}
        calls = []

        def refresh(fid):
            calls.append(fid)
            if len(calls) == 2:
                poke(self.w, gone=True)   # ytd.js leaves while the lookup keeps failing
            return {'url': self.base + '/new', 'filesize': len(DATA) + 1}
        src = self.source('/old', refresh)
        evs, err = [], []

        def run():
            try:
                src.get(0, 10)
            except OSError as e:
                err.append(e)
        evs = events(run)
        self.assertEqual(len(err), 1)
        self.assertEqual((src.url, src.refreshed), (self.base + '/old', 0), 'no switch')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('/new', [p for p, _, _ in self.srv.seen])
        self.assertEqual(len(evs), 1, evs)
        self.assertIn('format changed size', evs[0]['msg'])

    def test_refresh_that_fails_is_tried_again_with_the_link(self):
        self.srv.dead = {'/old'}
        calls = []

        def refresh(fid):
            calls.append(1)
            if len(calls) < 3:
                raise OSError('lookup: no link')
            return {'url': self.base + '/new', 'filesize': len(DATA)}
        src = self.source('/old', refresh)
        got = []
        evs = events(lambda: got.append(src.get(0, 10)))
        self.assertEqual(got, [DATA[:10]])
        self.assertEqual(len(calls), 3)
        self.assertEqual([e['ok'] for e in evs], [False, True])
        self.assertIn('lookup: no link', evs[0]['msg'])
        self.assertEqual(evs[1]['tries'], 3, 'the second and third lookups after a backoff each')
        self.assertEqual(src.dry, 0, 'a byte: the count starts again')

    def waits(self):
        """the backoff waits Source makes, without waiting"""
        waits, cv = [], self.w.cv

        class CV:
            def __enter__(s):
                return cv.__enter__()

            def __exit__(s, *a):
                return cv.__exit__(*a)

            def wait(s, t):
                waits.append(t)
                return True
        self.w.cv = CV()
        return waits

    def test_a_403_that_stays_gives_up_after_refresh_max_lookups(self):
        self.srv.dead = {'/old'}
        calls = []

        def refresh(fid):
            calls.append(fid)
            return {'url': self.base + '/old', 'filesize': len(DATA)}   # the new URL is refused too
        src = self.source('/old', refresh)
        waits, err = self.waits(), []

        def run():
            try:
                src.get(0, 10)
            except OSError as e:
                err.append(e)
        events(run)
        self.assertEqual(len(err), 1, 'given up, not looped')
        self.assertIn('HTTP 403 after 3 new lookups', str(err[0]))
        self.assertEqual(len(calls), ytfetch.REFRESH_MAX)
        self.assertEqual(len(self.srv.seen), ytfetch.REFRESH_MAX + 1)
        self.assertEqual(waits, [0.01] * (ytfetch.REFRESH_MAX - 1), 'the first lookup at once, the others after a backoff')

    def test_bytes_between_403s_start_the_count_again(self):
        self.srv.dead = {'/old'}
        calls = []
        src = self.source('/old', lambda fid: calls.append(fid) or {'url': self.base + '/new%d' % len(calls)})
        for i in range(2 * ytfetch.REFRESH_MAX):
            self.srv.dead = {src.url[len(self.base):]}   # each URL expires once it has served
            self.assertEqual(src.get(0, 10), DATA[:10], i)
        self.assertEqual(len(calls), 2 * ytfetch.REFRESH_MAX)

    def test_a_403_after_the_feed_closed_looks_nothing_up(self):
        poke(self.w, gone=True)
        self.srv.dead = {'/old'}
        calls = []
        src = self.source('/old', lambda fid: calls.append(fid) or {'url': self.base + '/new'})
        with self.assertRaises(OSError):
            src.get(0, 10)
        self.assertEqual(calls, [])

    def test_403_without_a_refresh_function_is_just_a_failed_try(self):
        self.srv.script[:] = [403, 403]
        src = self.source()
        got = []
        evs = events(lambda: got.append(src.get(0, 10)))
        self.assertEqual(got, [DATA[:10]])
        self.assertEqual([e['ok'] for e in evs], [False, True])
        self.assertEqual(src.refreshed, 0)

    def test_gone_window_ends_the_retry_loop(self):
        poke(self.w, gone=True)
        self.srv.script[:] = [500] * 1000
        src = self.source()
        err = []

        def run():
            with self.assertRaises(OSError) as c:
                src.get(0, 10)
            err.append(c.exception)
        evs = events(run)
        self.assertEqual(len(err), 1)
        self.assertEqual(evs, [], 'no event: it did not even count as down')
        self.assertEqual(len(self.srv.seen), 1)

    def test_window_closing_while_the_link_is_down_stops_it(self):
        ytfetch.NET_BACKOFF = (0.2,)
        self.srv.script[:] = [500] * 10000
        src = self.source()
        out, old = io.StringIO(), ytfetch._out
        ytfetch._out = out
        try:
            t, res = in_thread(lambda: src.get(0, 10))
            for _ in range(WAIT * 100):
                if out.getvalue():
                    break
                threading.Event().wait(0.01)
            self.assertTrue(out.getvalue(), 'it said the link is down')
            poke(self.w, gone=True)
            t.join(WAIT)
        finally:
            ytfetch._out = old
        self.assertFalse(t.is_alive())
        self.assertEqual([r[0] for r in res], ['err'])
        self.assertIsInstance(res[0][1], OSError)

    def test_short_body_is_a_failed_try(self):
        class Short(_H):
            def do_GET(self):  # noqa: N802
                self.server.n += 1
                if self.server.n == 1:
                    body = DATA[:5]
                    self.send_response(206)
                    self.send_header('Content-Length', '10')   # promises 10, sends 5
                    self.end_headers()
                    self.wfile.write(body)
                    self.close_connection = True
                    return
                _H.do_GET(self)
        srv = RangeServer(('127.0.0.1', 0), Short)
        srv.data, srv.seen, srv.script, srv.dead, srv.n = DATA, [], [], set(), 0
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        src = ytfetch.Source({'url': f'http://127.0.0.1:{srv.server_address[1]}/', 'filesize': len(DATA)}, self.w, timeout=5)
        got = []
        evs = events(lambda: got.append(src.get(0, 10)))
        self.assertEqual(got, [DATA[:10]])
        self.assertEqual([e['ok'] for e in evs], [False, True])


# ---------------------------------------------------------------- PacedReader

class _H11(_H):
    """_H over HTTP/1.1: the connection stays open between requests; server.hangup_after: close it (as an idle
    keep-alive one) after that many answers; server.redirect: a path answered 302 to server.base + '/v'."""
    protocol_version = 'HTTP/1.1'

    def do_GET(self):  # noqa: N802
        srv = self.server
        srv.conns.add(self.client_address)
        if self.path in srv.redirect:
            srv.seen.append((self.path, self.headers.get('Range'), None))
            self.send_response(302)
            self.send_header('Location', '/v')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        super().do_GET()
        srv.answers += 1
        if srv.hangup_after and srv.answers % srv.hangup_after == 0:
            self.close_connection = True


class KeepAliveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = RangeServer(('127.0.0.1', 0), _H11)
        cls.srv.data = DATA
        cls.base = f'http://127.0.0.1:{cls.srv.server_address[1]}'
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.srv.seen, self.srv.script, self.srv.dead, self.srv.redirect = [], [], set(), set()
        self.srv.conns, self.srv.answers, self.srv.hangup_after = set(), 0, 0
        self.addCleanup(setattr, ytfetch, 'NET_BACKOFF', ytfetch.NET_BACKOFF)
        ytfetch.NET_BACKOFF = (0.01,)

    def src(self, path='/v'):
        return ytfetch.Source({'url': self.base + path, 'filesize': len(DATA)}, ytfetch.Window(10 ** 9, 0))

    def test_one_connection_for_many_requests(self):
        s = self.src()
        got = b''.join(s.get(o, min(o + 7000, len(DATA))) for o in range(0, len(DATA), 7000))
        self.assertEqual(got, DATA)
        self.assertEqual(s.connections, 1, 'kept open')
        self.assertEqual(len(self.srv.conns), 1)

    def test_a_kept_connection_the_server_closed_is_opened_again_silently(self):
        self.srv.hangup_after = 2           # as an idle keep-alive connection the server drops
        s = self.src()
        ev = events(lambda: self.assertEqual(b''.join(s.get(o, o + 5000) for o in range(0, 30000, 5000)), DATA[:30000]))
        self.assertEqual(ev, [], 'not the link down')
        self.assertGreaterEqual(s.connections, 3)

    def test_a_redirect_is_followed_and_kept(self):
        self.srv.redirect = {'/moved'}
        s = self.src('/moved')
        self.assertEqual(s.get(100, 300), DATA[100:300])
        self.assertTrue(s.url.endswith('/v'), s.url)
        self.assertEqual(s.get(300, 500), DATA[300:500])
        self.assertEqual([p for p, _, _ in self.srv.seen], ['/moved', '/v', '/v'])

    def test_403_on_a_kept_connection_still_looks_up_again(self):
        s = ytfetch.Source({'url': self.base + '/old', 'filesize': len(DATA)}, ytfetch.Window(10 ** 9, 0),
                           refresh=lambda fid: {'url': self.base + '/v', 'filesize': len(DATA)})
        self.srv.dead = {'/old'}
        self.assertEqual(events(lambda: self.assertEqual(s.get(0, 100), DATA[:100])), [])
        self.assertEqual(s.refreshed, 1)


class PacedReaderTests(Served):
    def test_from_a_nonzero_start_it_returns_exactly_the_tail(self):
        start = 12345
        src = self.source()
        rd = ytfetch.PacedReader(src, start, self.w, lambda: True, chunk=4096)
        out = bytearray()
        while True:
            b = rd.read(1000)
            if not b:
                break
            out += b
        self.assertEqual(bytes(out), DATA[start:])
        self.assertEqual(rd.bytes, len(DATA) - start)
        self.assertEqual(self.srv.seen[0][1], f'bytes={start}-{start + 4095}')
        self.assertEqual(self.srv.seen[-1][1], f'bytes={start + 4096 * ((len(DATA) - start - 1) // 4096)}-{len(DATA) - 1}',
                         'the last range ends at the file\'s end')
        self.assertEqual(rd.read(10), b'', 'and stays at EOF')

    def test_reads_larger_than_a_chunk_and_exact_boundaries(self):
        rd = ytfetch.PacedReader(self.source(), 0, self.w, lambda: True, chunk=1000)
        got = rd.read(2500) + rd.read(500) + rd.read(1) + rd.read(10 ** 6)
        self.assertEqual(got, DATA)

    def test_says_before_each_request_and_each_wait_never_from_the_buffer(self):
        w = self.w = ytfetch.Window(500, 0)
        said, saying = [], threading.Event()

        def before():
            said.append(len(self.srv.seen))
            saying.set()
        rd = ytfetch.PacedReader(self.source(), 300, w, lambda: True, chunk=1000, before_wait=before)
        self.assertEqual(rd.read(10), DATA[300:310])
        self.assertEqual((len(self.srv.seen), said), (1, [0]), 'room: said before the request (a get blocks too)')
        w.fetched = 1000                       # 1000 past the play position 0: full
        saying.clear()
        self.assertEqual(rd.read(990), DATA[310:1300], 'the buffer needs no request, so no wait either')
        self.assertEqual((len(self.srv.seen), said), (1, [0]))
        t, res = in_thread(lambda: rd.read(10))
        self.assertTrue(saying.wait(WAIT), 'before_wait is called when it must wait')
        t.join(0.4)
        self.assertTrue(t.is_alive(), 'waiting for room')
        self.assertEqual(len(self.srv.seen), 1, 'no new Range request while full')
        poke(w, play=900)                      # 100 ahead of play: room
        t.join(WAIT)
        self.assertEqual(res, [('ok', DATA[1300:1310])])
        self.assertEqual(len(self.srv.seen), 2)
        self.assertEqual(said, [0, 1], 'once more, before the wait and its request')

    def test_the_first_range_has_its_own_size(self):
        rd = ytfetch.PacedReader(self.source(), 300, self.w, lambda: True, chunk=1000, first=150)
        self.assertEqual(rd.read(2000), DATA[300:2300])
        self.assertEqual([r for _, r, _ in self.srv.seen], ['bytes=300-449', 'bytes=450-1449', 'bytes=1450-2449'])

    def test_on_wait_runs_while_full(self):
        w = self.w = ytfetch.Window(500, 0)
        w.fetched = 10 ** 6
        ran = threading.Event()
        rd = ytfetch.PacedReader(self.source(), 0, w, lambda: True, chunk=1000, on_wait=ran.set)
        t, res = in_thread(lambda: rd.read(10))
        self.assertTrue(ran.wait(WAIT), 'the trimmer gets its turn while nothing is written')
        poke(w, fetched=0)
        t.join(WAIT)
        self.assertEqual(res, [('ok', DATA[:10])])

    def test_free_ram_gates_it_too(self):
        free, said = [False], threading.Event()
        rd = ytfetch.PacedReader(self.source(), 0, self.w, lambda: free[0], chunk=1000, before_wait=said.set)
        t, res = in_thread(lambda: rd.read(10))
        self.assertTrue(said.wait(WAIT))
        t.join(0.3)
        self.assertTrue(t.is_alive())
        self.assertEqual(self.srv.seen, [])
        free[0] = True
        poke(self.w)
        t.join(WAIT)
        self.assertEqual(res, [('ok', DATA[:10])])

    def test_window_gone_while_waiting_raises(self):
        w = self.w = ytfetch.Window(500, 0)
        w.fetched = 10 ** 6
        rd = ytfetch.PacedReader(self.source(), 0, w, lambda: True, chunk=1000)
        t, res = in_thread(lambda: rd.read(10))
        t.join(0.3)
        poke(w, gone=True)
        t.join(WAIT)
        self.assertEqual([r[0] for r in res], ['err'])
        self.assertIsInstance(res[0][1], OSError)


# ---------------------------------------------------------------- Prefixed

class PrefixedTests(unittest.TestCase):
    def test_prefix_then_inner_across_boundaries(self):
        prefix, body = b'abcdefg', bytes(range(100, 150))
        for n in (1, 2, 3, 6, 7, 8, 9, 13, 57, 58, 1000):
            with self.subTest(n=n):
                p = ytfetch.Prefixed(prefix, io.BytesIO(body))
                out, first = bytearray(), p.read(n)
                self.assertEqual(first, (prefix + body)[:n], 'the first read straddles the boundary when n > 7')
                out += first
                while True:
                    b = p.read(n)
                    if not b:
                        break
                    self.assertLessEqual(len(b), n)
                    out += b
                self.assertEqual(bytes(out), prefix + body)
                self.assertEqual(p.read(n), b'')

    def test_empty_prefix_and_empty_inner(self):
        self.assertEqual(ytfetch.Prefixed(b'', io.BytesIO(b'xyz')).read(10), b'xyz')
        p = ytfetch.Prefixed(b'ab', io.BytesIO(b''))
        self.assertEqual((p.read(1), p.read(5), p.read(5)), (b'a', b'b', b''))

    def test_demux_style_box_reads(self):
        """read_box on a Prefixed: a box header split across the prefix and the stream"""
        moov = struct.pack('>I4s', 12, b'moov') + b'ABCD'
        mdat = struct.pack('>I4s', 11, b'mdat') + b'xyz'
        p = ytfetch.Prefixed(moov[:3] + b'', io.BytesIO(moov[3:] + mdat))
        self.assertEqual(ytfetch.read_box(p), (b'moov', moov))
        self.assertEqual(ytfetch.read_box(p), (b'mdat', mdat))
        self.assertIsNone(ytfetch.read_box(p))


# ---------------------------------------------------------------- parse_sidx

def sidx_box(version, ts, ept, first, refs):
    """refs: [(reference_type, size, duration, starts_with_sap, sap_type)]"""
    body = struct.pack('>II', 1, ts)
    body += struct.pack('>QQ' if version else '>II', ept, first)
    body += struct.pack('>HH', 0, len(refs))
    for rt, size, dur, sap, st in refs:
        body += struct.pack('>III', rt << 31 | size, dur, sap << 31 | st << 28)
    return ytfetch.full(b'sidx', version << 24, body)


class SidxTests(unittest.TestCase):
    REFS = [(0, 1000, 90000, 1, 1), (0, 2500, 45000, 1, 1), (0, 70000, 123456, 0, 0), (0, 0x7fffffff, 1, 1, 2)]
    WANT = [(1000, 90000, 1), (2500, 45000, 1), (70000, 123456, 0), (0x7fffffff, 1, 1)]

    def test_version_0(self):
        b = sidx_box(0, 90000, 12345, 678, self.REFS)
        self.assertEqual(ytfetch.parse_sidx(b), (90000, 12345, 678, self.WANT))

    def test_version_1_with_64_bit_fields(self):
        b = sidx_box(1, 12800, 5 << 33, 1 << 33, self.REFS)
        self.assertEqual(ytfetch.parse_sidx(b), (12800, 5 << 33, 1 << 33, self.WANT))

    def test_no_references(self):
        self.assertEqual(ytfetch.parse_sidx(sidx_box(0, 1000, 0, 0, [])), (1000, 0, 0, []))

    def test_a_hierarchical_index_is_refused(self):
        b = sidx_box(0, 1000, 0, 0, [(0, 10, 1, 1, 1), (1, 10, 1, 1, 1)])
        with self.assertRaises(ValueError):
            ytfetch.parse_sidx(b)


# ---------------------------------------------------------------- a real fMP4 with a sidx

def ff(*args):
    subprocess.run([FF, '-v', 'error', '-y', *args], check=True)


def read_index(path):
    """(header bytes, [(pts_ms, pos, size, key)]) of an index.bin"""
    with open(path, 'rb') as f:
        b = f.read()
    n = struct.unpack_from('<I', b, 16)[0]
    return b[:ytfetch.HDR.size], [ytfetch.REC.unpack_from(b, ytfetch.HDR.size + i * ytfetch.REC.size) for i in range(n)]


def streamable(data):
    """ffmpeg's fragmented MP4 has no sidx: rebuild it as ftyp, moov, a version-0 sidx (one reference per
    moof+mdat, SAP type 1), then the fragments. Returns (file, info): info has moov, ts, firsts (each
    fragment's first dts), counts (samples per fragment), base (min dts+cto of the first fragment), offsets
    (each moof's position in file)."""
    tops = [(t, s - 8, e) for t, s, e in ytfetch.boxes(data)]
    moov_at, moov_end = next((s, e) for t, s, e in tops if t == b'moov')
    moov = data[moov_at:moov_end]
    tk = ytfetch.trak_info(moov)
    frags = []         # (moof start, moof end, mdat end)
    for i, (t, s, e) in enumerate(tops):
        if t == b'moof':
            assert tops[i + 1][0] == b'mdat' and tops[i + 1][1] == e
            frags.append((s, e, tops[i + 1][2]))
    firsts, counts, dts, base = [], [], 0, None
    for s, e, _ in frags:
        samples, dts_end = ytfetch.parse_moof(data[s:e], dts, tk['trex'])
        if base is None:
            base = min(x[2] + x[3] for x in samples)
        firsts.append(samples[0][2])
        counts.append(len(samples))
        dts = dts_end
    durs = [b - a for a, b in zip(firsts, firsts[1:] + [dts])]
    sizes = [m - s for s, _, m in frags]
    refs = b''.join(struct.pack('>III', sz, d, 1 << 31 | 1 << 28) for sz, d in zip(sizes, durs))
    sidx = ytfetch.full(b'sidx', 0, struct.pack('>IIIIHH', 1, tk['ts'], firsts[0], 0, 0, len(frags)), refs)
    head = data[:moov_end] + sidx
    out = head + data[frags[0][0]:frags[-1][2]]
    offsets = [len(head) + s - frags[0][0] for s, _, _ in frags]
    return out, dict(moov=moov, ts=tk['ts'], firsts=firsts, counts=counts, base=base, offsets=offsets)


@unittest.skipUnless(FF, 'needs ffmpeg')
class Fragmented(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=TMP)
        src, v = os.path.join(cls.tmp, 'src.mp4'), os.path.join(cls.tmp, 'v.mp4')
        ff('-f', 'lavfi', '-i', 'testsrc2=size=426x240:rate=30', '-frames:v', '600', '-c:v', 'libx264',
           '-preset', 'veryfast', '-bf', '2', '-g', '30', '-pix_fmt', 'yuv420p', '-an', *FRAG, v)
        with open(v, 'rb') as f:
            raw = f.read()
        cls.raw_boxes = [t for t, _, _ in ytfetch.boxes(raw)]
        cls.file, cls.info = streamable(raw)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def sub(self):
        return tempfile.mkdtemp(dir=self.tmp)

    def layout(self):
        return ytfetch.stream_layout(lambda s, e: self.file[s:e])

    def test_fixture_shape(self):
        self.assertEqual(self.raw_boxes[:2], [b'ftyp', b'moov'])
        self.assertNotIn(b'sidx', self.raw_boxes, 'ffmpeg writes none: that is why the test builds one')
        self.assertEqual(len(self.info['offsets']), 20)
        self.assertEqual(set(self.info['counts']), {30})

    def test_stream_layout(self):
        lay, info = self.layout(), self.info
        self.assertEqual(lay['init'], info['moov'])
        self.assertEqual(lay['end'], len(self.file))
        self.assertEqual(lay['base'], info['base'])
        self.assertEqual([o for o, _ in lay['frags']], info['offsets'])
        ms = [m for _, m in lay['frags']]
        self.assertEqual(ms[0], 0)
        self.assertTrue(all(a < b for a, b in zip(ms, ms[1:])), ms)
        self.assertEqual(ms, [max(0, round((d - info['base']) * 1000 / info['ts'])) for d in info['firsts']])
        for o, _ in lay['frags']:
            self.assertEqual(self.file[o + 4:o + 8], b'moof')

    def test_stream_layout_with_a_small_head_window_and_a_tiny_get(self):
        """the boxes straddle the first read: it asks for more"""
        with mock.patch.object(ytfetch, 'HEAD_BYTES', 300):
            lay = self.layout()
        self.assertEqual((lay['init'], lay['end']), (self.info['moov'], len(self.file)))
        self.assertEqual([o for o, _ in lay['frags']], self.info['offsets'])

    def test_stream_layout_none_without_a_sidx_or_with_a_wrong_one(self):
        info = self.info
        sidx_at = self.file.index(b'sidx') - 4
        moof_at = info['offsets'][0]
        nosidx = self.file[:sidx_at] + self.file[moof_at:]
        self.assertIsNone(ytfetch.stream_layout(lambda s, e: nosidx[s:e]))
        # a reference that does not start with a SAP: not a place to start from
        bad = bytearray(self.file)
        bad[sidx_at + 32 + 8] &= 0x7f   # the first reference's SAP bit
        self.assertIsNone(ytfetch.stream_layout(lambda s, e: bytes(bad[s:e])))
        # a timescale that is not the track's
        bad = bytearray(self.file)
        struct.pack_into('>I', bad, sidx_at + 16, info['ts'] + 1)
        self.assertIsNone(ytfetch.stream_layout(lambda s, e: bytes(bad[s:e])))

    def demux(self, f, base=None):
        d = self.sub()
        seen = []
        r = ytfetch.demux_video(f, d, None, lambda *x: seen.append(x), base=base)
        hdr, recs = read_index(os.path.join(d, 'index.bin'))
        with open(os.path.join(d, 'video.h264'), 'rb') as g:
            return r, hdr, recs, g.read(), seen

    def test_demux_from_a_fragment_matches_the_tail_of_the_whole(self):
        lay, info = self.layout(), self.info
        (n1, size1, ms1), hdr1, r1, v1, _ = self.demux(io.BytesIO(self.file))
        self.assertEqual((n1, len(r1), size1, len(v1)), (600, 600, len(v1), len(v1)))
        for k in (0, 1, 9, len(lay['frags']) // 2, len(lay['frags']) - 1):
            with self.subTest(fragment=k):
                off = lay['frags'][k][0]
                f = ytfetch.Prefixed(lay['init'], io.BytesIO(self.file[off:]))
                (n2, size2, _), hdr2, r2, v2, seen = self.demux(f, base=lay['base'])
                j = sum(info['counts'][:k])
                self.assertEqual(n2, len(r2))
                self.assertEqual(n2, len(r1) - j)
                self.assertTrue(r2[0][3], 'starts on a keyframe')
                self.assertEqual(r2[0][1], 0)
                self.assertEqual(r2[0][0], r1[j][0], 'the same ms as from 0')
                self.assertEqual([(m, s, key) for m, _, s, key in r2], [(m, s, key) for m, _, s, key in r1[j:]])
                shift = r1[j][1]
                self.assertEqual([p + shift for _, p, _, _ in r2], [p for _, p, _, _ in r1[j:]], 'pos differs by a constant')
                self.assertEqual(v2, v1[shift:], 'the same bytes from that record on')
                self.assertEqual(hdr2[:16] + hdr2[20:], hdr1[:16] + hdr1[20:], 'the same header but the frame count')
                self.assertEqual(seen[-1], (max(m for m, _, _, _ in r2), n2, size2))

    def test_demux_from_the_first_fragment_equals_from_zero(self):
        lay = self.layout()
        _, hdr1, r1, v1, _ = self.demux(io.BytesIO(self.file))
        off = lay['frags'][0][0]
        _, hdr2, r2, v2, _ = self.demux(ytfetch.Prefixed(lay['init'], io.BytesIO(self.file[off:])), base=lay['base'])
        self.assertEqual((hdr2, r2, v2), (hdr1, r1, v1))

    def test_layout_base_is_what_demux_takes_from_zero(self):
        """the first record's ms is 0 from 0, so base is the first fragment's earliest pts; without base a
        stream starting at fragment k would number its frames from 0 instead"""
        lay = self.layout()
        _, _, r1, _, _ = self.demux(io.BytesIO(self.file))
        self.assertEqual(min(r[0] for r in r1), 0)
        k = 5
        off = lay['frags'][k][0]
        _, _, r_nobase, _, _ = self.demux(ytfetch.Prefixed(lay['init'], io.BytesIO(self.file[off:])))
        self.assertEqual(r_nobase[0][0], 0)
        self.assertNotEqual(r1[sum(self.info['counts'][:k])][0], 0)


# ---------------------------------------------------------------- Trimmer

PUNCH = ytfetch.punch_fn()


@unittest.skipUnless(PUNCH, 'no fallocate through libc here')
class TrimmerTests(unittest.TestCase):
    KEY = 512 << 10      # bytes between keyframes
    N = 16               # keyframes, 1 s apart: 8 MiB

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=TMP)
        self.addCleanup(shutil.rmtree, self.tmp)
        if not ytfetch.can_punch(self.tmp, PUNCH):
            self.skipTest('this file system does not free punched blocks')
        self.path = os.path.join(self.tmp, 'video.h264')
        self.data = random.Random(3).randbytes(self.N * self.KEY)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, self.fd)
        os.write(self.fd, self.data)
        os.fsync(self.fd)

    def trimmer(self, behind=2000, play=0):
        w = ytfetch.Window(60000, behind, play)
        t = ytfetch.Trimmer(w, PUNCH)
        for i in range(self.N):
            t.key(i * 1000, i * self.KEY)
        return w, t

    def used(self):
        os.fsync(self.fd)
        return os.fstat(self.fd).st_blocks * 512

    def test_punches_up_to_the_last_keyframe_behind_the_play_position(self):
        w, t = self.trimmer(behind=2000)
        before = self.used()
        self.assertEqual(os.fstat(self.fd).st_size, self.N * self.KEY)
        w.play = 10000      # cut = 8000 ms: the keyframe at 8000 ms is the last at or before it
        evs = events(lambda: t.after_write(self.fd))
        self.assertEqual(evs, [{'ev': 'trim', 'ms': 8000, 'bytes': 8 * self.KEY}])
        self.assertEqual((t.done, t.ms), (8 * self.KEY, 8000))
        self.assertEqual(t.keys[0], (8000, 8 * self.KEY), 'keys before it are forgotten')
        self.assertEqual(os.fstat(self.fd).st_size, self.N * self.KEY, 'offsets stay')
        after = self.used()
        self.assertLess(after, before)
        self.assertLessEqual(after, before - 8 * self.KEY + (64 << 10), 'the 4 MiB behind are freed')
        self.assertEqual(os.pread(self.fd, 4096, 0), bytes(4096), 'a hole reads as zeros')
        self.assertEqual(os.pread(self.fd, 8 * self.KEY, 0), bytes(8 * self.KEY))
        self.assertEqual(os.pread(self.fd, 4 * self.KEY, 8 * self.KEY), self.data[8 * self.KEY:12 * self.KEY],
                         'and what is at and past the keyframe is intact')

    def test_does_nothing_below_trim_min_or_before_the_first_keyframe_is_behind(self):
        w, t = self.trimmer(behind=2000)
        before = self.used()
        for play in (0, 1500, 2000, 2999):          # cut <= 999: the keyframe at 0 is no distance from 0
            w.play = play
            self.assertEqual(events(lambda: t.after_write(self.fd)), [], play)
        w.play = 3000                               # cut 1000: key at 1000 is 512 KiB < TRIM_MIN
        self.assertEqual(events(lambda: t.after_write(self.fd)), [])
        self.assertLess(self.KEY, ytfetch.TRIM_MIN)
        self.assertEqual((t.done, t.ms), (0, 0))
        self.assertEqual(self.used(), before)
        w.play = 4000                               # cut 2000: key at 2000 is exactly TRIM_MIN from 0: punch
        self.assertEqual(events(lambda: t.after_write(self.fd)), [{'ev': 'trim', 'ms': 2000, 'bytes': 1 << 20}])
        self.assertEqual(self.KEY * 2, ytfetch.TRIM_MIN)

    def test_next_punch_waits_for_trim_min_more(self):
        w, t = self.trimmer(behind=2000)
        w.play = 10000
        events(lambda: t.after_write(self.fd))      # done = 4 MiB at 8000 ms
        w.play = 11000                              # cut 9000: 512 KiB past done
        self.assertEqual(events(lambda: t.after_write(self.fd)), [])
        w.play = 12000                              # cut 10000: 1 MiB past done
        self.assertEqual(events(lambda: t.after_write(self.fd)), [{'ev': 'trim', 'ms': 10000, 'bytes': 10 * self.KEY}])
        self.assertEqual(os.pread(self.fd, 10 * self.KEY, 0), bytes(10 * self.KEY))
        self.assertEqual(os.pread(self.fd, self.KEY, 10 * self.KEY), self.data[10 * self.KEY:11 * self.KEY])
        w.play = 12000                              # again with nothing new: silent
        self.assertEqual(events(lambda: t.after_write(self.fd)), [])

    def test_poll_trims_while_waiting_once_something_was_written(self):
        w, t = self.trimmer(behind=2000)
        w.play = 10000
        self.assertEqual(events(t.poll), [], 'nothing written yet: nothing to punch with')
        w.play = 0
        self.assertEqual(events(lambda: t.after_write(self.fd)), [])
        w.play = 10000                              # played on while the window waits (no write)
        self.assertEqual(events(t.poll), [{'ev': 'trim', 'ms': 8000, 'bytes': 8 * self.KEY}])

    def test_no_keys_yet_is_a_noop_and_the_play_position_ahead_of_the_keys_uses_the_last(self):
        w = ytfetch.Window(60000, 2000, 99999)
        t = ytfetch.Trimmer(w, PUNCH)
        self.assertEqual(events(lambda: t.after_write(self.fd)), [])
        w2, t2 = self.trimmer(behind=0)
        w2.play = 10 ** 6                            # far past the last key (15000 ms at 7.5 MiB)
        evs = events(lambda: t2.after_write(self.fd))
        self.assertEqual(evs, [{'ev': 'trim', 'ms': 15000, 'bytes': 15 * self.KEY}])


# ---------------------------------------------------------------- plan_stream

MiB = 1 << 20


class FakeYt:
    """a yt-dlp format selector: process_ie_result(info, download) -> a dict"""

    def __init__(self, fmt, wrap=False):
        self.fmt, self.wrap, self.calls = fmt, wrap, []

    def process_ie_result(self, info, download):
        assert download is False
        self.calls.append(info)
        if isinstance(self.fmt, Exception):
            raise self.fmt
        f = dict(self.fmt)
        return {'requested_downloads': [f]} if self.wrap else f


class PlanTests(unittest.TestCase):
    """The decision only: the layout, the punching probe, the free space and MemAvailable are stand-ins."""
    AHEAD, BEHIND, DUR = 120000, 45000, 100

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=TMP)
        self.addCleanup(shutil.rmtree, self.tmp)
        self.dir = os.path.join(self.tmp, 'cache-id')   # <cache>/<id>, on flash
        self.ram = os.path.join(self.tmp, 'ram-id')
        os.mkdir(self.dir)
        os.mkdir(self.ram)
        self.info = {'id': 'x', 'duration': self.DUR, 'formats': []}
        self.lay = {'init': b'moov', 'base': 0, 'frags': [(100, 0), (5000, 5000)], 'end': 10 * MiB, 'webm': False}
        self.free = {self.ram: 256 * MiB}
        self.mem, self.punchable, self.probed = 2048 * MiB, {self.ram, os.path.join(self.dir, 'win')}, []
        for name, fn in (('punch_fn', lambda: (lambda fd, off, n: None)),
                         ('can_punch', lambda d, punch: d in self.punchable),
                         ('ram_free', lambda d: self.free.get(d, 10 ** 12)),
                         ('mem_available', lambda: self.mem),
                         ('stream_layout', lambda get: self.layout('mp4')),
                         ('webm_layout', lambda get, size: self.layout('webm'))):
            patcher = mock.patch.object(ytfetch, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def layout(self, kind):
        self.probed.append(kind)
        if isinstance(self.lay, Exception):
            raise self.lay
        return self.lay

    def args(self, **kw):
        return types.SimpleNamespace(**dict(dict(stream=(self.AHEAD, self.BEHIND), dir=self.dir, video_dir=self.ram,
                                                 budget=256 * MiB, start_ms=0, video_only=False), **kw))

    def vfmt(self, **kw):
        return dict({'format_id': '137', 'protocol': 'https', 'url': 'https://v/x', 'filesize': 10 * MiB, 'ext': 'mp4'}, **kw)

    def plan(self, a, fv=None, wrap=False, stdin='pos 5\n'):
        yv = FakeYt(fv or self.vfmt(), wrap)
        res = []
        with mock.patch.object(sys, 'stdin', io.StringIO(stdin)):
            evs = events(lambda: res.append(ytfetch.plan_stream(a, None, 'https://www.youtube.com/watch?v=x', yv, self.info)))
        return res[0], evs, yv

    def need(self, ahead, behind, vsize=10 * MiB):
        bps = int(vsize / self.DUR)
        return int(bps * (ahead + behind) // 1000 + 3 * ytfetch.VIDEO_CHUNK + ytfetch.RAM_FLOOR)

    def assertSpills(self, res, evs, why):
        self.assertIsNone(res)
        self.assertEqual(len(evs), 1, evs)
        self.assertEqual((evs[0]['ev'], evs[0]['dir']), ('spill', self.dir), 'the whole video into <dir>, as before')
        self.assertTrue(evs[0]['why'].startswith(why), evs[0]['why'])

    def assertFlash(self, res, why):
        self.assertEqual((res['dir'], res['ram']), (os.path.join(self.dir, 'win'), False))
        self.assertTrue(os.path.isdir(res['dir']), 'the flash window folder is made')
        self.assertTrue(res['ram_why'].startswith(why), res['ram_why'])

    def test_streams_in_ram_when_it_fits(self):
        for wrap in (False, True):
            with self.subTest(requested_downloads=wrap):
                res, evs, yv = self.plan(self.args(), wrap=wrap)
                self.assertEqual(evs, [], 'plan_stream says nothing when it streams (fetch_stream does)')
                w = res['w']
                self.assertEqual((res['dir'], res['ram'], res['ram_why']), (self.ram, True, None))
                self.assertEqual((w.ahead, w.behind, w.fetched), (self.AHEAD, self.BEHIND, 0), '0.1 MiB/s: not cut')
                self.assertEqual(res['need'], self.need(self.AHEAD, self.BEHIND))
                self.assertIs(res['lay'], self.lay)
                self.assertEqual(res['src'].size, 10 * MiB)
                self.assertTrue(callable(res['punch']))
                self.assertEqual(len(yv.calls), 1)
                self.assertIsNot(yv.calls[0], self.info, 'works on a copy')
                with w.cv:
                    self.assertTrue(w.cv.wait_for(lambda: w.gone, WAIT), 'listening to stdin')
                self.assertEqual(w.play, 5)
                self.assertFalse(os.path.exists(os.path.join(self.dir, 'win')))

    def test_the_window_is_capped_in_bytes_at_the_formats_bitrate(self):
        # 1 MiB/s (1080p-ish): behind 16 MiB = 16 s, ahead the rest of 48 MiB = 32 s
        self.lay = dict(self.lay, end=100 * MiB)
        res, _, _ = self.plan(self.args(), fv=self.vfmt(filesize=100 * MiB))
        w = res['w']
        self.assertEqual((w.ahead, w.behind), (32000, 16000))
        self.assertEqual(res['need'], 48 * MiB + 3 * ytfetch.VIDEO_CHUNK + ytfetch.RAM_FLOOR)
        # 0.3125 MiB/s (720p): behind 45 s fits under 16 MiB, ahead cut to what is left of 48 MiB
        a, b = ytfetch.window_ms(120000, 45000, int(0.3125 * MiB))
        self.assertEqual(b, 45000)
        self.assertEqual(a, (48 * MiB - 45000 * int(0.3125 * MiB) // 1000) * 1000 // int(0.3125 * MiB))
        self.assertLess(a, 120000)
        # 4 MiB/s: ahead never under AHEAD_MIN_MS (a play starts at 6 s fetched)
        self.assertEqual(ytfetch.window_ms(120000, 45000, 4 * MiB), (ytfetch.AHEAD_MIN_MS, 4000))
        # a slow one: as asked
        self.assertEqual(ytfetch.window_ms(120000, 45000, 100 << 10), (120000, 45000))

    def test_start_ms_is_the_windows_start(self):
        res, _, _ = self.plan(self.args(start_ms=42000), stdin='')
        self.assertEqual((res['w'].play, res['w'].fetched), (42000, 42000))

    def test_low_memory_streams_the_window_to_flash(self):
        self.mem = ytfetch.MEM_FLOOR - 1
        res, evs, _ = self.plan(self.args())
        self.assertEqual(evs, [])
        self.assertFlash(res, '%d MiB of memory available' % ((ytfetch.MEM_FLOOR - 1) >> 20))
        self.mem = ytfetch.MEM_FLOOR                      # exactly: RAM
        self.assertTrue(self.plan(self.args())[0]['ram'])
        self.mem = None                                   # no /proc/meminfo: the tmpfs cap decides
        self.assertTrue(self.plan(self.args())[0]['ram'])

    def test_no_ram_folder_streams_the_window_to_flash(self):
        res, _, _ = self.plan(self.args(video_dir=None))
        self.assertFlash(res, 'no RAM folder')

    def test_past_the_budget_or_the_free_ram_streams_to_flash(self):
        need = self.need(self.AHEAD, self.BEHIND)
        res, _, _ = self.plan(self.args(budget=need - 1))
        self.assertFlash(res, 'needs ')
        self.assertTrue(self.plan(self.args(budget=need))[0]['ram'], 'exactly: fits')
        self.free[self.ram] = need - 1
        self.assertFlash(self.plan(self.args(budget=0))[0], 'needs ')
        self.free[self.ram] = need
        self.assertTrue(self.plan(self.args(budget=0))[0]['ram'])

    def test_no_hole_punching_in_ram_goes_to_flash_and_nowhere_spills(self):
        self.punchable.discard(self.ram)
        self.assertFlash(self.plan(self.args())[0], 'no hole punching on ' + self.ram)
        self.punchable.clear()
        res, evs, _ = self.plan(self.args())
        self.assertSpills(res, evs, 'no hole punching on ' + os.path.join(self.dir, 'win'))

    def test_spills_without_ranges_or_url_or_filesize_or_duration_without_asking_for_the_index(self):
        cases = [('protocol', self.vfmt(protocol='m3u8_native'), 'video not fetchable in ranges'),
                 ('filesize', self.vfmt(filesize=None), 'video not fetchable in ranges'),
                 ('url', self.vfmt(url=None), 'video not fetchable in ranges'),
                 ('formats', ValueError('no such format'), 'formats: no such format')]
        for name, fv, why in cases:
            with self.subTest(name):
                res, evs, _ = self.plan(self.args(), fv=fv)
                self.assertSpills(res, evs, why)
        self.info['duration'] = None
        res, evs, _ = self.plan(self.args())
        self.assertSpills(res, evs, 'no duration')
        self.assertEqual(self.probed, [])

    def test_no_sidx_or_no_cues_spills_never_fails(self):
        self.lay = None
        res, evs, _ = self.plan(self.args())
        self.assertSpills(res, evs, 'no fragment index (sidx)')
        res, evs, _ = self.plan(self.args(), fv=self.vfmt(ext='webm'))
        self.assertSpills(res, evs, 'no cues')
        self.assertEqual(self.probed, ['mp4', 'webm'])
        self.lay = ValueError('vp9: truncated')               # an index it cannot read: the same
        self.assertSpills(*self.plan(self.args(), fv=self.vfmt(ext='webm'))[:2], 'no cues')
        self.lay = {'init': b'', 'base': 0, 'frags': [(0, 0)], 'end': 10 * MiB - 1, 'webm': False}
        self.assertSpills(*self.plan(self.args())[:2], 'no fragment index')

    def test_a_webm_format_streams_from_its_cues(self):
        self.lay = dict(self.lay, webm=True)
        res, evs, _ = self.plan(self.args(), fv=self.vfmt(ext='webm', vcodec='vp9'))
        self.assertEqual((evs, self.probed, res['ram']), ([], ['webm'], True))

    def test_http_counts_as_ranged(self):
        self.assertIsNotNone(self.plan(self.args(), fv=self.vfmt(protocol='http'))[0])

    def test_ram_free_of_a_missing_dir_is_zero(self):
        mock.patch.stopall()
        self.assertEqual(ytfetch.ram_free(os.path.join(self.tmp, 'nope')), 0)
        self.assertGreater(ytfetch.ram_free(self.tmp), 0)


class MemTests(unittest.TestCase):
    def test_mem_available_reads_proc_meminfo(self):
        d = tempfile.mkdtemp(dir=TMP)
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 'meminfo')
        with open(p, 'w') as f:
            f.write('MemTotal:        5832000 kB\nMemFree:          100000 kB\nMemAvailable:    2000000 kB\n')
        with mock.patch.object(ytfetch, 'MEMINFO', p):
            self.assertEqual(ytfetch.mem_available(), 2000000 << 10)
            self.assertTrue(ytfetch.mem_ok())
            with open(p, 'w') as f:
                f.write('MemAvailable:     500000 kB\n')
            self.assertFalse(ytfetch.mem_ok(), 'under MEM_FLOOR')
        with mock.patch.object(ytfetch, 'MEMINFO', os.path.join(d, 'none')):
            self.assertIsNone(ytfetch.mem_available())
            self.assertTrue(ytfetch.mem_ok(), 'unknown: not a reason to wait')


# ---------------------------------------------------------------- VP9: a WebM's Cues

def vp9(out, *extra):
    ff('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-frames:v', '360', '-c:v', 'libvpx-vp9', '-deadline',
       'realtime', '-cpu-used', '8', '-b:v', '200k', '-g', '30', '-an', *extra, out)
    with open(out, 'rb') as f:
        return f.read()


@unittest.skipUnless(FF, 'needs ffmpeg')
class Webm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=TMP)
        cls.front = vp9(os.path.join(cls.tmp, 'front.webm'), '-cues_to_front', '1')   # Cues before the clusters (YouTube's)
        cls.back = vp9(os.path.join(cls.tmp, 'back.webm'))                            # Cues at the end: the SeekHead says where
        cls.live = vp9(os.path.join(cls.tmp, 'live.webm'), '-live', '1')               # no Cues at all

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def demux(self, f, base=None):
        d = tempfile.mkdtemp(dir=self.tmp)
        r = ytfetch.demux_webm(f, d, None, lambda *x: None, base=base)
        hdr, recs = read_index(os.path.join(d, 'index.bin'))
        with open(os.path.join(d, 'video.h264'), 'rb') as g:
            return r, hdr, recs, g.read()

    def test_layout_from_front_or_back_cues(self):
        for name in ('front', 'back'):
            with self.subTest(name):
                data = getattr(self, name)
                gets = []
                lay = ytfetch.webm_layout(lambda s, e: gets.append((s, e)) or data[s:e], len(data))
                self.assertEqual((lay['end'], lay['webm'], lay['base']), (len(data), True, 0))
                ms = [m for _, m in lay['frags']]
                self.assertEqual(ms[0], 0)
                self.assertTrue(all(a < b for a, b in zip(ms, ms[1:])), ms)
                self.assertGreaterEqual(len(ms), 2)
                for o, _ in lay['frags']:
                    self.assertEqual(data[o:o + 4], bytes.fromhex('1f43b675'), 'each points at a Cluster')
                self.assertTrue(all(e <= len(data) for _, e in gets))
                self.assertEqual(lay['init'], data[:len(lay['init'])])

    def test_no_cues_is_none(self):
        self.assertIsNone(ytfetch.webm_layout(lambda s, e: self.live[s:e], len(self.live)))

    def test_demux_from_a_cluster_matches_the_tail_of_the_whole(self):
        for name in ('front', 'back'):
            data = getattr(self, name)
            lay = ytfetch.webm_layout(lambda s, e: data[s:e], len(data))
            (n1, _, _), hdr1, r1, v1 = self.demux(io.BytesIO(data))
            for k in range(len(lay['frags'])):
                with self.subTest(name=name, cluster=k):
                    off, ms = lay['frags'][k]
                    (n2, _, _), hdr2, r2, v2 = self.demux(ytfetch.Prefixed(lay['init'], io.BytesIO(data[off:])), lay['base'])
                    j = next(i for i, r in enumerate(r1) if r[0] >= ms and r[3])
                    self.assertTrue(r2[0][3], 'starts on a keyframe')
                    self.assertEqual(r2[0][0], ms, 'the cue\'s time, as from 0')
                    self.assertEqual([(m, z, key) for m, _, z, key in r2], [(m, z, key) for m, _, z, key in r1[j:]])
                    self.assertEqual(v2, v1[r1[j][1]:], 'the same bytes from that frame on')
                    self.assertEqual(hdr2[:16] + hdr2[20:], hdr1[:16] + hdr1[20:])
                    self.assertEqual(hdr2[20:24], ytfetch.VP9)


# ---------------------------------------------------------------- fetch_stream over HTTP

class _FileServer(RangeServer):
    pass


@unittest.skipUnless(FF, 'needs ffmpeg')
class FetchStream(unittest.TestCase):
    """fetch_stream with --video-only from a local Range server: what it asks for, in which order, and what it writes"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=TMP)
        v = os.path.join(cls.tmp, 'v.mp4')
        ff('-f', 'lavfi', '-i', 'testsrc2=size=426x240:rate=30', '-frames:v', '900', '-c:v', 'libx264',
           '-preset', 'veryfast', '-g', '30', '-pix_fmt', 'yuv420p', '-an', *FRAG, v)
        with open(v, 'rb') as f:
            cls.mp4, _ = streamable(f.read())
        cls.webm = vp9(os.path.join(cls.tmp, 'v.webm'), '-cues_to_front', '1')
        cls.srv = _FileServer(('127.0.0.1', 0), _H)
        cls.srv.seen, cls.srv.script, cls.srv.dead = [], [], set()
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp)

    def run_stream(self, data, webm, start_ms=0):
        self.srv.data, self.srv.seen = data, []
        url = f'http://127.0.0.1:{self.srv.server_address[1]}/v'
        w = ytfetch.Window(10 ** 9, 0, start_ms)
        src = ytfetch.Source({'url': url, 'filesize': len(data)}, w)
        lay = (ytfetch.webm_layout(src.get, len(data)) if webm else ytfetch.stream_layout(src.get))
        d = tempfile.mkdtemp(dir=self.tmp)
        plan = {'w': w, 'src': src, 'lay': lay, 'punch': None, 'need': 1, 'dir': d, 'ram': True, 'ram_why': None}
        out, old = io.StringIO(), ytfetch._out
        asked, get = [], src.get

        def logged(s, e):   # each request, with the video events said before it
            asked.append((s, e, [json.loads(x)['ms'] for x in out.getvalue().splitlines() if '"video"' in x]))
            return get(s, e)
        src.get = logged
        ytfetch._out = out
        try:
            a = types.SimpleNamespace(start_ms=start_ms, video_only=True)
            ytfetch.fetch_stream(a, {'duration_s': 30}, plan, None, {})
        finally:
            ytfetch._out = old
        return lay, asked, [json.loads(x) for x in out.getvalue().splitlines()], d

    def test_first_range_covers_first_ms_and_progress_is_said_before_each_request(self):
        for name, webm in (('mp4', False), ('webm', True)):
            with self.subTest(name):
                data = self.webm if webm else self.mp4
                lay, asked, evs, d = self.run_stream(data, webm)
                frags = lay['frags']
                j = next(i for i, (_, m) in enumerate(frags) if m >= ytfetch.FIRST_MS)
                want = max(frags[j][0] - frags[0][0], ytfetch.HEAD_BYTES)
                self.assertEqual(asked[0][:2], (frags[0][0], frags[0][0] + want), 'one range to FIRST_MS')
                self.assertEqual(asked[0][2], [])
                self.assertTrue(asked[1][2] and asked[1][2][-1] >= ytfetch.FIRST_MS - 1000,
                                'before the second request ytd.js knows all of the first: %s' % asked[1][2])
                for i in range(2, len(asked)):
                    self.assertGreater(asked[i][2][-1], asked[i - 1][2][-1], 'and each time after')
                st = evs[0]
                self.assertEqual((st['ev'], st['dir'], st['stored'], st['codec'], st['from_ms']),
                                 ('stream', d, 'ram', 'vp9' if webm else 'h264', 0))
                self.assertEqual(evs[-1], {'ev': 'done'})
                self.assertEqual(sorted(os.listdir(d)), ['index.bin', 'video.h264'])
                # the same index and bytes as a whole fetch
                ref = tempfile.mkdtemp(dir=self.tmp)
                (ytfetch.demux_webm if webm else ytfetch.demux_video)(io.BytesIO(data), ref, None, lambda *x: None)
                self.assertEqual(read_index(os.path.join(d, 'index.bin')), read_index(os.path.join(ref, 'index.bin')))

    def test_a_late_start_begins_at_its_fragment(self):
        lay, asked, evs, d = self.run_stream(self.webm, True, start_ms=6500)
        k = max(i for i, (_, m) in enumerate(lay['frags']) if m <= 6500)
        self.assertEqual((evs[0]['fragment'], evs[0]['from_ms']), (k, lay['frags'][k][1]))
        self.assertEqual(asked[0][0], lay['frags'][k][0])
        _, recs = read_index(os.path.join(d, 'index.bin'))
        self.assertEqual(recs[0][0], lay['frags'][k][1])


if __name__ == '__main__':
    unittest.main()
