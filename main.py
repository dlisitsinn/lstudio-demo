"""Демо-бот LStudio: собирает сайт-образец под клиента и делает скриншоты.

    python main.py              — открыть окно в браузере
    python main.py --no-browser — запустить без окна
"""
import sys
import threading
import time
import webbrowser

from demo.builder import BASE
from demo.server import App, serve

PORT = 8766


def main():
    env, example = BASE / ".env", BASE / ".env.example"
    if not env.exists() and example.exists():
        env.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    url = f"http://127.0.0.1:{PORT}"
    try:
        server = serve(App(), PORT)
    except OSError:
        print(f"Похоже, демо-бот уже запущен. Окно: {url}")
        webbrowser.open(url)
        time.sleep(3)
        return
    print("🌸 Демо-бот LStudio запущен.")
    print(f"   Окно: {url}  (откроется в браузере само)")
    print("   Не закрывайте это чёрное окно, пока работаете.\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nДемо-бот остановлен.")


if __name__ == "__main__":
    main()
