"""Данные организации из её группы ВКонтакте: название, описание, телефон, адрес, товары с ценами, фото."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.vk.ru/method/"
V = "5.199"
DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class VKError(Exception):
    pass


def screen_from_url(url: str) -> str:
    """vk.com/salon_roza, https://vk.ru/club123, public123, @salon → salon_roza / club123 / public123."""
    s = (url or "").strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^(www\.)?(m\.)?(vk\.com|vk\.ru|vkontakte\.ru)/", "", s)
    if re.match(r"^[\w-]+\.[a-z]{2,}(/|$)", s):      # ссылка на другой сайт
        raise VKError("Это не ссылка на ВКонтакте")
    s = s.lstrip("@").split("?")[0].split("#")[0].strip("/")
    s = s.split("/")[0]
    if not s or not re.fullmatch(r"[A-Za-z0-9_.]+", s):
        raise VKError("Не похоже на ссылку на группу ВК")
    return s


class Client:
    def __init__(self, token: str, opener=None):
        if not token:
            raise VKError("Нет токена ВК. Впишите VK_USER_TOKEN в файл .env (тот же, что у основного бота)")
        self.token = token
        self.opener = opener or urllib.request.urlopen

    def call(self, method: str, **params):
        data = {k: v for k, v in params.items() if v is not None}
        data.update(access_token=self.token, v=V)
        body = urllib.parse.urlencode(data).encode()
        try:
            with self.opener(API + method, data=body, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise VKError(f"Нет связи с ВК: {exc}")
        if "error" in payload:
            e = payload["error"]
            raise VKError(f"ВК: {e.get('error_msg')} (код {e.get('error_code')})")
        return payload["response"]


def _best(sizes):
    """Самый большой вариант фото из списка sizes."""
    if not sizes:
        return ""
    s = max(sizes, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0) or "smxopqryzw".find(x.get("type", "s")))
    return s.get("url") or s.get("src") or ""


def _price(p) -> str:
    if not p:
        return ""
    if p.get("text"):
        return p["text"].replace("\xa0", " ")
    try:
        return f"{int(p.get('amount', 0)) // 100} ₽"
    except (TypeError, ValueError):
        return ""


def _hours(timetable) -> str:
    """{'mon': {'open_time': 600, 'close_time': 1200}, …} → «Пн–Пт 10:00–20:00, Сб 11:00–18:00»."""
    if not isinstance(timetable, dict):
        return ""

    def hm(m):
        return f"{int(m) // 60:02d}:{int(m) % 60:02d}"
    spans = []
    for i, k in enumerate(DAY_KEYS):
        d = timetable.get(k)
        t = f"{hm(d['open_time'])}–{hm(d['close_time'])}" if d and "open_time" in d else ""
        if spans and spans[-1][2] == t:
            spans[-1][1] = i
        else:
            spans.append([i, i, t])
    parts = []
    for a, b, t in spans:
        if not t:
            continue
        days = DAYS[a] if a == b else f"{DAYS[a]}–{DAYS[b]}"
        parts.append(f"{days} {t}")
    if len(parts) == 1 and parts[0].startswith("Пн–Вс"):
        return "ежедневно " + parts[0][6:]
    return ", ".join(parts)


def fetch(url: str, client: Client, photo_limit=24, item_limit=40) -> dict:
    screen = screen_from_url(url)
    res = client.call("groups.getById", group_id=screen,
                      fields="description,status,activity,city,contacts,addresses,site,cover,phone,market")
    groups = res.get("groups", res) if isinstance(res, dict) else res
    if not groups:
        raise VKError("Группа не найдена")
    g = groups[0]
    gid = g["id"]
    out = {
        "fields": {
            "name": g.get("name", ""),
            "tagline": (g.get("status") or g.get("activity") or "").strip(),
            "about": (g.get("description") or "").strip(),
            "city": (g.get("city") or {}).get("title", ""),
            "vk": f"https://vk.com/{g.get('screen_name') or 'club' + str(gid)}",
            "phone": "",
            "address": "",
            "hours": "",
        },
        "items": [],
        "photo_urls": [],
        "activity": g.get("activity", ""),
        "notes": [],
    }
    f = out["fields"]
    for c in g.get("contacts") or []:
        if c.get("phone") and not f["phone"]:
            f["phone"] = c["phone"]
    if g.get("phone") and not f["phone"]:
        f["phone"] = g["phone"]

    try:
        addr = client.call("groups.getAddresses", group_id=gid, count=1,
                           fields="address,city_id,title,phone,timetable")
        if addr.get("items"):
            a = addr["items"][0]
            f["address"] = a.get("address", "")
            f["phone"] = f["phone"] or a.get("phone", "")
            f["hours"] = _hours(a.get("timetable"))
    except VKError as exc:
        out["notes"].append(f"адрес не получен: {exc}")

    try:
        m = client.call("market.get", owner_id=-gid, count=min(item_limit, 200), extended=1)
        for it in m.get("items", []):
            photo = _best((it.get("photos") or [{}])[0].get("sizes")) if it.get("photos") else it.get("thumb_photo", "")
            out["items"].append({
                "title": it.get("title", "").strip(),
                "price": _price(it.get("price")),
                "desc": re.sub(r"\s+", " ", it.get("description") or "").strip()[:160],
                "cat": "",
                "photo_url": photo,
            })
    except VKError as exc:
        out["notes"].append(f"товары не получены: {exc}")

    try:
        ph = client.call("photos.getAll", owner_id=-gid, count=min(photo_limit * 2, 200), no_service_albums=0)
        urls = [_best(p.get("sizes")) for p in ph.get("items", [])]
        out["photo_urls"] = [u for u in urls if u][:photo_limit]
    except VKError as exc:
        out["notes"].append(f"фото не получены: {exc}")
    if not out["photo_urls"]:
        cover = _best((g.get("cover") or {}).get("images"))
        if cover:
            out["photo_urls"].append(cover)
    return out


def download(url: str, dest: Path, opener=None) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 lstudio-demo"})
        with (opener or urllib.request.urlopen)(req, timeout=30) as r:
            data = r.read()
        if len(data) < 500:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:
        return False


def read_token(base: Path) -> str:
    """Токен из своего .env, а если там пусто — из .env основного бота (папка lstudio-bot рядом)."""
    for env in (base / ".env", base.parent / "lstudio-bot" / ".env", base.parent / "lstudio-bot-main" / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("VK_USER_TOKEN="):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    m = re.search(r"access_token=([^&\s#]+)", val)
                    val = m.group(1) if m else val
                    if val:
                        return val
    return ""
