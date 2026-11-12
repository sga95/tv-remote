# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local TV remote: a web page on your phone that controls an Android TV on the same home network.

Run:  python remote_fast.py     then open the printed address on the phone (same Wi-Fi).

How it works, in one breath: the phone loads one page from this program; every button sends a
small request back; this program turns the request into one fixed command and hands it to the
TV through adb (Android's own debugging tool). Nothing leaves the home network.

The file reads top to bottom in this order:
  1. Settings          the small text files next to this script
  2. Commands          the complete list of things the remote may ever send to the TV
  3. Secrets, pairing  who is allowed to use the remote
  4. Talking to the TV adb, the volume reading
  5. Owner panel       live health numbers and the traffic watch
  6. The page          its style (CSS), its behaviour (JS) and its buttons (HTML)
  7. Web server        checks every request, then answers it
  8. Start             certificate, then serve

Safety rules this file keeps (each one is checked by test_remote_fast.py where it can be):
- It listens on the computer's home-network address only, and serves home-network clients only.
- It is closed by default: no TV address, no certificate or no paired phone means no remote.
- Every request needs a secret in its address. Secrets are compared in constant time and never logged.
- Whatever the phone sends is only ever used to look a command up in the fixed lists of section 2.
  Nothing typed or sent by a visitor becomes part of a command.
- adb is started with a list of arguments, never through a shell on this computer.
- Encryption (TLS) is mandatory. Secrets live in files next to the script, never in the code.
- Nothing is recorded about what is watched. Counters live in memory and vanish on exit.
"""
import collections, hashlib, hmac, ipaddress, json, os, re, secrets, socket, ssl, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

# ============================================================ 1. Settings
_dir = os.path.dirname(os.path.abspath(__file__))  # the folder this script is in


def _cfg(name):
    """Read one setting file next to this script ("" if it is not there).
    Settings live in files (tv.txt, adb.txt) so the code holds nothing personal and can be published as is."""
    f = os.path.join(_dir, name)
    return open(f).read().strip() if os.path.exists(f) else ""


ADB = _cfg("adb.txt") or "adb"  # path to the adb program; plain "adb" works when it is on PATH
TV = _cfg("tv.txt")             # the TV as address:port, for example 192.168.1.50:5555
if not TV:
    sys.exit("tv.txt is missing: put the TV address and port in it, for example 192.168.1.50:5555")
PORT = 8765                     # the port the phone connects to
# Windows only: start helper programs without a console window, so nothing flashes when this runs in the background.
NOWIN = 0x08000000 if sys.platform == "win32" else 0

# ============================================================ 2. Commands
# These tables are the whole vocabulary of the remote. A request can only pick an entry by its name.

KEYS = {  # button name -> Android key code
    "Power": "KEYCODE_POWER", "Home": "KEYCODE_HOME", "Back": "KEYCODE_BACK", "Menu": "KEYCODE_MENU",
    "Up": "KEYCODE_DPAD_UP", "Down": "KEYCODE_DPAD_DOWN", "Left": "KEYCODE_DPAD_LEFT", "Right": "KEYCODE_DPAD_RIGHT",
    "OK": "KEYCODE_DPAD_CENTER",
    "Vol+": "KEYCODE_VOLUME_UP", "Vol-": "KEYCODE_VOLUME_DOWN", "Mute": "KEYCODE_VOLUME_MUTE",
    "Ch+": "KEYCODE_CHANNEL_UP", "Ch-": "KEYCODE_CHANNEL_DOWN",
    "Play/Pause": "KEYCODE_MEDIA_PLAY_PAUSE", "Input": "KEYCODE_TV_INPUT",
    "Pause": "KEYCODE_MEDIA_PAUSE",  # not on the page: for phone automations (pause only, never toggles)
}
for digit in range(10):  # the number pad: "0".."9"
    KEYS[str(digit)] = f"KEYCODE_{digit}"

APPS = {  # button name -> (Android package, colour of the line under the button)
    "Netflix": ("com.netflix.ninja", "#e50914"), "Prime": ("com.amazon.amazonvideo.livingroom", "#00a8e1"),
    "YouTube": ("com.google.android.youtube.tv", "#ff0000"), "Spotify": ("com.spotify.tv.android", "#1db954"),
    "Twitch": ("tv.twitch.android.app", "#9146ff"), "Free My TV": ("io.freemytv", "#f5a623"),
}
LAUNCHER = "io.freemytv"  # the TV's home screen app: it is never force-stopped


def launch(name):
    """The command that opens an app. The TV has little memory, so opening one streaming app
    also closes the others in the list (never the launcher). Opening the launcher closes nothing."""
    pkg = APPS[name][0]
    cmd = "monkey -p " + pkg + " -c android.intent.category.LEANBACK_LAUNCHER 1"  # monkey = Android's way to start an app by package
    if pkg != LAUNCHER:
        cmd += "".join("; am force-stop " + other for other, _ in APPS.values() if other not in (pkg, LAUNCHER))
    return cmd


def _snd(dialog, leveler, treble, bass):
    """One sound preset = four of the TV's own sound settings, written one after the other."""
    values = (("sound_dialog_enhancement", dialog), ("sound_advanced_volume_leveler", leveler), ("sound_treble", treble), ("sound_bass", bass))
    return "; ".join(f"settings put global {key} {value}" for key, value in values)


# Sound presets. "Normal" holds the values the TV had before (kept in diag/audio-restore-2026-11-10.txt).
SND = {"Voice": _snd(1, 1, 60, 45), "Movie": _snd(0, 0, 55, 60), "Night": _snd(1, 1, 50, 40), "Normal": _snd(0, 0, 50, 50)}

# Volume. Reading the level works (stream 3 is the media stream), but the tested TV ignores a direct
# "set volume" (--set and --adj do nothing): it only obeys the volume key. So the volume bar on the page
# asks for 1 to 5 key presses at a time, and these ten fixed commands are all it can ask for.
# The presses of one request run side by side ("&"): measured on the TV, 3 take 0.5 s and 6 take 0.75 s,
# about twice as fast as one after the other. That is the TV's own pace; nothing faster was found.
VOL = "cmd media_session volume --stream 3 "
VSTEP = {}  # "up/2" -> "input keyevent KEYCODE_VOLUME_UP & input keyevent KEYCODE_VOLUME_UP &"
for way in ("up", "down"):
    for presses in (1, 2, 3, 4, 5):
        VSTEP[f"{way}/{presses}"] = f"input keyevent KEYCODE_VOLUME_{way.upper()} & " * presses

# Volume bar safety, so a slip of the finger cannot blast the speakers or wake the house. Change the numbers here.
# The bar never raises the volume above "day" (or above "night" between night_from and night_to o'clock),
# and one drag raises it by at most "drag" steps: for more, lift the finger and drag again. Lowering is never limited.
# Night is decided by the phone's own clock, inside the page: no time, place or time zone is asked of anyone.
VOL_LIMITS = {"day": 50, "night": 30, "night_from": 22, "night_to": 7, "drag": 10}

# Privacy switches. Android (12 and later) has a system-wide block for the microphone and one for the camera:
# while a block is on, the system hands every app silence or a black picture, whatever permissions the app holds.
# These four fixed commands turn the two blocks on and off; nothing else about the TV is changed.
PRIV = {
    "mic/block": "cmd sensor_privacy enable 0 microphone", "mic/allow": "cmd sensor_privacy disable 0 microphone",
    "cam/block": "cmd sensor_privacy enable 0 camera", "cam/allow": "cmd sensor_privacy disable 0 camera",
}
# The TV's list of installed apps, cut down on the TV to two kinds of line: "Package [name]" and, under an app
# that holds one, "android.permission.RECORD_AUDIO: granted=true" (or CAMERA). About 2 seconds of work for the TV.
_HOLDERS = "dumpsys package packages | grep -E '^  Package \\[|android.permission.(RECORD_AUDIO|CAMERA): granted=true'"
# "Revoke all": one more fixed command. It walks that same list on the TV and takes the microphone and camera
# permission away from every app that holds it. The app names come from the TV's own list, never from the phone.
# Android closes an app when it loses a permission, and the app has to ask again the next time it wants it.
PRIV["apps/revoke"] = _HOLDERS + (" | while read a b c; do case $a in Package) p=${b#[}; p=${p%]};; "
                                  "*) pm revoke $p ${a%:};; esac; done")
# One read-only question for the Privacy panel, answered in four parts separated by the @ marks:
# the state of the two blocks, how many cameras the TV sees, its audio inputs, and which apps hold the permissions.
PRIV_ASK = ("dumpsys sensor_privacy; echo @cam; dumpsys media.camera | grep 'Number of camera devices'; "
            "echo @in; dumpsys media.audio_policy | grep -E '^  [0-9]+\\. Port ID.*AUDIO_DEVICE_IN_'; "
            "echo @apps; " + _HOLDERS)

# Security panel. A read-only look at the settings that decide who can watch, listen or get in, plus two switches.
CAST = "com.google.android.apps.mediashell"  # Google's Cast receiver ("Chromecast built-in"): lets any device on the Wi-Fi send video to the TV
SEC = {
    # Off: the receiver is disabled for the TV's user and its network ports close. Nothing is uninstalled.
    "cast/off": "pm disable-user --user 0 " + CAST,
    # On again: enable it and start its service (measured: without the start it only comes back after a TV restart).
    "cast/on": "pm enable " + CAST + "; am startservice -n " + CAST + "/.MediaShellCastReceiverService",
    # Ask apps not to personalise advertising. A request to well-behaved apps, not a block: it cannot be verified from here.
    "ads/limit": "settings put secure limit_ad_tracking 1", "ads/free": "settings delete secure limit_ad_tracking",
}
# The read-only question, in eleven parts separated by "#next#": accessibility services, apps that read notifications,
# location, voice hotword, apps allowed to install apps, apps allowed to read usage, the advertising request,
# the Cast receiver (installed? disabled?), the network ports the TV listens on with the user id that owns each,
# and two lists that turn a user id into a name: the installed apps and the running programs.
SEC_ASK = "; echo '#next#'; ".join((
    "settings get secure enabled_accessibility_services", "settings get secure enabled_notification_listeners",
    "settings get secure location_mode", "settings get global hotword_detection_enabled",
    "cmd appops query-op REQUEST_INSTALL_PACKAGES allow", "cmd appops query-op GET_USAGE_STATS allow",
    "settings get secure limit_ad_tracking",
    "pm list packages | grep -c " + CAST + "; pm list packages -d | grep -c " + CAST,
    "ss -ltne | grep LISTEN", "pm list packages -U", "ps -A -o UID,NAME"))
# Well-known ports of an Android TV, to give the open doors a name. Any other port is shown as "unknown".
PORTS = {5555: "network debugging (adb, what this remote uses)", 8008: "Cast", 8009: "Cast", 8443: "Cast",
         6465: "Android TV remote service", 6466: "Android TV remote service", 6467: "Android TV remote service"}

# Optimize. One fixed command. Two of its steps are safe on any Android TV and need no list of apps:
#   am kill-all            ends background processes that Android itself marks as safe to end (never the app on screen)
#   pm trim-caches 999G    asks every app to drop its cache files (thumbnails, temporary downloads); apps rebuild them as needed
# Measured on the tested TV: the first step freed almost nothing (the remote already closes other apps when one is
# opened), the second freed 146 MB of storage. Caches are rewritten afterwards, so this is not something to press daily.
# A third step comes first: close the streaming apps of the APPS list that are NOT on screen. Some TVs start an app
# by themselves after a restart, or an app is opened from the TV's own menu, where this remote cannot close the others.
# The check runs on the TV: it reads which app is on screen and skips it, so what is being watched is never closed.
_CLOSE_OTHERS = ("fg=$(dumpsys activity activities | grep -m1 topResumedActivity); for p in "
                 + " ".join(pkg for pkg, _ in APPS.values() if pkg != LAUNCHER)
                 + '; do case "$fg" in *"$p/"*) ;; *) am force-stop $p;; esac; done')
TUNE = {"run": _CLOSE_OTHERS + "; am kill-all; pm trim-caches 999G"}
# Clean restart: the same clean-up, then the TV restarts. Measured on the tested TV: it was back and answering after
# 68 seconds, network debugging came back by itself, and every privacy and security setting was as it had been left.
# The page takes a picture of those settings before and compares after; see "Clean restart" in the page script.
TUNE["restart"] = TUNE["run"] + "; reboot"
# The read-only question for the Optimize panel: memory, storage of the data partition, seconds since the TV started,
# the Android version and the date of the last security patch the TV received. Last, the three parts of Android that
# the app store keeps updating even when the TV maker has stopped: the WebView (the web engine inside apps),
# Google Play services and the "Google Play system update" (system modules Google updates itself).
TUNE_ASK = ("grep -E 'MemTotal|MemAvailable' /proc/meminfo; echo @disk; df /data | tail -1; echo @up; cat /proc/uptime; "
            "echo @os; getprop ro.build.version.release; getprop ro.build.version.security_patch; "
            "echo @layers; dumpsys webviewupdate | grep 'Current WebView package'; "
            "dumpsys package com.google.android.gms | grep -m1 lastUpdateTime; "
            "dumpsys package com.google.android.modulemetadata | grep -m1 versionName")

# ============================================================ 3. Secrets and pairing
# Two secrets, each one the first part of an address:
#   TOKEN  opens the remote, and only on a phone that was paired before
#   ADMIN  the owner's address: the remote plus the status panel. Opening it on a phone pairs that phone.


def _secret(name):
    """Read a secret from its file. On the very first start the file does not exist yet: create it."""
    f = os.path.join(_dir, name)
    if not os.path.exists(f):
        with open(f, "w") as out:
            out.write(secrets.token_urlsafe(16))
    return open(f).read().strip()


TOKEN = _secret("token.txt")  # kept in a file so the address stays the same and the phone can bookmark it
ADMIN = _secret("admin.txt")


def _h(value):
    """SHA-256 of a text, as 64 hex characters."""
    return hashlib.sha256(value.encode()).hexdigest()


# Pairing: the owner address gives the phone a random cookie (256 bits, it carries no data).
# The server keeps only the SHA-256 hash of each cookie in device.txt (one per line, newest 10), never the cookie.
# So a leaked TOKEN alone is useless, and a stolen device.txt cannot be turned back into a cookie.
_devfile = os.path.join(_dir, "device.txt")
DEVS = [line.strip() for line in open(_devfile)] if os.path.exists(_devfile) else []
DEVS = [d if len(d) == 64 else _h(d) for d in DEVS if d]  # a very old device.txt held the plain cookie: hash it


def save_devs():
    with open(_devfile, "w") as f:
        f.write("\n".join(DEVS))


if DEVS:
    save_devs()  # writes back the hashed form, in case the line above converted an old file


def cookie_ok(headers):
    """True if the request carries the cookie of a paired phone.
    Every stored hash is compared, each in constant time, so the answer time tells an attacker nothing."""
    ok = False
    for part in headers.get("Cookie", "").split(";"):
        part = part.strip()
        if part.startswith("dev="):
            sent = _h(part[4:])
            for known in DEVS:
                ok |= hmac.compare_digest(sent, known)
    return ok


FAILS = {}  # client address -> (wrong secrets in a row, time the count or the lockout ends)

# ============================================================ 4. Talking to the TV
# Health counters for the owner panel: memory only, never written to disk, no history of keys.
ST = {"start": time.time(), "taps": 0, "restarts": 0, "ms_sum": 0.0, "ms_max": 0.0, "bad": 0, "locks": 0, "last_bad": ""}
_sh, _lock = None, threading.Lock()  # the one long-lived `adb shell`, and a lock so two taps never write into it at once


def adb(*args, timeout=4):
    """Run adb once and return what it printed. The arguments are a list: no shell on this computer is involved."""
    return subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout, creationflags=NOWIN).stdout


