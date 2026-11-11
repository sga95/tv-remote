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
- Owner view: the same remote plus a live status panel (TV reachable, apps in memory with their size, free RAM, adb shell state, uptime, taps, send time, wrong-secret attempts, lockouts, traffic watch, live records of suspicious events). Counters live in memory only.

## Files

| File | Purpose |
| --- | --- |
| `remote_fast.py` | The whole server and page. Standard library only. |
| `test_remote_fast.py` | 21 assert checks, run on a temporary copy with its own secrets. |
| `tv.txt` | TV address and adb port, for example `192.168.1.50:5555`. Required. |
| `adb.txt` | Path to `adb` (optional, defaults to `adb` on PATH). |
| `cert.pem`, `key.pem` | TLS certificate and key. Required: no certificate, no start. |
| `token.txt` | Secret for the plain remote URL (5 random words). Created if missing. |
| `admin.txt` | Second secret for the owner URL (5 random words). Created if missing. |
| `device.txt` | SHA-256 hashes of paired cookies, one per line. Created on pairing. |

The secret files and the key are never committed or published. The code holds no personal data.

## Setup

1. Install Python 3 and Android platform-tools (`adb`). Enable network debugging on the TV and connect once.
2. Put the TV address in `tv.txt` (and the adb path in `adb.txt` if adb is not on PATH).
3. Create a certificate for the laptop's LAN address, for example with OpenSSL, and save it as `cert.pem` and `key.pem` next to the script.
4. Run `python remote_fast.py` (or `pythonw` for no window). The console prints the two URLs. Secrets are also in `token.txt` and `admin.txt`.
5. On the phone open the **owner URL** once. This pairs the device (see Pairing). Then bookmark it, or use the plain URL.

## Security model

- Closed by default. It needs `tv.txt`, a certificate and a paired device before the plain URL answers. Without a certificate it refuses to start and never falls back to plain HTTP (TLS 1.2 minimum).
- Binds to the laptop LAN address only, serves private-range clients only.
- Two secrets in the URL path, compared in constant time. Five wrong secrets lock that client address for five minutes. The owner secret always gets through.
- Pairing: the owner URL gives the device a random 256-bit cookie (`Secure`, `HttpOnly`, `SameSite=Strict`, 90 days). The server stores only its SHA-256 hash (newest ten devices) and compares in constant time. Encrypting the cookie would add nothing: it is opaque random data and already encrypted by TLS.
- Commands to the TV come only from fixed tables (keys, apps, sound presets). User input is only a lookup key, never part of a command. The one exception is the volume level: digits only, 0 to 100, converted to a number and printed again by the server before it reaches the TV. POST only; no CSRF risk because the secret is in the URL and not in a cookie.
- Strict response headers (no-store, CSP with `default-src 'none'`, nosniff, no referrer, no framing, no camera/microphone/location). Requests and secrets are never logged. Nothing is recorded about what is watched.
- Traffic watch: every 15 seconds the server checks its own and adb's connections. Anything to the internet or to a host other than the TV is recorded and turns the panel to ALERT. Inbound from non-LAN addresses is recorded too.
- Exclusive port bind: a second server cannot share the port.
- Known limits: the volume bar shows the level read when the Future layout opens or the page comes back to the front, so a change made meanwhile with another remote shows only then; the 90s D-pad sends one step per press (no hold to repeat); no cap on simultaneous LAN connections; self-signed certificate (the browser warns, trust on first use); a paired phone that is unlocked can use the remote; the first key after the TV was off can be lost; the certificate is tied to the laptop's LAN address and has a one-year life.

## Running on Windows

- Autostart: a scheduled task "TV Remote" runs `pythonw.exe remote_fast.py` at logon (restart on failure, no admin rights). Remove it with `Unregister-ScheduledTask -TaskName 'TV Remote' -Confirm:$false`.
- Firewall: with inbound blocked by default, one allow rule is needed: TCP, the remote port, local subnet, the Python program, Private profile (`New-NetFirewallRule`, run once in an administrator PowerShell). Remove it with `Remove-NetFirewallRule`.
- Start waits for the network, so a logon before Wi-Fi is up does not stop it.

## Unpair a device

Delete `device.txt` and restart. Open the owner URL on the devices you want to keep.

## Tests

```
python test_remote_fast.py
```

Covers closed-by-default, pairing, forged cookie, allowlisted keys only, number pad, volume level (range, digits only, paired device only), owner-only status, non-LAN clients, lockout and owner bypass, ASCII-only page.
