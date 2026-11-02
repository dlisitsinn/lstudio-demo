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
  const h1 = document.querySelector("h1");
  return {
    title: meta("og:title") || (h1 ? h1.textContent : "") || document.title,
    h1: h1 ? h1.textContent.trim() : "",
    desc: meta("og:description") || meta("description"),
    image: meta("og:image"),
    text: (document.body.innerText || "").slice(0, 30000),
    html: document.documentElement.innerHTML.slice(0, 400000)
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
  const priceRe = /^\s*(от\s*)?\d[\d\s  .,]*\s*(₽|руб\.?|р\.)\s*$/i;
  const leaves = [...document.querySelectorAll("body *")].filter(e => e.children.length === 0 && priceRe.test(e.textContent || ""));
  const seen = new Set(), items = [];
  for (const pe of leaves) {
    let card = pe;
    for (let k = 0; k < 7 && card; k++) { card = card.parentElement; if (card && card.querySelector("img")) break; }
    if (!card || seen.has(card)) continue;
    seen.add(card);
    const texts = [...card.querySelectorAll("*")].filter(e => e.children.length === 0)
      .map(e => (e.textContent || "").trim()).filter(t => t.length > 2 && !priceRe.test(t));
    const img = card.querySelector("img");
    let photo = img ? (img.currentSrc || img.src) : "";
    if (img && img.srcset) { let bw = 0; img.srcset.split(",").forEach(p => { const [u, w] = p.trim().split(/\s+/); const n = parseInt(w) || 0; if (n > bw) { bw = n; photo = u; } }); }
    items.push({title: texts[0] || "", price: (pe.textContent || "").replace(/[\s  ]+/g, " ").trim(), photo});
  }
  return items.filter(i => i.title).slice(0, 40);
}"""

JS_SCROLL = """async (n) => { for (let i = 0; i < n; i++) { scrollBy(0, innerHeight); await new Promise(r => setTimeout(r, 450)); } }"""


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


def parse_info(info: dict) -> dict:
    """Текст страницы группы → поля формы."""
    title = re.sub(r"\s*[|—-]\s*(ВКонтакте|VK|ВК)\s*$", "", (info.get("h1") or info.get("title") or "").strip())
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


def fetch(url: str, base: Path, log=print, photo_limit=24) -> dict:
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
            page.evaluate(JS_SCROLL, 3)
            group_imgs = page.evaluate(JS_IMGS)
            gid = _group_id(info.get("html") or "") or _group_id(page.content())

            photos = []
            if gid:
                log("Смотрю фото группы…")
                try:
                    page.goto(f"{host}/photos-{gid}", wait_until="domcontentloaded", timeout=40000)
                    page.wait_for_timeout(2000)
                    page.evaluate(JS_SCROLL, 4)
                    photos = _clean_photos(page.evaluate(JS_IMGS), photo_limit)
                except Exception as exc:
                    notes.append(f"фото: {exc}")
            if len(photos) < 4:                  # мало фото в альбомах — добавляем с главной страницы
                photos += [u for u in _clean_photos(group_imgs, photo_limit) if u not in photos]
                photos = photos[:photo_limit]
            if not photos and info.get("image"):
                photos = [info["image"]]

            items = []
            if gid:
                log("Смотрю товары и цены…")
                try:
                    page.goto(f"{host}/market-{gid}", wait_until="domcontentloaded", timeout=40000)
                    page.wait_for_timeout(2000)
                    page.evaluate(JS_SCROLL, 3)
                    for it in page.evaluate(JS_MARKET):
                        items.append({"title": it["title"].strip()[:120], "price": it["price"], "desc": "",
                                      "cat": "", "photo_url": it.get("photo") or ""})
                except Exception as exc:
                    notes.append(f"товары: {exc}")
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
