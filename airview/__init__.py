"""airView Analyzer — аналог Ubiquiti airView для airOS 6.x.

Модули:
    config        — настройки и командная строка
    protocol      — разбор текстового протокола airView (CONFIGURATION/FRAME/SCAN RANGE)
    datasource    — абстрактный источник данных, Mock и Replay
    airview_client— TCP/HTTP-клиент к точке (порт 18888 + веб-логин)
    worker        — QThread-обёртка для GUI
    ui            — главное окно (PySide6 + PyQtGraph)
    main          — точка входа (GUI / headless / check)
"""

__version__ = "1.0.0"
