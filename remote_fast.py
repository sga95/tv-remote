# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tiny LAN web remote: serves a button page and forwards key events to a TV via adb.
Run: python remote_fast.py   Then open the printed URL on the phone (same Wi-Fi).

Hardening (LAN-only tool):
- binds to the laptop LAN address only, never 0.0.0.0
- secret path token gates every request, compared in constant time, never logged
- actions are POST only; key codes and app launches come from a fixed allowlist
- adb is called with an argument list (no shell on the laptop), user input is only a dict key
- closed by default: needs tv.txt, a certificate, and a paired device before the plain URL answers
- only private-range clients are served
- strict response headers (no-store, CSP, nosniff, no referrer, no framing)
TLS is mandatory (no cert.pem and key.pem = no start). Secrets live in files next to the script, never in the code.
Speed: one persistent `adb shell` (no adb process per tap), HTTP keep-alive, page reacts on pointerdown.
"""
import collections, hashlib, hmac, ipaddress, json, os, re, secrets, socket, ssl, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

_dir = os.path.dirname(os.path.abspath(__file__))


def _cfg(name):
    """Settings live in small files next to this script (tv.txt = TV address:port, adb.txt = path to adb),
    so the code itself holds nothing personal and can be published as is."""
    f = os.path.join(_dir, name)
    return open(f).read().strip() if os.path.exists(f) else ""


ADB = _cfg("adb.txt") or "adb"
TV = _cfg("tv.txt")
if not TV:
    sys.exit("tv.txt is missing: put the TV address and port in it, for example 192.168.1.50:5555")
PORT = 8765
KEYS = {  # label -> adb keycode
    "Power": "KEYCODE_POWER", "Home": "KEYCODE_HOME", "Back": "KEYCODE_BACK",
    "Up": "KEYCODE_DPAD_UP", "Down": "KEYCODE_DPAD_DOWN", "Left": "KEYCODE_DPAD_LEFT",
    "Right": "KEYCODE_DPAD_RIGHT", "OK": "KEYCODE_DPAD_CENTER", "Menu": "KEYCODE_MENU",
    "Vol+": "KEYCODE_VOLUME_UP", "Vol-": "KEYCODE_VOLUME_DOWN", "Mute": "KEYCODE_VOLUME_MUTE",
    "Play/Pause": "KEYCODE_MEDIA_PLAY_PAUSE", "Ch+": "KEYCODE_CHANNEL_UP", "Ch-": "KEYCODE_CHANNEL_DOWN",
    "Input": "KEYCODE_TV_INPUT",
    "Pause": "KEYCODE_MEDIA_PAUSE",  # not on the page: for phone automations (pause only, never toggles)
    **{str(n): f"KEYCODE_{n}" for n in range(10)},  # number pad of the 90s layout
}
VOL = "cmd media_session volume --stream 3 "  # 3 = STREAM_MUSIC; the volume bar reads and sets the level directly
APPS = {  # label -> (package, color)
    "Netflix": ("com.netflix.ninja", "#e50914"), "Prime": ("com.amazon.amazonvideo.livingroom", "#00a8e1"),
    "YouTube": ("com.google.android.youtube.tv", "#ff0000"), "Spotify": ("com.spotify.tv.android", "#1db954"),
    "Twitch": ("tv.twitch.android.app", "#9146ff"), "Free My TV": ("io.freemytv", "#f5a623"),
}
def _snd(dialog, leveler, treble, bass):
    return "; ".join(f"settings put global {k} {v}" for k, v in (
        ("sound_dialog_enhancement", dialog), ("sound_advanced_volume_leveler", leveler), ("sound_treble", treble), ("sound_bass", bass)))


# Audio presets, fixed strings only. Normal = the values read from the TV on 2026-11-10 (see diag/audio-restore-2026-11-10.txt).
SND = {"Voice": _snd(1, 1, 60, 45), "Movie": _snd(0, 0, 55, 60), "Night": _snd(1, 1, 50, 40), "Normal": _snd(0, 0, 50, 50)}
# Inline SVG icons (stroke style, ASCII only: the page can never turn into mojibake).
def _i(d):
    return '<svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.8 stroke-linecap=round stroke-linejoin=round>' + d + "</svg>"


_SPK = '<path d="M4 9v6h4l5 4V5L8 9z"/>'
CHEV = _i('<path d="M6 15l6-6 6 6"/>')
ICONS = {
    "Power": _i('<path d="M12 3v9"/><path d="M6.4 6.6a8 8 0 1 0 11.2 0"/>'),
    "Input": _i('<path d="M4 12h11"/><path d="M11 8l4 4-4 4"/><path d="M15 4h5v16h-5"/>'),
    "Home": _i('<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>'),
    "Back": _i('<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'),
    "Menu": _i('<path d="M4 7h16M4 12h16M4 17h16"/>'),
    "Vol+": _i(_SPK + '<path d="M18 9v6M15 12h6"/>'),
    "Vol-": _i(_SPK + '<path d="M15 12h6"/>'),
    "Mute": _i(_SPK + '<path d="M16 9l5 6M21 9l-5 6"/>'),
    "Play/Pause": _i('<path d="M4 5l8 7-8 7z"/><path d="M17 5v14M21 5v14"/>'),
    "Ch+": CHEV,
    "Ch-": '<svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.8 stroke-linecap=round stroke-linejoin=round><path d="M6 9l6 6 6-6"/></svg>',
}

_tf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")  # stable URL so the phone can bookmark it
if not os.path.exists(_tf):
    with open(_tf, "w") as f:
        f.write(secrets.token_urlsafe(16))
TOKEN = open(_tf).read().strip()
_af = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.txt")  # second secret: remote + status panel, for the owner only
if not os.path.exists(_af):
    with open(_af, "w") as f:
        f.write(secrets.token_urlsafe(16))
ADMIN = open(_af).read().strip()
# Phone pairing without IP rules: opening the owner URL on a device gives it a random cookie (256 bits, carries no data).
# The server keeps only SHA-256 hashes of the paired cookies in device.txt (one per line, newest 10), never the cookies.
# Once a pairing exists, the plain remote URL also needs a paired cookie, so a leaked token alone is useless.
_df = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device.txt")


def _h(v):
    return hashlib.sha256(v.encode()).hexdigest()


DEVS = [l.strip() for l in open(_df)] if os.path.exists(_df) else []
DEVS = [l if len(l) == 64 else _h(l) for l in DEVS if l]  # an older device.txt held the plain cookie: hash it


def save_devs():
    with open(_df, "w") as f:
        f.write("\n".join(DEVS))


save_devs() if DEVS else None


def cookie_ok(headers):
    """True if the request carries a paired cookie. Every stored hash is compared, in constant time."""
    ok = False
    for c in headers.get("Cookie", "").split(";"):
        c = c.strip()
        if c.startswith("dev="):
            h = _h(c[4:])
            for d in DEVS:
                ok |= hmac.compare_digest(h, d)
    return ok
# Health and security counters only, in memory, never written to disk. No per-key history, nothing about what is watched.
ST = {"start": time.time(), "taps": 0, "restarts": 0, "ms_sum": 0.0, "ms_max": 0.0, "bad": 0, "locks": 0, "last_bad": ""}


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((TV.split(":")[0], 1))  # no packet sent, just picks the LAN interface
    return s.getsockname()[0]


NOWIN = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: when run by pythonw at logon, adb/netstat must not flash console windows
EV = collections.deque(maxlen=40)  # live security records (memory only): (time, what, who)


def ev(what, who):
    EV.appendleft((time.strftime("%H:%M:%S"), what, who))


def watch():
    """Every 15 s: any connection of this server or of adb that goes to the internet or to a host other than the TV is recorded once."""
    seen, tv_ip = set(), TV.split(":")[0]
    while True:
        try:
            ids = {str(os.getpid())}
            for l in subprocess.run(["tasklist", "/FI", "IMAGENAME eq adb.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=8, creationflags=NOWIN).stdout.splitlines():
                if l.startswith('"adb.exe"'):
                    ids.add(l.split('","')[1])
            for l in subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=8, creationflags=NOWIN).stdout.splitlines():
                c = l.split()
                if len(c) == 5 and c[3] in ("ESTABLISHED", "SYN_SENT") and c[4] in ids:
                    lp, rh = c[1].rsplit(":", 1)[1], c[2].rsplit(":", 1)[0]
                    if lp == str(PORT) or ipaddress.ip_address(rh).is_loopback or rh == tv_ip:
                        continue  # a client of ours, local adb plumbing, or the TV: expected
                    k = (c[4], rh)
                    if k not in seen:
                        seen.add(k)
                        ev("OUTBOUND " + ("to internet" if not ipaddress.ip_address(rh).is_private else "to unexpected LAN host"), f"{rh} pid {c[4]}")
        except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
            pass
        time.sleep(15)


_sh, _lock = None, threading.Lock()
FAILS = {}  # client ip -> (wrong-token count, lockout deadline)


def tv(cmd):
    """Run one shell line on the TV through one persistent `adb shell`.
    cmd is built only from the fixed allowlists above. A dead shell is restarted once."""
    global _sh
    t0 = time.perf_counter()
    with _lock:
        for _ in range(2):
            try:
                if _sh is None or _sh.poll() is not None:
                    ST["restarts"] += _sh is not None
                    subprocess.run([ADB, "connect", TV], capture_output=True, timeout=8, creationflags=NOWIN)  # TV rebooted or Wi-Fi blipped
                    _sh = subprocess.Popen([ADB, "-s", TV, "shell"], stdin=subprocess.PIPE,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
                _sh.stdin.write((cmd + "\n").encode())
                _sh.stdin.flush()
                break
            except (OSError, subprocess.TimeoutExpired):
                _sh = None
        ms = (time.perf_counter() - t0) * 1000
        ST["taps"] += 1
        ST["ms_sum"] += ms
        ST["ms_max"] = max(ST["ms_max"], ms)


def vol_get():
    """Current TV volume and its top value, read with a one-off adb call (the persistent shell has no stdout). {} if unreadable."""
    try:
        o = subprocess.run([ADB, "-s", TV, "shell", VOL + "--get"], capture_output=True, text=True, timeout=4, creationflags=NOWIN).stdout
        m = re.search(r"volume is (\d+) in range \[\d+\.\.(\d+)\]", o)
        return {"v": int(m[1]), "max": int(m[2])}
    except (subprocess.TimeoutExpired, OSError, TypeError):
        return {}


def stats():
    try:
        socket.create_connection((TV.split(":")[0], int(TV.partition(":")[2] or 5555)), 0.5).close()
        up = True
    except OSError:
        up = False
    run, ram = "unknown", "unknown"  # live view only: which allowlisted apps are in memory now, never stored
    if up:
        try:
            pk = {p: n for n, (p, _) in APPS.items()}
            o = subprocess.run([ADB, "-s", TV, "shell", "ps -A -o RSS,NAME | grep -E '" + "|".join(pk) + "'; grep -E 'MemTotal|MemAvailable' /proc/meminfo"],
                               capture_output=True, text=True, timeout=4, creationflags=NOWIN).stdout.splitlines()
            apps = [f"{pk[l.split()[1].split(':')[0]]} {int(l.split()[0]) // 1024} MB" for l in o if len(l.split()) == 2 and l.split()[0].isdigit() and l.split()[1].split(':')[0] in pk]
            run = ", ".join(apps) or "none"
            m = {l.split()[0]: int(l.split()[1]) // 1024 for l in o if l.startswith("Mem")}
            ram = f"{m['MemAvailable:']} free of {m['MemTotal:']} MB"
        except (subprocess.TimeoutExpired, OSError, KeyError, IndexError, ValueError):
            pass
    n = max(ST["taps"], 1)
    u = int(time.time() - ST["start"])
    return {"TV adb port": "reachable" if up else "NOT reachable", "apps running": run, "TV free RAM": ram,
            "adb shell": "alive" if _sh and _sh.poll() is None else "idle/dead",
            "uptime": f"{u // 3600}h {u % 3600 // 60}m", "taps": ST["taps"],
            "write time avg/max": f"{ST['ms_sum'] / n:.1f} / {ST['ms_max']:.1f} ms", "shell restarts": ST["restarts"],
            "wrong tokens": ST["bad"], "lockouts": ST["locks"], "last wrong token": ST["last_bad"] or "never",
            "locked now": sum(1 for c, t in FAILS.values() if c >= 5 and t > time.time()),
            "traffic watch": ("not available on this system" if sys.platform != "win32" else "ALERT, see below" if any(e[1].startswith(("OUTBOUND", "INBOUND")) for e in EV) else "clean"),
            "live records": "\n  " + "\n  ".join(" ".join(e) for e in list(EV)[:8]) if EV else "none"}


CSS = """:root{--bg:#0a0a0f;--s:#14141c;--s2:#1d1d29;--bd:#242431;--tx:#e9e9ef;--mu:#7d7d8c;--ac:#6ea8ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.2 -apple-system,system-ui,sans-serif;padding:14px 14px max(24px,env(safe-area-inset-bottom));-webkit-user-select:none;user-select:none}
main{max-width:440px;margin:auto;display:grid;gap:12px}
button{font:inherit;color:var(--tx);background:var(--s);border:1px solid var(--bd);border-radius:20px;height:56px;display:flex;align-items:center;justify-content:center;touch-action:manipulation;-webkit-tap-highlight-color:transparent;transition:transform .08s,background .15s}
button:active{transform:scale(.95);background:var(--s2)}
button:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
svg{width:24px;height:24px}
.apps{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.app{height:46px;font-size:14px;font-weight:600;border-radius:16px;position:relative;overflow:hidden}
.app::after{content:"";position:absolute;left:22%;right:22%;bottom:0;height:3px;border-radius:3px 3px 0 0;background:var(--c)}
.pad{position:relative;height:250px;border-radius:30px;border:1px solid var(--bd);background:radial-gradient(circle,#34344a 1.3px,transparent 1.9px) var(--bx,0) var(--by,0)/22px 22px,radial-gradient(circle,#1f1f2d,#12121a 72%);touch-action:none;overflow:hidden}
.e{position:absolute;color:#4a4a60;transition:color .15s}.e svg{width:28px;height:28px}
.u{top:12px;left:50%;margin-left:-14px}.d{bottom:12px;left:50%;margin-left:-14px;transform:rotate(180deg)}
.l{left:12px;top:50%;margin-top:-14px;transform:rotate(-90deg)}.r{right:12px;top:50%;margin-top:-14px;transform:rotate(90deg)}
.ring{position:absolute;left:50%;top:50%;width:76px;height:76px;margin:-38px;border-radius:50%;border:1.5px solid #34344a;display:grid;place-items:center;font-style:normal;transition:.15s}.ring::after{content:"";width:10px;height:10px;border-radius:50%;background:#55556a;transition:.15s}.ring.hit::after{background:var(--ac)}
.hit{color:var(--ac)}.ring.hit{border-color:var(--ac);background:#6ea8ff22;transform:scale(1.08)}
.nav,.dock{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.pill,.mid{display:flex;flex-direction:column;gap:8px}
.pill{background:var(--s);border:1px solid var(--bd);border-radius:28px;padding:6px}
.pill button{flex:1;border:0;background:transparent;height:auto;min-height:52px;border-radius:22px}.pill button:active{background:var(--s2)}
.mid button{flex:1;height:auto;min-height:52px}
.pill em{font-size:11px;font-weight:700;color:var(--mu);text-align:center;letter-spacing:.1em;font-style:normal}
.pw{color:#ff6b61;border-color:#4a2522;background:#1c1213}
h2{margin:6px 2px 0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mu);font-weight:600}
.seg{display:grid;grid-template-columns:repeat(4,1fr);background:var(--s);border:1px solid var(--bd);border-radius:18px;padding:4px;gap:4px}
.seg button{height:42px;border:0;background:transparent;border-radius:14px;font-size:14px;color:var(--mu)}
.seg button.on{background:var(--s2);color:var(--tx);box-shadow:inset 0 0 0 1px #34344a}
#st{margin:6px 0 0;padding:12px;border-radius:16px;background:#0f0f15;border:1px solid var(--bd);font:12px/1.5 ui-monospace,monospace;color:#8fb59a;white-space:pre-wrap}
.ghost{position:absolute;left:50%;top:50%;width:34px;height:34px;margin:-17px;border-radius:50%;background:#ffffff26;box-shadow:0 0 0 6px #ffffff0d;opacity:0;pointer-events:none;animation:hint 7s 1.2s infinite}
.used .ghost{display:none}
@keyframes hint{0%{opacity:0;transform:translate(-70px,0)}8%{opacity:.9}30%{opacity:.9;transform:translate(70px,0)}38%,39%{opacity:0;transform:translate(0,-60px)}46%{opacity:.9}68%{opacity:.9;transform:translate(0,60px)}76%,100%{opacity:0;transform:translate(0,60px)}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}.ghost{animation:none}}
.sm .apps,.sm .pad,.sm .snd,.sm .nav button:last-child,.sm .dock .pill:last-child,.sm .mid button:not(.pw){display:none}
.sm .nav,.sm .dock{grid-template-columns:repeat(2,1fr)}.sm button{min-height:64px}
.sm .dp{display:grid;grid-template:repeat(3,92px)/repeat(3,92px)}.sm .dp svg{width:34px;height:34px}.sm .go{font-size:20px;font-weight:700}
#vol,.dp,.num{display:none}
#vol{position:relative;flex:1;min-height:100px;border-radius:22px;background:#04090f;border:1px solid var(--bd);overflow:hidden;touch-action:none}
#vol i{position:absolute;left:0;right:0;bottom:0;height:var(--v,0%);background:linear-gradient(0deg,#0b4a6b,var(--ac));box-shadow:0 0 18px var(--ac)}
#vol b{position:absolute;inset:0;display:grid;place-items:center;font:700 22px ui-monospace,monospace;text-shadow:0 0 8px #000}
.fx{--bg:#03060d;--s:#07121f;--s2:#0c2036;--bd:#14405f;--tx:#d8f6ff;--mu:#5b8fb0;--ac:#39e6ff;font-family:ui-monospace,monospace}
.fx button,.fx .pill,.fx .pad,.fx .seg{box-shadow:0 0 14px #39e6ff1f,inset 0 0 10px #39e6ff12}
.fx .pad{background:radial-gradient(circle,#1c5f85 1.3px,transparent 1.9px) var(--bx,0) var(--by,0)/22px 22px,radial-gradient(circle,#0a2236,#040b14 72%)}
.fx .e{color:#1c5f85}.fx .ring{border-color:#1c5f85}
.fx #vol{display:block}.fx .v{display:none}
.r90{--bg:#3a342c;--s:#4b4c50;--s2:#606166;--bd:#2a2a2d;--tx:#f1efe6;--mu:#a8a59a;--ac:#f1efe6;font-family:Arial Narrow,Arial,sans-serif}
.r90 main{max-width:330px;background:#1c1c1e;border-radius:38px;padding:22px 18px;border:2px solid #0d0d0e;box-shadow:inset 0 2px 0 #3a3a3e,0 12px 30px #0008}
.r90 button{border-radius:10px;font-weight:700;box-shadow:0 4px 0 #111,inset 0 1px 0 #7b7c82}.r90 button:active{transform:translateY(3px);box-shadow:0 1px 0 #111}
.r90 .pill,.r90 .seg{background:transparent;border:0;padding:0}.r90 .pill button,.r90 .seg button{background:var(--s);color:var(--tx)}.r90 .seg button.on{background:var(--s2)}
.r90 .pw{background:#c22d24;color:#fff}.r90 .go{border-radius:50%}
.r90 .pad{display:none}.r90 .dp,.r90 .num{display:grid}
.num{grid-template-columns:repeat(3,1fr);gap:10px;order:-1}.num button{height:44px}.num button:last-child{grid-column:2}
.dp{grid-template:repeat(3,62px)/repeat(3,62px);gap:8px;justify-content:center}.dp button{height:auto}
.gu{grid-area:1/2}.gl{grid-area:2/1}.go{grid-area:2/2}.gr{grid-area:2/3}.gd{grid-area:3/2}
.gl svg{transform:rotate(-90deg)}.gr svg{transform:rotate(90deg)}.gd svg{transform:rotate(180deg)}"""

JS = """const T='@TOK@',p=u=>{navigator.vibrate&&navigator.vibrate(10);fetch('/'+T+'/'+u,{method:'POST'})},
k=b=>p('key/'+encodeURIComponent(b)),a=n=>p('app/'+encodeURIComponent(n)),
s=(n,el)=>{p('snd/'+encodeURIComponent(n));el.parentNode.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===el))},
fx=(el,c)=>{el.classList.add(c);setTimeout(()=>el.classList.remove(c),180)};
const pad=document.getElementById('pad'),D={Left:'l',Right:'r',Up:'u',Down:'d'},nav=d=>{k(d);fx(pad.querySelector('.'+D[d]),'hit')};
const STEP=28,TAP=16;let sx=null,sy,ax,ay,t0,mv,px,py,ox=0,oy=0,lock,last,mt,rt,tick;
const stop=()=>{sx=null;clearInterval(tick)};
pad.onpointerdown=e=>{pad.setPointerCapture(e.pointerId);pad.classList.add('used');sx=ax=px=e.clientX;sy=ay=py=e.clientY;t0=mt=Date.now();mv=0;lock=last=null;rt=0;clearInterval(tick);
tick=setInterval(()=>{if(last==null||Date.now()-mt<150)return;const h=lock=='x'?px-sx:py-sy,sg=(last=='Right'||last=='Down')?1:-1,o=h*sg;
if(o<40)return;const gap=Math.max(70,380-(o-40)*4);if(Date.now()-rt>=gap){rt=Date.now();nav(last)}},30)};
pad.onpointermove=e=>{if(sx==null)return;mv=Math.max(mv,Math.abs(e.clientX-sx),Math.abs(e.clientY-sy));
ox=(ox+e.clientX-px)%22;oy=(oy+e.clientY-py)%22;px=e.clientX;py=e.clientY;mt=Date.now();pad.style.setProperty('--bx',ox+'px');pad.style.setProperty('--by',oy+'px');
const dx=px-ax,dy=py-ay;let d=null;
if(lock!='y'&&Math.abs(dx)>=STEP&&Math.abs(dx)>=1.2*Math.abs(dy))d=dx>0?'Right':'Left';
else if(lock!='x'&&Math.abs(dy)>=STEP&&Math.abs(dy)>=1.2*Math.abs(dx))d=dy>0?'Down':'Up';
if(!d)return;
const back=last&&((last=='Right'&&d=='Left')||(last=='Left'&&d=='Right')||(last=='Up'&&d=='Down')||(last=='Down'&&d=='Up'));
if(back&&Math.max(Math.abs(dx),Math.abs(dy))<STEP*1.7)return;
lock=(d=='Left'||d=='Right')?'x':'y';last=d;ax=px;ay=py;rt=Date.now();nav(d)};
pad.onpointerup=()=>{if(sx!=null&&mv<TAP&&Date.now()-t0<600){k('OK');fx(pad.querySelector('.ring'),'hit')}stop()};pad.onpointercancel=stop;
const vol=document.getElementById('vol');let vm=100,vv=null,vs=null,v0=null,vy,vt=0,vq;
const vshow=v=>{vv=v;vol.style.setProperty('--v',v/vm*100+'%');vol.lastChild.textContent=v};
const vget=()=>fetch('/'+T+'/vol').then(r=>r.json()).then(j=>{if(j.max){vm=j.max;vs=j.v;vshow(j.v)}}).catch(()=>{});
// The newest level wins and at most one command goes out every 60 ms (the TV needs about 40 ms for one), so the TV follows the finger without a queue.
const vsend=()=>{clearTimeout(vq);const w=60-(Date.now()-vt);if(w>0){vq=setTimeout(vsend,w);return}if(vs!==vv){vt=Date.now();vs=vv;fetch('/'+T+'/vol/'+vv,{method:'POST'})}};
// Relative drag, 3 px per step: touching the bar never jumps the volume, only moving the finger changes it.
vol.onpointerdown=e=>{if(vv==null)return;vol.setPointerCapture(e.pointerId);v0=vv;vy=e.clientY};
vol.onpointermove=e=>{if(v0==null)return;vshow(Math.max(0,Math.min(vm,v0+Math.round((vy-e.clientY)/3))));vsend()};
vol.onpointerup=vol.onpointercancel=()=>{v0=null};
const lay=n=>{document.body.className=n;try{localStorage.lay=n}catch(e){}document.querySelectorAll('#lay button').forEach(x=>x.classList.toggle('on',x.dataset.l==n));if(n=='fx')vget()};
let l0='';try{l0=localStorage.lay||''}catch(e){}lay(l0);document.onvisibilitychange=()=>{document.hidden||document.body.className!='fx'||vget()};"""

STATS_JS = """const st=()=>fetch('/'+T+'/stats').then(r=>r.json()).then(j=>{document.getElementById('st').textContent=Object.entries(j).map(e=>e.join(': ')).join('\\n')});st();setInterval(st,5000)"""


def page(tok, admin=False):
    def b(key, cls=""):
        return f'<button class="{cls}" onpointerdown="k(\'{key}\')" aria-label="{key}">{ICONS[key]}</button>'
    apps = "".join(f'<button class=app style="--c:{c}" onpointerdown="a(\'{n}\')">{n}</button>' for n, (_, c) in APPS.items())
    snd = "".join(f'<button onpointerdown="s(\'{n}\',this)">{n}</button>' for n in SND)
    edges = "".join(f'<i class="e {d}">{CHEV}</i>' for d in "udlr")
    # Extra layouts reuse the same page: a body class (kept in the phone's localStorage) shows or hides these blocks.
    num = "".join(f'<button onpointerdown="k(\'{n}\')">{n}</button>' for n in "1234567890")
    dp = "".join(f'<button class=g{c} onpointerdown="k(\'{n}\')" aria-label="{n}">{"OK" if n == "OK" else CHEV}</button>'
                 for c, n in zip("ulord", ("Up", "Left", "OK", "Right", "Down")))
    lay = "".join(f'<button data-l="{c}" onpointerdown="lay(\'{c}\')">{n}</button>' for c, n in (("", "Touch"), ("fx", "Future"), ("r90", "90s"), ("sm", "Simple")))
    body = (f'<main><section class=apps>{apps}</section><section class=num>{num}</section>'
            f'<section class=pad id=pad role=application aria-label="Touchpad: swipe to move, tap to select">{edges}<i class=ring></i><i class=ghost></i></section>'
            f'<section class=dp>{dp}</section>'
            f'<section class=nav>{b("Back")}{b("Home")}{b("Menu")}</section>'
            f'<section class=dock><div class=pill>{b("Vol+", "v")}<div id=vol role=slider aria-label="Volume: drag up or down"><i></i><b>--</b></div>{b("Mute")}{b("Vol-", "v")}</div>'
            f'<div class=mid>{b("Power", "pw")}{b("Play/Pause")}{b("Input")}</div>'
            f'<div class=pill>{b("Ch+")}<em>CH</em>{b("Ch-")}</div></section>'
            f'<h2 class=snd>Sound</h2><section class="seg snd">{snd}</section>'
            f'<h2>Layout</h2><section class=seg id=lay>{lay}</section>' + ("<pre id=st></pre>" if admin else "") + "</main>")
    js = JS.replace("@TOK@", tok) + (";" + STATS_JS if admin else "")
    return ('<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name=color-scheme content=dark><meta name=theme-color content="#0a0a0f"><meta name=apple-mobile-web-app-capable content=yes>'
            f'<title>TV remote</title><style>{CSS}</style></head><body>{body}<script>{js}</script></body></html>')


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: one TLS handshake per session, not per tap
    timeout = 30  # frees the thread of an idle connection

    def reply(self, code, body=b"", ctype="text/html; charset=utf-8", cookie=""):
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", f"dev={cookie}; Max-Age=7776000; Path=/; Secure; HttpOnly; SameSite=Strict")
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                     ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                     ("X-Frame-Options", "DENY"),
                     ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
                     ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"), ("Cross-Origin-Resource-Policy", "same-origin")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, path):
        """Return the path after the token, or None. Private clients and a valid token only."""
        ip = self.client_address[0]
        if not ipaddress.ip_address(ip).is_private:
            ev("INBOUND from non-LAN address", ip)
            return None
        n, until = FAILS.get(ip, (0, 0))
        head, _, rest = path.lstrip("/").partition("/")
        if n >= 5 and time.time() < until and not hmac.compare_digest(head.encode(), ADMIN.encode()):
            ev("request while locked out", ip)  # 5 wrong tokens: locked 5 min, but the owner secret always gets in
            return None
        # both digests are always computed, so timing does not reveal which secret was closer
        user, admin = hmac.compare_digest(head.encode(), TOKEN.encode()), hmac.compare_digest(head.encode(), ADMIN.encode())
        if admin or (user and cookie_ok(self.headers)):  # no paired device yet: only the owner URL works
            self.admin = admin
            FAILS.pop(ip, None)
            return rest
        if len(FAILS) > 1000:
            FAILS.clear()  # a flood of addresses must not grow memory without limit
        FAILS[ip] = (1 if n >= 5 else n + 1, time.time() + 300)  # n >= 5 here means the lockout just expired
        ST["bad"] += 1
        ST["locks"] += FAILS[ip][0] == 5
        ev("wrong token" if not user else "right token, device not paired", ip + (" -> LOCKED 5 min" if FAILS[ip][0] == 5 else ""))
        ST["last_bad"] = time.strftime("%H:%M:%S ") + ip
        return None

    def do_GET(self):
        rest = self.allowed(self.path)
        if rest == "":
            new = ""
            if self.admin and not cookie_ok(self.headers):  # the owner opens the owner URL on a device: pair it
                new = secrets.token_urlsafe(32)
                DEVS.append(_h(new))
                del DEVS[:-10]
                save_devs()
            self.reply(200, page(ADMIN if self.admin else TOKEN, self.admin).encode("utf-8"), cookie=new)
        elif rest == "vol":
            self.reply(200, json.dumps(vol_get()).encode(), "application/json")
        elif rest == "stats" and self.admin:
            self.reply(200, json.dumps(stats()).encode(), "application/json")
        else:
            self.reply(404)

    def do_POST(self):
        if self.headers.get("Content-Length", "0") != "0":
            self.close_connection = True  # the body is never read, so this connection must not be reused
        rest = self.allowed(self.path)
        kind, _, name = (rest or "").partition("/")
        name = unquote(name)
        if kind == "key" and name in KEYS:
            tv("input keyevent " + KEYS[name])
            return self.reply(200, b"ok", "text/plain")
        if kind == "app" and name in APPS:
            pkg = APPS[name][0]
            # launch first, then free the TV's small RAM: stop every other streaming app (never the launcher itself)
            stop = "".join("; am force-stop " + p for p, _ in APPS.values() if p not in (pkg, "io.freemytv")) if pkg != "io.freemytv" else ""
            tv("monkey -p " + pkg + " -c android.intent.category.LEANBACK_LAUNCHER 1" + stop)
            return self.reply(200, b"ok", "text/plain")
        if kind == "vol" and name.isdecimal() and len(name) <= 3 and int(name) <= 100:  # digits only, re-printed by int(): nothing else reaches the TV
            tv(VOL + "--set " + str(int(name)))
            return self.reply(200, b"ok", "text/plain")
        if kind == "snd" and name in SND:
            tv(SND[name])
            return self.reply(200, b"ok", "text/plain")
        self.reply(404)

    def log_message(self, *a):  # the token is in the URL, so never log requests
        pass


class S(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows SO_REUSEADDR lets a second server share the port and answer some requests
    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], OSError):  # phone closed/idle timeout: normal, stay silent
            super().handle_error(request, client_address)


if __name__ == "__main__":
    out = ""
    try:
        out = subprocess.run([ADB, "connect", TV], capture_output=True, text=True, timeout=10, creationflags=NOWIN).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass  # tv() reconnects on the first tap
    while True:
        try:
            ip = lan_ip()
            break
        except OSError:  # no network yet (logon before Wi-Fi): keep waiting, a crash here would never recover
            time.sleep(5)
    print(out, f"\nOpen on phone: https://{ip}:{PORT}/{TOKEN}\nOwner panel (remote + status): https://{ip}:{PORT}/{ADMIN}", flush=True)
    if sys.platform == "win32":  # the watch reads tasklist and netstat: Windows only, elsewhere the panel says so
        threading.Thread(target=watch, daemon=True).start()
    cert, key = os.path.join(_dir, "cert.pem"), os.path.join(_dir, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        sys.exit("cert.pem and key.pem are missing: the remote never serves plain HTTP")
    srv = S((ip, PORT), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print("TLS on: use https://", flush=True)
    srv.serve_forever()
