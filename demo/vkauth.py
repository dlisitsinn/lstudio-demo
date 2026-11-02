"""Подключение ВК через окно Chrome: вы входите во ВК и нажимаете «Разрешить», бот запоминает доступ.

Окно открывается с отдельным профилем браузера (папка data/vk-browser), как у основного бота.
Ключ доступа сохраняется в .env (строка VK_USER_TOKEN) и больше не спрашивается.
"""
import re
import time
from pathlib import Path

from . import shots

OAUTH_URL = ("https://oauth.vk.com/authorize?client_id=2685278&display=page"
             "&redirect_uri=https://oauth.vk.com/blank.html&scope=groups,photos,market,offline"
             "&response_type=token&v=5.199")
TOKEN_RE = re.compile(r"access_token=([^&#\s]+)")


def save_token(env: Path, token: str):
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    out, done = [], False
    for line in lines:
        if line.strip().startswith("VK_USER_TOKEN="):
            out.append("VK_USER_TOKEN=" + token)
            done = True
        else:
            out.append(line)
    if not done:
        out.append("VK_USER_TOKEN=" + token)
    env.write_text("\n".join(out) + "\n", encoding="utf-8")


def connect(base: Path, log=print, timeout=300, headless=False, url=OAUTH_URL) -> str:
    """Открывает окно Chrome со страницей разрешения ВК и ждёт, пока вы нажмёте «Разрешить»."""
    shots.ensure_playwright(log)
    from playwright.sync_api import sync_playwright
    profile = base / "data" / "vk-browser"
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx, last = None, None
        for channel in ("chrome", "msedge", None):
            try:
                kw = {"headless": headless, "viewport": None}
                if channel:
                    kw["channel"] = channel
                ctx = pw.chromium.launch_persistent_context(str(profile), **kw)
                break
            except Exception as exc:
                last = exc
        if ctx is None:
            raise RuntimeError(f"Не удалось открыть Chrome: {last}")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            log("Войдите во ВК в открывшемся окне и нажмите «Разрешить»")
            page.goto(url, wait_until="domcontentloaded")
            end = time.time() + timeout
            while time.time() < end:
                for p in ctx.pages:
                    m = TOKEN_RE.search(p.url or "")
                    if m:
                        token = m.group(1)
                        save_token(base / ".env", token)
                        return token
                if not ctx.pages:
                    raise ValueError("Окно закрыли до того, как нажали «Разрешить»")
                try:
                    ctx.pages[0].wait_for_timeout(500)   # даём браузеру обработать события
                except Exception:
                    time.sleep(0.5)
            raise ValueError("Не дождалась нажатия «Разрешить» (5 минут). Попробуйте ещё раз")
        finally:
            try:
                ctx.close()
            except Exception:
                pass
