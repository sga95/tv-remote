# Local TV remote

A phone web page that works as a remote control for an Android TV. One small Python file, local only: nothing leaves the home network and nothing depends on a cloud service, an account or a third-party Python package.

## What it does

- Touchpad: swipe moves the focus (D-pad), tap is OK. Hold the finger pushed in one direction and it keeps scrolling, faster the further you push. Swipes lock to one axis and a small step back does not trigger the opposite key.
- Back, Home, Menu, Power, Play/Pause, Input, Volume (up, mute, down), Channel up and down.
- App launchers (Netflix, Prime, YouTube, Spotify, Twitch, the launcher). Opening an app closes the other apps in the list, to free the TV's small RAM. Nothing runs in the background by design.
- Sound presets (Voice, Movie, Night, Normal) that write the TV's own sound settings. The original values are kept in a restore file.
- Layouts, chosen with the Layout control at the bottom of the page. The choice is kept on the phone (localStorage), the server stores nothing about it.
  - Touch (default): the page described above.
  - Future: neon look, and the volume buttons become a bar with a live number. Drag up or down anywhere on it: 3 px per step, relative to where the finger landed, so a touch alone never jumps the volume. The page reads the TV's level when the layout opens, then sets the level directly (`cmd media_session volume`, stream 3) with the newest value at most every 60 ms, so the TV follows the finger smoothly.
  - 90s: a classic button remote. Number pad, a D-pad cross with OK instead of the touchpad, raised rubber-style keys.
  - Simple: only a large D-pad with OK, Back, Home, volume, mute and power. Everything else is hidden. Chosen from a short review of remote usability sources: few large keys and a physical-style D-pad are what people get on with best.
- Owner view: the same remote plus a live status panel (TV reachable, apps in memory with their size, free RAM, adb shell state, uptime, taps, send time, wrong-secret attempts, lockouts, traffic watch, live records of suspicious events). Counters live in memory only.

## Files

| File | Purpose |
| --- | --- |
| `remote_fast.py` | The whole server and page. Standard library only. |
| `test_remote_fast.py` | Assert checks, run on a temporary copy with its own secrets. |
| `setup.py` | Guided setup: checks the files, finds adb, asks for the TV, creates the certificate and secrets, runs the checks. |
| `make_package.py` | Builds the one-file download with its SHA-256 (for maintainers). |
| `LICENSE` | GNU Affero General Public License v3. |
| `SHA256SUMS` | Only in the download: the hash of every file inside, checked by `setup.py`. |
| `tv.txt` | TV address and adb port, for example `192.168.1.50:5555`. Required. |
| `adb.txt` | Path to `adb` (optional, defaults to `adb` on PATH). |
| `cert.pem`, `key.pem` | TLS certificate and key. Required: no certificate, no start. |
| `token.txt` | Secret for the plain remote URL. `setup.py` writes five random words; if the server has to create it itself (no setup) it uses a random URL-safe string instead. |
| `admin.txt` | Second secret for the owner URL, same rule as `token.txt`. |
| `device.txt` | SHA-256 hashes of paired cookies, one per line. Created on pairing. |

The secret files and the key are never committed or published. The code holds no personal data.

## Download and verify

The release is one zip with its SHA-256. Before you open it, compare the hash of what you downloaded with the published one:

- Windows PowerShell: `Get-FileHash .\tv-remote-<version>.zip -Algorithm SHA256`
- Linux and macOS: `sha256sum tv-remote-<version>.zip`

They must be identical. Inside the zip, `SHA256SUMS` lists the hash of every file and `setup.py` checks them again before it configures anything; it stops on the first mismatch. The build is reproducible: the same files always give the same zip and the same hash.

## Setup

You need Python 3, Android platform-tools (`adb`, from developer.android.com/tools/releases/platform-tools) and OpenSSL (it ships with Git for Windows and with most Linux and macOS systems). Enable network debugging on the TV. Tested on Windows 11 only.

Guided (recommended): unzip, open a terminal in the folder, run `python setup.py`. It
1. verifies the files against `SHA256SUMS`;
2. finds adb or asks for its path;
3. asks for the TV address (home-network addresses only), writes `tv.txt` and tries `adb connect`;
4. creates the TLS certificate for this computer's LAN address (valid one year);
5. creates the two secrets (five random words each);
6. runs the self-check.

It never overwrites an existing file, can be run again safely and changes no system setting. It only prints the Windows commands for the firewall rule and for start at logon; you run them yourself. Then run `python remote_fast.py` (or `pythonw` for no window), open the **owner URL** printed in the console once on the phone to pair it, and bookmark it.

By hand: put the TV address in `tv.txt` (and the adb path in `adb.txt` if adb is not on PATH), create `cert.pem` and `key.pem` for the computer's LAN address with OpenSSL, then run `python remote_fast.py`. The secrets are created on first start and kept in `token.txt` and `admin.txt`.

## Security model

