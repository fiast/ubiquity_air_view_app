# airView Analyzer

Спектроанализатор для точек Ubiquiti airOS 6.x: живой спектр, Max Hold, Average и водопад
(PySide6 + PyQtGraph + NumPy). Работает с реальным устройством по TCP 18888 или  с
синтетикой/записью.

![Главное окно с живой точки: PowerBeam M5 400, 4920–6100 МГц](docs/screenshots/)

## Установка (Windows)

Готовый exe — в разделе **[Releases](https://github.com/fiast/ubiquity_air_view_app/releases)**:
скачайте `AirViewAnalyzer-win64.zip`, распакуйте и запустите `AirViewAnalyzer.exe`.

> SmartScreen предупредит о «неопознанном приложении» — сборка не подписана сертификатом.
> Снять блокировку: `Get-ChildItem .\AirViewAnalyzer -Recurse | Unblock-File`, затем
> «Дополнительные сведения → Выполнить всё равно».

## Быстрый старт

```powershell
python -m pip install -r requirements.txt

# GUI с точкой
python -m airview.main --host 192.168.1.20 --user manager --password "..."

# GUI без устройства
python -m airview.main --mock

# Проверка связи и метаданных устройства (без GUI)
python -m airview.main --check --host 192.168.1.20 --user manager --password "..."

# Запись дампа без GUI (Ctrl+C — остановка, --frames N — по числу кадров)
python -m airview.main --headless --frames 100 --host 192.168.1.20 --user manager --password "..." --record dumps\test.dat

# Воспроизведение записи (в репозитории есть пример: samples/powerbeam-m5-5150-5850.dat)
python -m airview.main --replay samples\powerbeam-m5-5150-5850.dat

# Сборка exe
.\build.ps1
```

Все параметры дублируются в GUI и сохраняются в `airview_settings.json` рядом с exe/скриптом.

## Готовый exe

`dist\AirViewAnalyzer\AirViewAnalyzer.exe` — собранная версия (Windows x64, Python 3.12, PySide6 6.12).
Переносится папкой `AirViewAnalyzer` целиком (рядом `_internal`). При первом запуске рядом
появится `airview_settings.json`.

Пересборка:

```powershell
.\build.bat            # или: powershell -ExecutionPolicy Bypass -File .\build.ps1
.\build.bat -Console   # консольная сборка — в ней видны логи и traceback
```

Если PowerShell ругается на ExecutionPolicy — используйте `build.bat`, он запускает
`build.ps1` с обходом политики.


## Факты с реального устройства (PowerBeam M5 400, XW.v6.3.11)

- Возможные диапазоны: **4920–6100 МГц** (только 5 ГГц; 2.4 ГГц у этого железа нет).
  Список пресетов приходит в `CONFIGURATION`, в UI он строится динамически.
- Ширина бина фиксирована: **312.5 кГц**. Число бинов = `(end − start) / 312500`:
  5150–5250 → 320, 5150–5850 → 2240, 4920–6100 → 3776.
- Частота кадров зависит от ширины: ~7.5 fps для 3776 бинов и ~53 fps для 320
  (устройство сканирует ~16–17 тыс. бинов/с).
- Порт 18888 открывается через **~11–13 с** после Launch; после отключения клиента
  AirView гаснет сам, веб-интерфейс на 30–60 с пропадает (перезагрузка радио) —
  клиент это переживает повторами логина.
- Неверный диапазон не даёт ошибку: устройство молча обрезает его до допустимого
  (`SCAN RANGE` в ответ). Клиент работает по фактически полученному диапазону.


## Что важно знать про airOS

- AirView запускается **только через веб-интерфейс** (`GET https://host/airview.cgi?start=1`),
  до этого порт 18888 закрыт. Порт открывается через ~11–13 с.
- В режиме AirView **радиоточка выключает радио** — управляйте ею по кабелю, не по Wi-Fi.
- Сертификат самоподписанный: клиент работает без проверки TLS (как и оригинал).
- Одновременно может быть активен только один AirView. Если он уже запущен, приложение
  спросит: подключиться (Attach) или перезапустить.

## Архитектура

```
airview/
  protocol.py      — разбор CONFIGURATION / SCAN RANGE / FRAME (ASCII-протокол)
  config.py        — Settings, JSON, argparse (GUI и CLI используют одно и то же)
  datasource.py    — DataSource (ABC), MockDataSource, ReplayDataSource
  airview_client.py— веб-логин → Launch → TCP 18888 → кадры, дампы, реконнект
  worker.py        — QThread: кадры/статусы/ошибки через сигналы
  ui.py            — окно: графики, водопад, панель подключения
  main.py          — GUI / --check / --headless
```

Источник данных подменяем: `SpectrumWorker` не знает, откуда кадры, а `AirViewClient`
реализует тот же `DataSource`, что и Mock.

## Протокол (снят с PowerBeam M5 400, XW.v6.3.11)

```
>> CONNECT: \n
<< CONFIGURATION: 2,<fw>,<build>,<id>,<model>,<model_full>,<mac>,...
     13-е поле — пресеты "start_hz|end_hz|label" через "~"
     14-е поле — ширина бина, Гц (312500)
     21..23    — min dBm, min/max MHz
>> REQUEST RANGE: 5150000000,5250000000\n
<< SCAN RANGE: 5150000000,5250000000\n
>> START SCAN: \n
<< RESULT: 0\n
>> GET FRAME: \n
<< FRAME: <номер>,<v1>,...,<vN>\n     (N = (end-start)/bin_hz, значения — dBm)
```

Особенности: кадры повторяются — клиент дедуплицирует по номеру; неизвестная команда
даёт пустой ответ; недопустимый диапазон молча обрезается устройством (клиент берёт
фактический `SCAN RANGE`). Частота кадров ~7.5 fps.

## Дампы

`.dat` — строки `таймстамп\x01строка` (формат совместим с `airviewer.py`), пишутся запросы
и ответы. С `--diagnostics` рядом кладётся `.log` с hex-дампом первых 256 байт каждого пакета.

## Отладка, если формат не совпал

1. `python -m airview.main --check ...` — видно, что вернулось на CONNECT и корректно ли распознан диапазон.
2. Включите `--diagnostics` и запустите `--headless --record dumps\raw.dat` — в `dumps\raw.log`
   будет байт-в-байт каждый обмен (что реально пришло и с каким таймстампом).
3. Если `CONNECT` вернул не `CONFIGURATION` — снимите трафик между Java-клиентом и точкой:
   `Wireshark → tcp.port == 18888 → Follow TCP Stream`. Сравните команды/ответы с этим README.
4. Сравните с оригиналом: `airviewer.py` (AnyWi) использует те же команды; разница — в парсинге.
5. Если устройство отвечает иначе (другая прошивка), правьте только `airview/protocol.py`:
   парсеры `parse_configuration` / `parse_frame` / `parse_scan_range` — единственное место,
   где зашит формат.

## Лицензия

Код приложения — свободный. Логика протокола сверена с `py-ubnt-airviewer`
(© AnyWi Technologies BV, BSD-2-Clause); оригинальный copyright сохранён в этом репозитории.
