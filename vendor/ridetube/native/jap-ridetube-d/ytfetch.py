#!/usr/bin/env python3
"""jap-ridetube-d on-car fetcher: yt-dlp (from ytdlp.pyz) + streaming fMP4 -> Annex-B and WebM -> VP9 frames, MGYI demuxers.

  ytfetch.py fetch <id> <dir> --cache-dir <d> [--height 720] [--avc1] [--node P] [--lease P] [--pyz P]
  ytfetch.py extract <id> <info.json> --cache-dir <d> [...]
  ytfetch.py serve --cache-dir <d> [...]
  ytfetch.py check [<id>] --cache-dir <d> [--avc1] [--uid N] [--node P] [--lease P] [--pyz P] [--json]
  ytfetch.py request --cache-dir <d> [--lease P]      (one request as JSON on stdin, see request_cmd)
  ytfetch.py update --cache-dir <d> [--lease P] [--pyz P] [--releases URL]
  --no-cert-time (any command): TLS skips only the certificates' validity dates (the head unit's clock is wrong)

fetch writes <dir>/meta.json, audio.m4a, video.h264 (+ .part while running) and index.bin, and prints
one JSON event per line on stdout: meta, audio, audio_head, audio_ready, video, done | error (then exit 1).
audio_head {ms, bytes}: <dir>/audio.head.m4a is a complete MP4 of the audio's first ms, before audio.m4a.
Video: VP9 (webm, profile 0) when there is one at the height step, else H.264; --avc1: H.264 only. A VP9 video.h264
holds raw VP9 frames and index.bin says so (codec "VP9 " at offset 20; zero = H.264). A VP9 stream it cannot read is
error "vp9: ..." (the daemon fetches again with --avc1).
fetch --info <f>: the extraction extract wrote there (if fresh), not a new one: ~2.5 s less on the car.
serve: loads yt-dlp (~1.2 s on the car), prints {"ev":"warm"}, then runs the one argv list read from stdin.
check is the owner's self-test, run as root over ssh (it drops to the fetch uid itself). check --json: only its
fast probes (yt-dlp, DNS, TLS), one JSON line each: the daemon's health command runs it as the fetch uid.
"""
import argparse
import copy
import http.client
import io
import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from fractions import Fraction

ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
HDR = struct.Struct('<4sHHHHHxxI4s8x')  # 32 bytes, record count at offset 16, codec at 20
VP9 = b'VP9 '                            # index.bin codec (ytdec: MFX_CODEC_VP9); H.264 leaves it zero
REC = struct.Struct('<IQIB')            # 17 bytes: pts_ms, pos, size, key
CA = '/var/opt/mgu-python/current/ssl/cacert.pem'
NODE = '/var/opt/jap-node22/current/bin/node'
LEASE = '/run/mgu-uplink/lease.json'
DEFAULT_CHECK_ID = 'dQw4w9WgXcQ'  # 3:33, avc1 720p 25 fps, AAC-LC 140: public and long-lived
# the video's own language first where it has several tracks (no AI dub / translation): yt-dlp notes it "original"
AUDIO_FMT = 'ba[ext=m4a][acodec^=mp4a.40.2][format_note*=original]/ba[ext=m4a][acodec^=mp4a.40.2]/ba[ext=m4a]/ba'
# audio: parallel Range requests (googlevideo paces each connection), in file order so the prefix grows first
AUDIO_CONNS, AUDIO_FIRST, AUDIO_CHUNK = 4, 1 << 20, 2 << 20
HEAD_MIN_MS = 60000  # audio.head.m4a covers at least this, and 1.5x the rest's estimated download time


def video_fmt(h, vp9=True):
    """VP9 (profile 0 webm: not vp9.2 HDR, not the m3u8 vp09) before H.264 at each height step.
    https only: an HLS (m3u8) pick needs ffmpeg, which the head unit doesn't have."""
    lo = h * 9 // 10
    vs = (['bv[vcodec=vp9][ext=webm][protocol=https]'] if vp9 else []) + ['bv[vcodec^=avc1][protocol=https]']
    return '/'.join(v + s for s in (f'[height<={h}][height>={lo}][fps<=30]', f'[height<={h}][fps<=30]', f'[height<={h}]')
                    for v in vs)


def playable_codec(f, avc1=False):
    """a video format of a codec ytdec decodes: H.264, or (unless avc1) VP9 profile 0 in webm"""
    vc = f.get('vcodec') or ''
    return vc.startswith('avc1') or (not avc1 and vc == 'vp9' and f.get('ext') == 'webm')


LIVE = ('is_live', 'is_upcoming', 'post_live')  # YouTube offers these as HLS only, until the live is processed


def unplayable(info, avc1=False):
    """Why this video can't be played, shown to the user as is; None when it can."""
    if info.get('is_live') or info.get('live_status') in LIVE:
        return "Live videos can't be played yet"
    fs = [f for f in info.get('formats') or [] if playable_codec(f, avc1)]
    if not fs:
        return 'YouTube offers this video in no format RideTube can play'
    if not any(f.get('protocol') in (None, 'https') for f in fs):
        return "This video is only offered as a stream RideTube can't play yet"
    return None


# ---------------------------------------------------------------- events

_out = sys.stdout
_out_lock = threading.Lock()


def emit(**kw):
    with _out_lock:
        try:
            _out.write(json.dumps(kw) + '\n')
            _out.flush()
        except OSError:
            os._exit(1)  # the daemon is gone


def clock_wrong(e):
    """The user's message when TLS fails because the head unit's clock is off (YouTube's certificate is newer than
    the clock, or the clock is past it); None for any other error."""
    t = str(getattr(e, 'verify_message', '') or e)
    if 'certificate is not yet valid' not in t and 'certificate has expired' not in t:
        return None
    return "Wrong head unit clock (" + time.strftime('%Y-%m-%d', time.gmtime()) + "): set the car's date and time"


BOT_MSG = "YouTube is blocking this connection as a bot: try another network, such as the phone's hotspot"


def bot_check(e):
    """The user's message when YouTube asks to sign in to prove it's no bot (an IP it distrusts; seen on a US car
    2026-11-19, yt-dlp's raw text there advised cookies); None for any other error."""
    return BOT_MSG if 'not a bot' in str(e) else None


# --no-cert-time: the daemon found the head unit's clock before the service's build date (ytd.js BUILT), so every
# certificate would look not yet valid. Only the validity dates are then not checked: OpenSSL still verifies the chain
# against the trusted CAs, and the host name still has to match.
NO_CHECK_TIME = 0x200000  # OpenSSL's X509_V_FLAG_NO_CHECK_TIME
cert_time = True


def tls_ctx():
    """The TLS context of every request ytfetch.py makes itself (yt-dlp's get the same flag: cert_time_hook)."""
    ctx = ssl.create_default_context(cafile=CA if os.path.isfile(CA) else None)
    if not cert_time:
        ctx.verify_flags |= NO_CHECK_TIME
    return ctx


def cert_time_hook(yt_dlp):
    """yt-dlp builds each request handler's context in RequestHandler._make_sslcontext: the flag goes on there, read
    when the context is made (a serve spare loads yt-dlp before its job says --no-cert-time). A yt-dlp without it:
    its dates stay checked (said on stderr), never a yt-dlp that does not load."""
    rh = getattr(getattr(getattr(yt_dlp, 'networking', None), 'common', None), 'RequestHandler', None)
    if rh is None:
        print('cert_time_hook: no yt_dlp.networking.common.RequestHandler: certificate dates stay checked', file=sys.stderr)
        return
    if getattr(rh._make_sslcontext, '_cert_time', False):
        return
    orig = rh._make_sslcontext

    def make_sslcontext(self, *args, **kw):
        ctx = orig(self, *args, **kw)
        if not cert_time:
            ctx.verify_flags |= NO_CHECK_TIME
        return ctx
    make_sslcontext._cert_time = True
    rh._make_sslcontext = make_sslcontext


def fail(msg):
    emit(ev='error', msg=str(msg)[-500:])
    os._exit(1)  # the other thread may sit in a socket read


MIN_FREE = 256 << 20  # bytes left free on the cache's file system while a video downloads

# ---------------------------------------------------------------- streaming the video through RAM (WP8)
# fetch --stream AHEAD_MS,BEHIND_MS [--video-dir D]: only the video's window is kept, in D (on the capped tmpfs) or,
# short of RAM, in <dir>/win on flash. The audio, meta.json and stream-info.json stay in <dir> (flash), as without
# --stream. The video is fetched in Range requests of VIDEO_CHUNK, each only while less than the window's ahead is
# fetched past the playback position (ytd.js sends "pos <ms>" lines on stdin), and video.h264 is hole-punched up to
# the last keyframe behind before it. ytdec preads each access unit at its own offset and every keyframe carries its
# SPS/PPS, so it never reads a punched part. --start-ms starts at the fragment (the sidx's subsegment or the WebM
# Cues' cluster, each a keyframe) holding that point: a seek, a resume or a catch-up after a dropped link costs one
# fragment, never the video from byte 0. The link: tried again for as long as ytd.js is there (backing off to 15 s),
# and an expired stream URL (403) is looked up again; the same format, the same bytes.
VIDEO_CHUNK = 2 << 20
TRIM_MIN = 1 << 20      # punch at least this much at a time
RAM_FLOOR = 24 << 20    # keep this much of the tmpfs free: the window waits instead
WINDOW_BYTES = 48 << 20  # the window (ahead + behind) at the format's bitrate, at most
BEHIND_BYTES = 16 << 20  # ... of which behind playback, at most
AHEAD_MIN_MS = 15000     # the byte cap never cuts ahead below this (a play starts at 6 s fetched)
FIRST_MS = 10000         # the first range covers this past the start (ytd.js starts at 6 s), not a blind chunk
MEM_FLOOR = 600 << 20    # MemAvailable under this: the window goes to flash, or (in RAM) the fetch waits
MEMINFO = '/proc/meminfo'
REFRESH_MAX = 3          # new lookups in a row with no byte in between: the URL is gone for good
HEAD_BYTES = 64 << 10   # ftyp, moov, sidx and the first moof of a YouTube DASH file (more is fetched when they are bigger)
NET_BACKOFF = (1, 2, 4, 8, 15)   # s between tries while the link is down, then 15 s on
REFRESH_ON = (403, 404, 410)     # the stream URL expired (~6 h) or moved: a new lookup
FALLOC_FL_KEEP_SIZE, FALLOC_FL_PUNCH_HOLE = 1, 2


