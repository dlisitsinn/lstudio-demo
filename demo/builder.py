"""Сборка демо-сайта: берём сайт-образец с GitHub, подставляем данные клиента, делаем скриншоты."""
import io
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TEMPLATES = BASE / "templates"
DATA = BASE / "data"
SOURCES = DATA / "sources"
UPLOADS = DATA / "uploads"
SITES = BASE / "sites"
APPLY_JS = Path(__file__).resolve().parent / "apply.js"

SOURCE_MAX_AGE = 6 * 3600          # образцы с GitHub перекачиваем раз в 6 часов


# ---------------- шаблоны ----------------
def list_templates():
    out = []
    for f in sorted(TEMPLATES.glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        t["id"] = f.stem
        out.append(t)
    out.sort(key=lambda t: t.get("order", 99))
    return out


def get_template(tid: str) -> dict:
    f = TEMPLATES / f"{tid}.json"
    if not f.exists() or "/" in tid or "\\" in tid:
        raise ValueError(f"Нет такого шаблона: {tid}")
    t = json.loads(f.read_text(encoding="utf-8"))
    t["id"] = tid
    return t


def guess_template(text: str) -> str:
    """Подбирает шаблон по словам из названия и описания организации."""
    text = (text or "").lower()
    best, score = "", 0
    for t in list_templates():
        s = sum(1 for k in t.get("keywords", []) if k in text)
        if s > score:
            best, score = t["id"], s
    return best


# ---------------- образцы с GitHub ----------------
def fetch_source(repo: str, branch: str = "main", force: bool = False, opener=None) -> Path:
    """Скачивает репозиторий-образец (например dlisitsinn/velvet) в data/sources. Возвращает папку."""
    dest = SOURCES / repo.replace("/", "__")
    stamp = dest / ".fetched"
    fresh = stamp.exists() and time.time() - stamp.stat().st_mtime < SOURCE_MAX_AGE
    if dest.exists() and fresh and not force:
        return dest
    data, last = None, None
    for url in (f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}",
                f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lstudio-demo"})
            with (opener or urllib.request.urlopen)(req, timeout=60) as r:
                data = r.read()
            break
        except Exception as exc:
            last = exc
    if data is None:
        if dest.exists():               # нет интернета — работаем со старой копией
            return dest
        raise ValueError(f"Не удалось скачать образец {repo} с GitHub: {last}")
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    z = zipfile.ZipFile(io.BytesIO(data))
    for name in z.namelist():
        if name.endswith("/"):
            continue
        rel = name.split("/", 1)[1] if "/" in name else name
        if not rel or ".." in Path(rel).parts or rel.startswith((".git/", ".github/")):
            continue
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(name))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    (dest / ".fetched").write_text(datetime.now().isoformat())
    return dest


# ---------------- данные клиента ----------------
_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slugify(name: str) -> str:
    s = "".join(_TR.get(c, c) for c in (name or "").lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40]
    return s or "demo"


def format_price(p) -> str:
    p = str(p or "").strip()
    if not p:
        return ""
    if re.search(r"[a-zA-Zа-яА-Я₽$€]", p.replace("от", "").replace("до", "")):
        return p                                    # уже с подписью: «по запросу», «1500 руб.»
    m = re.fullmatch(r"(от|до)?\s*([\d\s.,]+)\s*(-|–|—)?\s*([\d\s.,]*)", p)
    if not m:
        return p

    def num(x):
        d = re.sub(r"[^\d]", "", x)
        return f"{int(d):,}".replace(",", " ") if d else ""
    a, b = num(m.group(2)), num(m.group(4) or "")
    pre = (m.group(1) + " ") if m.group(1) else ""
    return f"{pre}{a}–{b} ₽" if b else f"{pre}{a} ₽"


def price_number(p) -> str:
    d = re.sub(r"[^\d]", "", str(p or "").split("–")[0])
    return f"{int(d):,}".replace(",", " ") if d else ""


def parse_items_text(text: str) -> list:
    """«# Категория» и строки «Название — цена | описание» → список позиций."""
    items, cat = [], ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            cat = line.lstrip("#").strip()
            continue
        desc = ""
        if "|" in line:
            line, desc = [x.strip() for x in line.split("|", 1)]
        title, price = line, ""
        m = re.match(r"^(.*?)\s*(?:—|–|-{1,2}|:|\.{3,}|…)\s*((?:от|до)?\s*[\d][\d\s.,]*(?:\s*[-–—]\s*[\d\s.,]+)?\s*(?:₽|р\.?|руб\.?)?)$",
                     line)
        if not m:   # «Брови 600» — цена просто через пробел в конце
            m = re.match(r"^(.*?[^\d\s])\s+((?:от\s*)?\d{2,}[\d\s]*(?:₽|р\.?|руб\.?)?)$", line)
        if m:
            title, price = m.group(1).strip(), m.group(2).strip()
        items.append({"title": title, "price": price, "desc": desc, "cat": cat, "photo": ""})
    return items


