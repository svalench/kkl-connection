"""
Форматтеры OBD PID и вывод в терминал.
Преобразует сырые ответы в человекочитаемый вид.
"""
from __future__ import annotations

import time
from typing import Callable


def _parse_hex_line(line: str) -> list[int]:
    """Разобрать строку вида '41 05 7B' в список байт."""
    parts = line.strip().split()
    result = []
    for p in parts:
        p = p.strip()
        if p and len(p) <= 2:
            try:
                result.append(int(p, 16))
            except ValueError:
                pass
    return result


# Форматтеры: (bytes) -> str | None. None = нет данных / не применимо
def fmt_pid_00(data: list[int]) -> str | None:
    """Поддерживаемые PID (битовая маска)."""
    if len(data) < 5:
        return None
    # data[0]=41, data[1]=00, data[2:6] - 4 байта маски
    mask = data[2:6] if len(data) >= 6 else data[2:]
    pids = []
    for i, byte in enumerate(mask):
        for bit in range(8):
            if byte & (1 << (7 - bit)):
                pid = (i * 8 + bit) + 1
                if pid <= 32:
                    pids.append(f"01 {pid:02X}")
    return f"Поддерживаемые PID: {', '.join(pids)}" if pids else "Поддерживаемые PID: —"


def fmt_pid_01(data: list[int]) -> str | None:
    """Коды неисправностей / MIL."""
    if len(data) < 3:
        return None
    b = data[2]
    mil = "вкл" if (b & 0x80) else "выкл"
    count = b & 0x7F
    return f"MIL: {mil}, кодов неисправностей: {count}"


def fmt_pid_04(data: list[int]) -> str | None:
    """Расчётная нагрузка (%), A*100/255."""
    if len(data) < 3:
        return None
    val = round(data[2] * 100 / 255)
    return f"Нагрузка: {val} %"


def fmt_pid_05(data: list[int]) -> str | None:
    """Температура ОЖ, A-40 °C."""
    if len(data) < 3:
        return None
    val = data[2] - 40
    return f"Температура ОЖ: {val} °C"


def fmt_pid_0C(data: list[int]) -> str | None:
    """Обороты, ((A*256)+B)/4 об/мин."""
    if len(data) < 4:
        return None
    rpm = ((data[2] * 256) + data[3]) // 4
    return f"Обороты: {rpm} об/мин"


def fmt_pid_0D(data: list[int]) -> str | None:
    """Скорость, A км/ч."""
    if len(data) < 3:
        return None
    return f"Скорость: {data[2]} км/ч"


def fmt_pid_0F(data: list[int]) -> str | None:
    """Температура воздуха на впуске, A-40 °C."""
    if len(data) < 3:
        return None
    val = data[2] - 40
    return f"Темп. впуска: {val} °C"


def fmt_pid_11(data: list[int]) -> str | None:
    """Положение дросселя, A*100/255 %."""
    if len(data) < 3:
        return None
    val = round(data[2] * 100 / 255)
    return f"Дроссель: {val} %"


def fmt_pid_2F(data: list[int]) -> str | None:
    """Уровень топлива, A*100/255 %."""
    if len(data) < 3:
        return None
    val = round(data[2] * 100 / 255)
    return f"Уровень топлива: {val} %"


def fmt_pid_5C(data: list[int]) -> str | None:
    """Напряжение OBD, (A*256+B)/1000 В."""
    if len(data) < 4:
        return None
    v = ((data[2] * 256) + data[3]) / 1000
    return f"Напряжение: {v:.1f} В"


# Маппинг PID -> (название, форматтер)
# Ключ без пробела: "0100", "010C" и т.д.
OBD_FORMATTERS: dict[str, tuple[str, Callable[[list[int]], str | None]]] = {
    "0100": ("Поддержка PID", fmt_pid_00),
    "0101": ("MIL / DTC", fmt_pid_01),
    "0104": ("Нагрузка", fmt_pid_04),
    "0105": ("Темп. ОЖ", fmt_pid_05),
    "010C": ("Обороты", fmt_pid_0C),
    "010D": ("Скорость", fmt_pid_0D),
    "010F": ("Темп. впуска", fmt_pid_0F),
    "0111": ("Дроссель", fmt_pid_11),
    "012F": ("Уровень топлива", fmt_pid_2F),
    "015C": ("Напряжение", fmt_pid_5C),
}

# PID для живого отображения (приоритет)
LIVE_PIDS = ["010D", "015C", "010C", "0105", "0104", "0111", "012F", "010F"]


def format_pid_response(pid_key: str, response_lines: list[str]) -> str | None:
    """
    Преобразовать ответ по PID в человекочитаемую строку.
    pid_key: "01 0D" или "010D"
    """
    pid_key = pid_key.replace(" ", "").upper()
    if pid_key not in OBD_FORMATTERS:
        return None
    _, formatter = OBD_FORMATTERS[pid_key]
    for line in response_lines:
        data = _parse_hex_line(line)
        if len(data) >= 3 and data[0] == 0x41 and data[1] == int(pid_key[2:], 16):
            return formatter(data)
    return None


def get_controllers_info(emulator) -> str:
    """
    Определить доступные контроллеры по ответу на 01 00.
    """
    success, err, lines = emulator.send_obd("01 00")
    if not success:
        return f"Контроллеры: недоступны ({err})"
    if not lines:
        return "Контроллеры: недоступны (нет ответа)"
    data = _parse_hex_line(lines[0])
    if len(data) >= 6:
        # ЭБУ ответил — по умолчанию считаем, что доступен двигатель (адрес 0x33)
        return "Контроллеры: ЭБУ двигателя (OBD2) — OK"
    return "Контроллеры: —"


def run_display_loop(emulator, interval_sec: float = 1.0, pids: list[str] | None = None) -> None:
    """
    Цикл опроса и вывода в терминал.
    Сначала выводит список контроллеров, затем периодически — параметры.
    """
    pids = pids or LIVE_PIDS
    print("\n--- Доступные контроллеры ---")
    print(get_controllers_info(emulator))
    print("\n--- Текущие данные ---")

    try:
        while True:
            for pid in pids:
                pid_spaced = f"01 {pid[2:4]}" if len(pid) == 4 else pid
                success, err, lines = emulator.send_obd(pid_spaced)
                if success and lines:
                    text = format_pid_response(pid_spaced, lines)
                    if text:
                        print(f"  {text}")
                    else:
                        name = OBD_FORMATTERS.get(pid.replace(" ", ""), ("?", lambda _: None))[0]
                        print(f"  {name}: —")
                else:
                    name = OBD_FORMATTERS.get(pid.replace(" ", ""), ("?", lambda _: None))[0]
                    print(f"  {name}: нет данных")
            print("  ---")
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\nОстановлено по Ctrl+C")
