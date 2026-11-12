"""Self-check for remote_fast.py. Run: python test_remote_fast.py
Works on a temporary copy with its own secrets, so your real token, owner and pairing files are never touched."""
import ast, io, os, re, shutil, subprocess, sys, tempfile

# Tripwire, on for the whole run (including the import of remote_fast.py): every attempt to open a connection,
# resolve a name or start a program is recorded and then refused, so this test never contacts or launches anything.
tried = []
WATCH = ("socket.connect", "socket.getaddrinfo", "socket.gethostby", "socket.sendto", "socket.sendmsg", "subprocess.",
         "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile", "urllib.", "http.client.", "ftplib.", "smtplib.", "webbrowser.")


def _hook(name, args):
    if name.startswith(WATCH):
        tried.append((name, args[1] if name.startswith(("socket.connect", "subprocess.", "socket.sendto")) else args[0]))
        raise OSError("refused by the test tripwire: " + name)


sys.addaudithook(_hook)

here = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(here, "remote_fast.py"), tmp)
open(os.path.join(tmp, "tv.txt"), "w").write("192.0.2.1:5555")  # documentation address, never contacted
path = os.path.join(tmp, "remote_fast.py")
ns = {"__name__": "t", "__file__": path}
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), ns)
H, TOKEN, ADMIN = ns["H"], ns["TOKEN"], ns["ADMIN"]
sent = []
ns["tv"] = sent.append  # nothing is sent to a TV


class F(H):
    def __init__(self, path, cookie="", ip="192.168.0.9"):
        self.client_address, self.path, self.headers, self.out = (ip, 1), path, ({"Cookie": cookie} if cookie else {}), None

    def reply(self, code, body=b"", ctype="", cookie=""):
        self.out = (code, cookie)


def get(path, **k):
    f = F(path, **k)
    f.do_GET()
    return f.out


def post(path, **k):
    f = F(path, **k)
    f.do_POST()
    return f.out


assert TOKEN != ADMIN
assert get("/" + TOKEN + "/")[0] == 404, "closed by default: no paired device yet"
code, cookie = get("/" + ADMIN + "/")
assert code == 200 and len(cookie) >= 40, "owner URL pairs a device"
assert cookie not in open(os.path.join(tmp, "device.txt")).read(), "only a hash is stored"
assert get("/" + TOKEN + "/")[0] == 404, "right secret without the cookie is refused"
assert get("/" + TOKEN + "/", cookie="dev=" + cookie)[0] == 200, "paired device works"
assert get("/" + TOKEN + "/", cookie="dev=forged")[0] == 404
assert post("/" + TOKEN + "/key/OK", cookie="dev=" + cookie)[0] == 200 and "KEYCODE_DPAD_CENTER" in sent[-1]
assert post("/" + TOKEN + "/key/OK; reboot", cookie="dev=" + cookie)[0] == 404, "only allowlisted keys"
assert post("/" + TOKEN + "/snd/Evil", cookie="dev=" + cookie)[0] == 404
assert post("/" + TOKEN + "/key/7", cookie="dev=" + cookie)[0] == 200 and sent[-1].endswith("KEYCODE_7"), "number pad"
assert post("/" + TOKEN + "/vol/down/2", cookie="dev=" + cookie)[0] == 200 and sent[-1] == "input keyevent KEYCODE_VOLUME_DOWN & input keyevent KEYCODE_VOLUME_DOWN & ", "volume bar presses the volume key"
assert post("/" + TOKEN + "/vol/up/6", cookie="dev=" + cookie)[0] == 404, "at most 5 volume steps per request"
assert post("/" + TOKEN + "/vol/up/1; reboot", cookie="dev=" + cookie)[0] == 404, "volume steps come from a fixed table"
ns["vol_get"] = lambda: {"v": 1, "max": 100}  # no adb in the test
assert get("/" + TOKEN + "/vol", cookie="dev=" + cookie)[0] == 200 and get("/" + TOKEN + "/vol")[0] == 404, "volume read needs a paired device"
assert post("/" + TOKEN + "/priv/mic/block", cookie="dev=" + cookie)[0] == 200 and sent[-1] == "cmd sensor_privacy enable 0 microphone", "privacy switch"
assert post("/" + TOKEN + "/priv/cam/allow", cookie="dev=" + cookie)[0] == 200 and sent[-1] == "cmd sensor_privacy disable 0 camera"
assert post("/" + TOKEN + "/priv/mic/erase", cookie="dev=" + cookie)[0] == 404, "privacy switches come from a fixed table"
assert post("/" + TOKEN + "/priv/apps/revoke", cookie="dev=" + cookie)[0] == 200 and "pm revoke $p" in sent[-1], "revoke all is one fixed command"
assert post("/" + TOKEN + "/priv/apps/revoke", cookie="dev=" + cookie) and "com." not in sent[-1], "no app name is ever put into the command by the server"
# What the TV reports is read, not assumed: feed privacy() a recorded answer and check what it understood.
ns["adb"] = lambda *a, **k: ("sensors=[\n{\nsensor=1\ntoggles={\ntoggle_type=1\nstate_type=1\n}\n}\n{\nsensor=2\ntoggles={\ntoggle_type=1\nstate_type=2\n}\n}\n]\n"
                             "@cam\nNumber of camera devices: 0\n@in\n  1. Port ID: 2; \"Built-In Mic\"; {AUDIO_DEVICE_IN_BUILTIN_MIC, @:bottom}\n"
                             "  2. Port ID: 3; \"HDMI In\"; {AUDIO_DEVICE_IN_HDMI, @:}\n@apps\n  Package [com.netflix.ninja] (1a2b):\n"
                             "        android.permission.RECORD_AUDIO: granted=true, flags=[ USER_SET]\n  Package [com.example.quiet] (3c4d):\n"
                             "  Package [com.example.app] (5e6f):\n        android.permission.RECORD_AUDIO: granted=true\n"
                             "        android.permission.CAMERA: granted=true\n")
