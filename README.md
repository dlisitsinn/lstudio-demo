# LStudio — демо-бот

Собирает демо-сайт для клиента на основе готовых сайтов-образцов (velvet, radiant-ride, pure-vision, cafe-astoria, sally) и делает скриншоты для компьютера и телефона.

Запуск: «Запустить демо-бота.bat» (Windows) или `run.command` (Mac). Окно: http://127.0.0.1:8766

Подробно — в файле ИНСТРУКЦИЯ.md.

Устройство:
- `templates/*.json` — какой образец брать и что в нём заменять (селекторы, списки, фото);
- `demo/apply.js` — подставляет данные клиента в образец прямо в браузере;
- `demo/builder.py` — скачивает образец с GitHub, готовит данные, собирает папку сайта;
- `demo/shots.py` — скриншоты через Playwright (Chrome / Edge);
- `demo/vkdata.py` — данные из группы ВК;
- `demo/server.py`, `demo/static/index.html` — окно бота.
