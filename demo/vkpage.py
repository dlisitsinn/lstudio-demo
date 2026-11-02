"""Данные группы ВК прямо со страницы — через окно Chrome, где вы вошли во ВК (как в основном боте).

Токен не нужен. Бот открывает группу в отдельном профиле браузера (data/vk-browser),
читает название, описание, телефон, адрес, фото и товары с ценами.
В первый раз откроется окно ВК — войдите, и дальше бот работает сам, без окна.
"""
import re
import time
from pathlib import Path

from . import shots
from .vkdata import VKError, screen_from_url

PHONE_RE = re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
ADDR_RE = re.compile(r"(ул\.|улица|просп|пр-т|пр\.|пер\.|переулок|бульвар|б-р|шоссе|площадь|мкр|микрорайон|тракт)", re.I)

JS_INFO = r"""() => {
  const meta = n => (document.querySelector(`meta[property="${n}"]`) || document.querySelector(`meta[name="${n}"]`) || {}).content || "";
  const names = [];
  const push = t => { t = (t || "").replace(/\s+/g, " ").trim(); if (t) names.push(t); };
  push(meta("og:title"));
  document.querySelectorAll('[class*="page_name"], [class*="GroupName"], [class*="group-name"], [class*="groupName"], [class*="ProfileName"], [data-testid*="name"], h1, h2')
    .forEach(e => { if (e.offsetParent !== null || e.tagName === "H1") push(e.textContent); });
  push(document.title);
  const links = [...document.querySelectorAll("a[href]")].map(a => a.getAttribute("href"))
    .filter(h => /market|uslugi|services|product|menu|catalog/i.test(h || ""));
  return {
    names: names.slice(0, 30),
    desc: meta("og:description") || meta("description"),
    image: meta("og:image"),
    text: (document.body.innerText || "").slice(0, 30000),
    html: document.documentElement.innerHTML.slice(0, 400000),
    links: links.slice(0, 40)
  };
}"""

JS_IMGS = r"""() => {
  const out = [];
  const add = (u, w) => { if (u && /userapi\.com|vkuserphoto|vk-portal|\/impg\//.test(u)) out.push([u, w || 0]); };
  document.querySelectorAll("img").forEach(i => {
    let best = i.currentSrc || i.src, bw = i.naturalWidth || 0;
    if (i.srcset) i.srcset.split(",").forEach(p => {
      const [u, w] = p.trim().split(/\s+/); const n = parseInt(w) || 0;
      if (u && n > bw) { bw = n; best = u; }
    });
    add(best, bw);
  });
  document.querySelectorAll('[style*="background-image"]').forEach(e => {
    const m = (e.style.backgroundImage || "").match(/url\(["']?(.*?)["']?\)/);
    if (m) add(m[1], e.clientWidth);
  });
  return out;
}"""

JS_MARKET = r"""() => {
  const priceRe = /^\s*(от\s*)?\d[\d\s\u00a0\u202f.,]*\s*(₽|руб\.?|р\.)\s*$/i;
  const tailRe = /^(.{3,}?)\s+(?:—\s*|-\s*)?((?:от\s*)?\d[\d\s\u00a0\u202f]*\s*(?:₽|руб\.?|р\.))\s*$/i;
  const leaves = [...document.querySelectorAll("body *")].filter(e => e.children.length === 0)
    .filter(e => { const t = (e.textContent || "").trim(); return t.length < 160 && (priceRe.test(t) || tailRe.test(t)); });
  const seen = new Set(), items = [], clean = t => (t || "").replace(/[\s\u00a0\u202f]+/g, " ").trim();
  for (const pe of leaves) {
    let card = null, up = pe;
    for (let k = 0; k < 8 && up && up !== document.body; k++) { up = up.parentElement; if (up && up !== document.body && up.querySelector("img")) { card = up; break; } }
    if (!card || seen.has(card)) card = pe;          // карточки с фото нет — позиция строкой
    if (seen.has(card)) continue;
    seen.add(card);
    const own = clean(pe.textContent), m = own.match(tailRe);
    let title = "", price = own;
    if (m && !priceRe.test(own)) { title = m[1]; price = m[2]; }
    if (!title) {
      const texts = [...card.querySelectorAll("*")].filter(e => e.children.length === 0)
        .map(e => clean(e.textContent)).filter(t => t.length > 2 && !priceRe.test(t) && !/^(в корзину|купить|подробнее|написать)$/i.test(t));
      title = texts[0] || "";
    }
    const img = card.querySelector("img");
    let photo = img ? (img.currentSrc || img.src) : "";
    if (img && img.srcset) { let bw = 0; img.srcset.split(",").forEach(p => { const [u, w] = p.trim().split(/\s+/); const n = parseInt(w) || 0; if (n > bw) { bw = n; photo = u; } }); }
    if (title) items.push({title, price: clean(price), photo});
  }
  return items.slice(0, 120);
}"""