assert ns["privacy"]() == {"mic_blocked": True, "cam_blocked": False, "cameras": 0, "mics": ["Built-In Mic"],
                           "mic_apps": ["Netflix", "com.example.app"], "cam_apps": ["com.example.app"]}, "privacy report is parsed from the TV's answer"
ns["adb"] = lambda *a, **k: "garbage"
assert ns["privacy"]() == {}, "an unreadable answer shows as no answer, never as a guessed state"
assert get("/" + TOKEN + "/privacy", cookie="dev=" + cookie)[0] == 200 and get("/" + TOKEN + "/privacy")[0] == 404, "privacy report needs a paired device"
assert post("/" + TOKEN + "/tune/run", cookie="dev=" + cookie)[0] == 200 and sent[-1] == ns["TUNE"]["run"] and sent[-1].endswith("; am kill-all; pm trim-caches 999G"), "optimize is one fixed command"
assert 'case "$fg" in *"$p/"*) ;;' in sent[-1] and "io.freemytv" not in sent[-1], "optimize skips the app on screen and never names the launcher"
assert post("/" + TOKEN + "/tune/restart", cookie="dev=" + cookie)[0] == 200 and sent[-1] == ns["TUNE"]["run"] + "; reboot", "clean restart is one fixed command"
assert post("/" + TOKEN + "/tune/wipe", cookie="dev=" + cookie)[0] == 404
calls = []
ns["adb"] = lambda *a, **k: calls.append(a) or ("" if len(calls) < 3 else "answer")
assert ns["ask"]("question") == "answer" and calls[1][0] == "connect", "a TV that does not answer is connected again and asked once more"
ns["adb"] = lambda *a, **k: "MemTotal:        1727336 kB\nMemAvailable:     755988 kB\n@disk\n/dev/block/x   7651016 1809612   5693948  25% /data\n@up\n14563.73 46033.10\n@os\n14\n2025-07-05\n@layers\n  Current WebView package (name, version): (com.google.android.webview, 157.0.8103.40)\n    lastUpdateTime=2026-11-09 18:26:13\n    versionName=2024-05-01 S+\n"
assert ns["health"]() == {"ram_free": 738, "ram_total": 1686, "disk_free": 5560, "disk_total": 7471, "seconds": 14563, "hours": 4,
                          "android": "14", "patch": "2025-07-05", "webview": "157.0.8103.40", "play_services": "2026-11-09",
                          "play_system": "2024-05-01"}, "optimize panel numbers are parsed from the TV's answer"
