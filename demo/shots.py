"""Скриншоты готового демо-сайта: компьютер и телефон, первый экран и вся страница кусками."""
import subprocess
import sys
from pathlib import Path

SHOT_CSS = """
html{scroll-behavior:auto!important}
*,*::before,*::after{animation-delay:0s!important;animation-duration:0s!important;transition:none!important;caret-color:transparent!important}
.reveal,[class*="reveal"],[data-aos],.fade-up,.fade-in{opacity:1!important;transform:none!important;visibility:visible!important}
"""

MODES = (
    # имя, ширина, высота, масштаб, телефон, высота куска «всей страницы»
    ("pc", 1440, 900, 1, False, 2700),
)


def ensure_playwright(log=print):
    try:
        import playwright.sync_api  # noqa: F401
        return
    except ImportError:
        pass
    log("Устанавливаю модуль для скриншотов (один раз, около минуты)…")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "playwright"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Не удалось установить playwright: " + (r.stderr or r.stdout)[-300:])
    import importlib
    importlib.invalidate_caches()


def _launch(pw, log=print):
    last = None
    for channel in ("chrome", "msedge", None):
        try:
            kw = {"headless": True}
            if channel:
                kw["channel"] = channel
            return pw.chromium.launch(**kw)
        except Exception as exc:
            last = exc
    log("Скачиваю браузер для скриншотов (один раз, ~150 МБ)…")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)
    try:
        return pw.chromium.launch(headless=True)
    except Exception as exc:
        raise RuntimeError(f"Не удалось открыть браузер: {exc or last}")


def _prepare_page(page, url, tpl):
    page.route("**/mc.yandex.ru/**", lambda r: r.abort())
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        page.goto(url, wait_until="load", timeout=30000)
    page.wait_for_timeout(700)
    for step in tpl.get("before_shot", []):
        try:
            if step.get("click_text"):
                page.get_by_text(step["click_text"], exact=True).first.click(timeout=4000)
            elif step.get("click"):
                page.locator(step["click"]).first.click(timeout=4000)
            elif step.get("eval"):
                page.evaluate(step["eval"])
            page.wait_for_timeout(step.get("wait", 600))
        except Exception:
            pass
    page.add_style_tag(content=SHOT_CSS + tpl.get("shot_css", ""))
    page.evaluate("window.__demoRun && window.__demoRun()")
    # прокрутка до конца — чтобы подгрузились картинки и сработали анимации появления
    page.evaluate("""async () => {
        const h = () => document.documentElement.scrollHeight;
        for (let y = 0; y < h(); y += Math.max(300, innerHeight * 0.8)) {
            scrollTo(0, y); await new Promise(r => setTimeout(r, 90));
        }
        scrollTo(0, 0);
    }""")
    page.wait_for_timeout(900)
    page.evaluate("window.__demoRun && window.__demoRun()")
    page.wait_for_timeout(300)


def shoot(url: str, out_dir: Path, tpl: dict, log=print, max_parts=8) -> list:
    """Делает скриншоты страницы url в папку out_dir. Возвращает имена файлов."""
    ensure_playwright(log)
    from playwright.sync_api import sync_playwright
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.*"):
        old.unlink()
    files = []
    with sync_playwright() as pw:
        browser = _launch(pw, log)
        try:
            for name, w, h, scale, mobile, part in MODES:
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=scale,
                                          is_mobile=mobile, has_touch=mobile, locale="ru-RU")
                page = ctx.new_page()
                _prepare_page(page, url, tpl)
                first = f"{name}-1-first-screen.png"
                page.screenshot(path=str(out_dir / first))
                files.append(first)
                total = page.evaluate("document.documentElement.scrollHeight")
                n = 0
                for top in range(0, total, part):
                    n += 1
                    if n > max_parts:
                        break
                    fname = f"{name}-2-page-{n:02d}.jpg"
                    page.screenshot(path=str(out_dir / fname), full_page=True, type="jpeg", quality=85,
                                    clip={"x": 0, "y": top, "width": w, "height": min(part, total - top)})
                    files.append(fname)
                ctx.close()
        finally:
            browser.close()
    return files
