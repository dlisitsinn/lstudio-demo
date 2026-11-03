import base64
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from demo import builder, server, vkdata  # noqa: E402
import updater  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    for name in ("SOURCES", "UPLOADS", "SITES"):
        monkeypatch.setattr(builder, name, tmp_path / name.lower())
    src = tmp_path / "sources" / "dlisitsinn__velvet"
    src.mkdir(parents=True)
    (src / "index.html").write_text(
        "<html><head><title>VELVET</title></head><body><h1>VELVET</h1>"
        "<!-- Yandex.Metrika counter --><script>ym(1)</script><!-- /Yandex.Metrika counter -->"
        "<script src='https://mc.yandex.ru/metrika/tag.js'></script></body></html>", encoding="utf-8")
    (src / ".fetched").write_text("now")
    return tmp_path


# ---------- разбор цен и полей ----------
def test_parse_items_text():
    items = builder.parse_items_text("# Ногти\nМаникюр — 1500\nПедикюр: от 2000 ₽ | с покрытием\n"
                                     "Брови 600\nСтрижка - 1500-2000\nКонсультация\n# Ресницы\nЛами 1800")
    got = [(i["title"], i["price"], i["desc"], i["cat"]) for i in items]
    assert got == [("Маникюр", "1500", "", "Ногти"), ("Педикюр", "от 2000 ₽", "с покрытием", "Ногти"),
                   ("Брови", "600", "", "Ногти"), ("Стрижка", "1500-2000", "", "Ногти"),
                   ("Консультация", "", "", "Ногти"), ("Лами", "1800", "", "Ресницы")]


@pytest.mark.parametrize("raw,out", [("1500", "1 500 ₽"), ("от 2000", "от 2 000 ₽"), ("1500-2000", "1 500–2 000 ₽"),
                                     ("по запросу", "по запросу"), ("950 ₽", "950 ₽"), ("", "")])
def test_format_price(raw, out):
    assert builder.format_price(raw) == out


def test_phone_and_vars():
    assert builder.pretty_phone("89120001122") == "8 (912) 000-11-22"
    assert builder.pretty_phone("8 (35133) 2-11-22") == "8 (35133) 2-11-22"
    tpl = {"defaults": {"tagline": "салон · {city}", "phone": "+7 (900) 000-00-00"}}
    v = builder.make_vars({"name": "Студия Роза", "city": "Карталы", "address": "ул. Ленина, 5"}, tpl)
    assert v["NAME"] == "СТУДИЯ РОЗА" and v["name_first"] == "Студия" and v["name_rest"] == "Роза"
    assert v["tagline"] == "салон · Карталы" and v["phone_href"] == "+79000000000"
    assert v["address_full"] == "Карталы, ул. Ленина, 5" and "%" in v["address_q"]


def test_guess_template():
    assert builder.guess_template("Студия маникюра Роза") == "beauty"
    assert builder.guess_template("Детейлинг центр, полировка") == "auto"
    assert builder.guess_template("Ремонт квартир под ключ") == "repair"
    assert builder.guess_template("Столовая, комплексные обеды") == "cafe"
    assert builder.guess_template("Will Taste необистро, кафе") == "restaurant"
    assert builder.guess_template("Букеты и цветы с доставкой") == "flowers"


def test_templates_valid():
    for t in builder.list_templates():
        assert (t.get("repo", "").startswith("dlisitsinn/") and t["ops"]) or (builder.BASE / t["local"]).is_dir(), t["id"]
        for op in t["ops"]:
            assert isinstance(op, dict)


