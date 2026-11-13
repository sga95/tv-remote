# Changelog

## 2026-11-13 (the remote adapts to the TV)
- The page asks the TV what it is (maker, model, Android version) and what it has, shows "Connected to: ..." at the top of the settings and offers only that: app buttons only for installed apps, sound presets only where their four settings exist (they are specific to TPV/Philips TVs), microphone and camera blocks only from Android 12 on ("needs Android 12" otherwise). No list of brands is kept. If the TV does not answer, nothing is hidden.
- Volume: the level is also read with the older command form (`media volume`) when the newer one fails, and the safety limits of the volume bar scale with the TV's own range instead of assuming 0 to 100.
- README: new section "Which TVs": tested on Philips Android TV (Android 14); expected to work, not yet tested, on other Android TV and Google TV sets; not supported on Samsung (Tizen), LG (webOS), Roku and Hisense VIDAA, which have no adb.
- New read-only endpoint behind the same secret and pairing checks: `GET tv`. Tests with recorded answers of two TVs and an unreadable one. Checked on the tested TV: everything stays as it was.

## 2026-11-13 (guided procedure and troubleshooting)
- README: "Get it running" is now a step-by-step procedure for someone with no technical background: nine steps, each with what to do, what you should see and what to do if you do not.
- README: a new table "When something does not work" with the problems met in practice (phone cannot open the page, blank or 404, buttons do nothing, TV in standby, first key lost after a restart, changed address, second copy running, antivirus, lost links) and, for the Raspberry Pi, Wi-Fi WPA3 and saving the config on a diskless system.
- README: a documented setup for a computer on a cable behind a powerline adapter, in another room (new address, certificate and pairing, same network as phone and TV, Windows Public profile, isolation, weak link). Marked as reasoned, not tested on a real powerline link.
- `setup.py` talks plain words: when the TV does not answer or asks for permission it says exactly what to do and lets you retry or skip; if the saved certificate was made for another address it offers a new one; new `python setup.py --check` looks at everything that can go wrong (TV, adb, address, certificate, secrets, pairing, remote running) and says what to do, changing nothing.

## 2026-11-13 (Raspberry Pi)
- The remote now runs on a Raspberry Pi 5 with Alpine Linux, without any code change. README: a new section "Running on a Raspberry Pi" (install, adb key, service, firewall, saving the config on a diskless system, reserved address) and the list of what differs from Windows (no traffic watch, the SD card holds the secrets).

## 2026-11-12 (known fixes the TV does not have)
- New data file `cve_data.json`: 959 fixes rated Critical or High from 47 official Android Security Bulletins (January 2023 to November 2026), taken mechanically from the bulletin tables, with source and date.
- Optimize panel: counts the fixes published after the TV's patch month for its Android version (critical, high) and names the ones usable from a distance (RCE). Computed by the server from the shipped file; nothing is looked up online while the remote runs.
- New maintainer script `make_cve_data.py` rebuilds the file before a release; it is the only file that goes on the internet and is not part of the running remote or of the download.
- `make_package.py` ships `cve_data.json`.

## 2026-11-12 (states made explicit, part 2)
- Fix: the wrong-secret count of an address below 5 never expired, so five slips spread over weeks ended in a lockout. A wrong secret is now forgotten after 5 minutes; five in a row still lock the address for 5 minutes and the lockout still ends by itself. The three states (clean, counting, locked) are written down in the code.
- Optimize and Clean restart run one at a time: while one runs both buttons are off, so a second press cannot start a second clean-up, spoil the before-and-after numbers or overlap a restart.
- Known and left as it is: the first key pressed right after the TV restarted can be lost, because the long-lived connection to the TV is found dead only when it is used.