JS_SCROLL = """async (n) => {
  let last = 0, same = 0;
  for (let i = 0; i < n; i++) {
    scrollTo(0, document.documentElement.scrollHeight);
    await new Promise(r => setTimeout(r, 700));
    const h = document.documentElement.scrollHeight;
    if (h === last) { if (++same >= 3) break; } else same = 0;
    last = h;
  }
}"""


def _logged_in(ctx) -> bool:
    try:
        return any(c["name"].startswith(("remixsid", "remixnsid")) and c.get("value") for c in ctx.cookies())
    except Exception:
        return False


def _host(ctx) -> str:
    try:
        for c in ctx.cookies():
            if c["name"].startswith(("remixsid", "remixnsid")) and c.get("value"):
                return "https://vk.ru" if c.get("domain", "").lstrip(".").endswith("vk.ru") else "https://vk.com"
    except Exception:
        pass
    return "https://vk.com"


def _launch(pw, profile: Path, headless: bool):
    last = None
    for channel in ("chrome", "msedge", None):
        try:
            kw = {"headless": headless, "viewport": None if not headless else {"width": 1280, "height": 900}}
            if headless:   # обычный адрес браузера, чтобы ВК показал полную страницу
                kw["user_agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
                kw["locale"] = "ru-RU"
            if channel:
                kw["channel"] = channel
            return pw.chromium.launch_persistent_context(str(profile), **kw)
        except Exception as exc:
            last = exc
    raise RuntimeError(f"Не удалось открыть Chrome: {last}")


def marker(base: Path) -> Path:
    return base / "data" / "vk-browser" / ".logged-in"


def login(base: Path, log=print, timeout=300) -> bool:
    """Открывает окно ВК и ждёт, пока вы войдёте. Повторно не спрашивает."""
    shots.ensure_playwright(log)
    from playwright.sync_api import sync_playwright
    profile = base / "data" / "vk-browser"
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = _launch(pw, profile, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://vk.com/", wait_until="domcontentloaded")
            log("Войдите во ВК в открывшемся окне Chrome — бот подождёт")
            end = time.time() + timeout
            while time.time() < end:
                if _logged_in(ctx):
                    page.wait_for_timeout(1500)
                    marker(base).write_text("ok")
                    return True
                if not ctx.pages:
                    raise ValueError("Окно закрыли до входа во ВК")
                ctx.pages[0].wait_for_timeout(700)
            raise ValueError("Не дождалась входа во ВК (5 минут). Попробуйте ещё раз")
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _group_id(html: str):
    for pat in (r'"group_id"\s*:\s*"?(\d+)', r'im\?sel=-(\d+)', r'/im/convo/-(\d+)', r'wall-(\d+)_',
                r'"oid"\s*:\s*-(\d+)', r'/(?:club|public)(\d+)', r'photo-(\d+)_', r'album-(\d+)_', r'market-(\d+)'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def _clean_photos(pairs, limit):
    out, seen = [], set()
    for u, w in pairs:
        u = u.replace("&amp;", "&")
        key = u.split("?")[0]
        if key in seen:
            continue
        m = re.search(r"size=(\d+)x(\d+)", u)
        size = max(int(m.group(1)), int(m.group(2))) if m else (w or 0)
        if size and size < 300:              # аватарки, иконки
            continue
        if "ava=1" in u or "/emoji/" in u:
            continue
        seen.add(key)
        out.append(u)
        if len(out) >= limit:
            break
    return out


GENERIC = {"вконтакте", "vk", "вк", "vk.com", "vk.ru", "сообщество", "группа", "мессенджер", "новости", "главная",
           "товары", "услуги", "меню", "фотографии", "фото", "видео", "клипы", "сообщения", "подписаться"}


def pick_name(info: dict) -> str:
    for n in info.get("names") or [info.get("h1", ""), info.get("title", "")]:
        n = re.sub(r"\s*[|—–-]\s*(ВКонтакте|VK|ВК)\s*$", "", (n or "").strip())
        n = re.sub(r"^(ВКонтакте|VK)\s*[|—–-]\s*", "", n)
        if n and n.lower() not in GENERIC and len(n) <= 80:
            return n
    return ""


def parse_info(info: dict) -> dict:
    """Текст страницы группы → поля формы."""
    title = pick_name(info)
    text = info.get("text") or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    phone = ""
    m = PHONE_RE.search(text)
    if m:
        phone = m.group(0)
    address = ""
    for l in lines:
        if ADDR_RE.search(l) and 5 < len(l) < 120 and not l.lower().startswith(("http", "подписаться")):
            address = l
            break
    hours = ""
    for l in lines:
        if re.search(r"\d{1,2}:\d{2}\s*[–—-]\s*\d{1,2}:\d{2}", l) and len(l) < 80:
            hours = l
            break
    desc = (info.get("desc") or "").strip()
    return {"name": title, "tagline": "", "about": desc, "phone": phone, "address": address, "hours": hours,
            "city": "", "vk": ""}


def fetch(url: str, base: Path, log=print, photo_limit=24, menu_url: str = "") -> dict:
    if not menu_url and re.search(r"/(market|uslugi|services|product)", url or ""):
        menu_url, gm = url, re.search(r"-(\d+)", url)      # дали ссылку прямо на товары
        url = f"vk.com/club{gm.group(1)}" if gm else url
    screen = screen_from_url(url)
    shots.ensure_playwright(log)
    from playwright.sync_api import sync_playwright
    profile = base / "data" / "vk-browser"
    profile.mkdir(parents=True, exist_ok=True)
    notes = []
    with sync_playwright() as pw:
        ctx = _launch(pw, profile, headless=True)
        if not _logged_in(ctx):
            ctx.close()
            raise VKError("Сначала войдите во ВК: нажмите «Войти во ВК через Chrome»")
        try:
            page = ctx.new_page()
            host = _host(ctx)
            log("Открываю группу…")
            page.goto(f"{host}/{screen}", wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(2500)
            info = page.evaluate(JS_INFO)
            if "/login" in page.url or "id.vk." in page.url:
                marker(base).unlink(missing_ok=True)
                raise VKError("ВК просит войти заново — нажмите «Войти во ВК через Chrome»")
            fields = parse_info(info)
            fields["vk"] = f"{host}/{screen}"
            page.evaluate(JS_SCROLL, 4)
            group_imgs = page.evaluate(JS_IMGS)
            gid = _group_id(info.get("html") or "") or _group_id(page.content())

            photos = []
            if gid:
                log("Смотрю фото группы…")
                try:
                    page.goto(f"{host}/photos-{gid}", wait_until="domcontentloaded", timeout=40000)
                    page.wait_for_timeout(2000)
                    page.evaluate(JS_SCROLL, 8)
                    photos = _clean_photos(page.evaluate(JS_IMGS), photo_limit)
                except Exception as exc:
                    notes.append(f"фото: {exc}")
            if len(photos) < 4:                  # мало фото в альбомах — добавляем с главной страницы
                photos += [u for u in _clean_photos(group_imgs, photo_limit) if u not in photos]
                photos = photos[:photo_limit]
            if not photos and info.get("image"):
                photos = [info["image"]]

            items, seen_titles = [], set()
            pages = []
            if menu_url:
                pages.append(menu_url if menu_url.startswith("http") else "https://" + menu_url.lstrip("/"))
            for h in info.get("links") or []:
                if gid and gid in h or screen in h:
                    pages.append(h if h.startswith("http") else host + h)
            if gid:
                pages += [f"{host}/market-{gid}", f"{host}/uslugi-{gid}", f"{host}/market/-{gid}"]
            done = set()
            for link in pages:
                key = link.split("#")[0]
                if key in done or len(done) >= 5:
                    continue
                done.add(key)
                log("Смотрю меню и цены…")
                try:
                    page.goto(link, wait_until="domcontentloaded", timeout=40000)
                    page.wait_for_timeout(2000)
                    page.evaluate(JS_SCROLL, 25)
                    for it in page.evaluate(JS_MARKET):
                        t = it["title"].strip()[:120]
                        if t.lower() in seen_titles:
                            continue
                        seen_titles.add(t.lower())
                        items.append({"title": t, "price": it["price"], "desc": "", "cat": "",
                                      "photo_url": it.get("photo") or ""})
                except Exception as exc:
                    notes.append(f"меню: {exc}")
                if len(items) >= 10 and not menu_url:
                    break
            if not gid:
                notes.append("не нашла номер группы — фото взяты только с главной страницы")
            if not items:
                notes.append("товаров с ценами в группе нет — впишите цены вручную")
            return {"fields": fields, "items": items, "photo_urls": photos, "activity": "", "notes": notes}
        finally:
            try:
                ctx.close()
            except Exception:
                pass
