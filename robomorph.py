#!/usr/bin/env python3
"""RoboMorph auto-Reels pipeline.

Video sources, tried in order (each free):
  1. inbox/      -> videos you drop in yourself (e.g. downloaded from Google Flow / Dreamina)
  2. Pexels API  -> free stock video (needs free PEXELS_API_KEY)
  3. Pixabay API -> free stock video (needs free PIXABAY_API_KEY)
  4. Wikimedia Commons + NASA images -> slow-zoom slideshow (no key needed)
If one source fails or runs out of quota, the next one is used automatically.
A buffer of ready-made videos is kept so a scheduled post never goes empty.

Commands:
  python3 robomorph.py produce   # fill the buffer up to BUFFER_SIZE
  python3 robomorph.py post      # post the oldest ready video (makes one first if buffer empty)
  python3 robomorph.py status    # show buffer and last posts
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
READY = BASE / "ready"
INBOX = BASE / "inbox"
POSTED = BASE / "posted"
LOGS = BASE / "logs"
STATE_FILE = BASE / "state.json"
TOPICS_FILE = BASE / "topics.json"

W, H, FPS = 1080, 1920, 30
UA = "RoboMorphBot/1.0 (Facebook page content pipeline)"
HUMAN_WORDS = re.compile(
    r"\b(person|people|man|men|woman|women|girl|boy|child|children|kid|kids|hand|hands|"
    r"finger|fingers|face|portrait|farmer|gardener|human|couple|family|selfie|model|"
    r"worker|lady|guy|baby|tourist|hiker|crowd)\b",
    re.I,
)


# ---------------------------------------------------------------- helpers
def load_env():
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cfg(key, default=""):
    return os.environ.get(key, default)


def log(msg):
    LOGS.mkdir(exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOGS / "robomorph.log", "a") as f:
        f.write(line + "\n")


def notify(msg):
    """Optional Telegram notification (works with the same bot Hermes uses, or a new one)."""
    tok, chat = cfg("TELEGRAM_BOT_TOKEN"), cfg("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": f"RoboMorph: {msg}"}, timeout=20)
    except Exception as e:  # noqa: BLE001
        log(f"telegram notify failed: {e}")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"next_topic": 0, "used_media": [], "history": []}


def save_state(st):
    st["used_media"] = st["used_media"][-3000:]
    st["history"] = st["history"][-300:]
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    tmp.replace(STATE_FILE)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:6])}...\n{r.stderr[-1500:]}")
    return r.stdout


def download(url, dest, headers=None):
    with requests.get(url, stream=True, timeout=120, headers=headers or {"User-Agent": UA}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def probe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip() or 0)


class QuotaOrEmpty(Exception):
    """Source has no results or its free quota is exhausted -> try the next source."""


# ---------------------------------------------------------------- sources
def pexels_clip(query, used, tmp):
    key = cfg("PEXELS_API_KEY")
    if not key:
        raise QuotaOrEmpty("no PEXELS_API_KEY")
    r = requests.get("https://api.pexels.com/videos/search",
                     params={"query": query, "per_page": 40, "orientation": "portrait"},
                     headers={"Authorization": key, "User-Agent": UA}, timeout=30)
    if r.status_code in (401, 403, 429):
        raise QuotaOrEmpty(f"pexels {r.status_code}")
    r.raise_for_status()
    vids = r.json().get("videos", [])
    random.shuffle(vids)
    for v in vids:
        mid = f"pexels:{v['id']}"
        slug = v.get("url", "")
        if mid in used or HUMAN_WORDS.search(slug.replace("-", " ")) or v.get("duration", 0) < 5:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                 and (f.get("height") or 0) >= 720]
        if not files:
            continue
        best = min(files, key=lambda f: abs((f.get("height") or 0) - 1920))
        dest = Path(tmp) / f"pexels_{v['id']}.mp4"
        download(best["link"], dest)
        return {"path": dest, "media_id": mid, "kind": "video",
                "credit": f"Video: {v.get('user', {}).get('name', 'Pexels')} / Pexels"}
    raise QuotaOrEmpty("pexels: nothing suitable")


def pixabay_clip(query, used, tmp):
    key = cfg("PIXABAY_API_KEY")
    if not key:
        raise QuotaOrEmpty("no PIXABAY_API_KEY")
    r = requests.get("https://pixabay.com/api/videos/",
                     params={"key": key, "q": query, "per_page": 50, "safesearch": "true"},
                     headers={"User-Agent": UA}, timeout=30)
    if r.status_code in (401, 403, 429):
        raise QuotaOrEmpty(f"pixabay {r.status_code}")
    r.raise_for_status()
    hits = r.json().get("hits", [])
    random.shuffle(hits)
    for h in hits:
        mid = f"pixabay:{h['id']}"
        if mid in used or HUMAN_WORDS.search(h.get("tags", "")) or h.get("duration", 0) < 5:
            continue
        vids = h.get("videos", {})
        f = vids.get("large") or vids.get("medium")
        if not f or not f.get("url"):
            continue
        dest = Path(tmp) / f"pixabay_{h['id']}.mp4"
        download(f["url"], dest)
        return {"path": dest, "media_id": mid, "kind": "video",
                "credit": f"Video: {h.get('user', 'Pixabay')} / Pixabay"}
    raise QuotaOrEmpty("pixabay: nothing suitable")


def commons_image(query, used, tmp):
    r = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": 30, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": 1600}, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    pages = list(r.json().get("query", {}).get("pages", {}).values())
    random.shuffle(pages)
    for p in pages:
        mid = f"commons:{p.get('pageid')}"
        title = p.get("title", "")
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        desc = re.sub(r"<[^>]+>", " ", meta.get("ImageDescription", {}).get("value", ""))
        if mid in used or HUMAN_WORDS.search(title + " " + desc):
            continue
        if (info.get("width") or 0) < 1000:
            continue
        url = info.get("thumburl") or info.get("url")
        ext = Path(url.split("?")[0]).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        dest = Path(tmp) / f"commons_{p['pageid']}{ext}"
        download(url, dest)
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:60]
        lic = meta.get("LicenseShortName", {}).get("value", "")
        return {"path": dest, "media_id": mid, "kind": "image",
                "credit": f"Foto: {artist or 'Wikimedia Commons'} ({lic}), Wikimedia Commons"}
    raise QuotaOrEmpty("commons: nothing suitable")


def nasa_image(query, used, tmp):
    r = requests.get("https://images-api.nasa.gov/search",
                     params={"q": query, "media_type": "image"}, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    items = r.json().get("collection", {}).get("items", [])
    random.shuffle(items)
    for it in items[:40]:
        data = (it.get("data") or [{}])[0]
        mid = f"nasa:{data.get('nasa_id')}"
        text = data.get("title", "") + " " + data.get("description", "")[:300]
        if mid in used or HUMAN_WORDS.search(text) or re.search(r"astronaut|crew", text, re.I):
            continue
        links = it.get("links") or []
        if not links:
            continue
        url = links[0]["href"].replace("~thumb", "~large")
        dest = Path(tmp) / f"nasa_{abs(hash(mid))}.jpg"
        try:
            download(url, dest)
        except Exception:  # noqa: BLE001
            continue
        return {"path": dest, "media_id": mid, "kind": "image", "credit": "Foto: NASA"}
    raise QuotaOrEmpty("nasa: nothing suitable")


def get_media(topic, idx, used, tmp):
    """Try every free source in order until one gives a clip for this segment."""
    vq = topic["queries"][idx % len(topic["queries"])]
    iq = topic["image_queries"][idx % len(topic["image_queries"])]
    attempts = [(pexels_clip, vq), (pixabay_clip, vq), (pexels_clip, topic["queries"][0]),
                (pixabay_clip, topic["queries"][0]), (commons_image, iq),
                (commons_image, topic["image_queries"][0]), (nasa_image, vq)]
    errors = []
    for fn, q in attempts:
        try:
            m = fn(q, used, tmp)
            used.add(m["media_id"])
            log(f"  segment {idx + 1}: {m['media_id']} via {fn.__name__} ('{q}')")
            return m
        except QuotaOrEmpty as e:
            errors.append(str(e))
        except Exception as e:  # noqa: BLE001  network errors etc.
            errors.append(f"{fn.__name__}: {e}")
    raise RuntimeError("all sources failed: " + " | ".join(errors))


# ---------------------------------------------------------------- render
def font_path(bold=True):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(p).exists():
            return p
    raise RuntimeError("font not found: apt install fonts-dejavu-core")


def drawtext(textfile, y, size, box=True):
    f = font_path()
    opts = (f"drawtext=fontfile={f}:textfile={textfile}:fontcolor=white:fontsize={size}:"
            f"line_spacing=12:x=(w-text_w)/2:y={y}")
    if box:
        opts += ":box=1:boxcolor=black@0.55:boxborderw=28"
    return opts


def render_segment(media, out, seconds, texts, tmp, n):
    vf_text = []
    for i, (txt, y, size) in enumerate(texts):
        tf = Path(tmp) / f"t_{n}_{i}.txt"
        tf.write_text(txt)
        vf_text.append(drawtext(tf, y, size))
    frames = int(seconds * FPS)
    if media["kind"] == "video":
        dur = probe_duration(media["path"])
        start = max(0.0, (dur - seconds) / 2) if dur > seconds + 1 else 0
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}")
        if dur < seconds:  # slow it down to fill the slot
            vf = f"setpts={seconds / max(dur, 0.5):.3f}*PTS," + vf
        inp = ["-ss", f"{start:.2f}", "-i", str(media["path"])]
    else:
        zoom = random.choice(["in", "out"])
        z = "min(1.0+0.0007*on,1.18)" if zoom == "in" else "max(1.18-0.0007*on,1.0)"
        vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
              f"setsar=1")
        inp = ["-loop", "1", "-i", str(media["path"])]
    vf = ",".join([vf] + vf_text + ["format=yuv420p"])
    run(["ffmpeg", "-y", "-v", "error", *inp, "-t", f"{seconds}", "-vf", vf, "-an",
         "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", str(out)])


def wrap(txt, width):
    return "\n".join(textwrap.wrap(txt, width))


def build_video(topic, st, out_dir):
    seconds = float(cfg("VIDEO_SECONDS", "30"))
    nseg = len(topic["facts"])
    seg_len = seconds / nseg
    used = set(st["used_media"])
    credits = []
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, fact in enumerate(topic["facts"]):
            media = get_media(topic, i, used, tmp)
            credits.append(media["credit"])
            texts = [(wrap(fact, 26), "h-text_h-360", 54)]
            if i == 0:
                texts.append((wrap(topic["title"], 20), "220", 72))
            if i == nseg - 1:
                texts.append(("Ikuti RoboMorph\nuntuk sains alam setiap hari", "220", 50))
            part = Path(tmp) / f"seg{i}.mp4"
            render_segment(media, part, seg_len, texts, tmp, i)
            parts.append(part)
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        slug = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{topic['id']}"
        out = out_dir / f"{slug}.mp4"
        # silent stereo track: no music, but keeps players and Facebook happy
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)])
    st["used_media"] = list(used)
    uniq = list(dict.fromkeys(credits))
    caption = f"{topic['caption']}\n\n{topic['tags']} #RoboMorph\n\n" + "\n".join(uniq)
    meta = {"topic": topic["id"], "title": topic["title"], "caption": caption,
            "created": datetime.now().isoformat(timespec="seconds"), "source": "auto"}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return out


# ---------------------------------------------------------------- buffer
def ready_items():
    READY.mkdir(exist_ok=True)
    return sorted(p for p in READY.glob("*.mp4") if p.with_suffix(".json").exists())


def take_inbox():
    """Videos you drop into inbox/ (from Flow, Dreamina, Kling...) are posted first.
    Optional caption: same file name with .txt."""
    INBOX.mkdir(exist_ok=True)
    vids = sorted(p for p in INBOX.iterdir() if p.suffix.lower() in (".mp4", ".mov"))
    for v in vids:
        cap_file = v.with_suffix(".txt")
        caption = cap_file.read_text().strip() if cap_file.exists() else \
            "🌱 Keajaiban kecil dari alam.\n\n#sains #alam #RoboMorph"
        dest = READY / f"000-inbox-{datetime.now().strftime('%Y%m%d%H%M%S')}-{v.stem}.mp4"
        shutil.move(str(v), dest)
        if cap_file.exists():
            cap_file.unlink()
        dest.with_suffix(".json").write_text(json.dumps(
            {"topic": "inbox", "title": v.stem, "caption": caption,
             "created": datetime.now().isoformat(timespec="seconds"), "source": "inbox"},
            ensure_ascii=False))
        log(f"inbox video queued first: {v.name}")


def produce(target=None):
    target = int(target or cfg("BUFFER_SIZE", "4"))
    take_inbox()
    topics = json.loads(TOPICS_FILE.read_text())
    st = load_state()
    made, tries = 0, 0
    while len(ready_items()) < target and tries < len(topics):
        topic = topics[st["next_topic"] % len(topics)]
        st["next_topic"] += 1
        tries += 1
        log(f"producing: {topic['id']}")
        try:
            out = build_video(topic, st, READY)
            made += 1
            log(f"ready: {out.name}")
        except Exception as e:  # noqa: BLE001
            log(f"failed {topic['id']}: {e}")
        save_state(st)
    return made


# ---------------------------------------------------------------- facebook
def fb_post_reel(video, caption):
    page, token = cfg("FB_PAGE_ID"), cfg("FB_PAGE_TOKEN")
    ver = cfg("FB_GRAPH_VERSION", "v21.0")
    if not (page and token):
        raise RuntimeError("FB_PAGE_ID / FB_PAGE_TOKEN not set in .env")
    base = f"https://graph.facebook.com/{ver}/{page}/video_reels"
    r = requests.post(base, data={"upload_phase": "start", "access_token": token}, timeout=60)
    r.raise_for_status()
    vid = r.json()["video_id"]
    size = video.stat().st_size
    with open(video, "rb") as f:
        r = requests.post(f"https://rupload.facebook.com/video-upload/{ver}/{vid}",
                          headers={"Authorization": f"OAuth {token}", "offset": "0",
                                   "file_size": str(size)}, data=f, timeout=600)
    r.raise_for_status()
    r = requests.post(base, data={"upload_phase": "finish", "video_id": vid,
                                  "video_state": "PUBLISHED", "description": caption,
                                  "access_token": token}, timeout=60)
    r.raise_for_status()
    if not r.json().get("success", False):
        raise RuntimeError(f"finish failed: {r.text[:300]}")
    return vid


def post():
    items = ready_items()
    if not items:
        log("buffer empty, producing one now")
        produce(1)
        items = ready_items()
    if not items:
        notify("GAGAL: tidak ada video yang bisa dibuat. Cek logs/robomorph.log")
        log("nothing to post")
        return 1
    video = items[0]
    meta = json.loads(video.with_suffix(".json").read_text())
    if cfg("DRY_RUN", "0") == "1":
        log(f"DRY_RUN: would post {video.name}\n{meta['caption']}")
        return 0
    last_err = None
    for attempt in range(3):
        try:
            vid = fb_post_reel(video, meta["caption"])
            POSTED.mkdir(exist_ok=True)
            st = load_state()
            st["history"].append({"time": datetime.now().isoformat(timespec="seconds"),
                                  "file": video.name, "fb_video_id": vid, "topic": meta["topic"]})
            save_state(st)
            keep = cfg("KEEP_POSTED", "0") == "1"
            for p in (video, video.with_suffix(".json")):
                shutil.move(str(p), POSTED / p.name) if keep else p.unlink()
            log(f"posted {video.name} -> fb video {vid}")
            notify(f"Terposting: {meta['title']}")
            return 0
        except Exception as e:  # noqa: BLE001
            last_err = e
            body = getattr(getattr(e, "response", None), "text", "")
            log(f"post attempt {attempt + 1} failed: {e} {body[:300]}")
            time.sleep(30 * (attempt + 1))
    notify(f"GAGAL posting {video.name}: {last_err}. Video tetap di antrean.")
    return 1


def status():
    items = ready_items()
    print(f"ready in buffer: {len(items)}")
    for p in items:
        print("  ", p.name)
    st = load_state()
    print("last posts:")
    for h in st["history"][-5:]:
        print("  ", h["time"], h["topic"], h["fb_video_id"])


def main():
    load_env()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "produce":
        produce(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "post":
        rc = post()
        produce()  # refill buffer after posting
        sys.exit(rc)
    elif cmd == "status":
        status()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