- Closed by default. It needs `tv.txt`, a certificate and a paired device before the plain URL answers. Without a certificate it refuses to start and never falls back to plain HTTP (TLS 1.2 minimum).
- Binds to the laptop LAN address only, serves private-range clients only.
- Two secrets in the URL path, compared in constant time. Five wrong secrets lock that client address for five minutes. The owner secret always gets through.
- Pairing: the owner URL gives the device a random 256-bit cookie (`Secure`, `HttpOnly`, `SameSite=Strict`, 90 days). The server stores only its SHA-256 hash (newest ten devices) and compares in constant time. Encrypting the cookie would add nothing: it is opaque random data and already encrypted by TLS.
- Commands to the TV come only from fixed tables (keys, apps, sound presets). User input is only a lookup key, never part of a command. The one exception is the volume level: digits only, 0 to 100, converted to a number and printed again by the server before it reaches the TV. POST only; no CSRF risk because the secret is in the URL and not in a cookie.
- Strict response headers (no-store, CSP with `default-src 'none'`, nosniff, no referrer, no framing, no camera/microphone/location). Requests and secrets are never logged. Nothing is recorded about what is watched.
- Traffic watch: every 15 seconds the server checks its own and adb's connections. Anything to the internet or to a host other than the TV is recorded and turns the panel to ALERT. Inbound from non-LAN addresses is recorded too. Limits: Windows only (it reads `tasklist` and `netstat`), TCP only, sampled every 15 seconds, so a very short connection can be missed. On other systems the panel says "not available on this system" and does not start the watch.
- Exclusive port bind: a second server cannot share the port.
- Known limits: the volume bar shows the level read when the Future layout opens or the page comes back to the front, so a change made meanwhile with another remote shows only then; the 90s D-pad sends one step per press (no hold to repeat); no cap on simultaneous LAN connections; self-signed certificate (the browser warns, trust on first use); a paired phone that is unlocked can use the remote; the first key after the TV was off can be lost; the certificate is tied to the laptop's LAN address and has a one-year life.

## Running on Windows

- Autostart: a scheduled task "TV Remote" runs `pythonw.exe remote_fast.py` at logon (restart on failure, no admin rights). Remove it with `Unregister-ScheduledTask -TaskName 'TV Remote' -Confirm:$false`.
- Firewall: with inbound blocked by default, one allow rule is needed: TCP, the remote port, local subnet, the Python program, Private profile (`New-NetFirewallRule`, run once in an administrator PowerShell). Remove it with `Remove-NetFirewallRule`.
- Start waits for the network, so a logon before Wi-Fi is up does not stop it.

## Unpair a device

Delete `device.txt` and restart. Open the owner URL on the devices you want to keep.

## License

GNU Affero General Public License v3.0 or later (see `LICENSE`). You may use and change the code, but if you distribute it, or let others use a modified version over a network, you must publish your version under the same license.

## Checked claims

Every promise above, and the check behind it (`python test_remote_fast.py`). "Not tested" is stated plainly.

| Claim | Checked by | State |
| --- | --- | --- |
| Closed by default, the plain URL needs a paired device | closed-by-default and right-secret-without-cookie checks | tested |
| Pairing cookie: only its hash is stored, a forged cookie is refused | stored-hash and forged-cookie checks | tested |
| Cookie is `Secure`, `HttpOnly`, `SameSite=Strict`, 90 days | cookie flags check | tested |
| Commands only from fixed tables, volume digits only | allowlist, `snd/Evil`, digits-only checks | tested |
| Status panel for the owner only | status panel check | tested |
| Private-range clients only | non-LAN client check | tested |
| Five wrong secrets lock the address, the owner still gets in | lockout checks | tested |
| Requests and secrets are never logged | log check | tested |
| No way to call out except adb to the TV (code) | import list, no dynamic code, only adb/tasklist/netstat started, only the TV address dialled, tripwire at run time | tested, see limits below |
| The page cannot load from or send to anywhere else; strict headers | page checks and exact CSP check | tested |
| TLS mandatory, never plain HTTP, TLS 1.2 minimum | none | not tested |
| Binds to the LAN address only, exclusive port | none | not tested |
| Secrets compared in constant time | none (read in the code: `hmac.compare_digest`) | not tested |
| Traffic watch reports outbound connections | none | not tested |

What the "stays local" checks show: this version of the file holds no way to call out except adb to the TV, and any change that adds one turns the test red until a person reviews it. What they do not show: a tripwire is not a defence against an author who hides code on purpose, and it says nothing about what adb (a Google program), Windows or the TV do on their own. `script-src 'unsafe-inline'` stays because the page is one self-contained file; the page is safe from injected script because nothing a visitor types is ever written into it.

## Tests

```
python test_remote_fast.py
```

Covers closed-by-default, pairing, forged cookie, allowlisted keys only, number pad, volume level (range, digits only, paired device only), owner-only status, non-LAN clients, lockout and owner bypass, ASCII-only page.
