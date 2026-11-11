"""Build the one-file download. Run: python make_package.py <version>   (for example 1.0.0)
Output in dist/: tv-remote-<version>.zip and tv-remote-<version>.zip.sha256.
The zip holds the program, the tests, the guided setup, the docs and SHA256SUMS (what setup.py checks).
It is reproducible: same files in, same zip and same SHA-256 out. Secrets, certificates and settings are never included."""
import hashlib, os, sys, zipfile

D = os.path.dirname(os.path.abspath(__file__))
FILES = ["README.md", "CHANGELOG.md", "remote_fast.py", "setup.py", "test_remote_fast.py"]
if len(sys.argv) != 2:
    sys.exit("usage: python make_package.py <version>")
version = sys.argv[1]
root = f"tv-remote-{version}"
blobs = {}
for f in FILES:
    data = open(os.path.join(D, f), "rb").read().replace(b"\r\n", b"\n")  # same bytes on every machine
    for bad in (b"C:" + b"\\Users\\", b"192.0.2.1", b"192.0.2.2", b"AUTHOR"):  # the author's own paths, addresses and name must never ship
        if bad in data:
            sys.exit(f"STOP: {f} contains {bad!r}, remove it before publishing")
    blobs[f] = data
sums = "".join(f"{hashlib.sha256(b).hexdigest()} *{n}\n" for n, b in sorted(blobs.items())).encode()
blobs["SHA256SUMS"] = sums
os.makedirs(os.path.join(D, "dist"), exist_ok=True)
out = os.path.join(D, "dist", root + ".zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(blobs):
        i = zipfile.ZipInfo(f"{root}/{name}", (2026, 1, 1, 0, 0, 0))
        i.external_attr = 0o644 << 16
        z.writestr(i, blobs[name], compress_type=zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(open(out, "rb").read()).hexdigest()
open(out + ".sha256", "w").write(f"{digest} *{root}.zip\n")
print(f"{root}.zip  {os.path.getsize(out)} bytes\nSHA-256 {digest}")