def ask(question, timeout=4):
    """One read-only question to the TV, answered as text.
    No answer at all means the link to the TV is gone (it restarted, or the Wi-Fi dropped): connect again and ask once more."""
    answer = adb("-s", TV, "shell", question, timeout=timeout)
    if not answer.strip():
        adb("connect", TV, timeout=8)
        answer = adb("-s", TV, "shell", question, timeout=timeout)
    return answer


def tv(cmd):
    """Send one command line to the TV. `cmd` always comes from the tables of section 2.
    Speed: starting adb for every tap takes too long, so one `adb shell` stays open and each tap is
    just one line written into it. If that shell died (TV restarted, Wi-Fi dropped) it is reopened once."""
    global _sh
    t0 = time.perf_counter()
    with _lock:
        for _ in range(2):  # first try, plus one retry after reopening the shell
            try:
                if _sh is None or _sh.poll() is not None:  # no shell yet, or it has ended
                    ST["restarts"] += _sh is not None
                    adb("connect", TV, timeout=8)
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
    """The TV's volume now and its top value, as {"v": 28, "max": 100}; {} if it cannot be read.
    The long-lived shell throws its output away, so this one question uses a separate adb call."""
    try:
        found = re.search(r"volume is (\d+) in range \[\d+\.\.(\d+)\]", ask(VOL + "--get"))
        return {"v": int(found[1]), "max": int(found[2])}
    except (subprocess.TimeoutExpired, OSError, TypeError):  # TypeError: the line was not in the answer
        return {}


