# TerraNore Test

Локальный тестовый стенд детерминированной экономической симуляции. Ядро не
зависит от HTTP, WebSocket или DOM и может использоваться отдельно для быстрых
прогонов.

## Запуск

```bash
./BUILD/build
```

В Windows можно запустить `BUILD\build.exe`. Сервер выберет свободный локальный
порт, напечатает адрес `http://127.0.0.1:<port>` и попытается открыть браузер.
Чтобы только вывести адрес (например, в CI), используйте:

```bash
python -m src.server --no-browser --port 8080
```

## Проверки и быстрый прогон ядра

```bash
python -m unittest discover -s tests -v
python -m src.simulation --years 100 --seed 42
```

Тестовый HTTP API размещён под `/api/test/v1`, WebSocket — по адресу
`/api/test/v1/ws`, формат сохранения — `test-save-v1`.

## Структура

* `src/simulation/model/` — состояния мира и экономики;
* `src/simulation/systems/` — последовательные фазы тика;
* `src/simulation/lod/` — правила детализации;
* `src/simulation/workers/` — детерминированный планировщик;
* `src/server/` — локальный API;
* `src/ui/` — браузерный интерфейс;
* `src/persistence/` — снимки и временные ряды;
* `tests/` и `BUILD/` — проверки и команды запуска.