ns["adb"] = lambda *a, **k: "garbage"
assert ns["health"]() == {}
assert get("/" + TOKEN + "/health", cookie="dev=" + cookie)[0] == 200 and get("/" + TOKEN + "/health")[0] == 404, "optimize panel needs a paired device"
assert post("/" + TOKEN + "/sec/cast/off", cookie="dev=" + cookie)[0] == 200 and sent[-1] == "pm disable-user --user 0 com.google.android.apps.mediashell", "security switch"
assert post("/" + TOKEN + "/sec/ads/limit", cookie="dev=" + cookie)[0] == 200 and sent[-1] == "settings put secure limit_ad_tracking 1"
assert post("/" + TOKEN + "/sec/cast/uninstall", cookie="dev=" + cookie)[0] == 404, "security switches come from a fixed table"
ns["adb"] = lambda *a, **k: "\n#next#\n".join((
    "null", "com.example.launcher/com.example.Listener", "0", "0", "No operations.", "", "null", "1\n0",
    "LISTEN 0 0 0.0.0.0:8008 0.0.0.0:* uid:10050 ino:1 sk:0\nLISTEN 0 0 *:5555 *:* uid:2000 ino:2 sk:0\n"
    "LISTEN 0 0 127.0.0.1:4000 0.0.0.0:* uid:10050 ino:3 sk:0\nLISTEN 0 0 *:40000 *:* uid:19998 ino:4 sk:0\nLISTEN 0 0 *:41000 *:* ino:5 sk:0\n",
    "package:com.example.cast uid:10050\npackage:com.netflix.ninja uid:10070\n",
    "UID NAME\n 2000 adbd\n19998 some@1.0-daemon\n10050 com.example.cast\n"))
assert ns["security"]() == {"accessibility": [], "listeners": ["com.example.launcher"], "location_on": False, "hotword_on": False,
                            "installers": [], "usage": [], "ads_limited": False, "cast": "on",
                            "ports": [[5555, "network debugging (adb, what this remote uses)", "adbd"], [8008, "Cast", "com.example.cast"],
                                      [40000, "unknown", "some@1.0-daemon"], [41000, "unknown", "the system"]]}, "security report is parsed from the TV's answer"
ns["adb"] = lambda *a, **k: "garbage"
assert ns["security"]() == {}
assert get("/" + TOKEN + "/security", cookie="dev=" + cookie)[0] == 200 and get("/" + TOKEN + "/security")[0] == 404, "security report needs a paired device"
assert get("/" + TOKEN + "/stats", cookie="dev=" + cookie)[0] == 404, "status panel is owner only"
assert get("/" + ADMIN + "/stats")[0] == 200
assert get("/" + ADMIN + "/", ip="8.8.8.8")[0] == 404, "non-LAN client refused"
for _ in range(5):
    get("/wrong/", ip="192.168.0.77")
assert get("/" + TOKEN + "/", cookie="dev=" + cookie, ip="192.168.0.77")[0] == 404, "5 wrong secrets lock the address"
assert get("/" + ADMIN + "/", ip="192.168.0.77")[0] == 200, "the owner secret still gets in"
assert all(ord(c) < 128 for c in ns["page"]("x", True)), "page is plain ASCII"

# --- Stays local: the code has no way to call out except adb to the TV (plus two read-only Windows tools for the traffic watch).
src = open(path, encoding="utf-8").read()
tree = ast.parse(src)
mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
assert mods == {"collections", "hashlib", "hmac", "ipaddress", "json", "os", "re", "secrets", "socket", "ssl", "subprocess", "sys",
                "threading", "time", "http.server", "urllib.parse"}, "imports changed: review the list, standard library only"