def punch_fn():
    """fallocate(PUNCH_HOLE) through libc, or None when this Python has no ctypes / libc has no fallocate."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        f = libc.fallocate
        f.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int64, ctypes.c_int64]
        f.restype = ctypes.c_int
    except (ImportError, OSError, AttributeError):
        return None

    def punch(fd, off, n):
        if f(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, off, n) != 0:
            e = ctypes.get_errno()
            raise OSError(e, os.strerror(e))
    return punch


def can_punch(d, punch):
    """A probe in d: punching frees blocks there (tmpfs since Linux 3.5)."""
    if punch is None:
        return False
    p = os.path.join(d, '.punch')
    try:
        fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, b'\1' * (256 << 10))
            os.fsync(fd)
            before = os.fstat(fd).st_blocks
            punch(fd, 0, 128 << 10)
            return os.fstat(fd).st_blocks < before
        finally:
            os.close(fd)
            os.remove(p)
    except OSError:
        return False


class Window:
    """The playback position (ytd.js's "pos <ms>" lines) against the video fetched so far, both in ms.
    gone: stdin closed (ytd.js went away): nothing more is fetched."""

    def __init__(self, ahead_ms, behind_ms, play=0):
        self.ahead, self.behind = ahead_ms, behind_ms
        self.play, self.fetched = play, play
        self.gone = False
        self.waited = 0.0          # seconds spent waiting for room
        self.cv = threading.Condition()

    def listen(self, f):
        def run():
            try:
                for line in f:
                    p = line.split()
                    if len(p) == 2 and p[0] == 'pos' and p[1].isdigit():
                        with self.cv:
                            self.play = int(p[1])
                            self.cv.notify_all()
            except (OSError, ValueError):
                pass
            with self.cv:
                self.gone = True
                self.cv.notify_all()
        threading.Thread(target=run, daemon=True).start()

    def full(self, free_ok):
        return self.fetched - self.play > self.ahead or not free_ok()

    def wait_room(self, free_ok, tick=None):
        """tick: called on each wake while it waits (the Trimmer: what has played goes, written or not)"""
        t0 = time.monotonic()
        with self.cv:
            while not self.gone and self.full(free_ok):
                if tick:
                    tick()
                self.cv.wait(1.0)
        self.waited += time.monotonic() - t0
        if self.gone:
            raise OSError('ytd.js closed the position feed')


class HTTPStatus(OSError):
    """an answer other than 206 to a Range request"""

    def __init__(self, code):
        super().__init__(f'HTTP {code}')
        self.code = code


class Source:
    """The video's stream URL: get(start, end) tries again for as long as the window lasts (backing off to 15 s), and
    a 403/404/410 looks the video up again (refresh) for a new URL of the same format and size. One connection, kept
    open between requests (keep-alive): a fresh one for each 2 MiB paid a TCP and a TLS handshake and TCP's slow start
    every time, a large share of each request over a phone's hotspot. A kept connection the server closed meanwhile
    (idle while the window was full) is opened again at once, not counted as the link down."""

    def __init__(self, f, window, refresh=None, timeout=20):
        self.url, self.headers, self.size = f['url'], dict(f.get('http_headers') or {}), f['filesize']
        self.fid, self.window, self.refresh_fn, self.timeout = f.get('format_id'), window, refresh, timeout
        self.ctx = tls_ctx()
        self.down = 0              # tries failed in a row (an event says so once, and when the link is back)
        self.refreshed = 0
        self.dry = 0               # lookups since the last byte (REFRESH_MAX: given up)
        self.conn, self.conn_to = None, None   # the kept connection, and the (scheme, host:port) it is to
        self.connections = 0       # opened so far (tests: one for many requests)

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
        self.conn, self.conn_to = None, None

    def _once(self, s, e):
        """bytes s..e-1 in one request on the kept connection (a new one when there is none, or it is to another host,
        or the kept one turned out closed before an answer), following redirects; HTTPStatus for any other answer"""
        for _ in range(5):   # redirects
            u = urllib.parse.urlsplit(self.url)
            target = (u.path or '/') + ('?' + u.query if u.query else '')
            for fresh in (False, True):
                reused = self.conn is not None and self.conn_to == (u.scheme, u.netloc) and not fresh
                if not reused:
                    self.close()
                    if u.scheme == 'https':
                        self.conn = http.client.HTTPSConnection(u.hostname, u.port, timeout=self.timeout, context=self.ctx)
                    else:
                        self.conn = http.client.HTTPConnection(u.hostname, u.port, timeout=self.timeout)
                    self.conn_to = (u.scheme, u.netloc)
                    self.connections += 1
                try:
                    self.conn.request('GET', target, headers=dict(self.headers, Range=f'bytes={s}-{e - 1}'))
                    r = self.conn.getresponse()
                    break
                except (OSError, http.client.HTTPException):
                    self.close()
                    if not reused:
                        raise
            if r.status in (301, 302, 303, 307, 308) and r.getheader('Location'):
                r.read()
                if r.will_close:
                    self.close()
                self.url = urllib.parse.urljoin(self.url, r.getheader('Location'))
                continue
            if r.status != 206:
                r.read()
                self.close()
                raise HTTPStatus(r.status)
            out = bytearray()
            try:
                while len(out) < e - s:
                    b = r.read(min(1 << 16, e - s - len(out)))
                    if not b:
                        raise OSError(f'short read at {s + len(out)} of {s}-{e}')
                    out += b
            except BaseException:
                self.close()
                raise
            if r.will_close or r.read(1):   # (a longer body than asked: the connection cannot be reused)
                self.close()
            return bytes(out)
        self.close()
        raise OSError('too many redirects')

    def refresh(self):
        f = self.refresh_fn(self.fid)
        if f.get('filesize') and f['filesize'] != self.size:
            raise OSError('the format changed size: %s, not %s' % (f['filesize'], self.size))
        self.url, self.headers = f['url'], dict(f.get('http_headers') or self.headers)
        self.refreshed += 1

    def get(self, s, e):
        """bytes s..e-1"""
        while True:
            try:
                out = self._once(s, e)
                if self.down:
                    emit(ev='net', ok=True, tries=self.down)
                    self.down = 0
                self.dry = 0
                return out
            except HTTPStatus as x:
                why = f'HTTP {x.code}'
                if self.window.gone:
                    raise OSError('ytd.js closed the position feed')
                if x.code in REFRESH_ON and self.refresh_fn:
                    if self.dry >= REFRESH_MAX:
                        raise OSError(f'{why} after {self.dry} new lookups')
                    self.dry += 1
                    try:
                        self.refresh()
                        if self.dry == 1:
                            continue           # at once, with the new URL; a second one after the backoff
                    except Exception as r:  # noqa: BLE001 - the lookup needs the link too: tried again below
                        why += f'; lookup again: {r}'
            except (OSError, http.client.HTTPException) as x:
                why = str(x) or type(x).__name__
            if self.window.gone:
                raise OSError('ytd.js closed the position feed')
            self.down += 1
            if self.down == 1:
                emit(ev='net', ok=False, msg=why[-200:])
            wait = NET_BACKOFF[min(self.down - 1, len(NET_BACKOFF) - 1)]
            with self.window.cv:
                if not self.window.gone:   # (set between the check above and here: its notify would be missed)
                    self.window.cv.wait(wait)


class PacedReader:
    """A file for the demuxers: the source's bytes from start on, in Range requests of chunk (the first one of first
    bytes), each only once the window has room. before_wait: called before each request and each wait (the progress
    events are throttled, so the last one may be behind what is fetched; said before blocking, ytd.js's fetched point
    is the real one: 3.5 s said while 27.5 s was in held a play's start for seconds). on_wait: called while it waits."""

    def __init__(self, src, start, window, free_ok, chunk=VIDEO_CHUNK, before_wait=None, first=None, on_wait=None):
        self.src, self.total, self.window, self.free_ok, self.chunk = src, src.size, window, free_ok, chunk
        self.before_wait, self.first, self.on_wait = before_wait, first, on_wait
        self.buf, self.at, self.off, self.bytes = b'', 0, start, 0

    def read(self, n):
        while len(self.buf) - self.at < n and self.off < self.total:
            if self.before_wait:
                self.before_wait()
            self.window.wait_room(self.free_ok, self.on_wait)
            e = min(self.off + (self.first or self.chunk), self.total)
            self.first = None
            data = self.src.get(self.off, e)
            self.bytes += len(data)
            self.buf, self.at, self.off = self.buf[self.at:] + data, 0, e
        b = self.buf[self.at:self.at + n]
        self.at += len(b)
        return b


class Prefixed:
    """prefix's bytes, then f's: the moov ahead of the fragments a stream starts at"""

    def __init__(self, prefix, f):
        self.prefix, self.f = prefix, f

    def read(self, n):
        if self.prefix:
            b, self.prefix = self.prefix[:n], self.prefix[n:]
            if len(b) < n:
                b += self.f.read(n - len(b))
            return b
        return self.f.read(n)


def parse_sidx(b):
    """sidx box bytes -> (timescale, earliest presentation time, first offset, [(size, duration, starts with SAP)])"""
    v = b[8]
    ts = struct.unpack_from('>I', b, 16)[0]
    if v == 0:
        ept, first = struct.unpack_from('>II', b, 20)
        p = 28
    else:
        ept, first = struct.unpack_from('>QQ', b, 20)
        p = 36
    n = struct.unpack_from('>H', b, p + 2)[0]
    p += 4
    subs = []
    for _ in range(n):
        r, d, sap = struct.unpack_from('>III', b, p)
        p += 12
        if r >> 31:
            raise ValueError('sidx: a hierarchical index')
        subs.append((r & 0x7fffffff, d, sap >> 31))
    return ts, ept, first, subs


def stream_layout(get):
    """The DASH file's start (get(s, e): bytes s..e-1): moov, the time base (the first fragment's earliest pts, as
    demux_video takes it from 0) and every fragment [(byte offset, start ms)]. None without a usable sidx."""
    head = get(0, HEAD_BYTES)
    boxes_at, i = {}, 0
    while True:
        if i + 8 > len(head):
            head += get(len(head), i + 8 + HEAD_BYTES)
        size, t = struct.unpack_from('>I4s', head, i)
        if size < 8:
            return None
        if i + size > len(head):
            head += get(len(head), i + size + (2 << 10))   # (and the next box's header)
        boxes_at[t] = (i, size)
        if t == b'moof':
            break
        i += size
    if b'moov' not in boxes_at or b'sidx' not in boxes_at:
        return None
    ms_, mz = boxes_at[b'moov']
    moov = head[ms_:ms_ + mz]
    tk = trak_info(moov)
    xs, xz = boxes_at[b'sidx']
    ts, ept, first, subs = parse_sidx(head[xs:xs + xz])
    if ts != tk['ts'] or not subs or not all(s[2] for s in subs):
        return None
    fs, fz = boxes_at[b'moof']
    samples, _ = parse_moof(head[fs:fs + fz], 0, tk['trex'])
    if not samples:
        return None
    base = min(s[2] + s[3] for s in samples)
    off, t, frags = xs + xz + first, ept, []
    for size, dur, _ in subs:
        frags.append((off, max(0, round((t - base) * 1000 / ts))))
        off, t = off + size, t + dur
    return {'init': moov, 'base': base, 'frags': frags, 'end': off, 'webm': False}


class Trimmer:
    """Punches video.h264 up to the last keyframe window.behind before the playback position."""

    def __init__(self, window, punch):
        self.window, self.punch = window, punch
        self.keys = []             # (ms, pos) of keyframes past the punched part
        self.done, self.ms = 0, 0  # punched up to this byte / this keyframe's ms
        self.fd = None             # video.h264.part, once written

    def key(self, ms, pos):
        self.keys.append((ms, pos))

    def poll(self):
        """the same while the fetch waits for room (no write to trigger it); nothing before the first write"""
        if self.fd is not None:
            self.after_write(self.fd)

    def after_write(self, fd):
        self.fd = fd
        cut = self.window.play - self.window.behind
        i = 0
        while i + 1 < len(self.keys) and self.keys[i + 1][0] <= cut:
            i += 1
        if not self.keys or self.keys[i][0] > cut or self.keys[i][1] - self.done < TRIM_MIN:
            return
        ms, pos = self.keys[i]
        self.punch(fd, self.done, pos - self.done)
        self.done, self.ms, self.keys = pos, ms, self.keys[i:]
        emit(ev='trim', ms=ms, bytes=pos)


class Throttle:
    def __init__(self, per_s=2):
        self.gap, self.last = 1 / per_s, 0

    def __call__(self):
        now = time.monotonic()
        if now - self.last < self.gap:
            return False
        self.last = now
        return True


# ---------------------------------------------------------------- DNS through the uplink's lease

def _skip_name(b, i):
    while True:
        n = b[i]
        if n & 0xc0 == 0xc0:
            return i + 2
        if n == 0:
            return i + 1
        i += n + 1


def _server(srv):
    """A lease DNS entry as (address, port): '1.1.1.1', '127.0.0.1:5354', '[::1]:5354' or a bare IPv6."""
    if srv.startswith('['):
        h, _, p = srv[1:].partition(']')
        return h, int(p[1:]) if p.startswith(':') else 53
    if srv.count(':') == 1:
        h, p = srv.split(':')
        return h, int(p)
    return srv, 53


def resolve_a(host, servers, timeout=3):
    qid = os.urandom(2)
    q = (qid + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
         + b''.join(bytes([len(p)]) + p for p in host.rstrip('.').encode('idna').split(b'.'))
         + b'\x00\x00\x01\x00\x01')
    err = 'no server'
    for srv in servers:
        try:
            addr = _server(srv)
        except ValueError:
            err = f'{srv}: bad server'
            continue
        s = socket.socket(socket.AF_INET6 if ':' in addr[0] else socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(timeout)
            s.sendto(q, addr)
            r = s.recv(4096)
            while r[:2] != qid:
                r = s.recv(4096)
            if r[3] & 15:
                err = f'{srv}: rcode {r[3] & 15}'
                continue
            i = _skip_name(r, 12) + 4
            ips = []
            for _ in range(struct.unpack_from('>H', r, 6)[0]):
                i = _skip_name(r, i)
                typ, cls, _ttl, ln = struct.unpack_from('>HHIH', r, i)
                i += 10
                if typ == 1 and cls == 1 and ln == 4:
                    ips.append(socket.inet_ntoa(r[i:i + 4]))
                i += ln
            if ips:
                return ips
            err = f'{srv}: no A record'
        except (OSError, IndexError, struct.error) as e:
            err = f'{srv}: {e}'
        finally:
            s.close()
    raise socket.gaierror(socket.EAI_NONAME, f'{host}: lease DNS: {err}')


def lease_servers(lease):
    try:
        with open(lease) as f:
            return [s for s in json.load(f).get('dns', []) if isinstance(s, str)]
    except (OSError, ValueError, AttributeError):
        return []


def _is_ip(h):
    for fam in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(fam, h)
            return True
        except OSError:
            pass
    return False


# ---------------------------------------------------------------- the MGU Store's proxy
# The daemon reads the Store's `proxy` setting (proxy.js) and gives it to every fetch-uid child as RT_PROXY (JSON:
# kind http|socks5, host, port, user, pass, label) or RT_PROXY_ERROR. With one, every connection ytfetch.py makes
# itself goes through the proxy (socket.create_connection, which http.client and urllib use), yt-dlp gets it as its
# `proxy`, and no name is looked up here but the proxy's own: the proxy looks them up. A wrong setting fails every
# connection; it never goes direct. The tunnels follow istt1899's fork (github.com/istt1899/ridetube, GPL-3.0).
PROXY = None
_PROXY_SET = False


def proxy_child_env():
    """PROXY as a child's env again (check's fetch child): os.environ no longer has it."""
    if PROXY is None:
        return {}
    if 'error' in PROXY:
        return {'RT_PROXY_ERROR': PROXY['error']}
    return {'RT_PROXY': json.dumps({k: PROXY[k] for k in ('kind', 'host', 'port', 'user', 'pass', 'label')})}


class ProxyError(OSError):
    pass


def proxy_env(env=None):
    env = os.environ if env is None else env
    if env.get('RT_PROXY_ERROR'):
        return {'error': str(env['RT_PROXY_ERROR'])[:200]}
    raw = env.get('RT_PROXY')
    if not raw:
        return None
    try:
        p = json.loads(raw)
        if p.get('kind') not in ('http', 'socks5') or not isinstance(p.get('host'), str) or not 0 < int(p['port']) < 65536:
            raise ValueError('bad')
        return {'kind': p['kind'], 'host': p['host'], 'port': int(p['port']), 'user': str(p.get('user') or ''),
                'pass': str(p.get('pass') or ''), 'label': str(p.get('label') or p['host'])}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {'error': 'proxy setting unreadable'}


def proxy_url(p):
    """yt-dlp's proxy: socks5h, so the proxy looks the names up (yt-dlp's socks5:// looks them up here)."""
    q = lambda x: urllib.parse.quote(x, safe='')  # noqa: E731
    auth = f"{q(p['user'])}:{q(p['pass'])}@" if p['user'] or p['pass'] else ''
    host = f"[{p['host']}]" if ':' in p['host'] else p['host']
    return f"{'socks5h' if p['kind'] == 'socks5' else 'http'}://{auth}{host}:{p['port']}"


def _recv_exact(sock, n):
    b = b''
    while len(b) < n:
        d = sock.recv(n - len(b))
        if not d:
            raise ProxyError('connection closed')
        b += d
    return b


SOCKS_REPLIES = ['', 'general failure', 'not allowed by its rules', 'network unreachable', 'host unreachable',
                 'connection refused', 'TTL expired', 'command not supported', 'address type not supported']


def _socks5(sock, p, host, port):
    auth = bool(p['user'] or p['pass'])
    sock.sendall(b'\x05\x01\x02' if auth else b'\x05\x01\x00')
    ver, method = _recv_exact(sock, 2)
    if ver != 5:
        raise ProxyError('not a SOCKS5 proxy')
    if method == 2 and auth:
        u, pw = p['user'].encode(), p['pass'].encode()
        sock.sendall(bytes([1, len(u)]) + u + bytes([len(pw)]) + pw)
        if _recv_exact(sock, 2)[1] != 0:
            raise ProxyError('user name or password refused')
    elif method != 0:
        raise ProxyError('it takes no user name and password' if auth else 'it wants a user name and password')
    try:
        dst = b'\x01' + socket.inet_pton(socket.AF_INET, host)
    except OSError:
        try:
            dst = b'\x04' + socket.inet_pton(socket.AF_INET6, host)
        except OSError:
            h = host.encode('idna')
            if len(h) > 255:
                raise ProxyError('host name over 255 bytes') from None
            dst = bytes([3, len(h)]) + h  # the proxy looks the name up
    sock.sendall(b'\x05\x01\x00' + dst + struct.pack('>H', port))
    hdr = _recv_exact(sock, 4)
    if hdr[0] != 5:
        raise ProxyError('not a SOCKS5 proxy')
    if hdr[1] != 0:
        raise ProxyError(SOCKS_REPLIES[hdr[1]] if hdr[1] < len(SOCKS_REPLIES) else f'refused (code {hdr[1]})')
    if hdr[3] == 1:
        _recv_exact(sock, 4 + 2)
    elif hdr[3] == 4:
        _recv_exact(sock, 16 + 2)
    elif hdr[3] == 3:
        _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
    else:
        raise ProxyError('bad answer')


def _connect(sock, p, host, port):
    target = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
    auth = ''
    if p['user'] or p['pass']:
        import base64
        auth = 'Proxy-Authorization: Basic ' + base64.b64encode(f"{p['user']}:{p['pass']}".encode()).decode() + '\r\n'
    sock.sendall(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n'.encode())
    head = b''
    while b'\r\n\r\n' not in head:  # byte by byte: what follows the head is the tunnel's (TLS)
        if len(head) > 8192:
            raise ProxyError('answer too long')
        head += _recv_exact(sock, 1)
    m = re.match(rb'HTTP/\d\.\d\s+(\d{3})', head)
    if not m:
        raise ProxyError('not an HTTP proxy')
    code = m.group(1).decode()
    if code == '407':
        raise ProxyError('user name or password refused')
    if code != '200':
        raise ProxyError(f'CONNECT refused (HTTP {code})')


def tunnel(p, host, port, timeout=None, source_address=None):
    """A socket to host:port through the proxy p, the caller's timeout kept for the handshake and after."""
    if 'error' in p:
        raise ProxyError(p['error'])
    t = timeout if isinstance(timeout, (int, float)) else 30
    try:
        sock = _create_connection((p['host'], p['port']), t, source_address)
    except OSError as e:
        raise ProxyError(f"proxy {p['label']}: {e.strerror or e}") from None
    try:
        (_socks5 if p['kind'] == 'socks5' else _connect)(sock, p, host, int(port))
        sock.settimeout(timeout if isinstance(timeout, (int, float)) else None)
        return sock
    except socket.timeout:
        sock.close()
        raise ProxyError(f"proxy {p['label']}: no answer") from None
    except ProxyError as e:
        sock.close()
        raise ProxyError(f"proxy {p['label']}: {e}") from None
    except BaseException:
        sock.close()
        raise


_create_connection = socket.create_connection
_LOCAL = ('127.0.0.1', 'localhost', '::1')


def _name_allowed(h):
    """With a proxy, the only name looked up here is the proxy's own (and this machine's)."""
    return PROXY is None or h in _LOCAL or _is_ip(h) or ('error' not in PROXY and h == PROXY['host'])


def install_proxy(p):
    global PROXY
    PROXY = p
    if p is None:
        return
    orig = socket.getaddrinfo

    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kw):
        h = address[0].decode() if isinstance(address[0], bytes) else address[0]
        if h in _LOCAL or ('error' not in p and h == p['host']):
            return _create_connection(address, timeout, source_address, **kw)
        return tunnel(p, h, address[1], timeout, source_address)

    def getaddrinfo(host, port, *args, **kw):
        h = host.decode() if isinstance(host, bytes) else host
        if h and not _name_allowed(h):
            raise socket.gaierror(socket.EAI_NONAME, f'{h}: looked up by the proxy only')
        return orig(host, port, *args, **kw)
    socket.create_connection = create_connection
    socket.getaddrinfo = getaddrinfo


def install_dns(lease):
    """The system resolver answers no internet names on the car: ask the uplink's DNS servers."""
    if getattr(socket.getaddrinfo, '_lease', None) == lease:  # serve installed it
        return lease_servers(lease)
    servers = lease_servers(lease)
    if not servers:
        return []
    orig, cache = socket.getaddrinfo, {}

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        h = host.decode() if isinstance(host, bytes) else host
        if not h or h == 'localhost' or _is_ip(h) or family == socket.AF_INET6:
            return orig(host, port, family, type, proto, flags)
        if not _name_allowed(h):
            raise socket.gaierror(socket.EAI_NONAME, f'{h}: looked up by the proxy only')
        if h not in cache:
            cache[h] = resolve_a(h, servers)
        p = int(port or 0)  # shortcut: service names ('https') unsupported, urllib/yt-dlp pass numbers
        return [(socket.AF_INET, type or socket.SOCK_STREAM, proto, '', (ip, p)) for ip in cache[h]]

    getaddrinfo._lease = lease
    socket.getaddrinfo = getaddrinfo
    return servers


# ---------------------------------------------------------------- MP4 boxes

def boxes(b, i=0, end=None):
    """Yield (type, payload_start, box_end) for the boxes in b[i:end]."""
    end = len(b) if end is None else end
    while i + 8 <= end:
        size, typ = struct.unpack_from('>I4s', b, i)
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack_from('>Q', b, i + 8)[0], 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            raise ValueError(f'bad {typ!r} box')
        yield typ, i + hdr, i + size
        i += size


def kid(b, s, e, *path):
    for name in path:
        s, e = next(((s2, e2) for t, s2, e2 in boxes(b, s, e) if t == name), (None, None))
        if s is None:
            raise ValueError(f'no {name.decode()} box')
    return s, e


def trak_info(moov):
    """First track of a DASH moov: timescale, stsd box bytes, stsz count, trex defaults."""
    s, e = kid(moov, 8, len(moov), b'trak')
    ms, _ = kid(moov, s, e, b'mdia', b'mdhd')
    ts = struct.unpack_from('>I', moov, ms + (20 if moov[ms] else 12))[0]
    st, ste = kid(moov, s, e, b'mdia', b'minf', b'stbl')
    ds, de = kid(moov, st, ste, b'stsd')
    zs, _ = kid(moov, st, ste, b'stsz')
    trex = (0, 0, 0)
    try:
        xs, _ = kid(moov, 8, len(moov), b'mvex', b'trex')
        trex = struct.unpack_from('>III', moov, xs + 12)  # default duration, size, flags
    except ValueError:
        pass
    return dict(ts=ts, stsd=moov[ds - 8:de], stsd_at=(ds, de),
                samples=struct.unpack_from('>I', moov, zs + 8)[0], trex=trex)


def parse_moof(b, next_dts, trex):
    """Samples of moof box bytes b: [(offset from moof start, size, dts, cto, flags)], next dts."""
    out = []
    for t, s, e in boxes(b, 8):
        if t != b'traf':
            continue
        hs, _ = kid(b, s, e, b'tfhd')
        fl = struct.unpack_from('>I', b, hs)[0] & 0xffffff
        p, base = hs + 8, None
        if fl & 1:
            base, p = struct.unpack_from('>Q', b, p)[0], p + 8
        if fl & 2:
            p += 4
        dd, dsz, dfl = trex
        if fl & 8:
            dd, p = struct.unpack_from('>I', b, p)[0], p + 4
        if fl & 0x10:
            dsz, p = struct.unpack_from('>I', b, p)[0], p + 4
        if fl & 0x20:
            dfl = struct.unpack_from('>I', b, p)[0]
        if base is not None:
            raise ValueError('tfhd base_data_offset unsupported')
        try:
            ts_, _ = kid(b, s, e, b'tfdt')
            next_dts = struct.unpack_from('>Q' if b[ts_] else '>I', b, ts_ + 4)[0]
        except ValueError:
            pass
        pos = 0
        for t2, rs, _ in boxes(b, s, e):
            if t2 != b'trun':
                continue
            v, rfl = b[rs], struct.unpack_from('>I', b, rs)[0] & 0xffffff
            n = struct.unpack_from('>I', b, rs + 4)[0]
            p = rs + 8
            if rfl & 1:
                pos, p = struct.unpack_from('>i', b, p)[0], p + 4
            first = None
            if rfl & 4:
                first, p = struct.unpack_from('>I', b, p)[0], p + 4
            for k in range(n):
                d, sz, sf, cto = dd, dsz, (first if k == 0 and first is not None else dfl), 0
                if rfl & 0x100:
                    d, p = struct.unpack_from('>I', b, p)[0], p + 4
                if rfl & 0x200:
                    sz, p = struct.unpack_from('>I', b, p)[0], p + 4
                if rfl & 0x400:
                    sf, p = struct.unpack_from('>I', b, p)[0], p + 4
                if rfl & 0x800:
                    cto, p = struct.unpack_from('>i' if v else '>I', b, p)[0], p + 4
                out.append((pos, sz, next_dts, cto, sf))
                pos += sz
                next_dts += d
    return out, next_dts


def is_key(flags):
    return not flags & 0x01010000  # ffmpeg's rule: not non-sync and not depends-on-others


# ---------------------------------------------------------------- audio: DASH m4a -> plain MP4, moov first

def box(t, *parts):
    body = b''.join(parts)
    return struct.pack('>I4s', 8 + len(body), t) + body


def full(t, flags, *parts):
    return box(t, struct.pack('>I', flags), *parts)


MATRIX = struct.pack('>9I', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


# The car's media player is given only the file (MediaPlayer.playFile has no other argument), so the cluster shows
# what the file says: iTunes-style tags in moov/udta/meta/ilst, as music players read them. Without them it shows
# "unknown title" and "unknown artist".
def tags_udta(title=None, artist=None, cover=None):
    """moov's udta with the title (©nam), the artist (©ART) and a JPEG or PNG cover (covr); b'' when none."""
    def text(t, s):
        return box(t, box(b'data', struct.pack('>II', 1, 0), str(s)[:255].encode('utf-8')))  # 1: UTF-8
    items = [text(t, s) for t, s in ((b'\xa9nam', title), (b'\xa9ART', artist)) if s]
    if cover:
        kind = 14 if cover[:8] == b'\x89PNG\r\n\x1a\n' else 13  # 13: JPEG, 14: PNG
        items.append(box(b'covr', box(b'data', struct.pack('>II', kind, 0), cover)))
    if not items:
        return b''
    hdlr = full(b'hdlr', 0, b'\0' * 4, b'mdir', b'appl', b'\0' * 8, b'\0')
    return box(b'udta', full(b'meta', 0, hdlr, box(b'ilst', *items)))


COVER_URL = 'https://i.ytimg.com/vi/{}/mqdefault.jpg'  # 320x180: the whole picture, no letterbox bars
COVER_MAX = 256 * 1024


def fetch_cover(vid, ctx=None, timeout=5):
    """The video's thumbnail as JPEG bytes for the cover tag, or None (no picture is no failure)."""
    try:
        with urllib.request.urlopen(COVER_URL.format(vid), timeout=timeout, context=ctx) as r:
            data = r.read(COVER_MAX + 1)
    except (OSError, http.client.HTTPException, ValueError) as e:
        if hasattr(e, 'close'):
            e.close()  # an HTTPError holds its response open
        print(f'cover: {e}', file=sys.stderr, flush=True)
        return None
    return data if 0 < len(data) <= COVER_MAX and data[:2] == b'\xff\xd8' else None


def flat_moov(ts, stsd, durs, sizes, chunks, offsets, udta=b''):
    stts, run = [], None
    for d in durs:
        if run and run[1] == d:
            run[0] += 1
        else:
            run = [1, d]
            stts.append(run)
    stsc = []
    for i, (_, n) in enumerate(chunks):
        if not stsc or stsc[-1][1] != n:
            stsc.append((i + 1, n))
    total = sum(durs)
    stbl = box(b'stbl', stsd,
               full(b'stts', 0, struct.pack('>I', len(stts)), *(struct.pack('>II', *r) for r in stts)),
               full(b'stsc', 0, struct.pack('>I', len(stsc)), *(struct.pack('>III', c, n, 1) for c, n in stsc)),
               full(b'stsz', 0, struct.pack('>II', 0, len(sizes)), struct.pack(f'>{len(sizes)}I', *sizes)),
               full(b'stco', 0, struct.pack('>I', len(offsets)), struct.pack(f'>{len(offsets)}I', *offsets)))
    minf = box(b'minf', full(b'smhd', 0, b'\0' * 4),
               box(b'dinf', full(b'dref', 0, struct.pack('>I', 1), full(b'url ', 1))), stbl)
    mdia = box(b'mdia', full(b'mdhd', 0, struct.pack('>IIIIHH', 0, 0, ts, total, 0x55c4, 0)),
               full(b'hdlr', 0, b'\0' * 4, b'soun', b'\0' * 12, b'SoundHandler\0'), minf)
    tkhd = full(b'tkhd', 3, struct.pack('>IIIII', 0, 0, 1, 0, total), b'\0' * 8,
                struct.pack('>HHHH', 0, 0, 0x0100, 0), MATRIX, b'\0' * 8)
    mvhd = full(b'mvhd', 0, struct.pack('>IIIIIH', 0, 0, ts, total, 0x10000, 0x0100), b'\0' * 10,
                MATRIX, b'\0' * 24, struct.pack('>I', 2))
    return box(b'moov', mvhd, box(b'trak', tkhd, mdia), udta)


def flatten_m4a(src, dst, limit=None, udta=b''):
    """Rewrite a fragmented (DASH) m4a as ftyp+moov+mdat; edit lists are dropped. Returns (dst size, ms).
    limit: only src[:limit] is written yet (the rest may be zeros): the samples entirely inside it; a plain
    MP4 then gives None (it cannot be cut). udta: tags_udta's box, in the moov (a plain MP4 is copied as it is)."""
    with open(src, 'rb') as f:
        top, limit_ = [], os.fstat(f.fileno()).st_size if limit is None else limit
        while True:
            at, h = f.tell(), f.read(16)
            if len(h) < 8 or at + 8 > limit_:
                break
            size, t = struct.unpack_from('>I4s', h)
            if size == 1:
                size = struct.unpack_from('>Q', h, 8)[0]
            elif size == 0:
                size = os.fstat(f.fileno()).st_size - at
            if size < 8 or (at + size > limit_ and t != b'mdat'):
                break  # a box not all written yet (an mdat is cut per sample below)
            if t in (b'moov', b'moof'):
                f.seek(at)
                top.append((t, at, f.read(size)))
            f.seek(at + size)
        moov = next((bx for t, _, bx in top if t == b'moov'), None)
        if moov is None:
            raise ValueError('audio: no moov')
        tk = trak_info(moov)
        if tk['samples']:  # already a plain MP4
            if limit is not None:
                return None
            shutil.copyfile(src, dst)
            return os.path.getsize(dst), 0
        end, durs, sizes, chunks, dts = None, [], [], [], 0  # chunks: (source offset, sample count), one per run
        cut = False
        for t, at, b in top:
            if t != b'moof' or cut:
                continue
            samples, nd = parse_moof(b, dts, tk['trex'])
            for i, (off, sz, sdts, _, _) in enumerate(samples):
                if at + off + sz > limit_:
                    cut = True
                    break
                if chunks and at + off == end:
                    chunks[-1][1] += 1
                else:
                    chunks.append([at + off, 1])
                end = at + off + sz
                sizes.append(sz)
                durs.append((samples[i + 1][2] if i + 1 < len(samples) else nd) - sdts)
            dts = nd
        if not sizes:
            raise ValueError('audio: no samples')
        ftyp = box(b'ftyp', b'M4A ', struct.pack('>I', 0x200), b'M4A isomiso2')
        n = len(chunks)
        head = len(ftyp) + len(flat_moov(tk['ts'], tk['stsd'], durs, sizes, chunks, [0] * n, udta)) + 8
        offsets, o, i = [], head, 0
        for _, k in chunks:
            offsets.append(o)
            o += sum(sizes[i:i + k])
            i += k
        moov2 = flat_moov(tk['ts'], tk['stsd'], durs, sizes, chunks, offsets, udta)
        if o - head + 8 > 0xffffffff:
            raise ValueError('audio: over 4 GB')
        with open(dst, 'wb') as g:
            g.write(ftyp + moov2 + struct.pack('>I4s', o - head + 8, b'mdat'))
            i = 0
            for at, k in chunks:
                nbytes = sum(sizes[i:i + k])
                i += k
                f.seek(at)
                g.write(f.read(nbytes))
    return os.path.getsize(dst), sum(durs) * 1000 // tk['ts']


def ranged_get(url, headers, dst, total, on_chunk=None, conns=AUDIO_CONNS, first=AUDIO_FIRST, chunk=AUDIO_CHUNK,
               ctx=None, timeout=15, gate=None):
    """total bytes of url into dst with conns parallel Range requests, handed out in file order; with gate (an Event)
    only the first connection runs until it is set.
    on_chunk(prefix, done): after each chunk, under a lock; prefix = the bytes written from 0 without a gap."""
    ranges = [(0, min(first, total))]
    while ranges[-1][1] < total:
        ranges.append((ranges[-1][1], min(ranges[-1][1] + chunk, total)))
    lock, st = threading.Lock(), {'next': 0, 'prefix': 0, 'done': 0, 'err': None}
    finished = [False] * len(ranges)
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)

    def get(s, e):
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=dict(headers, Range=f'bytes={s}-{e - 1}'))
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    if r.status != 206:
                        raise OSError(f'HTTP {r.status} to a Range request')
                    pos = s
                    while pos < e:
                        b = r.read(min(1 << 16, e - pos))
                        if not b:
                            raise OSError(f'short read at {pos} of {s}-{e}')
                        os.pwrite(fd, b, pos)
                        pos += len(b)
                return
            except (OSError, http.client.HTTPException) as ex:
                if attempt == 2 or st['err']:
                    raise
                time.sleep(0.5 * (attempt + 1))

    def worker(k):
        if k and gate is not None:
            gate.wait()
        while True:
            with lock:
                if st['err'] or st['next'] >= len(ranges):
                    return
                i = st['next']
                st['next'] += 1
            try:
                get(*ranges[i])
                with lock:
                    finished[i] = True
                    st['done'] += ranges[i][1] - ranges[i][0]
                    while st['prefix'] < len(ranges) and finished[st['prefix']]:
                        st['prefix'] += 1
                    if on_chunk:
                        on_chunk(ranges[st['prefix'] - 1][1] if st['prefix'] else 0, st['done'])
            except Exception as e:  # noqa: BLE001 - the first error ends every worker
                st['err'] = st['err'] or e
                if gate is not None:
                    gate.set()  # the gated workers see err and return
                return

    try:
        ts = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(min(conns, len(ranges)))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        os.close(fd)
    if st['err']:
        raise st['err']
    if not all(finished):
        raise OSError('ranged_get: ranges left')
    return total


# ---------------------------------------------------------------- video: fMP4 stream -> Annex-B + MGYI

class AnnexB:
    """Length-prefixed NALs -> Annex-B, with SPS/PPS before IDRs: byte-for-byte ffmpeg's h264_mp4toannexb."""

    def __init__(self, avcc):
        self.ls = (avcc[4] & 3) + 1
        i, sps, pps = 6, [], []
        for _ in range(avcc[5] & 31):
            n = struct.unpack_from('>H', avcc, i)[0]
            sps.append(avcc[i + 2:i + 2 + n])
            i += 2 + n
        for _ in range(avcc[i]):
            n = struct.unpack_from('>H', avcc, i + 1)[0]
            pps.append(avcc[i + 3:i + 3 + n])
            i += 2 + n
        if not sps or not pps:
            raise ValueError('avcC without SPS/PPS')
        self.sps = b''.join(b'\0\0\0\1' + x for x in sps)
        self.pps = b''.join(b'\0\0\0\1' + x for x in pps)
        self.new_idr, self.sps_seen, self.pps_seen = True, False, False

    def convert(self, data):
        out, i, ls = bytearray(), 0, self.ls
        while i + ls <= len(data):
            n = int.from_bytes(data[i:i + ls], 'big')
            nal = data[i + ls:i + ls + n]
            i += ls + n
            if not nal:
                continue
            t = nal[0] & 31
            if t == 7:
                self.sps_seen = self.new_idr = True
            elif t == 8:
                self.pps_seen = self.new_idr = True
                if not self.sps_seen:
                    out += self.sps
                    self.sps_seen = True
            if not self.new_idr and t == 5 and len(nal) > 1 and nal[1] & 0x80:
                self.new_idr = True
            if self.new_idr and t == 5 and not self.sps_seen and not self.pps_seen:
                out += self.sps + self.pps
                self.new_idr = False
            elif self.new_idr and t == 5 and self.sps_seen and not self.pps_seen:
                out += self.pps
            out += (b'\0\0\0\1' if not out or t in (7, 8) else b'\0\0\1') + nal
            if not self.new_idr and t == 1:
                self.new_idr, self.sps_seen, self.pps_seen = True, False, False
        return bytes(out)


def read_box(f):
    """(type, whole box bytes) from a stream, None at a clean EOF."""
    h = f.read(8)
    if not h:
        return None
    if len(h) < 8:
        raise ValueError('video: truncated')
    size, t = struct.unpack('>I4s', h)
    if size == 1:
        x = f.read(8)
        h += x
        size = struct.unpack('>Q', x)[0]
    if size == 0:
        body = f.read()
    else:
        if size < len(h):
            raise ValueError(f'video: bad {t!r} box')
        body = f.read(size - len(h))
        if len(body) != size - len(h):
            raise ValueError('video: truncated')
    return t, h + body


def demux_video(f, d, duration_s, on_progress, trim=None, base=None):
    """Stream fMP4 from f into d/video.h264.part + d/index.bin. Returns (frames, bytes, max_ms).
    trim (a Trimmer): told every keyframe, and called after each write to punch what has played.
    base: the time base, when f starts mid-file (stream_layout's): the index then says the same ms as from 0."""
    vfd = os.open(os.path.join(d, 'video.h264.part'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    ifd = os.open(os.path.join(d, 'index.bin'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    at, conv, tk, pos, count, max_ms, dts, moof, hdr = 0, None, None, 0, 0, 0, 0, None, False
    try:
        while (bx := read_box(f)) is not None:
            t, b = bx
            if t == b'moov':
                tk = trak_info(b)
                if tk['samples']:
                    raise ValueError('video: not fragmented')
                ds, _ = tk['stsd_at']
                etype = b[ds + 12:ds + 16]  # first sample entry: stsd payload + 8, its payload + 16
                if etype not in (b'avc1', b'avc3'):
                    raise ValueError(f'video: {etype!r}, not avc1')
                es, ee = ds + 16, ds + 8 + struct.unpack_from('>I', b, ds + 8)[0]
                w, h = struct.unpack_from('>HH', b, es + 24)
                cs, _ = kid(b, es + 78, ee, b'avcC')
                conv = AnnexB(b[cs:])
            elif t == b'moof':
                if conv is None:
                    raise ValueError('video: moof before moov')
                moof = (at, b)
            elif t == b'mdat' and moof:
                m_at, mb = moof
                samples, dts = parse_moof(mb, dts, tk['trex'])
                if not samples:
                    moof = None
                    at += len(b)
                    continue
                data_at = at + (16 if struct.unpack_from('>I', b)[0] == 1 else 8)
                payload = memoryview(b)[data_at - at:]
                if base is None:
                    base = min(s[2] + s[3] for s in samples)
                if not hdr:
                    hdr = True
                    dur = (samples[1][2] - samples[0][2]) if len(samples) > 1 else (tk['trex'][0] or 1)
                    fps = Fraction(tk['ts'], dur).limit_denominator(1001)
                    os.write(ifd, HDR.pack(b'MGYI', 1, w, h, fps.numerator, fps.denominator, 0, bytes(4)))
                vbuf, ibuf = [], []
                for off, sz, sdts, cto, sf in samples:
                    o = m_at + off - data_at
                    if o < 0 or o + sz > len(payload):
                        raise ValueError('video: sample outside mdat')
                    au = conv.convert(payload[o:o + sz])
                    key = is_key(sf)
                    if count == 0 and not key:
                        raise ValueError('video: first sample is not a keyframe')
                    ms = max(0, round((sdts + cto - base) * 1000 / tk['ts']))
                    max_ms = max(max_ms, ms)
                    vbuf.append(au)
                    ibuf.append(REC.pack(ms, pos, len(au), key))
                    if trim is not None and key:
                        trim.key(ms, pos)
                    pos += len(au)
                    count += 1
                # order matters to the reader: bytes, then their records, then the count
                os.write(vfd, b''.join(vbuf))
                os.write(ifd, b''.join(ibuf))
                os.pwrite(ifd, struct.pack('<I', count), 16)
                moof = None
                on_progress(max_ms, count, pos)
                if trim is not None:
                    trim.after_write(vfd)
            at += len(b)
    finally:
        os.close(vfd)
        os.close(ifd)
    if not count:
        raise ValueError('video: no frames')
    os.replace(os.path.join(d, 'video.h264.part'), os.path.join(d, 'video.h264'))
    return count, pos, max_ms


# ---------------------------------------------------------------- video: WebM (Matroska) stream -> VP9 frames + MGYI

EBML_MAGIC = b'\x1a'  # first byte of the EBML header ID 1A45DFA3; an MP4 starts with a box size (0x00)
SEGMENT, CLUSTER, INFO, TRACKS = 0x18538067, 0x1F43B675, 0x1549A966, 0x1654AE6B
SIMPLEBLOCK, BLOCKGROUP, BLOCK, REFBLOCK, TIMECODE = 0xA3, 0xA0, 0xA1, 0xFB, 0xE7


def _vint(f, marker):
    """An EBML ID (marker kept) or size (marker dropped; -1 = unknown) from f; None at a clean EOF."""
    b = f.read(1)
    if not b:
        return None
    n = 9 - b[0].bit_length()
    if n > 8:
        raise ValueError('vp9: bad EBML vint')
    rest = f.read(n - 1)
    if len(rest) != n - 1:
        raise ValueError('vp9: truncated')
    v = int.from_bytes(b + rest, 'big')
    if marker:
        return v
    v &= (1 << 7 * n) - 1
    return -1 if v == (1 << 7 * n) - 1 else v


def _element(f):
    eid = _vint(f, True)
    if eid is None:
        return None
    size = _vint(f, False)
    if size is None:
        raise ValueError('vp9: truncated')
    return eid, size


def _body(f, n):
    b = f.read(n)
    if len(b) != n:
        raise ValueError('vp9: truncated')
    return b


def _children(b):
    """(id, payload) of the elements in bytes b."""
    s = io.BytesIO(b)
    while (el := _element(s)) is not None:
        if el[1] < 0:
            raise ValueError(f'vp9: unknown-size {el[0]:x} inside a sized element')
        yield el[0], _body(s, el[1])


def _webm_track(tracks):
    """(number, w, h, default duration ns or 0) of the V_VP9 video track in a Tracks payload."""
    for eid, te in _children(tracks):
        if eid != 0xAE:
            continue
        t = {k: v for k, v in _children(te)}
        if t.get(0x83) != b'\x01':
            continue  # not video
        if t.get(0x86, b'').rstrip(b'\0') != b'V_VP9':
            raise ValueError(f"vp9: video track {t.get(0x86)!r}, not V_VP9")
        v = {k: int.from_bytes(x, 'big') for k, x in _children(t.get(0xE0, b''))}
        return int.from_bytes(t[0xD7], 'big'), v.get(0xB0, 0), v.get(0xBA, 0), int.from_bytes(t.get(0x23E383, b''), 'big')
    raise ValueError('vp9: no video track')


def demux_webm(f, d, duration_s, on_progress, trim=None, base=None):
    """Stream WebM from f into d/video.h264.part (raw VP9 frames, superframes whole) + d/index.bin (codec VP9).
    Segment and Cluster may have an unknown size: their children are read in turn; anything else is read by its
    size (Cues may come before the first Cluster). Returns (frames, bytes, max_ms).
    trim, base (ns): as demux_video's, when f starts at a cluster (webm_layout's); frames before its first keyframe go."""
    vfd = os.open(os.path.join(d, 'video.h264.part'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    ifd = os.open(os.path.join(d, 'index.bin'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    scale, track, cluster, pos, count, max_ms, pending, mid = 1000000, None, None, 0, 0, 0, [], base is not None

    def header(fps):
        os.write(ifd, HDR.pack(b'MGYI', 1, track[1], track[2], fps.numerator, fps.denominator, 0, VP9))

    def frame(rel, key, data):
        nonlocal base
        if cluster is None:
            raise ValueError('vp9: block outside a cluster')
        t = (cluster + rel) * scale  # ns
        base = t if base is None else base
        if not pending and not count and not key:
            if mid:
                return
            raise ValueError('vp9: first frame is not a keyframe')
        pending.append((max(0, round((t - base) / 1e6)), key, bytes(data)))
        if not count and not track[3] and len(pending) < 16:
            return  # no DefaultDuration: the rate from the first 16 frames
        if not count:
            span = pending[-1][0] - pending[0][0]
            header(Fraction(10 ** 9, track[3]).limit_denominator(1001) if track[3]
                   else Fraction(1000 * (len(pending) - 1), span or 1).limit_denominator(1001))
        flush()

    def flush():
        nonlocal pos, count, max_ms
        if not pending:
            return
        recs = []
        for ms, key, data in pending:
            recs.append(REC.pack(ms, pos, len(data), key))
            if trim is not None and key:
                trim.key(ms, pos)
            pos += len(data)
            max_ms = max(max_ms, ms)
        # order matters to the reader: bytes, then their records, then the count
        os.write(vfd, b''.join(x[2] for x in pending))
        os.write(ifd, b''.join(recs))
        count += len(pending)
        os.pwrite(ifd, struct.pack('<I', count), 16)
        pending.clear()
        on_progress(max_ms, count, pos)
        if trim is not None:
            trim.after_write(vfd)

    try:
        while (el := _element(f)) is not None:
            eid, size = el
            if eid in (SEGMENT, CLUSTER):
                if eid == CLUSTER:
                    cluster = None  # until its Timecode
                continue  # walk into it: its children follow
            if size < 0:
                raise ValueError(f'vp9: unknown-size element {eid:x}')
            if eid == INFO:
                scale = next((int.from_bytes(v, 'big') for k, v in _children(_body(f, size)) if k == 0x2AD7B1), scale)
            elif eid == TRACKS:
                track = _webm_track(_body(f, size))
            elif eid == TIMECODE:
                cluster = int.from_bytes(_body(f, size), 'big')
            elif eid in (SIMPLEBLOCK, BLOCKGROUP):
                b = _body(f, size)
                key = None
                if eid == BLOCKGROUP:
                    kids = dict(_children(b))
                    if BLOCK not in kids:
                        continue
                    b, key = kids[BLOCK], REFBLOCK not in kids
                if track is None:
                    raise ValueError('vp9: block before Tracks')
                n = 9 - b[0].bit_length()
                tn = int.from_bytes(b[:n], 'big') & ((1 << 7 * n) - 1)
                rel, flags = struct.unpack_from('>hB', b, n)
                if tn != track[0]:
                    continue
                if flags & 0x06:
                    raise ValueError('vp9: laced block')
                frame(rel, bool(flags & 0x80) if key is None else key, memoryview(b)[n + 3:])
            else:
                while size:  # skipped by its size, in pieces
                    size -= len(_body(f, min(size, 1 << 20)))
        if pending and not count:
            span = pending[-1][0] - pending[0][0]
            header(Fraction(1000 * (len(pending) - 1), span).limit_denominator(1001) if span else Fraction(30))
        flush()
    finally:
        os.close(vfd)
        os.close(ifd)
    if not count:
        raise ValueError('vp9: no frames')
    os.replace(os.path.join(d, 'video.h264.part'), os.path.join(d, 'video.h264'))
    return count, pos, max_ms


SEEKHEAD, CUES = 0x114D9B74, 0x1C53BB6B


def webm_layout(get, size):
    """The WebM file's start (get(s, e): bytes s..e-1): its bytes up to Tracks (what demux_webm reads before a
    cluster), the time base (the first cue's, as demux_webm takes it from 0; ns) and every cued cluster
    [(byte offset, start ms)], from the Cues before the first Cluster (YouTube's) or where the SeekHead says.
    None without usable Cues."""
    head = get(0, min(HEAD_BYTES, size))

    def at(i, n):   # bytes i..i+n-1: from the head, grown when they follow it, else asked on their own
        nonlocal head
        if len(head) < i + n <= size and i <= len(head) + HEAD_BYTES:
            head += get(len(head), min(size, i + n + (2 << 10)))
        return head[i:i + n] if i + n <= len(head) else get(i, i + n)

    def el(i):      # (id, size, header length) at i
        b = io.BytesIO(at(i, min(12, size - i)))
        eid, n = _element(b) or (None, -1)
        return eid, n, b.tell()

    eid, n, h = el(0)
    if eid != 0x1A45DFA3 or n < 0:
        return None
    i = h + n
    eid, n, h = el(i)
    if eid != SEGMENT:
        return None
    seg = i + h
    end = seg + n if n >= 0 else size
    if end > size:
        return None
    i, scale, track, init, cues, cues_at = seg, 1000000, None, 0, None, None
    while i < end:
        eid, n, h = el(i)
        if eid == CLUSTER or n < 0:
            break
        if eid in (INFO, TRACKS, CUES, SEEKHEAD):
            body = at(i + h, n)
            if eid == INFO:
                scale = next((int.from_bytes(v, 'big') for k, v in _children(body) if k == 0x2AD7B1), scale)
                init = max(init, i + h + n)
            elif eid == TRACKS:
                track, init = _webm_track(body), max(init, i + h + n)
            elif eid == CUES:
                cues = body
            else:
                for k, v in _children(body):
                    sk = dict(_children(v)) if k == 0x4DBB else {}
                    if sk.get(0x53AB) == CUES.to_bytes(4, 'big') and 0x53AC in sk:
                        cues_at = seg + int.from_bytes(sk[0x53AC], 'big')
        i += h + n
    if track is None:
        return None
    if cues is None and cues_at is not None and cues_at < end:
        eid, n, h = el(cues_at)
        if eid == CUES and n > 0:
            cues = at(cues_at + h, n)
    if cues is None:
        return None
    points = {}
    for k, cp in _children(cues):
        t = off = None
        for k2, v in _children(cp) if k == 0xBB else ():
            if k2 == 0xB3:
                t = int.from_bytes(v, 'big')
            elif k2 == 0xB7:
                tp = dict(_children(v))
                if int.from_bytes(tp.get(0xF7, b''), 'big') == track[0] and 0xF1 in tp:
                    off = seg + int.from_bytes(tp[0xF1], 'big')
        if t is not None and off is not None and (off not in points or t < points[off]):
            points[off] = t   # a cluster holding several cued keyframes: its first
    if not points:
        return None
    pts = sorted(points.items())
    if any(b[1] <= a[1] for a, b in zip(pts, pts[1:])):
        return None
    base = pts[0][1] * scale
    return {'init': at(0, init), 'base': base, 'frags': [(o, round((t * scale - base) / 1e6)) for o, t in pts],
            'end': end, 'webm': True}


# ---------------------------------------------------------------- fetch

class _Log:
    def __init__(self):
        self.last = ''

    def debug(self, msg):
        pass

    info = warning = debug

    def error(self, msg):
        self.last = msg


def pick_pyz(pyz):
    """The yt-dlp to load: --pyz (an update, below), unless the service's own ytdlp.pyz is as new or that one is
    unreadable (a newer RideTube service brought a newer yt-dlp than the last update)."""
    own = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ytdlp.pyz')
    if not pyz or os.path.abspath(pyz) == own:
        return own
    mine, theirs = pyz_version(own), pyz_version(pyz)
    if not theirs:
        return own
    return own if mine and ytdlp_version_key(mine) >= ytdlp_version_key(theirs) else pyz


def load_ytdlp(pyz, cache_dir):
    if 'yt_dlp' in sys.modules:  # serve preloaded it
        return sys.modules['yt_dlp']
    picked = pick_pyz(pyz)
    sys.path.insert(0, picked)
    if os.path.isfile(CA) and 'certifi' not in sys.modules:
        # yt-dlp uses certifi when it can import it: this one names the car's CA bundle (none is in ytdlp.pyz)
        import types
        certifi = types.ModuleType('certifi')
        certifi.where = lambda: CA
        certifi.contents = lambda: open(CA, encoding='ascii').read()
        sys.modules['certifi'] = certifi
    else:
        os.environ['TMPDIR'] = cache_dir
    try:
        import yt_dlp
    except Exception:  # noqa: BLE001 - an update that does not import: the service's own ytdlp.pyz
        own = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ytdlp.pyz')
        if picked == own:
            raise
        sys.path.remove(picked)
        for n in [n for n in sys.modules if n.split('.')[0] in ('yt_dlp', 'yt_dlp_ejs')]:
            del sys.modules[n]
        sys.path.insert(0, own)
        import yt_dlp
    try:
        import yt_dlp.networking.common  # noqa: F401 - cert_time_hook's
    except ImportError:
        pass
    no_captions(yt_dlp)
    cert_time_hook(yt_dlp)
    return yt_dlp


def no_captions(yt_dlp):
    """Drop the player responses' captions before yt-dlp lists them: it builds a URL per auto-caption language x
    format (~22k urlencode calls, ~30 s of the car's CPU on a long video) and we never use subtitles."""
    ie = getattr(getattr(getattr(yt_dlp.extractor, 'youtube', None), '_video', None), 'YoutubeIE', None)
    orig = getattr(ie, '_initial_extract', None)
    if orig is None or getattr(orig, '_no_captions', False):
        return

    def initial_extract(self, *args, **kw):
        r = orig(self, *args, **kw)
        for pr in (r[4] if isinstance(r, tuple) and len(r) > 4 and isinstance(r[4], list) else []):
            if isinstance(pr, dict):
                pr.pop('captions', None)
        return r
    initial_extract._no_captions = True
    ie._initial_extract = initial_extract


def ydl_params(a, fmt, hook=None):
    return {'format': fmt, 'cachedir': a.cache_dir, 'logger': _Log(), 'quiet': True, 'noprogress': True,
            'no_warnings': True, 'noplaylist': True, 'fixup': 'never', 'concurrent_fragment_downloads': 4,
            # the API's default is no retry: one 20 s stall (googlevideo pacing) ended the video; a retry resumes it
            'retries': 10, 'socket_timeout': 30,
            'js_runtimes': {'node': {'path': a.node}}, 'remote_components': [],
            'progress_hooks': [hook] if hook else []} | ydl_proxy()


def ydl_proxy():
    """The Store's proxy for yt-dlp (its own connections skip socket.create_connection). A wrong setting: none, and
    the name guard (install_proxy) fails its lookups, so it cannot go direct either."""
    return {'proxy': proxy_url(PROXY)} if PROXY and 'error' not in PROXY else {}


# visionos: avc1 https + AAC 140, no PO token, no JS solve, no tv_samsung ad wait (5 s); measured 2026-11-03
PLAYER_CLIENTS = ['visionos']


def usable(info, avc1=False):
    fs = info.get('formats') or []
    return (any(playable_codec(f, avc1) and f.get('protocol') in (None, 'https') for f in fs)
            and any(f.get('ext') == 'm4a' and f.get('vcodec') == 'none' for f in fs))


def redirect(info):
    """The watch URL a Premium-only music video points to (yt-dlp gives {_type: url, url: <the free version's id>}
    instead of formats, seen on d1QurfIzO2E 2026-11-20); None for anything else."""
    t = info.get('url') if info.get('_type') == 'url' and info.get('ie_key') == 'Youtube' else None
    return 'https://www.youtube.com/watch?v=' + t if isinstance(t, str) and ID_RE.match(t) else None


def extract(yt_dlp, a, url, hop=True):
    """One extraction: PLAYER_CLIENTS first, then once more with yt-dlp's default clients; one redirect followed."""
    info = extract_once(yt_dlp, a, url)
    to = hop and redirect(info)
    if not to:
        return info
    info = extract(yt_dlp, a, to, hop=False)
    info['original_url'] = url   # the asked video: load_info matches the prefetch on it
    return info


def extract_once(yt_dlp, a, url):
    if PLAYER_CLIENTS:
        p = dict(ydl_params(a, None), extractor_args={'youtube': {'player_client': PLAYER_CLIENTS}})
        try:
            info = yt_dlp.YoutubeDL(p).extract_info(url, download=False, process=False)
            if usable(info, a.avc1):
                return info
        except Exception:  # noqa: BLE001 - fall back to the default clients
            pass
    return yt_dlp.YoutubeDL(ydl_params(a, None)).extract_info(url, download=False, process=False)


INFO_MAX_S = 1800  # stream URLs last ~6 h; a prefetch older than this is redone


def load_info(path, vid, avc1=False):
    """extract's file for vid, if fresh; else None (fetch extracts itself)."""
    try:
        if path and time.time() - os.path.getmtime(path) < INFO_MAX_S:
            with open(path) as f:
                info = json.load(f)
            # a followed redirect keeps the asked id in original_url
            if (info.get('id') == vid or (info.get('original_url') or '').endswith('=' + vid)) and usable(info, avc1):
                return info
    except (OSError, ValueError, AttributeError):
        pass
    return None


def info_dir(path):
    """extract's folder (cache/ytdlp/info): made and pruned here, as the fetch uid (the daemon, root, never
    writes below the fetch uid's folders)."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    for n in os.listdir(d):
        try:
            if time.time() - os.lstat(os.path.join(d, n)).st_mtime > INFO_MAX_S:
                os.unlink(os.path.join(d, n))
        except OSError:
            pass


def extract_cmd(a):
    if not ID_RE.match(a.id):
        fail('bad id')
    info_dir(a.dir)
    install_dns(a.lease)
    yt_dlp = load_ytdlp(a.pyz, a.cache_dir)
    try:
        info = extract(yt_dlp, a, 'https://www.youtube.com/watch?v=' + a.id)
    except Exception as e:  # noqa: BLE001
        fail(clock_wrong(e) or bot_check(e) or f'extract: {e}')
    tmp = a.dir + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(yt_dlp.YoutubeDL.sanitize_info(info), f)
    os.replace(tmp, a.dir)
    emit(ev='info', id=a.id, title=info.get('title'), duration_s=info.get('duration'))
    return 0


def audio_ranged(a, ya, info, meta, udta=lambda: b''):
    """The audio format yt-dlp picks, fetched with ranged_get; audio.head.m4a + audio_head once the prefix
    covers max(HEAD_MIN_MS, 1.5x the rest's estimated time). None when the format is not plain https with a size.
    udta(): the tags for the head (as far as known when it is written)."""
    f = ya.process_ie_result(copy.deepcopy(info), download=False)
    f = (f.get('requested_downloads') or [f])[0]
    total = f.get('filesize')
    if f.get('protocol') not in ('https', 'http') or not total or not f.get('url'):
        return None
    src = os.path.join(a.dir, 'audio.src.m4a')
    head, head_part = os.path.join(a.dir, 'audio.head.m4a'), os.path.join(a.dir, 'audio.head.m4a.part')
    dur_ms = (meta['duration_s'] or 0) * 1000
    t0, tick, st = time.monotonic(), Throttle(), {'head': None}
    # one connection until the head: four take the link from the video's start (measured: head 8 s, not 18 s)
    gate = threading.Event()

    def on_chunk(prefix, done):
        if prefix >= total:
            gate.set()
        if tick():
            emit(ev='audio', pct=min(100, round(done * 100 / total)))
        if st['head'] is not None or not prefix or prefix >= total:
            return
        rate = done / max(time.monotonic() - t0, 0.1)
        need = max(HEAD_MIN_MS, 1500 * (total - done) / rate)
        try:
            r = flatten_m4a(src, head_part, limit=prefix, udta=udta())
        except (ValueError, struct.error, OSError) as e:
            print(f'head at {prefix}: {e}', file=sys.stderr, flush=True)
            gate.set()
            return
        if r is None:
            st['head'] = 0  # a plain MP4: no head, audio_ready only
            gate.set()
            return
        if r[1] >= min(need, dur_ms or need):
            os.replace(head_part, head)
            st['head'] = r[1]
            gate.set()
            emit(ev='audio_head', ms=r[1], bytes=r[0])

    ctx = tls_ctx()
    try:
        ranged_get(f['url'], f.get('http_headers') or {}, src, total, on_chunk, conns=AUDIO_CONNS, first=AUDIO_FIRST,
                   chunk=AUDIO_CHUNK, ctx=ctx, gate=gate)
    except BaseException:
        os.remove(src)  # yt-dlp's fallback would take this holed file for a finished download (continuedl)
        raise
    finally:
        try:
            os.remove(head_part)
        except OSError:
            pass
    return src


def ram_free(d):
    try:
        s = os.statvfs(d)
        return s.f_bavail * s.f_frsize
    except OSError:
        return 0


def mem_available():
    """MemAvailable in bytes, None when /proc/meminfo has none (not Linux)"""
    try:
        with open(MEMINFO) as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) << 10
    except (OSError, ValueError, IndexError):
        pass
    return None


def mem_ok():
    m = mem_available()
    return m is None or m >= MEM_FLOOR


def window_ms(ahead, behind, bps):
    """(ahead, behind) ms: the asked ones, cut to WINDOW_BYTES (BEHIND_BYTES of it behind) at bps bytes/s"""
    behind = min(behind, BEHIND_BYTES * 1000 // bps)
    ahead = min(ahead, max(AHEAD_MIN_MS, (WINDOW_BYTES - behind * bps // 1000) * 1000 // bps))
    return int(ahead), int(behind)


def plan_stream(a, yt_dlp, url, yv, info):
    """--stream: the plan to fetch only the video's window (a dict), in --video-dir (RAM) when it fits there with
    MemAvailable over MEM_FLOOR, else in <dir>/win on flash; or None (a spill event says why): then the whole video is
    fetched into <dir> as without --stream (no ranges, no sidx or Cues, no hole punching). Decided before anything
    is written; the format's index is read here."""
    ahead, behind = a.stream
    why, fv = None, None
    try:
        fv = yv.process_ie_result(copy.deepcopy(info), download=False)
        fv = (fv.get('requested_downloads') or [fv])[0]
    except Exception as e:  # noqa: BLE001 - the old path reports what yt-dlp says
        why = f'formats: {e}'
    dur = info.get('duration') or 0
    if not why and (fv.get('protocol') not in ('https', 'http') or not fv.get('url') or not fv.get('filesize')):
        why = 'video not fetchable in ranges'
    elif not why and not dur:
        why = 'no duration'
    w = lay = src = None
    if not why:
        def refresh(fid):
            nfo = extract(yt_dlp, a, url)
            for f in nfo.get('formats') or []:
                if f.get('format_id') == fid and f.get('url'):
                    return f
            raise OSError(f'format {fid} gone')

        w = Window(ahead, behind, a.start_ms)
        w.listen(sys.stdin)   # (now: ytd.js going away ends the retries of the index's requests too)
        src = Source(fv, w, refresh)
        webm = fv.get('ext') == 'webm'
        try:
            lay = webm_layout(src.get, src.size) if webm else stream_layout(src.get)
        except (ValueError, struct.error, TypeError, KeyError, IndexError) as e:
            lay = None
            print(f'index: {e}', file=sys.stderr, flush=True)
        except OSError as e:   # the link, or the URL gone for good
            fail(f'video: {e}')
        if lay is None or lay['end'] != src.size:
            why = 'no cues to stream it from' if webm else 'no fragment index (sidx) to stream it from'
    if why:
        emit(ev='spill', why=why, dir=a.dir)
        return None
    bps = max(1, int(fv['filesize'] / dur))
    w.ahead, w.behind = window_ms(ahead, behind, bps)
    need = int(bps * (w.ahead + w.behind) // 1000 + 3 * VIDEO_CHUNK + RAM_FLOOR)
    punch, vdir, ram_why = punch_fn(), None, None
    if not a.video_dir:
        ram_why = 'no RAM folder'
    elif not mem_ok():
        ram_why = f'{mem_available() >> 20} MiB of memory available'
    else:
        room = min(a.budget, ram_free(a.video_dir)) if a.budget else ram_free(a.video_dir)
        if need > room:
            ram_why = f'needs {need >> 20} MiB of RAM, {room >> 20} MiB there'
        elif not can_punch(a.video_dir, punch):
            ram_why = 'no hole punching on ' + a.video_dir
        else:
            vdir = a.video_dir
    if vdir is None:
        vdir = os.path.join(a.dir, 'win')
        os.makedirs(vdir, exist_ok=True)
        if not can_punch(vdir, punch):
            emit(ev='spill', why='no hole punching on ' + vdir, dir=a.dir)
            return None
    return {'w': w, 'src': src, 'lay': lay, 'punch': punch, 'need': need, 'dir': vdir, 'ram': vdir == a.video_dir,
            'ram_why': ram_why}


def fetch_stream(a, meta, plan, audio, done_a):
    """The video from the fragment at --start-ms, through a PacedReader into the plan's folder, punched behind
    playback; the audio as before, into <dir> (none with --video-only)."""
    w, src, lay, vdir = plan['w'], plan['src'], plan['lay'], plan['dir']
    frags = lay['frags']
    k = 0
    for i, (_, ms) in enumerate(frags):
        if ms <= a.start_ms:
            k = i
    off, from_ms = frags[k]
    w.fetched = w.play = from_ms if a.start_ms else 0
    # the first range: up to the first fragment FIRST_MS past the start (one request, then ytd.js can start)
    j = next((i for i in range(k + 1, len(frags)) if frags[i][1] >= from_ms + FIRST_MS), None)
    first = min(max((frags[j][0] if j is not None else lay['end']) - off, HEAD_BYTES), 4 * VIDEO_CHUNK)
    emit(ev='stream', ahead_ms=w.ahead, behind_ms=w.behind, need=plan['need'], video_bytes=src.size, from_ms=from_ms,
         fragment=k, fragments=len(frags), dir=vdir, stored='ram' if plan['ram'] else 'flash',
         codec='vp9' if lay['webm'] else 'h264', **({'why': plan['ram_why']} if plan['ram_why'] else {}))
    tick_v, dur_ms, last = Throttle(), (meta['duration_s'] or 0) * 1000, {'said': None, 'now': None}

    def say_video():
        if last['now'] and last['now'] != last['said']:
            ms, frames, nbytes = last['now']
            last['said'] = last['now']
            emit(ev='video', ms=ms, frames=frames, bytes=nbytes, dl=reader.bytes, waited=round(w.waited),
                 pct=min(100, round(ms * 100 / dur_ms)) if dur_ms else 0)

    if plan['ram']:
        def free_ok():
            return ram_free(vdir) >= RAM_FLOOR and mem_ok()
    else:
        def free_ok():   # (flash: the window waits rather than fill /var/opt, shared with the Store)
            return ram_free(vdir) >= MIN_FREE
    trim = Trimmer(w, plan['punch'])
    reader = PacedReader(src, off, w, free_ok, before_wait=say_video, first=first, on_wait=trim.poll)
    ta = None
    if not a.video_only:
        ta = threading.Thread(target=audio, daemon=True)
        ta.start()

    def vprog(ms, frames, nbytes):
        w.fetched = ms
        last['now'] = (ms, frames, nbytes)
        if tick_v():
            say_video()

    demux = demux_webm if lay['webm'] else demux_video
    try:
        frames, nbytes, ms = demux(Prefixed(lay['init'], reader), vdir, meta['duration_s'], vprog, trim=trim,
                                   base=lay['base'])
    except Exception as e:  # noqa: BLE001 - a VP9 stream we cannot read: the daemon fetches again with --avc1
        fail(f'vp9: {e}' if lay['webm'] and not isinstance(e, OSError) else f'video: {e}')
    emit(ev='video', ms=ms, frames=frames, bytes=nbytes, dl=reader.bytes, waited=round(w.waited), pct=100)
    if ta is not None:
        ta.join()
        if not done_a:
            fail('audio: unfinished')
    emit(ev='done')
    return 0


def fetch(a):
    if not ID_RE.match(a.id):
        fail('bad id')
    os.umask(0o022)
    install_dns(a.lease)
    yt_dlp = load_ytdlp(a.pyz, a.cache_dir)
    url = 'https://www.youtube.com/watch?v=' + a.id
    tick_a, done_a = Throttle(), {}

    def audio_hook(st):
        if st.get('status') == 'downloading' and tick_a():
            total = st.get('total_bytes') or st.get('total_bytes_estimate')
            if total:
                emit(ev='audio', pct=min(100, round(st.get('downloaded_bytes', 0) * 100 / total)))

    ya = yt_dlp.YoutubeDL(dict(ydl_params(a, AUDIO_FMT, audio_hook),
                               outtmpl={'default': os.path.join(a.dir, 'audio.src.%(ext)s')}))
    yv = yt_dlp.YoutubeDL(dict(ydl_params(a, video_fmt(a.height, not a.avc1)), outtmpl={'default': '-'}))
    info = load_info(a.info, a.id, a.avc1)
    looked_up = info is None
    try:
        info = info or extract(yt_dlp, a, url)
    except Exception as e:  # noqa: BLE001 - any yt-dlp failure is reported to the daemon
        fail(clock_wrong(e) or bot_check(e) or f'extract: {e}')
    plan = plan_stream(a, yt_dlp, url, yv, info) if a.stream else None
    if a.video_only and not plan:
        fail('video: cannot stream it again from there')   # (a restart: ytd.js plays on without the picture)
    if plan and looked_up:
        # this lookup, for a fetch that starts again at another point (a seek, a dropped link): --info, no 2.5 s lookup
        tmp = os.path.join(a.dir, 'stream-info.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(yt_dlp.YoutubeDL.sanitize_info(info), f)
        os.replace(tmp, os.path.join(a.dir, 'stream-info.json'))
    meta = {'id': a.id, 'title': info.get('title'), 'channel': info.get('channel') or info.get('uploader'),
            'duration_s': info.get('duration'),
            'chapters': [[int((c.get('start_time') or 0) * 1000), str(c.get('title') or '')[:80]]
                         for c in (info.get('chapters') or [])[:100]]}
    with open(os.path.join(a.dir, 'meta.json.tmp'), 'w') as f:
        json.dump(meta, f)
    os.replace(os.path.join(a.dir, 'meta.json.tmp'), os.path.join(a.dir, 'meta.json'))
    emit(ev='meta', title=meta['title'], duration_s=meta['duration_s'], chapters=meta['chapters'])
    why = unplayable(info, a.avc1)
    if why:
        fail(why)
    # the tags the car's player shows (title, channel, the thumbnail as cover): the cover fetched alongside, so
    # playback never waits for it; the head gets what is there when it is written, audio.m4a waits up to 5 s
    art = {'cover': None}

    def cover():
        art['cover'] = fetch_cover(a.id, tls_ctx())

    tc = threading.Thread(target=cover, daemon=True)
    if not a.video_only:
        tc.start()

    def udta():
        return tags_udta(meta['title'], meta['channel'], art['cover'])

    def audio():
        try:
            src = None
            try:
                src = audio_ranged(a, ya, info, meta, udta)
            except Exception as e:  # noqa: BLE001 - yt-dlp's own download, as before 0.2.1
                print(f'ranged audio failed, yt-dlp downloads it: {e}', file=sys.stderr, flush=True)
            if src is None:
                r = ya.process_ie_result(copy.deepcopy(info), download=True)
                src = r['requested_downloads'][0]['filepath']
            part = os.path.join(a.dir, 'audio.m4a.part')
            tc.join(timeout=5)
            n, _ = flatten_m4a(src, part, udta=udta())
            os.replace(part, os.path.join(a.dir, 'audio.m4a'))
            os.remove(src)
            done_a['ok'] = True
            emit(ev='audio_ready', bytes=n)
        except BaseException as e:  # noqa: BLE001
            fail(f'audio: {ya.params["logger"].last or e}')

    if plan:
        return fetch_stream(a, meta, plan, audio, done_a)

    rfd, wfd = os.pipe()
    sys.stdout = io.TextIOWrapper(os.fdopen(wfd, 'wb'))  # yt-dlp writes outtmpl '-' to sys.stdout.buffer

    def video():
        try:
            yv.process_ie_result(copy.deepcopy(info), download=True)
        except BaseException as e:  # noqa: BLE001
            fail(f'video: {yv.params["logger"].last or e}')
        sys.stdout.buffer.close()

    ta = threading.Thread(target=audio, daemon=True)
    tv = threading.Thread(target=video, daemon=True)
    ta.start()
    tv.start()
    tick_v, dur_ms = Throttle(), (meta['duration_s'] or 0) * 1000

    def vprog(ms, frames, nbytes):
        if tick_v():
            # the whole video lands on /var/opt, shared with the Store and the other tools: stop before it fills
            free = shutil.disk_usage(a.dir).free
            if free < MIN_FREE:
                fail(f'head unit storage almost full ({free >> 20} MiB free): video stopped')
            emit(ev='video', ms=ms, frames=frames, bytes=nbytes,
                 pct=min(100, round(ms * 100 / dur_ms)) if dur_ms else 0)

    webm = False
    try:
        with os.fdopen(rfd, 'rb') as f:
            webm = f.peek(1)[:1] == EBML_MAGIC
            frames, nbytes, ms = (demux_webm if webm else demux_video)(f, a.dir, meta['duration_s'], vprog)
    except Exception as e:  # noqa: BLE001
        # a VP9 stream we cannot read: the daemon fetches again with --avc1 (a network error is not that)
        fail(f'vp9: {e}' if webm and not isinstance(e, OSError) else f'video: {yv.params["logger"].last or e}')
    emit(ev='video', ms=ms, frames=frames, bytes=nbytes, pct=100)
    tv.join()
    ta.join()
    if not done_a:
        fail('audio: unfinished')
    emit(ev='done')
    sys.stdout = _out
    return 0


# ---------------------------------------------------------------- check (as root on the car)

def check(a):
    vid = a.id or DEFAULT_CHECK_ID
    if not ID_RE.match(vid):
        sys.exit('bad id')
    work = os.path.join(a.cache_dir, 'ytfetch-check')
    if os.geteuid() == 0:
        uid = a.uid
        if uid is None:
            with open('/var/opt/jap-ridetube-d/state/uids.json') as f:
                uid = int(json.load(f)['fetch'])
        if not a.json:
            os.makedirs(work, exist_ok=True)
            os.chown(work, uid, uid)
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    if a.json:
        return check_json(a)
    os.makedirs(work, exist_ok=True)
    ok_all = []

    def line(name, ok, t0, info=''):
        ok_all.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name:<22} {time.monotonic() - t0:7.2f}s  {info}", flush=True)

    t = time.monotonic()
    servers = install_dns(a.lease)
    try:
        if PROXY:  # the proxy looks the names up: nothing to resolve here
            line('dns', 'error' not in PROXY, t, PROXY.get('error') or f"looked up by the proxy {PROXY['label']}")
        else:
            ips = socket.getaddrinfo('www.youtube.com', 443)
            line('dns', bool(servers), t, f"{ips[0][4][0]} via {servers or 'system resolver (no lease)'}")
    except OSError as e:
        line('dns', False, t, e)
    t = time.monotonic()
    try:
        import ssl
        import urllib.request
        ctx = tls_ctx()
        with urllib.request.urlopen('https://www.youtube.com/robots.txt', context=ctx, timeout=10) as r:
            line('tls robots.txt', r.status == 200, t, f'HTTP {r.status} {len(r.read())} B')
    except Exception as e:  # noqa: BLE001
        line('tls robots.txt', False, t, e)
    t = time.monotonic()
    try:
        v = subprocess.run([a.node, '--version'], capture_output=True, text=True, timeout=10).stdout.strip()
        line('node', v.startswith('v') and int(v[1:].split('.')[0]) >= 22, t, f'{a.node} {v} (yt-dlp needs >= 22)')
    except (OSError, subprocess.SubprocessError) as e:
        line('node', False, t, e)
    cache = os.path.join(work, 'ytdlp-cache')
    shutil.rmtree(cache, ignore_errors=True)
    a.cache_dir = cache
    yt_dlp = load_ytdlp(a.pyz, cache)
    for name in ('extract cold', 'extract warm'):
        t = time.monotonic()
        try:
            info = extract(yt_dlp, a, 'https://www.youtube.com/watch?v=' + vid)
            line(name, bool(info.get('formats')), t, f"{len(info.get('formats') or [])} formats")
        except Exception as e:  # noqa: BLE001
            line(name, False, t, e)
    out = os.path.join(work, vid)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    cmd = [sys.executable, '-I', os.path.abspath(__file__), 'fetch', vid, out, '--cache-dir', cache,
           '--height', str(a.height), '--node', a.node, '--lease', a.lease] + (['--pyz', a.pyz] if a.pyz else []) + \
        ([] if cert_time else ['--no-cert-time']) + (['--avc1'] if a.avc1 else [])
    t = time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, env=proxy_child_env(), cwd='/')
    t_audio = t_head = t_lead = t_dl = None  # t_dl: first progress event, after yt-dlp's 'sleep as required by the site'
    rate20, last = 0, {}
    for raw in p.stdout:
        ev, now = json.loads(raw), time.monotonic() - t
        last = ev
        if ev['ev'] in ('audio', 'video') and t_dl is None:
            t_dl = now
        if ev['ev'] == 'video' and now - t_dl <= 20:
            rate20 = ev['bytes'] / max(now - t_dl, 0.01)
        if ev['ev'] == 'audio_head':
            t_head = now
            print(f"     audio_head at {now:.2f}s: {ev['ms']} ms, {ev['bytes']} B", flush=True)
        if ev['ev'] == 'audio_ready':
            t_audio = now
            print(f"     audio_ready at {now:.2f}s: {ev['bytes']} B, "
                  f"{ev['bytes'] / max(now - (t_dl or 0), 0.01) / 1e6:.2f} MB/s from the first byte", flush=True)
        if ev['ev'] == 'video' and ev['ms'] >= 10000 and t_lead is None:
            t_lead = now
            print(f"     10 s video lead at {now:.2f}s: {ev['bytes']} B", flush=True)
        if ev['ev'] == 'error':
            print('     error:', ev['msg'], flush=True)
    rc = p.wait()
    line('fetch', rc == 0 and last.get('ev') == 'done', t,
         f"head {t_head and round(t_head, 2)}s, audio {t_audio and round(t_audio, 2)}s, 10s-lead {t_lead and round(t_lead, 2)}s, "
         f"first byte {t_dl and round(t_dl, 2)}s, video first 20 s {rate20 / 1e6:.2f} MB/s")
    t = time.monotonic()
    try:
        n = validate(os.path.join(out, 'index.bin'), os.path.join(out, 'video.h264'))
        line('index', True, t, f'{n} records')
    except (OSError, AssertionError, ValueError, struct.error) as e:
        line('index', False, t, e)
    print('ALL PASS' if all(ok_all) else 'SOME FAILED', flush=True)
    return 0 if all(ok_all) else 1


def _why(e):
    """An exception as one of a few fixed phrases (health.js shows only these): resolve_a's errors name the DNS server."""
    if isinstance(e, urllib.error.URLError) and isinstance(e.reason, BaseException):
        e = e.reason
    if isinstance(e, urllib.error.HTTPError):
        return f'HTTP {e.code}'
    if isinstance(e, ProxyError):
        return 'proxy failed'
    if isinstance(e, ssl.SSLCertVerificationError):
        return 'wrong clock: ' + time.strftime('%Y-%m-%d', time.gmtime()) if clock_wrong(e) else 'certificate not trusted'
    for kind, phrase in ((ssl.SSLError, 'TLS error'), ((socket.timeout, TimeoutError), 'timed out'),
                         (socket.gaierror, 'name not resolved'), (ConnectionRefusedError, 'connection refused'),
                         (ConnectionResetError, 'connection reset'), (ImportError, 'not found')):
        if isinstance(e, kind):
            return phrase
    import errno
    return {errno.ENETUNREACH: 'network unreachable', errno.EHOSTUNREACH: 'no route to host'}.get(getattr(e, 'errno', None), 'failed')


def check_json(a):
    """check --json: the daemon's health probes only (yt-dlp loads, DNS, TLS to YouTube), no extract, no fetch, no
    file written. One JSON line each, flushed as it is known: {"ev":"check","name","ok","s","info"} (+ "version",
    "servers"), then {"ev":"done","ok"}. No address in any line: the daemon shows them on the car's screen."""
    ok_all = []

    def line(name, ok, t0, info, **kw):
        ok_all.append(bool(ok))
        print(json.dumps(dict(ev='check', name=name, ok=bool(ok), s=round(time.monotonic() - t0, 2), info=info, **kw)),
              flush=True)

    t = time.monotonic()
    try:
        load_ytdlp(a.pyz, a.cache_dir)
        from yt_dlp.version import __version__ as v
        line('ytdlp', True, t, 'loaded', version=v)
    except Exception as e:  # noqa: BLE001
        line('ytdlp', False, t, _why(e))
    t = time.monotonic()
    servers = install_dns(a.lease)
    resolved = False
    try:
        if PROXY:  # the proxy looks the names up: TLS is tried whatever this row says
            resolved = True
            line('dns', 'error' not in PROXY, t, 'proxy setting wrong' if 'error' in PROXY else 'looked up by the proxy',
                 servers=len(servers))
        else:
            socket.getaddrinfo('www.youtube.com', 443)
            resolved = True
            line('dns', bool(servers), t, 'resolved' if servers else 'no uplink lease', servers=len(servers))
    except OSError as e:
        line('dns', False, t, _why(e), servers=len(servers))
    t = time.monotonic()
    if not resolved:
        line('tls', False, t, 'skipped: no DNS')
    else:
        try:
            ctx = tls_ctx()
            with urllib.request.urlopen('https://www.youtube.com/robots.txt', context=ctx, timeout=5) as r:
                line('tls', r.status == 200, t, f'HTTP {r.status}')
        except Exception as e:  # noqa: BLE001
            line('tls', False, t, _why(e))
    print(json.dumps({'ev': 'done', 'ok': all(ok_all)}), flush=True)
    return 0 if all(ok_all) else 1


REQUEST_MAX = 8 << 20


def request_cmd():
    """request: one HTTP request for the daemon, as its `ytd.js --fetch` child does it (the daemon runs this one instead
    while the clock is wrong: Node 8 cannot skip only the certificate dates). {method, url, headers, body (JSON or
    null), timeoutMs} on stdin; {status, body (utf8)} or {error} on stdout. No redirects, 8 MiB at most, plain http
    only to this machine (the tests' fake relay)."""
    try:
        q = json.loads(sys.stdin.read())
        u = urllib.parse.urlsplit(str(q['url']))
        loop = u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost')
        if u.scheme != 'https' and not loop:
            raise ValueError('https only')
        body = None if q.get('body') is None else json.dumps(q['body'], separators=(',', ':'), ensure_ascii=False).encode()
        h = dict(q.get('headers') or {})
        if body is not None:
            h['Content-Type'] = 'application/json'
        timeout = (q.get('timeoutMs') or 10000) / 1000
        c = (http.client.HTTPConnection(u.hostname, u.port, timeout=timeout) if loop
             else http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout, context=tls_ctx()))
        c.request(str(q.get('method') or 'GET'), (u.path or '/') + ('?' + u.query if u.query else ''), body=body, headers=h)
        r = c.getresponse()
        data = r.read(REQUEST_MAX + 1)
        if len(data) > REQUEST_MAX:
            raise ValueError('answer too big')
        out = {'status': r.status, 'body': data.decode('utf-8', 'replace')}
    except Exception as e:  # noqa: BLE001 - every failure is the daemon's error text
        out = {'error': clock_wrong(e) or bot_check(e) or str(e)[:300] or type(e).__name__}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode('utf-8'))
    sys.stdout.flush()
# ---------------------------------------------------------------- yt-dlp updates
# YouTube changes break yt-dlp now and then, and a fixed yt-dlp is out within days; the one in ytdlp.pyz waits for the
# next RideTube service. update fetches the newest yt-dlp release itself, as the fetch uid: the official zipapp,
# checked against the release's SHA2-256SUMS, repacked as tools/build_pyz.sh does (yt_dlp, yt_dlp_ejs and
# __main__.py, precompiled for this Python), loaded once in a child, then named in <cache-dir>/pyz/current.json. The
# daemon passes that as --pyz; ytdlp.pyz stays the fallback. Nothing is fetched when the newest is not newer than both.

RELEASES = 'https://github.com/yt-dlp/yt-dlp/releases'
YTDLP_VERSION_RE = re.compile(r'^\d{4}\.\d{2}\.\d{2}(\.\d+)?$')
ZIPAPP_MAX = 32 << 20      # the zipapp is ~3 MiB
SUMS_MAX = 64 << 10


def ytdlp_version_key(v):
    return tuple(int(x) for x in v.split('.'))


def pyz_version(pyz):
    """yt-dlp's __version__ in a pyz or the zipapp, read without importing it; None when unreadable."""
    import zipfile
    try:
        if os.path.isdir(pyz):  # an unpacked yt-dlp on sys.path (the tests' stand-in)
            with open(os.path.join(pyz, 'yt_dlp', 'version.py'), 'rb') as f:
                src = f.read()
        else:
            with zipfile.ZipFile(pyz) as z:
                src = z.read('yt_dlp/version.py')
        m = re.search(r"__version__\s*=\s*'([^']+)'", src.decode('utf-8', 'replace'))
    except Exception:  # noqa: BLE001 - BadZipFile, zlib.error: a damaged pyz is no version, never a crash
        return None
    return m.group(1) if m and YTDLP_VERSION_RE.match(m.group(1)) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def latest_ytdlp(releases, ctx, timeout):
    """The newest release's tag, from where <releases>/latest redirects (github.com, no API rate limit)."""
    import urllib.error
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    loc = None
    try:
        with opener.open(urllib.request.Request(releases + '/latest', method='HEAD'), timeout=timeout) as r:
            loc = r.headers.get('Location')
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get('Location')
    tag = (loc or '').rstrip('/').rsplit('/', 1)[-1]
    return tag if YTDLP_VERSION_RE.match(tag) else None


def download(url, dst, limit, ctx, timeout):
    """url (redirects followed) -> dst; its sha256. Past limit bytes: ValueError."""
    import hashlib
    h, n = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r, open(dst, 'wb') as f:
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            n += len(b)
            if n > limit:
                raise ValueError(f'{url}: over {limit} bytes')
            h.update(b)
            f.write(b)
    return h.hexdigest()


def repack_zipapp(src, out):
    """The official zipapp -> out, as tools/build_pyz.sh makes ytdlp.pyz: only yt_dlp, yt_dlp_ejs and __main__.py
    (no native code), every module precompiled (unchecked-hash .pyc beside each .py: the zip can't hold new ones)."""
    import compileall
    import py_compile
    import zipfile
    tree = out + '.tree'
    shutil.rmtree(tree, ignore_errors=True)
    with zipfile.ZipFile(src) as z:       # the zipapp: a shebang line, then the zip
        names = [n for n in z.namelist() if (n == '__main__.py' or n.split('/')[0] in ('yt_dlp', 'yt_dlp_ejs')) and not n.endswith('/')]
        if not any(n.startswith('yt_dlp/') for n in names) or not any(n.startswith('yt_dlp_ejs/') for n in names):
            raise ValueError('unexpected zipapp layout')
        bad = [n for n in names if n.endswith(('.so', '.dylib', '.pyd'))]
        if bad:
            raise ValueError(f'native code in the zipapp: {bad!r}')
        for n in names:
            z.extract(n, tree)
    if not compileall.compile_dir(tree, quiet=2, legacy=True, stripdir=tree,
                                  invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH):
        raise ValueError('a module does not compile')
    when = time.gmtime(315532800)[:6]     # zip's floor, 1980: one time for every entry, as build_pyz.sh
    with zipfile.ZipFile(out + '.tmp', 'w', zipfile.ZIP_DEFLATED) as z:
        for d, dirs, files in os.walk(tree):
            dirs.sort()
            for f in sorted(files):
                p = os.path.join(d, f)
                zi = zipfile.ZipInfo(os.path.relpath(p, tree), when)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                with open(p, 'rb') as fh:
                    z.writestr(zi, fh.read())
    with open(out + '.tmp', 'rb') as fh:
        os.fsync(fh.fileno())
    os.replace(out + '.tmp', out)
    shutil.rmtree(tree, ignore_errors=True)


def loads(pyz):
    """yt-dlp's version as a fresh Python imports it from pyz (as check.sh does), else None."""
    code = 'import sys;sys.path.insert(0,sys.argv[1]);import yt_dlp,yt_dlp_ejs;print(yt_dlp.version.__version__)'
    try:
        r = subprocess.run([sys.executable, '-I', '-c', code, pyz], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=300, env={})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.decode('utf-8', 'replace').strip() if r.returncode == 0 else None


def update(a):
    """update --cache-dir <d> [--pyz <bundled>] [--releases URL]: one {"ev":"ytdlp", state, version, bundled} on
    stdout, state "current" (nothing newer) or "updated" (current.json names it now); {"ev":"error"} otherwise."""
    try:
        os.nice(10)                       # repacking compiles ~1000 modules: after the player and the HMI
    except OSError:
        pass
    install_dns(a.lease)
    root = os.path.join(a.cache_dir, 'pyz')
    os.makedirs(root, exist_ok=True)
    bundled = pyz_version(a.pyz or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ytdlp.pyz'))
    cur = None
    try:
        with open(os.path.join(root, 'current.json')) as f:
            c = json.load(f)
        if YTDLP_VERSION_RE.match(c['version']) and pyz_version(c['pyz']) == c['version']:
            cur = c
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if cur and bundled and ytdlp_version_key(bundled) >= ytdlp_version_key(cur['version']):
        os.remove(os.path.join(root, 'current.json'))   # a newer service brought this one or newer: its own again
        shutil.rmtree(os.path.dirname(cur['pyz']), ignore_errors=True)
        cur = None
    have = max([v for v in (bundled, cur and cur['version']) if v], key=ytdlp_version_key, default=None)
    ctx = tls_ctx()
    releases = (a.releases or RELEASES).rstrip('/')
    try:
        latest = latest_ytdlp(releases, ctx, 20)
    except OSError as e:
        fail(f'yt-dlp releases: {e}')
    if not latest:
        fail('yt-dlp releases: no latest release')
    if have and ytdlp_version_key(latest) <= ytdlp_version_key(have):
        emit(ev='ytdlp', state='current', version=have, bundled=bundled)
        return 0
    if shutil.disk_usage(root).free < MIN_FREE + (64 << 20):
        fail('yt-dlp: not enough space on the head unit')
    work = os.path.join(root, latest)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    app = os.path.join(work, 'yt-dlp')
    try:
        download(f'{releases}/download/{latest}/SHA2-256SUMS', app + '.sums', SUMS_MAX, ctx, 30)
        with open(app + '.sums', encoding='utf-8', errors='replace') as f:
            want = [ln.split()[0].lower() for ln in f if len(ln.split()) == 2 and ln.split()[1].lstrip('*') == 'yt-dlp']
        if len(want) != 1:
            raise ValueError('no yt-dlp line in SHA2-256SUMS')
        if download(f'{releases}/download/{latest}/yt-dlp', app, ZIPAPP_MAX, ctx, 300) != want[0]:
            raise ValueError('sha256 mismatch: not the file the release lists')
        if pyz_version(app) != latest:
            raise ValueError(f'the zipapp is not {latest}')
        pyz = os.path.join(work, 'ytdlp.pyz')
        repack_zipapp(app, pyz)
        got = loads(pyz)
        if got != latest:
            raise ValueError(f'does not load (got {got!r})')
    except (OSError, ValueError) as e:
        shutil.rmtree(work, ignore_errors=True)
        fail(f'yt-dlp {latest}: {e}')
    for n in (app, app + '.sums'):
        os.remove(n)
    tmp = os.path.join(root, 'current.json.tmp')
    with open(tmp, 'w') as f:
        json.dump({'version': latest, 'pyz': pyz, 'sha256': want[0]}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(root, 'current.json'))
    for n in os.listdir(root):            # the older updates
        if n != latest and YTDLP_VERSION_RE.match(n):
            shutil.rmtree(os.path.join(root, n), ignore_errors=True)
    emit(ev='ytdlp', state='updated', version=latest, bundled=bundled)
    return 0


def validate(index_path, h264_path):
    """mgyi.validate's rules; returns the record count."""
    with open(index_path, 'rb') as f:
        data = f.read()
    magic, ver, _w, _h, _fn, _fd, n, _codec = HDR.unpack_from(data)
    assert magic == b'MGYI' and ver == 1, 'bad header'
    assert len(data) == HDR.size + n * REC.size, 'bad length'
    recs = [REC.unpack_from(data, HDR.size + i * REC.size) for i in range(n)]
    assert recs and recs[0][1] == 0 and recs[0][3] == 1, 'first record must be key at pos 0'
    assert all(x[1] + x[2] == y[1] for x, y in zip(recs, recs[1:])), 'gap/overlap'
    assert sum(r[2] for r in recs) == os.path.getsize(h264_path), 'sizes != file size'
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=('fetch', 'extract', 'serve', 'check', 'request', 'update'))
    ap.add_argument('id', nargs='?')
    ap.add_argument('dir', nargs='?')
    ap.add_argument('--cache-dir', required=True)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--node', default=NODE)
    ap.add_argument('--lease', default=LEASE)
    ap.add_argument('--pyz')
    ap.add_argument('--uid', type=int)
    ap.add_argument('--info')
    ap.add_argument('--avc1', action='store_true', help='fetch: H.264 only (no VP9)')
    ap.add_argument('--json', action='store_true', help='check: the health probes only, as JSON lines')
    ap.add_argument('--no-cert-time', action='store_true', help="TLS: skip only the certificate's validity dates")
    ap.add_argument('--releases')
    # WP8: only the video's window kept, in --video-dir (RAM) or <dir>/win (flash); else the whole video as before
    ap.add_argument('--stream', type=lambda s: tuple(int(x) for x in s.split(',')), metavar='AHEAD_MS,BEHIND_MS')
    ap.add_argument('--video-dir', help='--stream: the RAM folder for the window (on the capped tmpfs)')
    ap.add_argument('--budget', type=int, default=0, help='bytes of RAM the stream may plan for (0: the tmpfs free)')
    ap.add_argument('--start-ms', type=int, default=0, help='--stream: from the fragment holding this point')
    ap.add_argument('--video-only', action='store_true', help='--stream: the audio is there already (a restart)')
    a = ap.parse_args(argv)
    if (a.start_ms or a.video_only or a.video_dir) and not a.stream:
        fail('--start-ms, --video-only and --video-dir go with --stream')
    global cert_time
    cert_time = not a.no_cert_time
    global _PROXY_SET
    if not _PROXY_SET:  # once: serve runs main() again for its job
        _PROXY_SET = True
        install_proxy(proxy_env())
        os.environ.pop('RT_PROXY', None)  # yt-dlp starts node with this env: the password stays here
        os.environ.pop('RT_PROXY_ERROR', None)
    if PROXY and 'error' in PROXY and a.cmd in ('fetch', 'extract', 'serve', 'update'):
        fail(PROXY['error'])  # yt-dlp gets no proxy then, and an IP-literal URL would pass the name guard
    if a.cmd == 'request':
        install_dns(a.lease)
        return request_cmd()
    if a.cmd == 'check':
        return check(a)
    if a.cmd == 'update':
        return update(a)
    if a.cmd == 'serve':
        install_dns(a.lease)
        load_ytdlp(a.pyz, a.cache_dir).YoutubeDL(ydl_params(a, None))  # the first YoutubeDL() costs ~0.35 s more on the car
        emit(ev='warm')
        try:
            job = json.loads(sys.stdin.readline())
        except ValueError:
            return 0  # the daemon closed us unused
        if not isinstance(job, list) or not job or job[0] not in ('fetch', 'extract'):
            fail('bad job')
        return main([str(x) for x in job])
    if not a.id or not a.dir:
        fail('usage: fetch|extract <id> <dir|info.json> --cache-dir <d>')
    return extract_cmd(a) if a.cmd == 'extract' else fetch(a)


if __name__ == '__main__':
    sys.exit(main())
