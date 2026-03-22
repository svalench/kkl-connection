# kkl-connection

Библиотека и утилита для диагностики автомобилей **VAG** (VW, Audi, Skoda, Seat) через **USB KKL**-кабель по линии **K-Line**: **ISO 9141-2**, **KWP2000 (ISO 14230)** и базовая поддержка **KWP1281 (KW1281)**.

## Возможности

- Низкоуровневый драйвер: 5-baud slow init, KWP2000 fast init, handshake, повторные попытки инициализации, keepalive, учёт таймингов ISO.
- Эмулятор **ELM323** поверх KKL: отправка OBD-команд в hex, сборка кадров ISO/KWP, снятие эхо с линии, автопереподключение при серии ошибок.
- Консольный вывод параметров по **OBD PID** с форматированием.
- Опциональная настройка **FTDI** (latency timer на Linux).
- Тесты на `pytest`.

## Требования

- **Python 3.10+**
- **[pyserial](https://pypi.org/project/pyserial/)** ≥ 3.5

Важно: не устанавливайте пакет **`serial`** с PyPI — это другой проект. Нужен именно **`pyserial`** (модуль импортируется как `serial`, но пакет называется `pyserial`).

## Установка

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Запуск (CLI)

```bash
python main.py -p /dev/ttyUSB0          # Linux
python main.py -p COM3                  # Windows
python main.py -p /dev/ttyUSB0 -v       # подробные логи
python main.py -p /dev/ttyUSB0 -b 9600  # если ЭБУ на 9600
python main.py --no-fast-init           # только ISO 9141-2 (без KWP fast init)
python main.py --pids 010D,010C,0105    # свои PID (через запятую)
python main.py --pids 03                # режим 03 — сохранённые DTC
python main.py --latency              # попытка FTDI latency 1 ms (Linux)
python main.py -i 0.5                 # интервал опроса, с
```

### Аргументы `main.py`

| Аргумент | Описание |
|----------|----------|
| `-p`, `--port` | Последовательный порт (по умолчанию `COM1`) |
| `-b`, `--baud` | Скорость, по умолчанию **10400** (K-Line) |
| `-i`, `--interval` | Период опроса в секундах |
| `-v`, `--verbose` | Уровень логирования DEBUG |
| `--no-fast-init` | Отключить KWP2000 fast init |
| `--pids` | Список PID/сервисов через запятую |
| `--latency` | Настройка FTDI latency (см. `kkl.configure_ftdi_port`) |

## Структура проекта

| Файл | Назначение |
|------|------------|
| `kkl.py` | Драйвер K-Line: init, кадры, чтение, keepalive, FTDI |
| `elm323_emulator.py` | Эмулятор ELM323 поверх `kkl` |
| `obd_display.py` | Форматтеры PID и цикл вывода в терминал |
| `kwp1281.py` | Черновая поддержка KW1281 (VAG до ~2004) |
| `exceptions.py` | Исключения слоя KKL |
| `main.py` | Точка входа CLI |
| `tests/` | Юнит-тесты |

## Использование из кода

```python
from elm323_emulator import ELM323Emulator

emu = ELM323Emulator(port="/dev/ttyUSB0", baud=10400, verbose=True)
ok, err, lines = emu.send_obd("01 0C")
emu.close()
```

```python
from kkl import init_bus, send_frame, read_response, close, send_keepalive

ser, err, proto = init_bus("/dev/ttyUSB0")
if ser:
    # proto: "kwp2000" или "iso9141"
    send_keepalive(ser, proto)
    close(ser)
```

Модуль `kwp1281` — отдельный сценарий (другой протокол и часто **9600** бод после 5-baud init); см. docstring в `kwp1281.py`.

## Тесты

```bash
pytest tests/ -v
```

## Совместимость

- ОС: **Windows**, **Linux** (macOS — при наличии USB-UART с корректным драйвером).
- Кабели: FTDI FT232RL (рекомендуется с `--latency` на Linux), Prolific, CH340 и аналоги.
- Авто: типичный диапазон **1995–2010** по K-Line (до массового CAN).

## Лицензия и дисклеймер

Проект предназначен для образовательных и сервисных задач. Работа с ЭБУ на работающем автомобиле — на ваш риск; соблюдайте правила безопасности и законодательства.