names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
assert not names & {"eval", "exec", "compile", "__import__", "getattr", "globals", "vars", "importlib"}, "no dynamic code or hidden lookups"
attrs = lambda mod: {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == mod}
assert attrs("subprocess") <= {"run", "Popen", "PIPE", "DEVNULL", "TimeoutExpired"}
assert attrs("socket") <= {"socket", "AF_INET", "SOCK_DGRAM", "create_connection"}
assert attrs("os") <= {"path", "getpid"}, "os is used for file paths and the own process id only"
calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
for c in calls:
    if isinstance(c.func.value, ast.Name) and c.func.value.id == "subprocess":
        prog = ast.unparse(c.args[0].elts[0]) if isinstance(c.args[0], ast.List) else "?"
        assert prog in ("ADB", "'tasklist'", "'netstat'"), "only adb, tasklist and netstat are ever started: " + prog
        assert not any(k.arg == "shell" for k in c.keywords), "never through a shell on the computer"
    if c.func.attr in ("connect", "create_connection", "connect_ex", "sendto"):
        assert ast.unparse(c.args[0]).startswith("(TV.split(':')[0], "), "the only address ever dialled is the TV from tv.txt"

# Same thing at run time: the real functions (not the stand-ins above) are driven and every attempt is checked.
del tried[:]
ns2 = {"__name__": "t2", "__file__": path}
exec(compile(src, path, "exec"), ns2)  # a fresh copy with the real tv() and vol_get()
if os.name != "nt":
    ns2["NOWIN"] = 0  # the Windows-only "no console window" flag
ns2["tv"]("input keyevent KEYCODE_HOME"), ns2["vol_get"](), ns2["stats"]()
try:
    ns2["lan_ip"]()
except OSError:
    pass
TVA, ADBP = ns2["TV"], ns2["ADB"]
assert tried, "the tripwire saw the attempts"
for name, what in tried:
    if name.startswith("subprocess."):
        cmd = what if isinstance(what, str) else subprocess.list2cmdline(list(what))  # Windows reports one command line, Linux a list
        assert cmd == subprocess.list2cmdline([ADBP, "connect", TVA]) or cmd.startswith(subprocess.list2cmdline([ADBP, "-s", TVA, "shell"])), "unexpected program: %r" % (what,)
    else:
        assert (what[0] if isinstance(what, tuple) else what) == TVA.split(":")[0], "unexpected address: %s %r" % (name, what)

# The page cannot load from, or send to, anywhere but the server it came from.
pg = ns["page"]("x", True)
assert not re.search(r"[a-z][a-z0-9+.-]*://|\b(src|href|action)\s*=", pg, re.I), "no outside address, no link, no form in the page"
# A panel never stays on "Asking the TV...": a request that fails on the way is shown like a TV that did not answer.
for show in ("showPrivacy", "showSecurity", "showHealth"):
    assert f".then({show}).catch(() => {show}({{}}))" in pg, show + " shows a failed request as no answer"
assert not re.search(r"fetch\((?!'/'\+T\+)|XMLHttpRequest|WebSocket|sendBeacon|EventSource|import\(", pg), "every request goes to the same server, under the secret path"


class R(H):
    def __init__(self):
        self.h, self.wfile = {}, io.BytesIO()

    def send_response(self, code):
        pass

    def send_header(self, k, v):
        self.h[k] = v

    def end_headers(self):
        pass


r = R()
r.reply(200)
assert r.h["Content-Security-Policy"] == "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
assert r.h["Cache-Control"] == "no-store" and r.h["Referrer-Policy"] == "no-referrer"
r = R()
r.reply(200, cookie="abc")
assert all(f in r.h["Set-Cookie"] for f in ("Secure", "HttpOnly", "SameSite=Strict", "Max-Age=7776000")), "pairing cookie flags"
import contextlib
with contextlib.redirect_stdout(io.StringIO()) as o1, contextlib.redirect_stderr(io.StringIO()) as o2:
    R().log_message("%s", "GET /secret-token/ HTTP/1.1")
assert o1.getvalue() + o2.getvalue() == "", "requests and secrets are never logged"
shutil.rmtree(tmp)
print("all checks passed")
