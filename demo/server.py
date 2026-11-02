"""Окно бота: маленький веб-сервер на компьютере (http://127.0.0.1:8766)."""
import base64
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import builder, shots, vkdata, vkpage

STATIC = Path(__file__).resolve().parent / "static"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
MAX_UPLOAD = 25 * 1024 * 1024


class App:
    def __init__(self, base: Path = builder.BASE, vk_client_factory=None, shooter=None):
        self.base = base
        self.build_lock = threading.Lock()
        self.status = ""
        self._vk_factory = vk_client_factory or (lambda: vkdata.Client(vkdata.read_token(self.base)))
        self._shoot = shooter or shots.shoot
        self._page_fetch = vkpage.fetch

    # ---------- загрузки ----------
    @staticmethod
    def new_job() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)

    @staticmethod
    def _job_dir(job: str) -> Path:
        if not re.fullmatch(r"[\w-]{1,40}", job or ""):
            raise ValueError("плохой номер загрузки")
        d = builder.UPLOADS / job
        d.mkdir(parents=True, exist_ok=True)
        return d

    def upload(self, job: str, name: str, data_url: str) -> dict:
        m = re.match(r"data:([\w/+.-]+);base64,(.*)$", data_url or "", re.S)
        if not m:
            raise ValueError("Файл не прочитался")
        raw = base64.b64decode(m.group(2))
        if len(raw) > MAX_UPLOAD:
            raise ValueError("Фото больше 25 МБ")
        ext = Path(name or "").suffix.lower()
        if ext not in IMG_EXT:
            ext = mimetypes.guess_extension(m.group(1)) or ".jpg"
            ext = ".jpg" if ext == ".jpe" else ext
        d = self._job_dir(job)
        fname = f"{len(list(d.iterdir())) + 1:03d}-{secrets.token_hex(3)}{ext}"
        (d / fname).write_bytes(raw)
        return {"url": f"/files/{job}/{fname}"}

    # ---------- ВК ----------
    def from_vk(self, url: str, job: str) -> dict:
        data = None
        if vkdata.read_token(self.base):          # есть токен — сначала через API, это быстрее
            try:
                data = vkdata.fetch(url, self._vk_factory())
            except vkdata.VKError:
                data = None
        if data is None:                          # иначе — читаем страницу группы в окне Chrome
            self.status = "Открываю группу ВК…"
            try:
                data = self._page_fetch(url, self.base, log=lambda m: setattr(self, "status", m))
            finally:
                self.status = ""
        d = self._job_dir(job)
        photos = []
        for i, u in enumerate(data["photo_urls"]):
            f = d / f"vk-{i + 1:02d}.jpg"
            if vkdata.download(u, f):
                photos.append(f"/files/{job}/{f.name}")
        items = []
        for i, it in enumerate(data["items"]):
            photo = ""
            if it.get("photo_url"):
                f = d / f"vk-item-{i + 1:02d}.jpg"
                if vkdata.download(it["photo_url"], f):
                    photo = f"/files/{job}/{f.name}"
            items.append({"title": it["title"], "price": it["price"], "desc": it["desc"], "cat": it["cat"],
                          "photo": photo})
        f = data["fields"]
        guess = builder.guess_template(" ".join([f["name"], data.get("activity", ""), f["tagline"], f["about"][:400]]))
        return {"fields": f, "items": items, "photos": photos, "template": guess, "notes": data["notes"]}

    def vk_connected(self) -> bool:
        return vkpage.marker(self.base).exists()

    def vk_connect(self) -> dict:
        self.status = "Открываю Chrome…"
        try:
            vkpage.login(self.base, log=lambda m: setattr(self, "status", m))
        finally:
            self.status = ""
        return {"ok": True}

    # ---------- сборка ----------
    def build(self, req: dict) -> dict:
        tid = req.get("template") or builder.guess_template((req.get("fields") or {}).get("name", "")) or "beauty"
        if not self.build_lock.acquire(timeout=1):
            raise ValueError("Уже собираю другой сайт — подождите минуту")
        try:
            self.status = "Собираю сайт…"
            info = builder.build(tid, req.get("fields") or {}, req.get("items") or [], req.get("photos") or [])
            return self._shots_for(info)
        finally:
            self.status = ""
            self.build_lock.release()

    def reshoot(self, slug: str) -> dict:
        info = self._info(slug)
        with self.build_lock:
            return self._shots_for(info)

    def _shots_for(self, info: dict) -> dict:
        out = builder.SITES / info["slug"]
        tpl = builder.get_template(info["template"])
        self.status = "Делаю скриншоты…"
        files = self._shoot((out / info.get("page", "index.html")).as_uri(), out / "screens", tpl,
                            log=lambda m: setattr(self, "status", m))
        info["shots"] = files
        return info

    def _info(self, slug: str) -> dict:
        if not re.fullmatch(r"[\w.-]+", slug or ""):
            raise ValueError("плохое имя сайта")
        f = builder.SITES / slug / "demo" / "info.json"
        if not f.exists():
            raise ValueError("Сайт не найден")
        return json.loads(f.read_text(encoding="utf-8"))

    def open_folder(self, slug: str, sub: str = "screens"):
        self._info(slug)
        path = builder.SITES / slug / sub
        path = path if path.exists() else builder.SITES / slug
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606 — открываем папку в проводнике
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"ok": True}

    def delete(self, slug: str):
        self._info(slug)
        shutil.rmtree(builder.SITES / slug, ignore_errors=True)
        return {"ok": True}


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _file(self, root: Path, rel: str):
            p = (root / unquote(rel)).resolve()
            if root.resolve() not in p.parents and p != root.resolve():
                return self._send(403, b"forbidden", "text/plain")
            if p.is_dir():
                p = p / "index.html"
            if not p.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(200, p.read_bytes(), ctype)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._file(STATIC, "index.html")
            if path.startswith("/static/"):
                return self._file(STATIC, path[len("/static/"):])
            if path.startswith("/files/"):
                return self._file(builder.UPLOADS, path[len("/files/"):])
            if path.startswith("/sites/"):
                return self._file(builder.SITES, path[len("/sites/"):])
            if path == "/api/templates":
                return self._json([{"id": t["id"], "title": t.get("title_ui", t["id"]), "repo": t["repo"]}
                                   for t in builder.list_templates()])
            if path == "/api/history":
                return self._json(builder.history())
            if path == "/api/status":
                return self._json({"status": app.status, "busy": app.build_lock.locked()})
            if path == "/api/vk_state":
                return self._json({"connected": app.vk_connected()})
            if path == "/api/job":
                return self._json({"job": app.new_job()})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 40 * 1024 * 1024:
                    return self._json({"error": "Слишком большой файл"}, 413)
                data = json.loads(self.rfile.read(n) or b"{}")
                if path == "/api/upload":
                    res = app.upload(data.get("job"), data.get("name"), data.get("data"))
                elif path == "/api/vk_connect":
                    res = app.vk_connect()
                elif path == "/api/vk":
                    res = app.from_vk(data.get("url"), data.get("job"))
                elif path == "/api/parse":
                    res = {"items": builder.parse_items_text(data.get("text"))}
                elif path == "/api/guess":
                    res = {"template": builder.guess_template(data.get("text"))}
                elif path == "/api/build":
                    res = app.build(data)
                elif path == "/api/reshoot":
                    res = app.reshoot(data.get("slug"))
                elif path == "/api/open":
                    res = app.open_folder(data.get("slug"), data.get("sub") or "screens")
                elif path == "/api/delete":
                    res = app.delete(data.get("slug"))
                else:
                    return self._json({"error": "нет такой команды"}, 404)
                self._json(res)
            except (ValueError, vkdata.VKError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:  # показываем человеку понятную ошибку, подробности — в консоль
                traceback.print_exc()
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    return H


def serve(app: App, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