def privacy():
    """What the Privacy panel shows, read from the TV each time, never assumed and never stored; {} if the TV does not answer.
    Example: {"mic_blocked": False, "cam_blocked": False, "cameras": 0, "mics": ["Built-In Mic"],
              "mic_apps": ["Netflix", "com.example.app"], "cam_apps": []}"""
    try:
        state, cameras, inputs, holders = re.split(r"@cam|@in|@apps", ask(PRIV_ASK, timeout=12))
    except (subprocess.TimeoutExpired, OSError, ValueError):  # ValueError: the answer did not have its four parts
        return {}
    # In the system's report sensor 1 is the microphone and 2 the camera; state 1 means the block is on.
    block = dict(re.findall(r"sensor=(\d)\s+toggles=\{\s+toggle_type=\d+\s+state_type=(\d)", state))
    count = re.search(r"devices: (\d+)", cameras)
    # Audio inputs that can be a microphone (built in, USB, Bluetooth, headset). HDMI, tuner and line inputs are left out.
    mics = [name for name, kind in re.findall(r'"([^"]+)"; \{AUDIO_DEVICE_IN_(\w+)', inputs) if re.search("MIC|USB|BLUETOOTH|HEADSET", kind)]
    # Walk the app list: remember the app each "Package [name]" line names, and file it under every permission line that follows.
    label = {pkg: name for name, (pkg, _) in APPS.items()}  # show "Netflix" rather than its package name
    holding, app = {"RECORD_AUDIO": [], "CAMERA": []}, ""
    for line in holders.splitlines():
        named = re.match(r"\s*Package \[([\w.]+)\]", line)
        granted = re.search(r"android\.permission\.(RECORD_AUDIO|CAMERA): granted=true", line)
        if named:
            app = label.get(named[1], named[1])
        elif granted and app not in holding[granted[1]]:
            holding[granted[1]].append(app)
    return {"mic_blocked": block.get("1") == "1", "cam_blocked": block.get("2") == "1",
            "cameras": int(count[1]) if count else None, "mics": mics,
            "mic_apps": holding["RECORD_AUDIO"], "cam_apps": holding["CAMERA"]}


def security():
    """What the Security panel shows, read from the TV each time; {} if the TV does not answer.
    Lists hold package names. "cast" is "on", "off" or "none" (no Cast receiver on this TV).
    "ports" is [[8008, "Cast", "com.google.android.apps.mediashell"], ...]: port, what it is known as, who holds it open."""
    try:
        (access, listeners, location, hotword, installers, usage, ads, cast, ports,
         apps, programs) = ask(SEC_ASK, timeout=12).split("#next#")
        installed, disabled = (int(n) for n in cast.split())
    except (subprocess.TimeoutExpired, OSError, ValueError):  # ValueError: the answer did not have its eleven parts
        return {}

    def services(text):  # "pkg/class:pkg2/class" or "null" -> ["pkg", "pkg2"]
        return sorted({item.split("/")[0] for item in text.strip().split(":") if "/" in item})

    def packages(text):  # one package per line; other lines ("No operations.") have a space in them
        return [line.strip() for line in text.splitlines() if "." in line and " " not in line.strip()]

    # Who owns a user id: an installed app if there is one (shown by its button name when it has one),
    # otherwise the running program with that id (system services are programs, not apps).
    label = {pkg: name for name, (pkg, _) in APPS.items()}
    owner = {uid: name for uid, name in re.findall(r"^\s*(\d+)\s+(\S+)\s*$", programs, re.M)}
    owner.update({uid: label.get(pkg, pkg) for pkg, uid in re.findall(r"package:(\S+) uid:(\d+)", apps)})
    # Listening sockets, without the ones only the TV itself can reach (loopback addresses).
    # A line without "uid:" belongs to the system itself (user id 0).
    doors = {}
    for address, port, uid in re.findall(r"LISTEN\s+\d+\s+\d+\s+(\S+):(\d+)\s+\S+(?:[^\n]*?uid:(\d+))?", ports):
        if not address.startswith("127.") and "::1" not in address:
            doors[int(port)] = owner.get(uid, "unknown program") if uid else "the system"
    return {"accessibility": services(access), "listeners": services(listeners),
            "location_on": location.strip() not in ("0", "null"), "hotword_on": hotword.strip() == "1",
            "installers": packages(installers), "usage": packages(usage), "ads_limited": ads.strip() == "1",
            "cast": "none" if not installed else "off" if disabled else "on",
            "ports": [[port, PORTS.get(port, "unknown"), doors[port]] for port in sorted(doors)]}


def health():
    """What the Optimize panel shows, in MB and hours, read from the TV each time; {} if the TV does not answer.
    Example: {"ram_free": 738, "ram_total": 1686, "disk_free": 5706, "disk_total": 7471, "seconds": 14563, "hours": 4,
              "android": "14", "patch": "2025-07-05", "webview": "157.0.8103.40", "play_services": "2026-11-09",
              "play_system": "2024-05-01"}   (a text is "" when the TV does not state it)"""
    def find(pattern, text):  # the first group of the pattern, or "" if it is not there
        found = re.search(pattern, text)
        return found[1] if found else ""

    try:
        mem, disk, up, system, layers = re.split(r"@disk|@up|@os|@layers", ask(TUNE_ASK, timeout=10))
        kb = {name: int(value) for name, value in re.findall(r"(MemTotal|MemAvailable):\s+(\d+)", mem)}
        size, used, free = (int(n) for n in disk.split()[1:4])  # df prints: device, size, used, free (in kB), percent, mount point
        # Only real version numbers and dates are passed on to the page, never free text from the TV.
        return {"ram_free": kb["MemAvailable"] // 1024, "ram_total": kb["MemTotal"] // 1024,
                "disk_free": free // 1024, "disk_total": size // 1024,
                "seconds": int(float(up.split()[0])), "hours": int(float(up.split()[0]) // 3600),
                "android": find(r"([\d.]+)", system), "patch": find(r"(\d{4}-\d{2}-\d{2})", system),
                "webview": find(r"WebView package[^\n]*?, ([\d.]+)\)", layers),
                "play_services": find(r"lastUpdateTime=(\d{4}-\d{2}-\d{2})", layers),
                "play_system": find(r"versionName=(\d{4}-\d{2}-\d{2})", layers)}
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, IndexError):  # no answer, or not the answer expected
        return {}


