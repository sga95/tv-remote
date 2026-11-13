# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guided setup for the local TV remote. Run: python setup.py

It walks through six steps, each one a function below (step_verify ... step_test), and main() runs them in order:
check the downloaded files, find adb, ask for the TV, create the certificate, create the two secrets, run the self-check.
Everything it writes is a small text file next to this script; remote_fast.py reads those files when it starts.

Safe to run again: it never overwrites an existing secret, certificate or setting.
python setup.py --check  does not change anything: it looks at everything that can go wrong and says what to do.
Standard library only. It does not change firewall, startup or system settings: it prints the commands for you to run."""
import ipaddress, hashlib, os, re, secrets, shutil, socket, subprocess, sys

D = os.path.dirname(os.path.abspath(__file__))  # the folder this script is in: every file is read and written here
# Word list for the secrets. Five random words from it give about 35 bits of randomness: enough here, because the
# server locks an address out after five wrong tries, and far easier to read and type than random letters.
WORDS = """albero ape arancia asino aquila bacio barca bosco burro cane casa cavallo cielo cigno cinema colle corda
cuore delfino dado faro fiore fiume foglia formica fragola freccia fuoco gatto gelato giglio giardino lago lampada
leone libro luna maglia mare mela miele monte nave nebbia neve nido nuvola oca orso ombra onda orologio ostrica
palla panda pane penna pesce piazza pino pioggia pizza ponte porta prato pulce quadro rana razzo riso rosa ruota
sabbia sale scala scoiattolo sedia sole stella strada tavolo tazza tenda tigre torre tulipano uccello uva valle vento
verde volpe zebra zucca amico arco bandiera biscotto bottone campana candela castello cometa conchiglia cuscino diamante
estate farfalla fontana gabbiano gufo isola lepre limone lupo mulino notte olivo pantera papavero pennello pera
pinguino piuma prugna razza rondine sentiero serpente sirena tartaruga tempesta topo tramonto trota uovo vela violino zaino""".split()
NOWIN = 0x08000000 if os.name == "nt" else 0  # Windows: run helper programs without opening a console window


def say(m=""):
    """Print a line at once (flush), so the text shows before the next question."""
    print(m, flush=True)


def ask(q, default=""):
    """Ask a question; an empty answer means the default shown in [brackets]."""
    a = input(f"{q}" + (f" [{default}]" if default else "") + ": ").strip()
    return a or default


def path(name):
    """Full path of a file next to this script."""
    return os.path.join(D, name)


def read(name):
    """Content of a setting file, or "" if it does not exist yet."""
    return open(path(name)).read().strip() if os.path.exists(path(name)) else ""


def run(cmd, **k):
    """Run a program given as a list of arguments (never through a shell) and capture what it prints."""
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
    """Find a working adb: the saved path, then PATH, then ask. Saves the path in adb.txt unless plain "adb" already works."""
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
    """Ask for the TV address (home-network addresses only), save it in tv.txt and try a first connection."""
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
    tv_state(adb, f"{ip}:5555", tries=4)
    return ip


def tv_state(adb, tv, tries=1):
    """Connect to the TV and say, in plain words, what is wrong if it does not answer. Returns True when the TV is ready.
    With tries > 1 it waits for you to fix the problem and press Enter."""
    for left in range(tries, 0, -1):
        out = run([adb, "connect", tv]).stdout.strip()
        state = run([adb, "-s", tv, "get-state"]).stdout.strip()
        if state == "device":
            say(f"   the TV answers ({out or 'connected'})")
            return True
        low = (out + " " + state).lower()
        if "unauthorized" in low:
            say("   The TV is asking for permission. LOOK AT THE TV SCREEN: a box says \"Allow debugging?\".")
            say("   Tick \"Always allow from this computer\", then choose OK.")
        elif "refused" in low or "failed" in low or "cannot connect" in low or "no route" in low or "timed out" in low:
            say("   The TV does not answer. Check: (1) the TV is switched ON, not in standby, (2) debugging is ON in its")
            say("   developer options (README, step 1), (3) the address is right, (4) the TV and this computer are on the same Wi-Fi.")
        else:
            say(f"   adb says: {out or 'no answer'}. Wake the TV with its own remote and check that debugging is ON.")
        if left == 1:
            break
        try:
            if input("   Fix it, then press Enter to try again (or type s and Enter to skip): ").strip().lower().startswith("s"):
                break
        except EOFError:
            break
    say("   Not connected yet. You can continue: run  python setup.py --check  later to see what is still wrong.")
    return False


def lan_ip(tv_ip):
    """This computer's address on the network that leads to the TV. A UDP "connect" sends nothing; it only picks the network card."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((tv_ip, 1))
    return s.getsockname()[0]


