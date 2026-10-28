r"""Collect reel links from ONE Instagram Saved collection, add new ones to the ledger, write them to queue\links.txt.

Run by hand:  python collect_links.py "AI cooking"
Reads only. Uses the session cookies in C:\dev\reels\cookies.txt (SECRET, never printed).
Stops at once if Instagram shows a login or challenge page. No proxies, no tricks: one slow browser, human-ish pauses.
"""
import csv, re, sys, time, random
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(r"C:\dev\reels")
COOKIES = ROOT / "cookies.txt"
LEDGER = ROOT / "ledger.csv"
QUEUE = ROOT / "queue" / "links.txt"
INBOX = ROOT / "1-inbox"
USER = "yusha.aanan"
FIELDS = ["id", "link", "collection", "status", "added", "note"]   # status: queued / downloaded / analysed / actioned / failed


def load_cookies():
    out = []
    for line in COOKIES.read_text(encoding="utf-8", errors="ignore").splitlines():
        p = line.removeprefix("#HttpOnly_").split("\t")   # other '#' lines are comments and fail the 7-field check
        if len(p) == 7 and "instagram.com" in p[0]:
            out.append({"name": p[5], "value": p[6], "domain": p[0], "path": p[2], "secure": p[3] == "TRUE",
                        "expires": int(p[4]) if p[4].isdigit() and int(p[4]) > 0 else -1})
    return out


def read_ledger():
    if not LEDGER.exists():
        return {}
    with LEDGER.open(newline="", encoding="utf-8-sig") as f:   # tolerate a BOM if another tool re-saved the file
        return {r["id"]: r for r in csv.DictReader(f)}


def write_ledger(rows):
    with LEDGER.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows.values())


def reel_id(href):
    m = re.search(r"/(?:reel|p)/([A-Za-z0-9_-]+)", href)
    return m.group(1) if m else None


def pause(a=1.5, b=3.5):
    time.sleep(random.uniform(a, b))


def collect(collection):
    ids = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_cookies(load_cookies())
        page = ctx.new_page()
        page.goto(f"https://www.instagram.com/{USER}/saved/", wait_until="domcontentloaded")
        pause(3, 5)
        if any(w in page.url for w in ("login", "challenge", "suspended")):
            browser.close()
            sys.exit(f"STOP: Instagram wants a login/challenge ({page.url.split('?')[0]}). Not retrying.")
        card = page.locator(f'a[href*="/saved/"]:has-text("{collection}")').first
        if card.count() == 0:
            names = page.eval_on_selector_all('a[href*="/saved/"]', "els=>els.map(e=>e.innerText.trim()).filter(Boolean)")
            browser.close()
            sys.exit(f"STOP: no collection called '{collection}'. Found: {sorted(set(names))}")
        card.click()
        pause(3, 5)
        same = 0
        for _ in range(60):
            before = len(ids)
            for href in page.eval_on_selector_all('a[href*="/reel/"], a[href*="/p/"]', "els=>els.map(e=>e.getAttribute('href'))"):
                i = reel_id(href)
                if i:
                    ids[i] = "reel" if "/reel/" in href else "post"
            same = same + 1 if len(ids) == before else 0
            if same >= 3:
                break
            page.mouse.wheel(0, 4000)
            pause(2, 4)
        browser.close()
    return ids


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: python collect_links.py "<collection name>"')
    collection = sys.argv[1]
    ids = collect(collection)
    ledger = read_ledger()
    QUEUE.parent.mkdir(exist_ok=True)
    new = []
    for i, kind in ids.items():
        if i in ledger:
            if collection not in ledger[i]["collection"].split(" | "):
                ledger[i]["collection"] += f" | {collection}"   # same reel saved in several folders
            continue
        have = (INBOX / f"ig_{i}.mp4").exists()
        ledger[i] = {"id": i, "link": f"https://www.instagram.com/reel/{i}/", "collection": collection,
                     "status": "downloaded" if have else "queued", "added": time.strftime("%Y-%m-%d %H:%M"),
                     "note": "" if kind == "reel" else "not a reel (photo/carousel?), may fail"}
        if not have:
            new.append(ledger[i]["link"])
    write_ledger(ledger)
    with QUEUE.open("a", encoding="utf-8") as f:
        f.writelines(l + "\n" for l in new)
    if len(ids) <= 30:
        print("found:", ", ".join(f"{i}[{ledger[i]['status']}]" for i in ids))
    print(f"{collection}: {len(ids)} found, {len(new)} new queued, {len(ids) - len(new)} already known/downloaded")
    print(f"queue: {QUEUE}   ledger: {LEDGER}")


if __name__ == "__main__":
    main()
