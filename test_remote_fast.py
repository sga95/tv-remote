"""Self-check for remote_fast.py. Run: python test_remote_fast.py
Works on a temporary copy with its own secrets, so your real token, owner and pairing files are never touched."""
import os, shutil, tempfile

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
assert get("/" + TOKEN + "/stats", cookie="dev=" + cookie)[0] == 404, "status panel is owner only"
assert get("/" + ADMIN + "/stats")[0] == 200
assert get("/" + ADMIN + "/", ip="8.8.8.8")[0] == 404, "non-LAN client refused"
for _ in range(5):
    get("/wrong/", ip="192.168.0.77")
assert get("/" + TOKEN + "/", cookie="dev=" + cookie, ip="192.168.0.77")[0] == 404, "5 wrong secrets lock the address"
assert get("/" + ADMIN + "/", ip="192.168.0.77")[0] == 200, "the owner secret still gets in"
assert all(ord(c) < 128 for c in ns["page"]("x", True)), "page is plain ASCII"
shutil.rmtree(tmp)
print("all checks passed")