def phone_href(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d[0] in "78":
        d = "7" + d[1:]
    elif len(d) == 10:
        d = "7" + d
    return "+" + d if d else ""


def pretty_phone(phone: str) -> str:
    """89120001122 → 8 (912) 000-11-22; другие форматы оставляем как есть."""
    p = (phone or "").strip()
    d = re.sub(r"\D", "", p)
    if len(d) == 11 and d[0] in "78" and d[1] == "9":
        lead = "+7" if p.startswith("+") or d[0] == "7" else "8"
        return f"{lead} ({d[1:4]}) {d[4:7]}-{d[7:9]}-{d[9:11]}"
    if len(d) == 10 and d[0] == "9":
        return f"+7 ({d[0:3]}) {d[3:6]}-{d[6:8]}-{d[8:10]}"
    return p


def make_vars(f: dict, tpl: dict) -> dict:
    name = (f.get("name") or "").strip() or "Ваша компания"
    words = name.split()
    about = [p.strip() for p in re.split(r"\n\s*\n|\n", f.get("about") or "") if p.strip()]
    defaults = tpl.get("defaults", {})
    city = (f.get("city") or "").strip()
    v = {
        "name": name,
        "NAME": name.upper(),
        "name_first": words[0],
        "name_rest": " ".join(words[1:]),
        "NAME_FIRST": words[0].upper(),
        "NAME_REST": " ".join(words[1:]).upper(),
        "letter": next((c for c in name if c.isalnum()), "·").upper(),
        "tagline": (f.get("tagline") or "").strip(),
        "city": city,
        "address": (f.get("address") or "").strip(),
        "phone": pretty_phone(f.get("phone")),
        "phone_href": phone_href(f.get("phone")),
        "hours": (f.get("hours") or "").strip(),
        "email": (f.get("email") or "").strip(),
        "vk": (f.get("vk") or "").strip(),
        "year": str(datetime.now().year),
        "about1": about[0] if about else "",
        "about2": about[1] if len(about) > 1 else "",
        "about3": about[2] if len(about) > 2 else "",
    }
    # пустые поля заполняем текстами шаблона (в них можно писать {name}, {city})
    for k, val in defaults.items():
        if not v.get(k):
            v[k] = re.sub(r"\{(\w+)\}", lambda m: v.get(m.group(1), ""), val).strip()
    v["phone_href"] = v["phone_href"] or phone_href(v["phone"])
    v["phone_wa"] = v["phone_href"].lstrip("+")
    if not v["address"] and city:
        v["address"] = city
    v["vk_short"] = re.sub(r"^https?://(www\.)?", "", v["vk"]).rstrip("/")
    v["city_or_address"] = v["city"] or v["address"]
    full = v["address"] if (not city or city.lower() in v["address"].lower()) else f"{city}, {v['address']}"
    v["address_full"] = full
    v["address_q"] = urllib.parse.quote(full)
    return v


def prepare(fields: dict, items: list, photos: list, tpl: dict, site_dir: Path) -> dict:
    """Готовит window.__DEMO__ и копирует фото клиента в папку сайта."""
    pdir = site_dir / "demo" / "photos"
    pdir.mkdir(parents=True, exist_ok=True)
    copied = {}

    def take(url: str) -> str:
        """URL загруженного файла (/files/...) → относительный путь внутри сайта."""
        if not url:
            return ""
        if url in copied:
            return copied[url]
        src = _upload_path(url)
        if not src or not src.is_file():
            return ""
        name = f"p{len(copied) + 1:02d}{src.suffix.lower() or '.jpg'}"
        shutil.copy2(src, pdir / name)
        copied[url] = "demo/photos/" + name
        return copied[url]

    clean = []
    for it in items or []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        price = format_price(it.get("price"))
        main = take(it.get("photo"))
        extra = [p for p in (take(u) for u in it.get("photos") or []) if p and p != main]
        clean.append({"title": title, "price": price, "price_num": price_number(price),
                      "desc": (it.get("desc") or "").strip(), "cat": (it.get("cat") or "").strip(),
                      "photo": main or (extra[0] if extra else ""),
                      "photos": ([main] if main else []) + extra})
    cats, order = {}, []
    for it in clean:
        c = it["cat"] or tpl.get("default_cat", "Услуги")
        if c not in cats:
            cats[c] = []
            order.append(c)
        cats[c].append(it)
    gallery = [p for p in (take(u) for u in photos or []) if p]
    slot_names = tpl.get("slots", [])
    pool = gallery or [it["photo"] for it in clean if it["photo"]]
    slots = {}
    for i, s in enumerate(slot_names):
        if pool:
            slots[s] = pool[i % len(pool)] if tpl.get("repeat_photos", True) or i < len(pool) else ""
    v = make_vars(fields, tpl)
    v["logo"] = take(fields.get("logo") or "")
    return {
        "v": v,
        "items": clean,
        "cats": [{"name": c, "items": cats[c], "photo": next((i["photo"] for i in cats[c] if i["photo"]), "")}
                 for c in order],
        "photos": slots,
        "gallery": gallery,
        "gallery_items": [{"photo": g, "title": "", "price": ""} for g in gallery],
        "ops": tpl.get("ops", []),
        "watch": bool(tpl.get("watch")),
    }


def _upload_path(url: str):
    if url.startswith("/files/"):
        rel = url[len("/files/"):]
        p = (UPLOADS / rel).resolve()
        if UPLOADS.resolve() in p.parents:
            return p
    return None


_METRIKA = re.compile(r"<!--\s*Yandex\.Metrika counter\s*-->.*?<!--\s*/Yandex\.Metrika counter\s*-->", re.S | re.I)
_METRIKA_SCRIPT = re.compile(r"<script\b(?:(?!</script>).)*?mc\.yandex\.ru(?:(?!</script>).)*</script>", re.S | re.I)
_METRIKA_NS = re.compile(r"<noscript>(?:(?!</noscript>).)*mc\.yandex\.ru(?:(?!</noscript>).)*</noscript>", re.S | re.I)


def patch_html(html: str, title: str) -> str:
    html = _METRIKA.sub("", html)
    html = _METRIKA_SCRIPT.sub("", html)
    html = _METRIKA_NS.sub("", html)
    head_add = '<meta name="robots" content="noindex, nofollow">'
    html = re.sub(r"<head([^>]*)>", lambda m: f"<head{m.group(1)}>{head_add}", html, count=1, flags=re.I)
    if title:
        safe = title.replace("&", "&amp;").replace("<", "&lt;")
        html = re.sub(r"<title>.*?</title>", f"<title>{safe}</title>", html, count=1, flags=re.S | re.I)
    tail = '<script src="demo/data.js"></script><script src="demo/apply.js"></script>'
    if re.search(r"</body>", html, re.I):
        html = re.sub(r"</body>", tail + "</body>", html, count=1, flags=re.I)
    else:
        html += tail
    return html


def build(template_id: str, fields: dict, items: list, photos: list, force_fetch=False, opener=None) -> dict:
    tpl = get_template(template_id)
    if tpl.get("local"):                       # образец лежит прямо в боте (папка samples/…)
        src = BASE / tpl["local"]
    else:
        src = fetch_source(tpl["repo"], tpl.get("branch", "main"), force=force_fetch, opener=opener)
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    slug = f"{slugify(fields.get('name'))}-{template_id}-{stamp}"
    out = SITES / slug
    shutil.copytree(src, out, ignore=shutil.ignore_patterns(".fetched", ".git", "README.md", "CNAME"))
    demo = prepare(fields, items, photos, tpl, out)
    v = demo["v"]
    title = re.sub(r"\{(\w+)\}", lambda m: v.get(m.group(1), ""), tpl.get("title", "{name}")).strip(" —-")
    index = out / tpl.get("page", "index.html")
    index.write_text(patch_html(index.read_text(encoding="utf-8"), title), encoding="utf-8")
    (out / "demo").mkdir(exist_ok=True)
    (out / "demo" / "data.js").write_text("window.__DEMO__ = " + json.dumps(demo, ensure_ascii=False, indent=1) + ";\n",
                                          encoding="utf-8")
    shutil.copy2(APPLY_JS, out / "demo" / "apply.js")
    info = {"slug": slug, "name": v["name"], "template": template_id, "template_title": tpl.get("title_ui", template_id),
            "created": datetime.now().strftime("%d.%m.%Y %H:%M"), "page": tpl.get("page", "index.html"),
            "fields": fields, "items": items, "photos": photos}
    (out / "demo" / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def history(limit=40) -> list:
    out = []
    if not SITES.exists():
        return out
    for d in sorted(SITES.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "demo" / "info.json"
        if not f.exists():
            continue
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        shots = d / "screens"
        info["shots"] = sorted(p.name for p in shots.glob("*.*")) if shots.exists() else []
        out.append(info)
        if len(out) >= limit:
            break
    return out
