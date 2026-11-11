# Changelog

## 2026-11-11 (late)
- Touchpad: hold the finger pushed in one direction to keep scrolling, faster the further you push; axis lock; small steps back no longer trigger the opposite key (a step back needs a clearly larger move); swipe step 28 px; tap is easier to land (tolerance 16 px, 600 ms).

## 2026-11-11
- Look redesigned: hero touchpad with edge chevrons and a centre ring that light up on swipe and tap, app row with colour underline, Back/Home/Menu row, volume and channel pills, segmented Sound control, SVG icons, owner status panel styled to match.
- Touchpad is self-explanatory without text: a dot grid slides with the finger, and a "ghost finger" shows a horizontal and a vertical swipe until the first touch (off with reduced motion).
- Secure by default: settings moved out of the code into `tv.txt` and `adb.txt`; refuses to start without a certificate (no plain HTTP fallback); the plain URL is closed until a device is paired; stricter CSP plus Permissions-Policy and CORP; lockout table bounded.
- Pairing cookie: 256-bit random, only the SHA-256 hash stored (up to 10 devices), 90 days.
- Stability: exclusive port bind (a second server can no longer share the port; an old process once answered with the old page); waits for the network at logon; adb reconnects before respawning the shell; a POST with a body closes the connection.
- Added `test_remote_fast.py` (16 checks) and this documentation.

## 2026-11-10
- Faster: one persistent `adb shell` instead of one adb process per key, HTTP keep-alive with threaded server, page sends on pointer down without waiting.
- Security: lockout after 5 wrong secrets, owner secret and status panel, pairing by cookie, live traffic watch and records, words instead of random letters in the secrets, TLS.
- Opening an app closes the other apps. Sound presets (Voice, Movie, Night, Normal) with a restore file for the original values.
- Autostart at logon through a scheduled task; firewall allow rule for the remote port (local subnet, Python program only).
- First version: allowlisted keys and apps, ASCII-only icons (numeric HTML entities) so the page cannot garble, strict headers, no request logging.