def openssl_bin():
    """openssl on PATH, or the copy that ships with Git for Windows. "" if neither exists."""
    return shutil.which("openssl") or next((p for p in (r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe") if os.path.exists(p)), "")


def cert_ip(ssl_bin):
    """The address the saved certificate was made for ("" if it cannot be read)."""
    if not ssl_bin or not os.path.exists(path("cert.pem")):
        return ""
    m = re.search(r"IP Address:([0-9.]+)", run([ssl_bin, "x509", "-in", path("cert.pem"), "-noout", "-ext", "subjectAltName"]).stdout)
    return m.group(1) if m else ""


def step_cert(tv_ip):
    """Create the TLS certificate with openssl, for this computer's home-network address. Returns False if it could not."""
    say("4. Encrypted connection (TLS certificate)")
    ssl_bin = openssl_bin()
    if os.path.exists(path("cert.pem")) and os.path.exists(path("key.pem")):
        old, me = cert_ip(ssl_bin), lan_ip(tv_ip)
        if not old or old == me:
            say("   certificate already there: kept")
            return True
        say(f"   The certificate was made for the address {old}, but this computer is now {me}: the phone would show an error.")
        try:
            again = input("   Make a new certificate now? [Y/n]: ").strip().lower()
        except EOFError:
            again = "n"
        if again.startswith("n"):
            return True
        os.replace(path("cert.pem"), path("cert.pem.old"))
        os.replace(path("key.pem"), path("key.pem.old"))
        say("   old certificate kept as cert.pem.old and key.pem.old")
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
    """Create the two secrets, unless they exist. secrets.choice uses the system's secure random source."""
    say("5. Secrets (five random words each, never shared, never committed)")
    for f, what in (("token.txt", "plain remote URL"), ("admin.txt", "owner URL")):
        if read(f):
            say(f"   {f} already there: kept")
        else:
            open(path(f), "w").write("-".join(secrets.choice(WORDS) for _ in range(5)))
            say(f"   {f} created ({what})")


def step_test():
    """Run the self-check and show its last line (all checks passed, or the first failure)."""
    say("6. Self-check")
    if os.path.exists(path("test_remote_fast.py")):
        r = run([sys.executable, path("test_remote_fast.py")], timeout=60)
        say("   " + (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:]])[-1])
    else:
        say("   test file not present: skipped")


def check():
    """python setup.py --check : look at everything that can go wrong and say, in plain words, what to do about each thing."""
    say("Local TV remote: health check\n")
    bad = []

    def ok(m):
        say(f"  OK        {m}")

    def no(m, fix):
        bad.append(m)
        say(f"  PROBLEM   {m}\n            what to do: {fix}")

    tv = read("tv.txt")
    if not tv:
        no("the TV address is not set", "run  python setup.py  and answer the questions")
        return sys.exit(1)
    ip = tv.split(":")[0]
    ok(f"TV address is set ({tv})")
    adb = read("adb.txt") or shutil.which("adb") or ""
    try:
        good = "Android Debug Bridge" in run([adb, "version"]).stdout if adb else False
    except (OSError, subprocess.TimeoutExpired):
        good = False
    if good:
        ok("adb works")
        if tv_state(adb, tv):
            ok("the TV is switched on and has allowed this computer")
        else:
            no("the TV does not answer", "see the lines above; the most common cause is the TV in standby (wake it with its own remote)")
    else:
        no("adb is missing", "install Android platform-tools and run  python setup.py")
    try:
        me = lan_ip(ip)
        ok(f"this computer is on the home network as {me}")
    except OSError:
        me = ""
        no("this computer cannot reach the home network", "connect it to the same Wi-Fi as the TV")
    if os.path.exists(path("cert.pem")) and os.path.exists(path("key.pem")):
        made = cert_ip(openssl_bin())
        if made and me and made != me:
            no(f"the certificate was made for {made}, this computer is now {me}", "run  python setup.py  and answer Y to the new certificate; then open the links again on the phone")
        else:
            ok("the certificate matches this computer")
    else:
        no("there is no certificate", "run  python setup.py")
    for f, what in (("token.txt", "plain link"), ("admin.txt", "owner link")):
        ok(f"{f} exists ({what})") if read(f) else no(f"{f} is missing", "start the remote once (python remote_fast.py) or run  python setup.py")
    ok("a phone is paired") if os.path.exists(path("device.txt")) else no("no phone is paired yet", "open the OWNER link once on the phone (it is in admin.txt, after https://<this computer>:8765/)")
    if me:
        try:
            socket.create_connection((me, 8765), 3).close()
            ok("the remote is running and answers on port 8765")
        except OSError:
            no("the remote is not running", "start it with  python remote_fast.py  (leave that window open), then run this check again")
    say("\nAll good." if not bad else f"\n{len(bad)} problem(s). Fix the first one, then run  python setup.py --check  again.")
    say("If the phone still cannot open the page while everything above is OK: the phone must be on the SAME Wi-Fi (not a guest network),")
    say("and on Windows the firewall must allow port 8765 (README, \"When something does not work\").")
    sys.exit(1 if bad else 0)


def main():
    if "--check" in sys.argv:
        return check()
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
    say("  - Something does not work? Run  python setup.py --check  and follow what it says.")
    if sys.platform == "win32":
        say("  - Windows firewall (administrator PowerShell, once), only if inbound traffic is blocked:")
        say("      New-NetFirewallRule -DisplayName 'TV Remote' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress LocalSubnet -Program '<path to pythonw.exe>' -Profile Private")
        say("  - Start at logon (normal PowerShell, optional): register a task that runs pythonw.exe remote_fast.py from this folder (see README).")
    if not cert:
        say("\nThe certificate is missing: fix that first, the remote will not start without it.")
        sys.exit(1)


if __name__ == "__main__":
    main()
