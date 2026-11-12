# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rebuild cve_data.json from the official Android Security Bulletins. For maintainers. Run: python make_cve_data.py

This is the ONLY file in the project that goes on the internet, and it is never part of the running remote:
the remote itself only reads the finished cve_data.json from its own folder. It is run by hand before a release,
on the maintainer's computer, and it sends nothing but plain requests for public pages.

What it does: for every month since START it downloads that month's bulletin from source.android.com, finds the
tables whose last column is "Updated AOSP versions" (the fixes in Android itself; chip-maker and kernel tables are
left out because they depend on the hardware) and keeps, for each fix rated Critical or High: its CVE number, the
part of Android it is in, the kind of flaw, the rating and the Android versions it applies to.
Nothing is typed in by hand, so a wrong number can only come from the bulletin itself.
"""
import datetime, json, os, re, sys, time, urllib.error, urllib.request

START = (2023, 1)  # first bulletin to keep: old enough for any TV still in use that this remote supports
SOURCE = "source.android.com/docs/security/bulletin"
D = os.path.dirname(os.path.abspath(__file__))


def text(html):
    """The visible text of a piece of HTML, on one line."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def fixes(html):
    """Every Critical or High fix of one bulletin page, as [CVE, part of Android, kind, rating, versions]."""
    found, part = [], ""
    # Walk the page in order: a heading names the part of Android, the tables after it list that part's fixes.
    for heading, table in re.findall(r'<h3[^>]*id="([^"]+)"|(<table.*?</table>)', html, re.S):
        if heading:
            part = heading.replace("-", " ").lower()
            continue
        rows = [[text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)] for row in re.findall(r"<tr>(.*?)</tr>", table, re.S)]
        if not rows or rows[0][-1:] != ["Updated AOSP versions"]:
            continue  # not a table of fixes in Android itself
        for row in rows[1:]:
            if len(row) == 5 and re.fullmatch(r"CVE-\d{4}-\d+", row[0]) and row[3] in ("Critical", "High"):
                found.append([row[0], part, row[2], row[3], row[4].replace(" ", "")])
    return found


def get(path):
    """One public page of source.android.com, as text. Any failure stops the run: a half-built list is never written."""
    request = urllib.request.Request("https://source.android.com" + path, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        sys.exit(f"STOP: {path} could not be read ({error}); nothing was written")


def main():
    today = datetime.date.today()
    # The addresses are not guessed (their pattern changed in 2026): they are taken from the official index page.
    index = get("/docs/security/bulletin/asb-overview")
    pages = {month: path for path, month in re.findall(r'href="(/docs/security/bulletin/(?:\d{4}/)?(\d{4}-\d{2})-01)"', index)}
    bulletins = {}
    for name in sorted(pages):
        if (int(name[:4]), int(name[5:])) >= START:
            bulletins[name] = fixes(get(pages[name]))
            print(name, len(bulletins[name]), "fixes")
            time.sleep(0.3)  # be gentle with the site
    if len(bulletins) < 12 or sum(len(rows) for rows in bulletins.values()) < 100:
        sys.exit("STOP: far too little found, the site layout has probably changed; nothing was written")
    data = {"source": "Android Security Bulletins, " + SOURCE, "made": today.isoformat(),
            "columns": ["CVE", "part of Android", "kind", "rating", "Android versions"], "bulletins": bulletins}
    with open(os.path.join(D, "cve_data.json"), "w", encoding="utf-8", newline="\n") as out:
        json.dump(data, out, indent=0, separators=(",", ":"))
        out.write("\n")
    print("cve_data.json written:", len(bulletins), "bulletins,", sum(len(rows) for rows in bulletins.values()), "fixes")


if __name__ == "__main__":
    main()
