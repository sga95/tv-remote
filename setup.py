# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guided setup for the local TV remote. Run: python setup.py
Safe to run again: it never overwrites an existing secret, certificate or setting.
Standard library only. It does not change firewall, startup or system settings: it prints the commands for you to run."""
import ipaddress, hashlib, os, secrets, shutil, socket, subprocess, sys

D = os.path.dirname(os.path.abspath(__file__))
WORDS = """albero ape arancia asino aquila bacio barca bosco burro cane casa cavallo cielo cigno cinema colle corda
cuore delfino dado faro fiore fiume foglia formica fragola freccia fuoco gatto gelato giglio giardino lago lampada
leone libro luna maglia mare mela miele monte nave nebbia neve nido nuvola oca orso ombra onda orologio ostrica
palla panda pane penna pesce piazza pino pioggia pizza ponte porta prato pulce quadro rana razzo riso rosa ruota
sabbia sale scala scoiattolo sedia sole stella strada tavolo tazza tenda tigre torre tulipano uccello uva valle vento
verde volpe zebra zucca amico arco bandiera biscotto bottone campana candela castello cometa conchiglia cuscino diamante
estate farfalla fontana gabbiano gufo isola lepre limone lupo mulino notte olivo pantera papavero pennello pera
pinguino piuma prugna razza rondine sentiero serpente sirena tartaruga tempesta topo tramonto trota uovo vela violino zaino""".split()
NOWIN = 0x08000000 if os.name == "nt" else 0


def say(m=""):
    print(m, flush=True)


def ask(q, default=""):
    a = input(f"{q}" + (f" [{default}]" if default else "") + ": ").strip()
    return a or default


def path(name):
    return os.path.join(D, name)


def read(name):
    return open(path(name)).read().strip() if os.path.exists(path(name)) else ""


def run(cmd, **k):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=k.pop("timeout", 20), creationflags=NOWIN, **k)


def step_verify():
    """If SHA256SUMS is present, every listed file must match before anything is configured."""
    say("1. Checking the package files against SHA256SUMS")
    if not os.path.exists(path("SHA256SUMS")):
        say("   no SHA256SUMS next to this script (running from a clone): skipped")
        return
    for line in open(path("SHA256SUMS")):
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        got = hashlib.sha256(open(path(name), "rb").read()).hexdigest() if os.path.exists(path(name)) else ""
        if got.lower() != digest.lower():
            sys.exit(f"   STOP: {name} does not match SHA256SUMS. Download the package again from the official page.")
    say("   all files match")


def step_adb():
    say("2. Android Debug Bridge (adb)")
    found = read("adb.txt") or shutil.which("adb") or ""
    while True:
        if found:
            try:
                if "Android Debug Bridge" in run([found, "version"]).stdout:
                    break
            except (OSError, subprocess.TimeoutExpired):
                pass
        found = ask("   adb not found. Full path to adb (get Android platform-tools from developer.android.com/tools/releases/platform-tools)")
        if not found:
            sys.exit("   adb is required. Install platform-tools and run setup again.")
        found = os.path.abspath(found.strip('"'))  # accept a path pasted with quotes or a relative one
    if found != "adb" and not shutil.which("adb") == found:
        open(path("adb.txt"), "w").write(found)
    say(f"   using {found}")
    return found


def step_tv(adb):
    say("3. Your TV")
    cur = read("tv.txt")
    if cur:
        say(f"   tv.txt already set to {cur}: kept")
        return cur.split(":")[0]
    while True:
        ip = ask("   TV address on your home network (TV settings > network; example 192.168.1.50)")
        try:
            if ipaddress.ip_address(ip).is_private:
                break
        except ValueError:
            pass
        say("   that is not a private home-network address, try again")
    open(path("tv.txt"), "w").write(f"{ip}:5555")
    out = run([adb, "connect", f"{ip}:5555"]).stdout.strip()
    say(f"   adb says: {out or 'no answer'}")
    if "connected" not in out:
        say("   If it did not connect: enable Developer options and network/USB debugging on the TV, then run setup again.")
    return ip


def lan_ip(tv_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((tv_ip, 1))
    return s.getsockname()[0]


def step_cert(tv_ip):
    say("4. Encrypted connection (TLS certificate)")
    if os.path.exists(path("cert.pem")) and os.path.exists(path("key.pem")):
        say("   certificate already there: kept")
        return True
    ssl_bin = shutil.which("openssl") or next((p for p in (r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe") if os.path.exists(p)), "")
    if not ssl_bin:
        say("   openssl not found (it ships with Git for Windows and with most Linux/macOS systems).")
        say("   Install it or Git, then run setup again. The remote never starts without a certificate.")
        return False
    me = lan_ip(tv_ip)
    r = run([ssl_bin, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-keyout", path("key.pem"), "-out", path("cert.pem"),
             "-subj", "/CN=tv-remote", "-addext", f"subjectAltName=IP:{me}"], timeout=60)
    ok = os.path.exists(path("cert.pem")) and os.path.exists(path("key.pem"))
    say(f"   created for this computer's address {me}, valid one year" if ok else f"   failed: {r.stderr.strip()[:200]}")
    say("   If this computer's address changes, delete cert.pem and key.pem and run setup again.")
    return ok


def step_secrets():
    say("5. Secrets (five random words each, never shared, never committed)")
    for f, what in (("token.txt", "plain remote URL"), ("admin.txt", "owner URL")):
        if read(f):
            say(f"   {f} already there: kept")
        else:
            open(path(f), "w").write("-".join(secrets.choice(WORDS) for _ in range(5)))
            say(f"   {f} created ({what})")


def step_test():
    say("6. Self-check")
    if os.path.exists(path("test_remote_fast.py")):
        r = run([sys.executable, path("test_remote_fast.py")], timeout=60)
        say("   " + (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:]])[-1])
    else:
        say("   test file not present: skipped")


def main():
    say("Local TV remote: guided setup\n")
    step_verify()
    adb = step_adb()
    tv = step_tv(adb)
    cert = step_cert(tv)
    step_secrets()
    step_test()
    say("\nDone. Next:")
    say("  - Start it:  python remote_fast.py   (the console prints your two URLs; they are also in token.txt and admin.txt)")
    say("  - On the phone open the OWNER URL once: that pairs the phone. Then bookmark it.")
    if sys.platform == "win32":
        say("  - Windows firewall (administrator PowerShell, once), only if inbound traffic is blocked:")
        say("      New-NetFirewallRule -DisplayName 'TV Remote' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress LocalSubnet -Program '<path to pythonw.exe>' -Profile Private")
        say("  - Start at logon (normal PowerShell, optional): register a task that runs pythonw.exe remote_fast.py from this folder (see README).")
    if not cert:
        say("\nThe certificate is missing: fix that first, the remote will not start without it.")
        sys.exit(1)


if __name__ == "__main__":
    main()