def lan_ip():
    """This computer's address on the network that leads to the TV.
    A UDP "connect" sends nothing; it only makes the system choose the right network card."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((TV.split(":")[0], 1))
    return s.getsockname()[0]


# ============================================================ 5. Owner panel
EV = collections.deque(maxlen=40)  # live security records, newest first, memory only: (time, what, who)


def ev(what, who):
    EV.appendleft((time.strftime("%H:%M:%S"), what, who))


def watch():
    """Traffic watch (Windows). Every 15 seconds: list the network connections of this program and of adb.
    Anything that is not a phone of ours, adb's own local plumbing or the TV is recorded once.
    It proves the promise "nothing leaves the home network" while the remote runs."""
    seen, tv_ip = set(), TV.split(":")[0]
    while True:
        try:
            pids = {str(os.getpid())}  # our own process id, plus every adb.exe below
            for line in subprocess.run(["tasklist", "/FI", "IMAGENAME eq adb.exe", "/FO", "CSV", "/NH"],
                                       capture_output=True, text=True, timeout=8, creationflags=NOWIN).stdout.splitlines():
                if line.startswith('"adb.exe"'):
                    pids.add(line.split('","')[1])
            for line in subprocess.run(["netstat", "-ano", "-p", "tcp"],
                                       capture_output=True, text=True, timeout=8, creationflags=NOWIN).stdout.splitlines():
                col = line.split()  # protocol, local address:port, remote address:port, state, process id
                if len(col) == 5 and col[3] in ("ESTABLISHED", "SYN_SENT") and col[4] in pids:
                    local_port, remote = col[1].rsplit(":", 1)[1], col[2].rsplit(":", 1)[0]
                    if local_port == str(PORT) or ipaddress.ip_address(remote).is_loopback or remote == tv_ip:
                        continue  # expected: a phone talking to us, adb talking to itself, or the TV
                    if (col[4], remote) not in seen:
                        seen.add((col[4], remote))
                        where = "to unexpected LAN host" if ipaddress.ip_address(remote).is_private else "to internet"
                        ev("OUTBOUND " + where, f"{remote} pid {col[4]}")
        except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
            pass  # one failed look is not worth stopping the watch
        time.sleep(15)


def stats():
    """Everything the owner panel shows. Built fresh on each call; what the TV is running is shown, never stored."""
    try:
        socket.create_connection((TV.split(":")[0], int(TV.partition(":")[2] or 5555)), 0.5).close()
        up = True
    except OSError:
        up = False
    running, ram = "unknown", "unknown"
    if up:
        try:
            label = {pkg: name for name, (pkg, _) in APPS.items()}
            # One question to the TV: which of our apps are in memory (size, name), and how much memory is free.
            answer = adb("-s", TV, "shell", "ps -A -o RSS,NAME | grep -E '" + "|".join(label) + "'; grep -E 'MemTotal|MemAvailable' /proc/meminfo")
            apps, mem = [], {}
            for line in answer.splitlines():
                part = line.split()
                if len(part) == 2 and part[0].isdigit() and part[1].split(":")[0] in label:  # "123456 com.netflix.ninja"
                    apps.append(f"{label[part[1].split(':')[0]]} {int(part[0]) // 1024} MB")
                elif line.startswith("Mem"):                                                 # "MemTotal:  1700000 kB"
                    mem[part[0]] = int(part[1]) // 1024
            running = ", ".join(apps) or "none"
            ram = f"{mem['MemAvailable:']} free of {mem['MemTotal:']} MB"
        except (subprocess.TimeoutExpired, OSError, KeyError, IndexError, ValueError):
            pass
    taps = max(ST["taps"], 1)
    uptime = int(time.time() - ST["start"])
    if sys.platform != "win32":
        traffic = "not available on this system"
    else:
        traffic = "ALERT, see below" if any(e[1].startswith(("OUTBOUND", "INBOUND")) for e in EV) else "clean"
    return {"TV adb port": "reachable" if up else "NOT reachable", "apps running": running, "TV free RAM": ram,
            "adb shell": "alive" if _sh and _sh.poll() is None else "idle/dead",
            "uptime": f"{uptime // 3600}h {uptime % 3600 // 60}m", "taps": ST["taps"],
            "write time avg/max": f"{ST['ms_sum'] / taps:.1f} / {ST['ms_max']:.1f} ms", "shell restarts": ST["restarts"],
            "wrong tokens": ST["bad"], "lockouts": ST["locks"], "last wrong token": ST["last_bad"] or "never",
            "locked now": sum(1 for count, until in FAILS.values() if count >= 5 and until > time.time()),
            "traffic watch": traffic,
            "live records": "\n  " + "\n  ".join(" ".join(e) for e in list(EV)[:8]) if EV else "none"}


# ============================================================ 6. The page
# One self-contained page: style, script and buttons travel together, nothing is loaded from anywhere else.
# All four layouts are the same page. A class on <body> ("" = Touch, fx = Future, r90 = 90s, sm = Simple)
# shows, hides and restyles the blocks; the choice is remembered on the phone only.
LAYOUTS = (("", "Touch"), ("fx", "Future"), ("r90", "90s"), ("sm", "Simple"))


def _i(shape):
    """An icon: line drawing on a 24x24 grid, in the colour of the text around it. Plain ASCII, so it can never garble."""
    return '<svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.8 stroke-linecap=round stroke-linejoin=round>' + shape + "</svg>"


_SPK = '<path d="M4 9v6h4l5 4V5L8 9z"/>'  # the loudspeaker shared by the three volume icons
CHEV = _i('<path d="M6 15l6-6 6 6"/>')    # an arrow pointing up; CSS turns it for the other directions
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
    "Ch-": _i('<path d="M6 9l6 6 6-6"/>'),
}

CSS = """
/* Colours, one place: background, surface, pressed surface, border, text, muted text, accent. Layouts only change these. */
:root{--bg:#0a0a0f;--s:#14141c;--s2:#1d1d29;--bd:#242431;--tx:#e9e9ef;--mu:#7d7d8c;--ac:#6ea8ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.2 -apple-system,system-ui,sans-serif;padding:max(14px,env(safe-area-inset-top)) 14px max(24px,env(safe-area-inset-bottom));-webkit-user-select:none;user-select:none}

/* First screen = the remote. .scr is as tall as the screen with the browser bars hidden (lvh), so the settings (footer)
   always start below the first screen. #sv is an invisible ruler as tall as the screen with the bars shown (svh): the
   remote is sized to it once, so it always fits and nothing has to be re-measured while the page scrolls. */
.scr{min-height:calc(100vh - 14px);min-height:calc(100lvh - 14px)}
#sv{position:fixed;top:0;left:0;width:0;height:100vh;height:100svh;visibility:hidden;pointer-events:none}
main{max-width:440px;width:100%;margin:0 auto;display:grid;gap:12px}
footer{max-width:440px;margin:0 auto;padding-top:24px;display:grid;gap:12px}
.snd{order:2}
/* The strip under the remote: the safe place to start a scroll (keys act the moment they are touched), or tap it to jump to the settings */
.more{max-width:440px;height:30px;margin:6px auto 0;display:flex;align-items:center;justify-content:center;color:var(--mu)}
.more svg{transform:rotate(180deg)}

/* Buttons and icons */
button{font:inherit;color:var(--tx);background:var(--s);border:1px solid var(--bd);border-radius:20px;height:56px;display:flex;align-items:center;justify-content:center;touch-action:manipulation;-webkit-tap-highlight-color:transparent;transition:transform .08s,background .15s}
button:active{transform:scale(.95);background:var(--s2)}
button:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
button:disabled{opacity:.4}  /* nothing to act on (for example: no camera found) */
svg{width:24px;height:24px}

/* App row: each button has a short line in the app's own colour (--c) */
.apps{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.app{height:46px;font-size:14px;font-weight:600;border-radius:16px;position:relative;overflow:hidden}
.app::after{content:"";position:absolute;left:22%;right:22%;bottom:0;height:3px;border-radius:3px 3px 0 0;background:var(--c)}

/* Touchpad: a dot grid that slides with the finger (--bx, --by), four edge arrows (.e) and a centre ring. .hit = lit for a moment. */
.pad{position:relative;height:250px;border-radius:30px;border:1px solid var(--bd);background:radial-gradient(circle,#34344a 1.3px,transparent 1.9px) var(--bx,0) var(--by,0)/22px 22px,radial-gradient(circle,#1f1f2d,#12121a 72%);touch-action:none;overflow:hidden}
.e{position:absolute;color:#4a4a60;transition:color .15s}
.e svg{width:28px;height:28px}
.u{top:12px;left:50%;margin-left:-14px}
.d{bottom:12px;left:50%;margin-left:-14px;transform:rotate(180deg)}
.l{left:12px;top:50%;margin-top:-14px;transform:rotate(-90deg)}
.r{right:12px;top:50%;margin-top:-14px;transform:rotate(90deg)}
.ring{position:absolute;left:50%;top:50%;width:76px;height:76px;margin:-38px;border-radius:50%;border:1.5px solid #34344a;display:grid;place-items:center;font-style:normal;transition:.15s}
.ring::after{content:"";width:10px;height:10px;border-radius:50%;background:#55556a;transition:.15s}
.ring.hit::after{background:var(--ac)}
.hit{color:var(--ac)}
.ring.hit{border-color:var(--ac);background:#6ea8ff22;transform:scale(1.08)}

/* Rows under the pad: Back/Home/Menu, then three columns: volume pill, Power/Play/Input, channel pill */
.nav,.dock{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.pill,.mid{display:flex;flex-direction:column;gap:8px}
.pill{background:var(--s);border:1px solid var(--bd);border-radius:28px;padding:6px}
.pill button{flex:1;border:0;background:transparent;height:auto;min-height:52px;border-radius:22px}
.pill button:active{background:var(--s2)}
.mid button{flex:1;height:auto;min-height:52px}
.pill em{font-size:11px;font-weight:700;color:var(--mu);text-align:center;letter-spacing:.1em;font-style:normal}
.pw{color:#ff6b61;border-color:#4a2522;background:#1c1213}

/* Small headings and the "pick one" rows (Sound, Layout). .on = the chosen one */
h2{margin:6px 2px 0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mu);font-weight:600}
.seg{display:grid;grid-template-columns:repeat(4,1fr);background:var(--s);border:1px solid var(--bd);border-radius:18px;padding:4px;gap:4px}
.seg button{height:42px;border:0;background:transparent;border-radius:14px;font-size:14px;color:var(--mu)}
.seg button.on{background:var(--s2);color:var(--tx);box-shadow:inset 0 0 0 1px #34344a}

/* Privacy panel: what the TV reported (#pvt) and the two switches. A switch that is .on means "blocked". */
#prv,#secb{grid-template-columns:repeat(2,1fr)}
#pvt,#opt,#sct{margin:0;padding:12px;border-radius:16px;background:#0f0f15;border:1px solid var(--bd);font:12px/1.5 ui-monospace,monospace;color:var(--mu);white-space:pre-wrap}

/* Owner status panel */
#st{margin:6px 0 0;padding:12px;border-radius:16px;background:#0f0f15;border:1px solid var(--bd);font:12px/1.5 ui-monospace,monospace;color:#8fb59a;white-space:pre-wrap}

/* "Ghost finger": shows a sideways and an up-down swipe until the pad is first touched (.used). Off when the phone asks for less motion. */
.ghost{position:absolute;left:50%;top:50%;width:34px;height:34px;margin:-17px;border-radius:50%;background:#ffffff26;box-shadow:0 0 0 6px #ffffff0d;opacity:0;pointer-events:none;animation:hint 7s 1.2s infinite}
.used .ghost{display:none}
@keyframes hint{0%{opacity:0;transform:translate(-70px,0)}8%{opacity:.9}30%{opacity:.9;transform:translate(70px,0)}38%,39%{opacity:0;transform:translate(0,-60px)}46%{opacity:.9}68%{opacity:.9;transform:translate(0,60px)}76%,100%{opacity:0;transform:translate(0,60px)}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}.ghost{animation:none}}

/* Layout "Simple" (.sm): hide everything but a large D-pad, Back, Home, volume and power */
.sm .apps,.sm .pad,.sm .snd,.sm .nav button:last-child,.sm .dock .pill:last-child,.sm .mid button:not(.pw){display:none}
.sm .nav,.sm .dock{grid-template-columns:repeat(2,1fr)}
.sm button{min-height:64px}
.sm .dp{display:grid;grid-template:repeat(3,92px)/repeat(3,92px)}
.sm .dp svg{width:34px;height:34px}
.sm .go{font-size:20px;font-weight:700}

/* Blocks that only some layouts show: volume bar, button D-pad, number pad */
#vol,.dp,.num{display:none}
/* Volume bar: <i> is the fill, as tall as --v; <b> is the number on top */
#vol{position:relative;flex:1;min-height:100px;border-radius:22px;background:#04090f;border:1px solid var(--bd);overflow:hidden;touch-action:none}
#vol i{position:absolute;left:0;right:0;bottom:0;height:var(--v,0%);background:linear-gradient(0deg,#0b4a6b,var(--ac));box-shadow:0 0 18px var(--ac)}
#vol b{position:absolute;inset:0;display:grid;place-items:center;font:700 22px ui-monospace,monospace;text-shadow:0 0 8px #000}
#vol.cap b{color:#ffb84d}  /* the number turns amber when the drag has reached its safety limit */

/* Layout "Future" (.fx): neon colours and glow, volume bar instead of Vol+/Vol- (.v), number pad instead of the channel pill */
.fx{--bg:#03060d;--s:#07121f;--s2:#0c2036;--bd:#14405f;--tx:#d8f6ff;--mu:#5b8fb0;--ac:#39e6ff;font-family:ui-monospace,monospace}
.fx button,.fx .pill,.fx .pad,.fx .seg{box-shadow:0 0 14px #39e6ff1f,inset 0 0 10px #39e6ff12}
.fx .pad{background:radial-gradient(circle,#1c5f85 1.3px,transparent 1.9px) var(--bx,0) var(--by,0)/22px 22px,radial-gradient(circle,#0a2236,#040b14 72%)}
.fx .e{color:#1c5f85}
.fx .ring{border-color:#1c5f85}
.fx #vol{display:block}
.fx .v{display:none}
.fx .num{display:grid;grid-template-columns:repeat(5,1fr);order:1}
.fx .num button:last-child{grid-column:auto}
.fx .dock .pill:last-child{display:none}
.fx .dock{grid-template-columns:repeat(2,1fr)}

/* Layout "90s" (.r90): a dark plastic body, raised rubber keys, number pad on top, button D-pad instead of the touchpad */
.r90{--bg:#3a342c;--s:#4b4c50;--s2:#606166;--bd:#2a2a2d;--tx:#f1efe6;--mu:#a8a59a;--ac:#f1efe6;font-family:Arial Narrow,Arial,sans-serif}
.r90 main{max-width:330px;background:#1c1c1e;border-radius:38px;padding:22px 18px;border:2px solid #0d0d0e;box-shadow:inset 0 2px 0 #3a3a3e,0 12px 30px #0008}
.r90 button{border-radius:10px;font-weight:700;box-shadow:0 4px 0 #111,inset 0 1px 0 #7b7c82}
.r90 button:active{transform:translateY(3px);box-shadow:0 1px 0 #111}
.r90 .pill,.r90 .seg{background:transparent;border:0;padding:0}
.r90 .pill button,.r90 .seg button{background:var(--s);color:var(--tx)}
.r90 .seg button.on{background:var(--s2)}
.r90 .pw{background:#c22d24;color:#fff}
.r90 .go{border-radius:50%}
.r90 .pad{display:none}
.r90 .dp,.r90 .num{display:grid}

/* Number pad (3 columns, the 0 centred) and button D-pad (a cross: up, left, OK, right, down) */
.num{grid-template-columns:repeat(3,1fr);gap:10px;order:-1}
.num button{height:44px}
.num button:last-child{grid-column:2}
.dp{grid-template:repeat(3,62px)/repeat(3,62px);gap:8px;justify-content:center}
.dp button{height:auto}
.gu{grid-area:1/2}.gl{grid-area:2/1}.go{grid-area:2/2}.gr{grid-area:2/3}.gd{grid-area:3/2}
.gl svg{transform:rotate(-90deg)}.gr svg{transform:rotate(90deg)}.gd svg{transform:rotate(180deg)}
"""

JS = """
// ---------------------------------------------------------------- Sending
// Every request goes back to the server this page came from, under the secret path T.
const T = '@TOK@';
// A press: a short buzz where the phone supports it, then the request. Nothing waits for the answer.
const press = path => { navigator.vibrate && navigator.vibrate(10); fetch('/'+T+'/'+path, {method: 'POST'}); };
const k = name => press('key/' + encodeURIComponent(name));   // a remote key
const a = name => press('app/' + encodeURIComponent(name));   // open an app
// A sound preset: send it, then mark its button as the chosen one in its row.
const s = (name, el) => { press('snd/' + encodeURIComponent(name)); el.parentNode.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === el)); };
// Light an element up for a moment (feedback for swipes and taps).
const flash = (el, cls) => { el.classList.add(cls); setTimeout(() => el.classList.remove(cls), 180); };

// ---------------------------------------------------------------- Touchpad
// Swipe = arrow keys, tap = OK. Hold the finger pushed to one side and it keeps stepping, faster the further you push.
const pad = document.getElementById('pad');
const ARROW = {Left: 'l', Right: 'r', Up: 'u', Down: 'd'};                    // direction -> class of its arrow on the pad
const OPPOSITE = {Left: 'Right', Right: 'Left', Up: 'Down', Down: 'Up'};
const move = dir => { k(dir); flash(pad.querySelector('.' + ARROW[dir]), 'hit'); };
const STEP = 28;   // px the finger travels for one step
const TAP = 16;    // a touch that moved less than this, and lasted under 0.6 s, is a tap
let startX = null, startY,        // where the finger landed (null = no finger on the pad)
    anchorX, anchorY,             // where the last step was counted from
    lastX, lastY,                 // the finger's previous position
    downAt, movedAt, steppedAt,   // times: finger down, last movement, last step sent
    reach,                        // furthest distance from the start: tells a tap from a swipe
    gridX = 0, gridY = 0,         // how far the dot grid has slid
    axis, dir,                    // the locked axis ('x' or 'y') and the last direction sent
    timer;                        // the hold-to-repeat timer

// Runs 33 times a second while a finger is down. If the finger rests (150 ms) at least 40 px away from
// where it started, repeat the last direction: every 380 ms at first, down to every 70 ms far out.
const repeat = () => {
  if (dir == null || Date.now() - movedAt < 150) return;
  const sign = (dir == 'Right' || dir == 'Down') ? 1 : -1;
  const pushed = (axis == 'x' ? lastX - startX : lastY - startY) * sign;
  if (pushed < 40) return;
  const gap = Math.max(70, 380 - (pushed - 40) * 4);
  if (Date.now() - steppedAt >= gap) { steppedAt = Date.now(); move(dir); }
};
const release = () => { startX = null; clearInterval(timer); };

pad.onpointerdown = e => {
  pad.setPointerCapture(e.pointerId);   // keep getting this finger's moves even if it leaves the pad
  pad.classList.add('used');            // hides the ghost-finger hint for good
  startX = anchorX = lastX = e.clientX;
  startY = anchorY = lastY = e.clientY;
  downAt = movedAt = Date.now(); reach = 0; axis = dir = null; steppedAt = 0;
  clearInterval(timer);
  timer = setInterval(repeat, 30);
};
pad.onpointermove = e => {
  if (startX == null) return;
  reach = Math.max(reach, Math.abs(e.clientX - startX), Math.abs(e.clientY - startY));
  // slide the dot grid with the finger (22 px = one grid cell, so it wraps without a jump)
  gridX = (gridX + e.clientX - lastX) % 22; gridY = (gridY + e.clientY - lastY) % 22;
  lastX = e.clientX; lastY = e.clientY; movedAt = Date.now();
  pad.style.setProperty('--bx', gridX + 'px'); pad.style.setProperty('--by', gridY + 'px');
  // Has the finger gone one STEP from the anchor, clearly more along one axis than the other?
  // After the first step the swipe is locked to that axis, so a slightly diagonal finger does not wander.
  const dx = lastX - anchorX, dy = lastY - anchorY;
  let d = null;
  if (axis != 'y' && Math.abs(dx) >= STEP && Math.abs(dx) >= 1.2 * Math.abs(dy)) d = dx > 0 ? 'Right' : 'Left';
  else if (axis != 'x' && Math.abs(dy) >= STEP && Math.abs(dy) >= 1.2 * Math.abs(dx)) d = dy > 0 ? 'Down' : 'Up';
  if (!d) return;
  // Turning back needs a clearly longer move (1.7 steps), so a small wobble does not undo the last step.
  if (dir && OPPOSITE[dir] == d && Math.max(Math.abs(dx), Math.abs(dy)) < STEP * 1.7) return;
  axis = (d == 'Left' || d == 'Right') ? 'x' : 'y';
  dir = d; anchorX = lastX; anchorY = lastY; steppedAt = Date.now();
  move(d);
};
pad.onpointerup = () => {
  if (startX != null && reach < TAP && Date.now() - downAt < 600) { k('OK'); flash(pad.querySelector('.ring'), 'hit'); }
  release();
};
pad.onpointercancel = release;

// ---------------------------------------------------------------- Volume bar (Future layout)
// The number follows the finger at once; the TV walks to it. The TV only obeys volume keys, so the page asks for
// at most 5 presses per request (the first leaves the moment the finger moves), sends the next request only when
// those are done and always towards the newest number. Nothing queues up, a change of direction takes effect at
// once, and the TV moves at about 8 steps a second, which is as fast as it accepts them.
const vol = document.getElementById('vol');
const LIMIT = @LIMITS@;   // the safety numbers from VOL_LIMITS in the server file
// Night by this phone's own clock only. Nothing is asked of any service and nothing is sent.
const isNight = () => {
  const hour = new Date().getHours(), from = LIMIT.night_from, to = LIMIT.night_to;
  return from > to ? (hour >= from || hour < to) : (hour >= from && hour < to);
};
let volMax = 100,
    wanted = null,      // the number on the bar (null until the TV's level has been read)
    reached = null,     // where the TV is, counting the key presses already sent
    dragFrom = null,    // the bar's number when the finger landed (null = no finger on the bar)
    dragY,              // where the finger landed
    dragTop,            // the highest number this drag may reach
    busy = 0;           // 1 while the TV is still working through the last presses
const showVol = v => { wanted = v; vol.style.setProperty('--v', v / volMax * 100 + '%'); vol.lastChild.textContent = v; };
// Ask the server for the TV's real level. Ignored while a drag or key presses are in progress.
const readVol = () => fetch('/'+T+'/vol').then(r => r.json()).then(j => {
  if (j.max && dragFrom == null && !busy) { volMax = j.max; reached = j.v; showVol(j.v); }
}).catch(() => {});
const walk = () => {
  if (busy) return;
  const steps = Math.max(-5, Math.min(5, wanted - reached));
  if (!steps) { dragFrom == null && readVol(); return; }    // arrived: once the finger is up, read the real level back
  reached += steps; busy = 1;
  fetch('/'+T+'/vol/' + (steps > 0 ? 'up/' : 'down/') + Math.abs(steps), {method: 'POST'});
  setTimeout(() => { busy = 0; walk(); }, 250 + 85 * Math.abs(steps));   // the time the TV needs for these presses (measured)
};
// The drag is relative, 3 px per step: touching the bar never jumps the volume, only moving the finger changes it.
vol.onpointerdown = e => {
  if (wanted == null) return;
  vol.setPointerCapture(e.pointerId);
  dragFrom = wanted; dragY = e.clientY;
  // Safety ceiling for this drag: the day or night maximum and at most LIMIT.drag steps up, whichever is lower.
  // If the TV is already louder than that (set with another remote), the bar simply cannot raise it further.
  dragTop = Math.max(dragFrom, Math.min(volMax, isNight() ? LIMIT.night : LIMIT.day, dragFrom + LIMIT.drag));
};
vol.onpointermove = e => {
  if (dragFrom == null) return;
  const asked = dragFrom + Math.round((dragY - e.clientY) / 3);
  vol.classList.toggle('cap', asked > dragTop);
  showVol(Math.max(0, Math.min(dragTop, asked)));   // lowering is never limited
  walk();
};
vol.onpointerup = vol.onpointercancel = () => { if (dragFrom != null) { dragFrom = null; vol.classList.remove('cap'); walk(); } };

// ---------------------------------------------------------------- Privacy panel (in the settings)
// Shows what the TV itself reports about cameras and microphones, and two switches for Android's system-wide blocks.
// The state always comes from the TV: after a switch is pressed the TV is asked again, nothing is assumed.
let privacy = null;   // the TV's last answer (null until it has answered)
const list = names => names.length ? names.join(', ') : 'none';
const showPrivacy = j => {
  const text = document.getElementById('pvt');
  if (!('mic_blocked' in j)) {
    privacy = null; text.textContent = 'The TV did not answer.';
    document.getElementById('rev').textContent = 'Revoke all permissions';   // never left on "Revoking..."
    return;
  }
  privacy = j;
  // A control with nothing to act on is greyed out and says why, so the panel reads at a glance.
  // Exception: a block that is already on stays clickable even with no device found, so it can always be turned off.
  const found = {mic: j.mics.length > 0, cam: j.cameras > 0};
  for (const what of ['mic', 'cam']) {
    const button = document.getElementById(what), blocked = j[what + '_blocked'];
    button.disabled = !found[what] && !blocked;
    button.textContent = (what == 'mic' ? 'Microphone: ' : 'Camera: ') + (blocked ? 'BLOCKED' : found[what] ? 'allowed' : 'not found');
    button.classList.toggle('on', blocked);
  }
  const holders = j.mic_apps.length + j.cam_apps.length, rev = document.getElementById('rev');
  rev.disabled = !holders;
  rev.textContent = holders ? 'Revoke all permissions' : 'No permissions to revoke';
  text.textContent = 'Camera: ' + (j.cameras == null ? 'unknown' : j.cameras ? 'FOUND (' + j.cameras + ')' : 'not found')
    + '\\nMicrophone: ' + (j.mics.length ? 'FOUND (' + j.mics.join(', ') + ')' : 'not found')
    + '\\nApps holding the microphone permission: ' + list(j.mic_apps)
    + '\\nApps holding the camera permission: ' + list(j.cam_apps);
};
// A panel is always in one of three states: asking, answered, or no answer. A request that fails on the way
// (phone off the Wi-Fi, the remote's computer asleep) is shown as "no answer", never left on "Asking the TV...".
const readPrivacy = () => fetch('/'+T+'/privacy').then(r => r.json()).then(showPrivacy).catch(() => showPrivacy({}));
// A switch: ask for the opposite of what the TV last reported, then read the TV again.
const priv = what => {
  if (!privacy || document.getElementById(what).disabled) return;
  press('priv/' + what + '/' + (privacy[what + '_blocked'] ? 'allow' : 'block'));
  setTimeout(readPrivacy, 700);
};
// Revoke all: take the microphone and camera permission away from every app, then read the TV again (it needs a few seconds).
const revoke = el => { press('priv/apps/revoke'); el.textContent = 'Revoking...'; setTimeout(readPrivacy, 5000); };
// Reading takes the TV about 2 seconds, so it is done when the panel scrolls into view, not on every page load.
new IntersectionObserver(seen => { seen[0].isIntersecting && readPrivacy(); }).observe(document.getElementById('pvt'));

// ---------------------------------------------------------------- Security panel (in the settings)
// A read-only check of the TV's settings, one line each: "ok" when nothing is exposed, "CHECK" when something is
// worth a look. Then the network ports the TV listens on, and two switches. Everything shown comes from the TV.
let secState = null;   // the TV's last answer
const line = (names, what) => (names.length ? 'CHECK ' : 'ok    ') + what + ': ' + list(names);
const showSecurity = j => {
  const text = document.getElementById('sct');
  if (!('ports' in j)) { secState = null; text.textContent = 'The TV did not answer.'; return; }
  secState = j;
  text.textContent = line(j.accessibility, 'Accessibility services (can read the screen)')
    + '\\n' + line(j.listeners, 'Apps that read notifications')
    + '\\n' + line(j.installers, 'Apps allowed to install apps')
    + '\\n' + line(j.usage, 'Apps that see what you use')
    + '\\n' + (j.location_on ? 'CHECK Location: on' : 'ok    Location: off')
    + '\\n' + (j.hotword_on ? 'CHECK Voice hotword: listening' : 'ok    Voice hotword: off')
    + '\\nOpen doors on the home network:'
    + (j.ports.length ? j.ports.map(p => '\\n  ' + p[0] + '  ' + p[1] + ', held open by ' + p[2]).join('') : ' none');
  const cast = document.getElementById('cast'), ads = document.getElementById('adl');
  cast.disabled = j.cast == 'none';
  cast.textContent = 'Cast: ' + (j.cast == 'none' ? 'not found' : j.cast == 'on' ? 'on' : 'OFF');
  cast.classList.toggle('on', j.cast == 'off');
  ads.textContent = 'Ad personalisation: ' + (j.ads_limited ? 'LIMITED' : 'not limited');
  ads.classList.toggle('on', j.ads_limited);
};
const readSecurity = () => fetch('/'+T+'/security').then(r => r.json()).then(showSecurity).catch(() => showSecurity({}));
// A switch: ask for the opposite of what the TV last reported, then read the TV again (Cast needs a few seconds to start or stop).
const secure = what => {
  if (!secState || document.getElementById(what == 'cast' ? 'cast' : 'adl').disabled) return;
  press('sec/' + (what == 'cast' ? 'cast/' + (secState.cast == 'on' ? 'off' : 'on') : 'ads/' + (secState.ads_limited ? 'free' : 'limit')));
  setTimeout(readSecurity, what == 'cast' ? 7000 : 800);
};
new IntersectionObserver(seen => { seen[0].isIntersecting && readSecurity(); }).observe(document.getElementById('sct'));

// ---------------------------------------------------------------- Optimize panel (in the settings)
// Shows the TV's free memory, free storage and how long it has been on. The button runs the fixed clean-up on the TV,
// then the TV is read again and the change is shown next to each number, so the effect is measured, not claimed.
let before = null;   // the numbers from just before the last clean-up (null = no clean-up yet)
const gained = (now, was) => was == null || now == was ? '' : ' (' + (now > was ? '+' : '') + (now - was) + ' MB)';
// How old the TV's last security patch is, counted with this phone's own clock: nothing is looked up anywhere.
// Android publishes security fixes every month, so each month of age is one round of fixes this TV does not have.
const monthsSince = date => { const then = new Date(date), now = new Date(); return (now.getFullYear() - then.getFullYear()) * 12 + now.getMonth() - then.getMonth(); };
const aged = date => date ? date + ' (' + monthsSince(date) + ' months old)' : 'the TV does not say';
const patchLine = j => {
  // The parts the app store keeps updating after the TV maker has stopped. They do not replace system patches,
  // but while they are fresh a good share of the usual ways in (web content, Google's own services) stays patched.
  const layers = '\\nStill updated through the store:'
    + '\\n  WebView (web engine inside apps): ' + (j.webview || 'the TV does not say')
    + '\\n  Google Play services: ' + aged(j.play_services)
    + '\\n  Google Play system update: ' + aged(j.play_system);
  if (!j.patch) return '\\nSecurity patch: the TV does not say' + layers;
  const months = monthsSince(j.patch);
  return '\\nAndroid ' + j.android + ', security patch ' + j.patch + ' (' + months + ' months old)'
    + (months > 6 ? '\\nWARNING: ' + months + ' monthly rounds of security fixes are missing. Check for a system update in the TV menu;'
      + ' if none comes any more, see "When updates stop" in the README.' : '') + layers;
};
const showHealth = j => {
  const text = document.getElementById('opt');
  document.getElementById('tunebtn').textContent = 'Optimize now';
  if (!('ram_free' in j)) { text.textContent = 'The TV did not answer.'; return; }
  const was = before || {};
  text.textContent = note + 'Memory free: ' + j.ram_free + ' of ' + j.ram_total + ' MB' + gained(j.ram_free, was.ram_free)
    + '\\nStorage free: ' + j.disk_free + ' of ' + j.disk_total + ' MB' + gained(j.disk_free, was.disk_free)
    + '\\nOn for: ' + (j.hours < 48 ? j.hours + ' h' : Math.floor(j.hours / 24) + ' days')
    + (j.hours >= 168 ? '\\nOn for over a week: a clean restart clears more than Optimize can.' : '')
    + patchLine(j);
  before = null;
  last = j;
};
let last = null;     // the numbers now on screen
let note = '';       // the report of the last clean restart, kept above the numbers until the page is closed
const readHealth = () => fetch('/'+T+'/health').then(r => r.json()).then(showHealth).catch(() => showHealth({}));

// Clean restart. 1) Take a picture of what matters: the privacy blocks, who holds the permissions, the security
// settings, the open ports and who holds them. 2) Clean up and restart the TV. 3) Wait until it answers again with a
// fresh "on for" time. 4) Take the picture again and list every change that makes the TV more exposed than before.
// The picture lives in this page only, for these few minutes: nothing is saved, on the phone or on the server.
const sleep = ms => new Promise(done => setTimeout(done, ms));
const picture = () => Promise.all(['privacy', 'security', 'health'].map(part => fetch('/'+T+'/'+part).then(r => r.json())))
  .then(([p, s, h]) => ({p, s, h, ok: 'mic_blocked' in p && 'ports' in s && 'ram_free' in h}));
const added = (was, now) => now.filter(x => !was.includes(x));
const worse = (was, now) => {
  const found = [];
  if (was.p.mic_blocked && !now.p.mic_blocked) found.push('the microphone block is off');
  if (was.p.cam_blocked && !now.p.cam_blocked) found.push('the camera block is off');
  added(was.p.mic_apps, now.p.mic_apps).forEach(app => found.push(app + ' holds the microphone permission again'));
  added(was.p.cam_apps, now.p.cam_apps).forEach(app => found.push(app + ' holds the camera permission again'));
  if (was.s.cast == 'off' && now.s.cast == 'on') found.push('Cast is on again');
  if (was.s.ads_limited && !now.s.ads_limited) found.push('the ad personalisation limit is gone');
  if (!was.s.location_on && now.s.location_on) found.push('location is on');
  if (!was.s.hotword_on && now.s.hotword_on) found.push('the voice hotword is listening');
  added(was.s.accessibility, now.s.accessibility).forEach(app => found.push(app + ' is a new accessibility service'));
  added(was.s.listeners, now.s.listeners).forEach(app => found.push(app + ' now reads notifications'));
  added(was.s.installers, now.s.installers).forEach(app => found.push(app + ' may now install apps'));
  added(was.s.usage, now.s.usage).forEach(app => found.push(app + ' now sees what you use'));
  // A port counts as new only if its owner did not hold a port before: apps pick a different port number at each start.
  const owners = was.s.ports.map(port => port[2]);
  now.s.ports.filter(port => !owners.includes(port[2])).forEach(port => found.push('port ' + port[0] + ' is open, held by ' + port[2]));
  return found;
};
const restart = async el => {
  if (el.disabled || !confirm('Restart the TV now? It takes about a minute and stops whatever is playing.')) return;
  const text = document.getElementById('opt');
  el.disabled = true;
  try {
    const was = await picture().catch(() => ({}));
    if (!was.ok) { text.textContent = 'The TV did not answer, so nothing was done.'; return; }
    press('tune/restart');
    const started = Date.now();
    text.textContent = 'Cleaning up and restarting the TV. Keep this page open...';
    let back = false;
    while (!back && Date.now() - started < 300000) {   // give it 5 minutes
      await sleep(5000);
      const h = await fetch('/'+T+'/health').then(r => r.json()).catch(() => ({}));
      back = 'seconds' in h && h.seconds < was.h.seconds;   // answering again, and its clock started over
    }
    if (!back) { text.textContent = 'The TV has not come back after 5 minutes. If it is on, turn network debugging on again in its menu.'; return; }
    const took = Math.round((Date.now() - started) / 1000);
    text.textContent = 'The TV is back after ' + took + ' s. Checking it...';
    await sleep(25000);   // let its services start before looking
    const now = await picture().catch(() => ({}));
    if (!now.ok) { text.textContent = 'The TV restarted but did not answer the check. Open the panels above to read its state.'; return; }
    const found = worse(was, now);
    note = 'Clean restart done in ' + took + ' s. Memory free before: ' + was.h.ram_free + ' MB.\\n'
      + (found.length ? 'CHECK, more exposed than before the restart:\\n' + found.map(f => '  - ' + f).join('\\n')
          + '\\nIf you do not expect this, press Power to turn the TV off.\\n'
        : 'Nothing is more exposed than before: blocks, permissions, Cast, settings and port owners are as you left them.\\n');
    showPrivacy(now.p); showSecurity(now.s); showHealth(now.h);
  } finally { el.disabled = false; }
};
const tune = el => { if (!last) return; before = last; press('tune/run'); el.textContent = 'Optimizing...'; setTimeout(readHealth, 5000); };
new IntersectionObserver(seen => { seen[0].isIntersecting && readHealth(); }).observe(document.getElementById('opt'));

// ---------------------------------------------------------------- Fit to the screen
// Scale the whole remote with one zoom factor so it fits the screen as it is with the browser bars showing
// (the smallest it ever gets; #sv measures it), whether the bars sit at the top or the bottom. Never below 0.7
// (keys too small) or above 1.6. It is done when the page opens, when the layout changes and when the phone is
// turned, and NOT while scrolling: browser bars slide in and out during a scroll, and re-measuring on each of
// those changes made the page stutter and jam.
const M = document.querySelector('main');
const fit = () => {
  const visible = document.getElementById('sv').offsetHeight || innerHeight;
  const freeHeight = visible - parseFloat(getComputedStyle(document.body).paddingTop) - 50;   // 50 = the strip under the remote + a little air
  M.style.zoom = 1;   // measure the remote at its natural size first
  M.style.zoom = Math.max(.7, Math.min(1.6, freeHeight / M.offsetHeight, (innerWidth - 28) / M.offsetWidth));
};
let fitWidth = innerWidth;
onresize = () => { if (innerWidth != fitWidth) { fitWidth = innerWidth; fit(); } };   // a change of height alone is a browser bar: ignore it

// ---------------------------------------------------------------- Layouts
// A layout is just a class on <body>. The choice is kept on this phone (localStorage), the server never learns it.
const lay = name => {
  document.body.className = name;
  try { localStorage.lay = name; } catch (e) {}   // private browsing may refuse: the layout then lasts until reload
  document.querySelectorAll('#lay button').forEach(x => x.classList.toggle('on', x.dataset.l == name));
  if (name == 'fx') readVol();
  fit();
};
let saved = '';
try { saved = localStorage.lay || ''; } catch (e) {}
lay(saved);
// Back from another app: the volume may have changed meanwhile, read it again.
document.onvisibilitychange = () => { document.hidden || document.body.className != 'fx' || readVol(); };
"""

# Owner page only: fill the status panel now and every 5 seconds.
STATS_JS = """
const showStats = () => fetch('/'+T+'/stats').then(r => r.json()).then(j => {
  document.getElementById('st').textContent = Object.entries(j).map(e => e.join(': ')).join('\\n');
});
showStats(); setInterval(showStats, 5000);
"""


def page(tok, admin=False):
    """Build the whole page. `tok` is the secret the page will use for its own requests; `admin` adds the status panel.
    Every name written into the page comes from the tables in this file, never from a visitor."""
    def key(name, cls=""):  # a button with an icon; it acts on touch-down, not on release, to feel instant
        return f'<button class="{cls}" onpointerdown="k(\'{name}\')" aria-label="{name}">{ICONS[name]}</button>'

    apps = "".join(f'<button class=app style="--c:{colour}" onpointerdown="a(\'{name}\')">{name}</button>' for name, (_, colour) in APPS.items())
    sound = "".join(f'<button onpointerdown="s(\'{name}\',this)">{name}</button>' for name in SND)
    arrows = "".join(f'<i class="e {side}">{CHEV}</i>' for side in "udlr")
    digits = "".join(f'<button onpointerdown="k(\'{n}\')">{n}</button>' for n in "1234567890")
    dpad = "".join(f'<button class=g{cls} onpointerdown="k(\'{name}\')" aria-label="{name}">{"OK" if name == "OK" else CHEV}</button>'
                   for cls, name in zip("ulord", ("Up", "Left", "OK", "Right", "Down")))
    layouts = "".join(f'<button data-l="{cls}" onpointerdown="lay(\'{cls}\')">{name}</button>' for cls, name in LAYOUTS)
    body = (
        '<div class=scr><main>'
        f'<section class=apps>{apps}</section>'
        f'<section class=num>{digits}</section>'
        f'<section class=pad id=pad role=application aria-label="Touchpad: swipe to move, tap to select">{arrows}<i class=ring></i><i class=ghost></i></section>'
        f'<section class=dp>{dpad}</section>'
        f'<section class=nav>{key("Back")}{key("Home")}{key("Menu")}</section>'
        '<section class=dock>'
        f'<div class=pill>{key("Vol+", "v")}<div id=vol role=slider aria-label="Volume: drag up or down"><i></i><b>--</b></div>{key("Mute")}{key("Vol-", "v")}</div>'
        f'<div class=mid>{key("Power", "pw")}{key("Play/Pause")}{key("Input")}</div>'
        f'<div class=pill>{key("Ch+")}<em>CH</em>{key("Ch-")}</div>'
        '</section>'
        f'<h2 class=snd>Sound</h2><section class="seg snd">{sound}</section>'
        '</main>'
        f'<div class=more aria-label="Settings below" onclick="document.querySelector(\'footer\').scrollIntoView()">{CHEV}</div>'
        '</div><i id=sv></i>'
        f'<footer><h2>Layout</h2><section class=seg id=lay>{layouts}</section>'
        '<h2>Privacy</h2><p id=pvt>Asking the TV...</p>'
        '<section class=seg id=prv><button id=mic onpointerdown="priv(\'mic\')">Microphone</button>'
        '<button id=cam onpointerdown="priv(\'cam\')">Camera</button></section>'
        '<button id=rev onclick="revoke(this)">Revoke all permissions</button>'
        '<h2>Security</h2><p id=sct>Asking the TV...</p>'
        '<section class=seg id=secb><button id=cast onpointerdown="secure(\'cast\')">Cast</button>'
        '<button id=adl onpointerdown="secure(\'ads\')">Ad personalisation</button></section>'
        '<h2>Optimize TV</h2><p id=opt>Asking the TV...</p>'
        '<button id=tunebtn onclick="tune(this)">Optimize now</button>'
        '<button id=rst onclick="restart(this)">Clean restart</button>'
        + ("<pre id=st></pre>" if admin else "") + '</footer>')
    script = JS.replace("@TOK@", tok).replace("@LIMITS@", json.dumps(VOL_LIMITS)) + (STATS_JS if admin else "")
    return ('<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name=color-scheme content=dark><meta name=theme-color content="#0a0a0f"><meta name=apple-mobile-web-app-capable content=yes>'
            f'<title>TV remote</title><style>{CSS}</style></head><body>{body}<script>{script}</script></body></html>')


# ============================================================ 7. Web server
# Sent with every answer. In short: keep nothing, load nothing from elsewhere, never show this page inside another.
HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),  # the secret is in the address: never pass the address on
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
                                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keeps the connection open: one encrypted handshake per session, not per tap
    timeout = 30                   # an idle connection gives its thread back after 30 s

    def reply(self, code, body=b"", ctype="text/html; charset=utf-8", cookie=""):
        self.send_response(code)
        if cookie:  # pairing: 90 days, sent over TLS only, hidden from page scripts, never sent from other sites
            self.send_header("Set-Cookie", f"dev={cookie}; Max-Age=7776000; Path=/; Secure; HttpOnly; SameSite=Strict")
        for name, value in (("Content-Type", ctype), ("Content-Length", str(len(body)))) + HEADERS:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, path):
        """The gate every request passes. Returns the part of the address after the secret, or None (= answer 404).
        Also sets self.admin. A refusal looks the same whatever the reason, so it reveals nothing."""
        ip = self.client_address[0]
        if not ipaddress.ip_address(ip).is_private:  # home-network addresses only
            ev("INBOUND from non-LAN address", ip)
            return None
        count, until = FAILS.get(ip, (0, 0))
        secret, _, rest = path.lstrip("/").partition("/")
        # Both comparisons always run, each in constant time: the answer time does not tell which secret was closer.
        user = hmac.compare_digest(secret.encode(), TOKEN.encode())
        admin = hmac.compare_digest(secret.encode(), ADMIN.encode())
        if count >= 5 and time.time() < until and not admin:
            ev("request while locked out", ip)  # 5 wrong secrets = locked for 5 minutes; the owner secret still gets in
            return None
        if admin or (user and cookie_ok(self.headers)):  # the plain remote also needs a paired phone
            self.admin = admin
            FAILS.pop(ip, None)
            return rest
        # Refused: count it.
        if len(FAILS) > 1000:
            FAILS.clear()  # a flood of addresses must not grow memory without limit
        FAILS[ip] = (1 if count >= 5 else count + 1, time.time() + 300)  # count >= 5 here means an old lockout just ended
        locked = FAILS[ip][0] == 5
        ST["bad"] += 1
        ST["locks"] += locked
        ST["last_bad"] = time.strftime("%H:%M:%S ") + ip
        ev("right token, device not paired" if user else "wrong token", ip + (" -> LOCKED 5 min" if locked else ""))
        return None

    def do_GET(self):
        """GET only reads: the page, the volume level, the owner's status numbers."""
        rest = self.allowed(self.path)
        if rest == "":
            cookie = ""
            if self.admin and not cookie_ok(self.headers):  # the owner address on a new phone: pair it
                cookie = secrets.token_urlsafe(32)
                DEVS.append(_h(cookie))
                del DEVS[:-10]  # keep the newest 10 phones
                save_devs()
            self.reply(200, page(ADMIN if self.admin else TOKEN, self.admin).encode("utf-8"), cookie=cookie)
        elif rest == "vol":
            self.reply(200, json.dumps(vol_get()).encode(), "application/json")
        elif rest == "privacy":
            self.reply(200, json.dumps(privacy()).encode(), "application/json")
        elif rest == "security":
            self.reply(200, json.dumps(security()).encode(), "application/json")
        elif rest == "health":
            self.reply(200, json.dumps(health()).encode(), "application/json")
        elif rest == "stats" and self.admin:
            self.reply(200, json.dumps(stats()).encode(), "application/json")
        else:
            self.reply(404)

    def do_POST(self):
        """POST acts: /<secret>/<kind>/<name>. The name only selects an entry of a table; unknown = 404."""
        if self.headers.get("Content-Length", "0") != "0":
            self.close_connection = True  # a body is never read, so a connection that sent one is not reused
        kind, _, name = (self.allowed(self.path) or "").partition("/")
        name = unquote(name)
        if kind == "key" and name in KEYS:
            cmd = "input keyevent " + KEYS[name]
        elif kind == "app" and name in APPS:
            cmd = launch(name)
        elif kind == "vol" and name in VSTEP:
            cmd = VSTEP[name]
        elif kind == "snd" and name in SND:
            cmd = SND[name]
        elif kind == "priv" and name in PRIV:
            cmd = PRIV[name]
        elif kind == "sec" and name in SEC:
            cmd = SEC[name]
        elif kind == "tune" and name in TUNE:
            cmd = TUNE[name]
        else:
            return self.reply(404)
        tv(cmd)
        self.reply(200, b"ok", "text/plain")

    def log_message(self, *a):
        pass  # the secret is part of the address, so requests are never logged


class S(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows the opposite would let a second program share the port and answer for us

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], OSError):  # a phone closing or timing out is normal: stay silent
            super().handle_error(request, client_address)


# ============================================================ 8. Start
if __name__ == "__main__":
    connected = ""
    try:
        connected = adb("connect", TV, timeout=10).strip()
    except (OSError, subprocess.TimeoutExpired):
        pass  # not fatal: tv() connects again on the first tap
    while True:
        try:
            ip = lan_ip()
            break
        except OSError:  # no network yet (started at logon, before Wi-Fi): wait, since stopping here would never recover
            time.sleep(5)
    print(connected, f"\nOpen on phone: https://{ip}:{PORT}/{TOKEN}\nOwner panel (remote + status): https://{ip}:{PORT}/{ADMIN}", flush=True)
    if sys.platform == "win32":  # the watch reads tasklist and netstat, which exist on Windows only
        threading.Thread(target=watch, daemon=True).start()
    cert, key = os.path.join(_dir, "cert.pem"), os.path.join(_dir, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        sys.exit("cert.pem and key.pem are missing: the remote never serves plain HTTP")
    srv = S((ip, PORT), H)  # bound to the home-network address only, never to every address
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print("TLS on: use https://", flush=True)
    srv.serve_forever()
