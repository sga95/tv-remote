# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the one-file download. Run: python make_package.py <version>   (for example 1.0.0)
Output in dist/: tv-remote-<version>.zip and tv-remote-<version>.zip.sha256.
The zip holds the program, the tests, the guided setup, the docs and SHA256SUMS (what setup.py checks).
It is reproducible: same files in, same zip and same SHA-256 out. Secrets, certificates and settings are never included."""
import hashlib, os, sys, zipfile

D = os.path.dirname(os.path.abspath(__file__))  # the folder this script is in
# The only files that go into the download. Anything else in the folder (secrets, certificate, settings) cannot get in.
FILES = ["LICENSE", "SECURITY.md", "README.md", "CHANGELOG.md", "docs/layouts.jpg", "remote_fast.py", "setup.py", "test_remote_fast.py"]
if len(sys.argv) != 2:
    sys.exit("usage: python make_package.py <version>")
version = sys.argv[1]
root = f"tv-remote-{version}"
# Strings that must never ship: user paths always, plus whatever the author lists in forbidden.txt (kept local, never committed).
FORBIDDEN = [b"C:" + b"\\Users\\"]
if os.path.exists(os.path.join(D, "forbidden.txt")):
    FORBIDDEN += [l.strip().encode() for l in open(os.path.join(D, "forbidden.txt")) if l.strip()]
blobs = {}  # file name -> its bytes, exactly as they will be zipped
for f in FILES:
    data = open(os.path.join(D, f), "rb").read()
    text = not f.endswith(".jpg")  # the picture is binary: no line-ending change, no text scan
    if text:
        data = data.replace(b"\r\n", b"\n")  # same bytes on every machine
        for bad in FORBIDDEN:
            if bad in data:
                sys.exit(f"STOP: {f} contains {bad!r}, remove it before publishing")
    blobs[f] = data
# SHA256SUMS lists the hash of every file inside; setup.py checks it before configuring anything.
sums = "".join(f"{hashlib.sha256(b).hexdigest()} *{n}\n" for n, b in sorted(blobs.items())).encode()
blobs["SHA256SUMS"] = sums
os.makedirs(os.path.join(D, "dist"), exist_ok=True)
out = os.path.join(D, "dist", root + ".zip")
# Reproducible zip: files in name order, one fixed date and fixed permissions, so the same input always gives the same hash.
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(blobs):
        i = zipfile.ZipInfo(f"{root}/{name}", (2026, 1, 1, 0, 0, 0))
        i.external_attr = 0o644 << 16
        z.writestr(i, blobs[name], compress_type=zipfile.ZIP_DEFLATED)
# The hash of the zip itself, published next to it so a download can be verified before it is opened.
digest = hashlib.sha256(open(out, "rb").read()).hexdigest()
open(out + ".sha256", "w").write(f"{digest} *{root}.zip\n")
print(f"{root}.zip  {os.path.getsize(out)} bytes\nSHA-256 {digest}")
