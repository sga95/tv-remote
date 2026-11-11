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
assert post("/" + TOKEN + "/vol/down/2", cookie="dev=" + cookie)[0] == 200 and sent[-1] == "input keyevent KEYCODE_VOLUME_DOWN KEYCODE_VOLUME_DOWN", "volume bar presses the volume key"
assert post("/" + TOKEN + "/vol/up/4", cookie="dev=" + cookie)[0] == 404, "at most 3 volume steps per request"
assert post("/" + TOKEN + "/vol/up/1; reboot", cookie="dev=" + cookie)[0] == 404, "volume steps come from a fixed table"
ns["vol_get"] = lambda: {"v": 1, "max": 100}  # no adb in the test
assert get("/" + TOKEN + "/vol", cookie="dev=" + cookie)[0] == 200 and get("/" + TOKEN + "/vol")[0] == 404, "volume read needs a paired device"
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