## 2026-11-12 (panels never stay stuck)
- Privacy, Security and Optimize panels: a request that fails on the way (phone off the Wi-Fi, the remote's computer asleep) is now shown as "The TV did not answer." Before, the panel stayed on "Asking the TV..." and the Optimize button on "Optimizing..." with no sign that anything had gone wrong.
- "Revoke all permissions" goes back to its own label when the TV does not answer after a revoke. Before, it stayed on "Revoking...".
- Page script only: no new command, no new endpoint, nothing changes on the server side or in what is sent to the TV. Checked by running the page script against a failing and an empty answer; not yet checked on a real phone and TV. Test added.

## 2026-11-12 (apps left open)
- Optimize (and so Clean restart) now also closes the streaming apps of the app list that are not on screen. Found on the tested TV: after a restart the TV started one streaming app by itself, and opening another app from the TV's own menu left it running in the background (about 160 MB). The app on screen is read from the TV and skipped; checked on the TV that it keeps its process.

## 2026-11-12 (clean restart)
- New "Clean restart" button in the Optimize panel: confirmation, picture of the privacy and security state, clean-up, restart, wait until the TV answers again, second picture, list of everything that is more exposed than before. Nothing is stored; the remote never turns the TV off by itself.
- Measured on the tested TV: back after 68 seconds with network debugging on, all settings kept, free memory from 476 to 703 MB.
- Read-only questions to the TV now reconnect once when the TV does not answer (after a restart or a Wi-Fi drop).
- New command behind the same checks: `POST tune/restart`. Tests added.

## 2026-11-12 (who holds the ports, what still updates)
- Security panel: every listening port now names who holds it open (the app, the system program, or the system), found from the socket's user id. An unknown port is no longer anonymous.
- Optimize panel: three read-only lines on the parts of Android the store still updates (WebView version, Google Play services date, Google Play system update date, with their age).

## 2026-11-12 (security panel)
- New Security panel in the settings: a read-only check (accessibility services, notification readers, installers, usage access, location, voice hotword) and the list of network ports the TV listens on, with names for the well-known ones.
- Cast switch: `pm disable-user` and `pm enable` plus a service start for Google's Cast receiver. Measured on the tested TV: off closes the Cast ports, on restores them; without the service start they only came back after a restart.
- Ad personalisation switch: sets or removes `limit_ad_tracking`. A request to apps, not a block.
- New endpoints behind the same secret and pairing checks: `GET security`, `POST sec/cast/on|off`, `POST sec/ads/limit|free`. Tests added.

## 2026-11-12 (optimize panel)
- New Optimize TV panel in the settings: free memory, free storage, time since the TV started, Android version and the age of the last security patch, read from the TV when the panel scrolls into view.
- Button "Optimize now": one fixed command (`am kill-all; pm trim-caches 999G`), then the TV is read again and the change is shown next to each number. Measured on the tested TV: memory unchanged, 146 MB of storage freed.
- Security patch age counted with the phone's clock, warning past six months. No CVE list is shipped and nothing is looked up online.
- README: new section "When updates stop" with the steps that reduce the risk on a TV that gets no more updates, and what stays exposed.
- New endpoints behind the same secret and pairing checks: `GET health`, `POST tune/run`. Tests added.
- Privacy panel: a control with nothing to act on is greyed out and says why ("Camera: not found", "No permissions to revoke"); a block that is on stays clickable.

## 2026-11-12 (privacy panel)
- New Privacy panel in the settings below the remote. It asks the TV (one read-only adb call) how many cameras it sees, which microphone inputs it has and which apps may record sound or use a camera, and shows the answer; nothing is stored.
- Two switches, Microphone and Camera, turn Android's system-wide blocks on and off (`cmd sensor_privacy enable|disable 0 microphone|camera`, four fixed commands). The state shown always comes from the TV, read again after each press.
- Measured on the tested TV (Android 14): no camera, one built-in microphone input, both blocks accepted, stored, reported and reversible. Not measured: a recording to prove silence, and a TV restart.
- The panel says Camera and Microphone FOUND or not found, and lists the apps that really hold the permission (`granted=true` in the TV's app list). The first version read the appops list, which still named apps after their permission was gone.
- New button "Revoke all permissions": one fixed command on the TV that revokes the microphone and camera permission from every app holding it. Run on the tested TV and read back: the holder list went from one app to none. Android closes an app that loses a permission.
- The panel is read when it scrolls into view (about 2 seconds of work for the TV), not on every page load.
- New endpoints behind the same secret and pairing checks: `GET privacy`, `POST priv/mic|cam/block|allow`, `POST priv/apps/revoke`. Tests added (fixed table, parsing of a recorded answer, unreadable answer, paired device only).

## 2026-11-12 (designed README)
- README redesigned for GitHub, every fact kept: opening with a picture of the four layouts (`docs/layouts.jpg`, made from the real page with a placeholder secret), three-point summary, two Mermaid diagrams (data path, request gate), layout table, collapsible details, numbered setup. Nothing is loaded from a third-party host. Stale numbers corrected (1 to 5 volume steps, about 8 steps per second).

## 2026-11-12 (scroll, faster and safer volume, readable code)
- Fix: the page stuttered and jammed while scrolling on a phone. The fit-to-screen code re-measured the remote every time a browser bar slid in or out, which happens during every scroll. It now sizes the remote once to the small viewport (`100svh`, bars showing) and again only on layout change or rotation; the first screen is as tall as the large viewport (`100lvh`), so the settings still start below it.
- New strip with an arrow under the remote: a safe place to start a scroll (keys act on touch), and a tap jumps to the settings.
- Volume bar faster: up to 5 key presses per request, sent side by side. Measured on the TV: 3 presses in 0.5 s, 6 in 0.75 s, about 8 steps per second instead of 4. Nothing faster exists on this TV: `--set`, `--adj` and `--show --set` are ignored.
- Volume bar safety (`VOL_LIMITS`): maximum 50 by day and 30 at night (22:00 to 07:00), at most 10 steps up per drag, lowering never limited, amber number at the limit. Night comes from the phone's own clock in the page; no service is asked and nothing is sent or stored.
- `setup.py` and `make_package.py`: explanatory comments and docstrings added, no logic change.
- `remote_fast.py` rewritten for reading, same behaviour: sections in reading order, a file map and the safety rules at the top, detailed comments, named helpers, readable CSS and JS.

## 2026-11-12
- Fix: the Future volume bar moved its number but not the TV. Measured on the TV: `cmd media_session volume --set` and `--adj` are accepted and ignored, only the volume key changes the level (one step per key, about 0.2 s each). The bar now sends 1 to 3 key presses per request, the next one only when those are done and towards the newest number, then reads the real level back. `POST vol/<0-100>` is replaced by `POST vol/up/<1-3>` and `POST vol/down/<1-3>`, a fixed table.
- Future layout: the channel up and down buttons are replaced by a number pad.
- The first screen is the remote only: the Layout control and the owner status panel moved below it, visible after a scroll.
- Fit to screen now measures the really visible area (`visualViewport`, dynamic viewport height, safe-area inset at the top), so browser bars at the top or bottom, shown or hidden, no longer cover the remote.

## 2026-11-11 (fit to screen)
- Every layout now scales as one piece to the screen (0.7 to 1.6 times, from the free height and width) and is centred; it rescales on rotation and resize. Checked at 375x812, 430x932 and 768x1024 (no scrolling in any layout); 320x568 and landscape phones stop at 0.7 and scroll.

## 2026-11-11 (proofs and fixes)
- Test: the "stays local" proof is in `test_remote_fast.py`: a tripwire refuses every connection and program start while it runs, the code may import only the standard library modules in use, start only adb, tasklist and netstat, dial only the TV address, and the page may not reference anything outside itself. Also checks the cookie flags and that nothing is logged. Verified to turn red on deliberate breakages (extra import, curl instead of adb, loose CSP, cookie without Secure, request logging).
- Fixes: `NOWIN` console flag only on Windows (adb calls no longer fail elsewhere); the status check probes the port from `tv.txt` instead of 5555; the traffic watch starts only on Windows and the panel says "not available on this system" elsewhere instead of "clean".
- README: secret wording matches the code, traffic watch limits, a table of every claim with its check and an honest "not tested" where there is none.

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