def test_apply_js_has_no_syntax_errors():
    node = shutil.which("node")
    if not node:
        pytest.skip("node не установлен")
    import subprocess
    r = subprocess.run([node, "--check", str(BASE / "demo" / "apply.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------- сборка ----------
def test_build_copies_photos_and_strips_metrika(tmp_dirs):
    up = builder.UPLOADS / "job1"
    up.mkdir(parents=True)
    (up / "a.png").write_bytes(PNG)
    (up / "b.png").write_bytes(PNG)
    info = builder.build("beauty", {"name": "Роза", "city": "Карталы"},
                         [{"title": "Маникюр", "price": "1500", "photo": "/files/job1/b.png"}, {"title": ""}],
                         ["/files/job1/a.png", "/files/../../etc/passwd"])
    out = builder.SITES / info["slug"]
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "mc.yandex" not in html and "ym(1)" not in html
    assert 'noindex' in html and "demo/apply.js" in html and "<title>Роза" in html
    demo = json.loads((out / "demo" / "data.js").read_text(encoding="utf-8").split("=", 1)[1].rstrip(";\n"))
    assert demo["items"] == [{"title": "Маникюр", "price": "1 500 ₽", "price_num": "1 500", "desc": "", "cat": "",
                              "photo": "demo/photos/p01.png", "photos": ["demo/photos/p01.png"]}]
    assert demo["gallery"] == ["demo/photos/p02.png"]          # чужой путь не скопирован
    assert demo["photos"]["hero"] == "demo/photos/p02.png"
    assert (out / "demo" / "photos" / "p02.png").exists()
    assert builder.history()[0]["slug"] == info["slug"]


def test_fetch_source_unpacks_zip(tmp_dirs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("sally-main/index.html", "<html></html>")
        z.writestr("sally-main/img/a.jpg", "x")
        z.writestr("sally-main/../evil.txt", "x")

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    d = builder.fetch_source("dlisitsinn/sally", opener=lambda req, timeout: R(buf.getvalue()))
    assert (d / "index.html").exists() and (d / "img" / "a.jpg").exists()
    assert not (builder.SOURCES / "evil.txt").exists()


# ---------- ВК ----------
class FakeVK:
    def __init__(self):
        self.calls = []

    def call(self, method, **p):
        self.calls.append(method)
        if method == "groups.getById":
            return {"groups": [{"id": 42, "name": "Студия Роза", "screen_name": "roza", "status": "маникюр и брови",
                                "description": "Лучший маникюр", "city": {"title": "Карталы"},
                                "contacts": [{"phone": "89120001122"}]}]}
        if method == "groups.getAddresses":
            t = {"open_time": 600, "close_time": 1200}
            return {"items": [{"address": "ул. Ленина, 5", "timetable": {k: t for k in vkdata.DAY_KEYS}}]}
        if method == "market.get":
            return {"items": [{"title": "Маникюр", "price": {"text": "1 500 ₽"}, "description": "с покрытием",
                               "thumb_photo": "https://x/1.jpg"}]}
        if method == "photos.getAll":
            return {"items": [{"sizes": [{"type": "s", "url": "https://x/s.jpg", "width": 75, "height": 75},
                                         {"type": "w", "url": "https://x/w.jpg", "width": 1280, "height": 960}]}]}
        raise vkdata.VKError("нет")


def test_vk_fetch():
    assert vkdata.screen_from_url("https://vk.com/roza?w=wall") == "roza"
    assert vkdata.screen_from_url("vk.ru/club123") == "club123"
    with pytest.raises(vkdata.VKError):
        vkdata.screen_from_url("https://example.com/a b")
    d = vkdata.fetch("vk.com/roza", FakeVK())
    f = d["fields"]
    assert f["name"] == "Студия Роза" and f["city"] == "Карталы" and f["phone"] == "89120001122"
    assert f["address"] == "ул. Ленина, 5" and f["hours"] == "ежедневно 10:00–20:00"
    assert d["items"][0]["price"] == "1 500 ₽" and d["photo_urls"] == ["https://x/w.jpg"]


def test_hours_format():
    t = {"mon": {"open_time": 600, "close_time": 1200}, "tue": {"open_time": 600, "close_time": 1200},
         "sat": {"open_time": 660, "close_time": 1080}}
    assert vkdata._hours(t) == "Пн–Вт 10:00–20:00, Сб 11:00–18:00"


def test_read_token(tmp_path):
    bot = tmp_path / "lstudio-bot"
    bot.mkdir()
    (bot / ".env").write_text("VK_USER_TOKEN=https://oauth.vk.com/blank.html#access_token=abc123&expires_in=0\n")
    me = tmp_path / "lstudio-demo"
    me.mkdir()
    (me / ".env").write_text("VK_USER_TOKEN=\n")
    assert vkdata.read_token(me) == "abc123"


# ---------- окно бота ----------
def test_server_upload_and_build(tmp_dirs):
    shots = []

    def fake_shoot(url, out_dir, tpl, log=print):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "pc-1-first-screen.png").write_bytes(PNG)
        shots.append(url)
        return ["pc-1-first-screen.png"]
    app = server.App(shooter=fake_shoot)
    job = app.new_job()
    url = app.upload(job, "photo.png", "data:image/png;base64," + base64.b64encode(PNG).decode())["url"]
    assert url.startswith(f"/files/{job}/")
    with pytest.raises(ValueError):
        app.upload("../x", "a.png", "data:image/png;base64,AA==")
    info = app.build({"template": "beauty", "fields": {"name": "Роза"}, "items": [], "photos": [url]})
    assert info["shots"] == ["pc-1-first-screen.png"] and shots[0].startswith("file:")
    assert app.reshoot(info["slug"])["shots"]
    app.delete(info["slug"])
    assert builder.history() == []


def test_server_vk_downloads(tmp_dirs, monkeypatch):
    monkeypatch.setattr(vkdata, "download", lambda u, dest, opener=None: dest.write_bytes(PNG) or True)
    monkeypatch.setattr(vkdata, "read_token", lambda base: "tok")
    app = server.App(vk_client_factory=FakeVK)
    res = app.from_vk("vk.com/roza", app.new_job())
    assert res["template"] == "beauty" and len(res["photos"]) == 1
    assert res["items"][0]["photo"].endswith("vk-item-01.jpg")


# ---------- обновление ----------
def test_updater_never_touches_user_data():
    for p in (".env", "data/uploads/a.jpg", "sites/x/index.html"):
        assert updater.is_protected(p)
    assert not updater.is_protected("demo/apply.js")


def test_server_vk_falls_back_to_browser(tmp_dirs, monkeypatch):
    monkeypatch.setattr(vkdata, "download", lambda u, dest, opener=None: dest.write_bytes(PNG) or True)
    monkeypatch.setattr(vkdata, "read_token", lambda base: "")
    app = server.App()
    app._page_fetch = lambda url, base, log=print, menu_url="": {
        "fields": {"name": "Цветы Флора", "tagline": "", "about": "", "city": "", "vk": url, "phone": "",
                   "address": "", "hours": ""},
        "items": [{"title": "Букет", "price": "3 500 ₽", "desc": "", "cat": "", "photo_url": "https://x/1.jpg"}],
        "photo_urls": ["https://x/2.jpg"], "activity": "", "notes": []}
    res = app.from_vk("vk.com/flora", app.new_job())
    assert res["template"] == "flowers" and res["items"][0]["photo"] and len(res["photos"]) == 1


def test_vkpage_parse_and_photos():
    from demo import vkpage
    assert vkpage.pick_name({"names": ["ВКонтакте", "Will Taste | ВКонтакте"]}) == "Will Taste"
    f = vkpage.parse_info({"names": ["ВКонтакте", "Will Taste"], "desc": "Кафе необистро",
                           "text": "Will Taste\nПодписаться\nг. Карталы, ул. Ленина, 14\nПн–Вс 10:00–22:00\n+7 (912) 000-11-22"})
    assert f["name"] == "Will Taste" and f["about"] == "Кафе необистро"
    assert f["address"] == "г. Карталы, ул. Ленина, 14" and f["hours"] == "Пн–Вс 10:00–22:00"
    assert f["phone"] == "+7 (912) 000-11-22"
    ph = vkpage._clean_photos([("https://sun9.userapi.com/a.jpg?size=50x50", 50),
                               ("https://sun9.userapi.com/b.jpg?size=807x605&amp;q=1", 0),
                               ("https://sun9.userapi.com/b.jpg?size=604x453", 0)], 10)
    assert ph == ["https://sun9.userapi.com/b.jpg?size=807x605&q=1"]
    assert vkpage._group_id('<a href="/wall-12345_67">') == "12345"


def test_vkpage_market_js_on_fake_page(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from demo import vkpage
    page_html = tmp_path / "m.html"
    page_html.write_text('''<html><body>
      <div class="card"><div><img src="https://sun9.userapi.com/p1.jpg?size=600x600"></div>
        <div><span>Букет из 15 роз</span><span>3 500 ₽</span></div></div>
      <div class="card"><div><img src="https://sun9.userapi.com/p2.jpg?size=600x600"></div>
        <div><span>Тюльпаны</span><span>от 2 900 ₽</span></div></div>
      <ul><li>Борщ со сметаной — 350 ₽</li><li>Морс 120 ₽</li></ul>
    </body></html>''', encoding="utf-8")
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(page_html.as_uri())
            items = pg.evaluate(vkpage.JS_MARKET)
            imgs = pg.evaluate(vkpage.JS_IMGS)
            b.close()
    except Exception as exc:
        pytest.skip(f"браузер недоступен: {exc}")
    assert [(i["title"], i["price"]) for i in items] == [("Букет из 15 роз", "3 500 ₽"), ("Тюльпаны", "от 2 900 ₽"),
                                                         ("Борщ со сметаной", "350 ₽"), ("Морс", "120 ₽")]
    assert items[0]["photo"].endswith("p1.jpg?size=600x600") and len(imgs) == 2
