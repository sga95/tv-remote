# Changelog

## 2026-11-11 (package and license)
- Licensed under GNU AGPL-3.0-or-later (`LICENSE`, SPDX line in the code files).
- Guided setup `setup.py`: checks `SHA256SUMS`, finds adb, asks for the TV address, creates the certificate and secrets, runs the self-check; never overwrites, changes no system setting, prints the firewall and logon commands for you to run.
- `make_package.py` builds the one-file download (`tv-remote-<version>.zip` plus its SHA-256): reproducible, includes `SHA256SUMS`, refuses to ship the author's paths, addresses or name.
- README: download-and-verify section, guided setup, license.

## 2026-11-11 (night)
- Layout control at the bottom of the page: Touch (default, unchanged), Future, 90s and Simple. The choice is kept on the phone only.
- Future layout: neon look and a volume bar with a live number. Relative drag (3 px per step), level read from the TV and set directly, newest value at most every 60 ms.
- 90s layout: number pad (keys 0 to 9 added to the allowlist), D-pad cross with OK, rubber-style keys.
- Simple layout: large D-pad with OK, Back, Home, volume, mute and power, nothing else.
- New endpoints, both behind the same secret and pairing checks: `GET vol` (level and range) and `POST vol/<0-100>` (digits only).
- Tests: 21 checks (number pad, volume range, digits only, paired device only).

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
