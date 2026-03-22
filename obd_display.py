"""
Форматтеры OBD PID и вывод в терминал.
Преобразует сырые ответы в человекочитаемый вид.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger("obd_display")


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


def _decode_dtc_pair(b1: int, b2: int) -> str:
    """Два байта DTC по SAE J2012 (режим 03 / 07)."""
    types = "PCBU"
    c_type = (b1 >> 6) & 0x03
    d2 = (b1 >> 4) & 0x03
    d3 = b1 & 0x0F
    d4 = (b2 >> 4) & 0x0F
    d5 = b2 & 0x0F
    return f"{types[c_type]}{d2}{d3:X}{d4:X}{d5:X}"


# Форматтеры: (bytes) -> str | None. None = нет данных / не применимо
def fmt_pid_00(data: list[int]) -> str | None:
    """Поддерживаемые PID (битовая маска)."""
    if len(data) < 5:
        return None
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


def fmt_pid_03(data: list[int]) -> str | None:
    """Сохранённые DTC / P-коды (ответ режима 03: префикс 0x43)."""
    if len(data) < 2 or data[0] != 0x43:
        return None
    codes = []
    i = 1
    while i + 1 < len(data):
        hi, lo = data[i], data[i + 1]
        if hi == 0 and lo == 0:
            break
        codes.append(_decode_dtc_pair(hi, lo))
        i += 2
    return "DTC (сохр.): " + (", ".join(codes) if codes else "нет")


def fmt_pid_01_03_fuel(data: list[int]) -> str | None:
    """Режим 01 PID 03 — статус топливной системы (A, B)."""
    if len(data) < 4:
        return None
    if data[0] != 0x41 or data[1] != 0x03:
        return None
    return f"Статус топлива: A=0x{data[2]:02X} B=0x{data[3]:02X}"


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


def fmt_pid_06(data: list[int]) -> str | None:
    """STFT Bank1, (A-128)*100/128 %."""
    if len(data) < 3:
        return None
    pct = round((data[2] - 128) * 100 / 128)
    return f"STFT (банк 1): {pct} %"


def fmt_pid_07(data: list[int]) -> str | None:
    """LTFT Bank1, (A-128)*100/128 %."""
    if len(data) < 3:
        return None
    pct = round((data[2] - 128) * 100 / 128)
    return f"LTFT (банк 1): {pct} %"


def fmt_pid_0A(data: list[int]) -> str | None:
    """Давление топлива (kPa), A*3."""
    if len(data) < 3:
        return None
    return f"Давление топлива: {data[2] * 3} кПа"


def fmt_pid_0B(data: list[int]) -> str | None:
    """MAP (kPa), A."""
    if len(data) < 3:
        return None
    return f"MAP: {data[2]} кПа"


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


def fmt_pid_0E(data: list[int]) -> str | None:
    """Угол опережения зажигания, (A-128)/2 °."""
    if len(data) < 3:
        return None
    deg = (data[2] - 128) / 2
    return f"Опережение зажигания: {deg:.1f} °"


def fmt_pid_0F(data: list[int]) -> str | None:
    """Температура воздуха на впуске, A-40 °C."""
    if len(data) < 3:
        return None
    val = data[2] - 40
    return f"Темп. впуска: {val} °C"


def fmt_pid_10(data: list[int]) -> str | None:
    """MAF, ((A*256)+B)/100 г/с."""
    if len(data) < 4:
        return None
    maf = ((data[2] * 256) + data[3]) / 100
    return f"MAF: {maf:.2f} г/с"


def fmt_pid_11(data: list[int]) -> str | None:
    """Положение дросселя, A*100/255 %."""
    if len(data) < 3:
        return None
    val = round(data[2] * 100 / 255)
    return f"Дроссель: {val} %"


def fmt_pid_1F(data: list[int]) -> str | None:
    """Время с запуска двигателя, (A*256)+B с."""
    if len(data) < 4:
        return None
    sec = (data[2] * 256) + data[3]
    return f"Время с запуска: {sec} с"


def fmt_pid_2F(data: list[int]) -> str | None:
    """Уровень топлива, A*100/255 %."""
    if len(data) < 3:
        return None
    val = round(data[2] * 100 / 255)
    return f"Уровень топлива: {val} %"


def fmt_pid_33(data: list[int]) -> str | None:
    """Барометрическое давление, A кПа."""
    if len(data) < 3:
        return None
    return f"Барометр: {data[2]} кПа"


def fmt_pid_42(data: list[int]) -> str | None:
    """Напряжение бортсети, ((A*256)+B)/1000 В."""
    if len(data) < 4:
        return None
    v = ((data[2] * 256) + data[3]) / 1000
    return f"Напряжение бортсети: {v:.2f} В"


def fmt_pid_46(data: list[int]) -> str | None:
    """Температура окружающей среды, A-40 °C."""
    if len(data) < 3:
        return None
    return f"Темп. окружающей среды: {data[2] - 40} °C"


def fmt_pid_5C(data: list[int]) -> str | None:
    """Напряжение OBD, (A*256+B)/1000 В."""
    if len(data) < 4:
        return None
    v = ((data[2] * 256) + data[3]) / 1000
    return f"Напряжение: {v:.1f} В"


# Маппинг PID -> (название, форматтер)
OBD_FORMATTERS: dict[str, tuple[str, Callable[[list[int]], str | None]]] = {
    "0100": ("Поддержка PID", fmt_pid_00),
    "0101": ("MIL / DTC", fmt_pid_01),
    "0103": ("Статус топлива", fmt_pid_01_03_fuel),
    "0104": ("Нагрузка", fmt_pid_04),
    "0105": ("Темп. ОЖ", fmt_pid_05),
    "0106": ("STFT банк 1", fmt_pid_06),
    "0107": ("LTFT банк 1", fmt_pid_07),
    "010A": ("Давл. топлива", fmt_pid_0A),
    "010B": ("MAP", fmt_pid_0B),
    "010C": ("Обороты", fmt_pid_0C),
    "010D": ("Скорость", fmt_pid_0D),
    "010E": ("Опережение", fmt_pid_0E),
    "010F": ("Темп. впуска", fmt_pid_0F),
    "0110": ("MAF", fmt_pid_10),
    "0111": ("Дроссель", fmt_pid_11),
    "011F": ("Время с запуска", fmt_pid_1F),
    "012F": ("Уровень топлива", fmt_pid_2F),
    "0133": ("Барометр", fmt_pid_33),
    "0142": ("Напряжение бортсети", fmt_pid_42),
    "0146": ("Темп. окружающей среды", fmt_pid_46),
    "015C": ("Напряжение", fmt_pid_5C),
}

SERVICE_FORMATTERS: dict[str, tuple[str, Callable[[list[int]], str | None]]] = {
    "03": ("DTC сохранённые", fmt_pid_03),
}

# PID для живого отображения (приоритет)
LIVE_PIDS = ["010D", "015C", "010C", "0105", "0104", "0111", "012F", "010F"]


def format_pid_response(pid_key: str, response_lines: list[str]) -> str | None:
    """
    Преобразовать ответ по PID в человекочитаемую строку.
    pid_key: "01 0D" или "010D" или сервис "03".
    """
    pid_key = pid_key.replace(" ", "").upper()
    if pid_key in SERVICE_FORMATTERS:
        _, formatter = SERVICE_FORMATTERS[pid_key]
        for line in response_lines:
            data = _parse_hex_line(line)
            if data:
                r = formatter(data)
                if r:
                    return r
        return None
    if pid_key not in OBD_FORMATTERS:
        return None
    _, formatter = OBD_FORMATTERS[pid_key]
    pid_byte = int(pid_key[2:], 16)
    for line in response_lines:
        data = _parse_hex_line(line)
        if len(data) >= 3 and data[0] == 0x41 and data[1] == pid_byte:
            return formatter(data)
    return None


def get_controllers_info(emulator) -> str:
    """Определить доступные контроллеры по ответу на 01 00."""
    success, err, lines = emulator.send_obd("01 00")
    if not success:
        return f"Контроллеры: недоступны ({err})"
    if not lines:
        return "Контроллеры: недоступны (нет ответа)"
    data = _parse_hex_line(lines[0])
    if len(data) >= 6:
        return "Контроллеры: ЭБУ двигателя (OBD2) — OK"
    return "Контроллеры: —"


def run_display_loop(
    emulator,
    interval_sec: float = 1.0,
    pids: list[str] | None = None,
    clear_screen: bool = True,
) -> None:
    """
    Цикл опроса и вывода в терминал.
    clear_screen: ANSI очистка перед каждым циклом.
    """
    pids = list(pids or LIVE_PIDS)
    pid_fail_streak: dict[str, int] = {}
    ok_count = 0
    err_count = 0

    print("\n--- Доступные контроллеры ---")
    info = get_controllers_info(emulator)
    print(info)

    if "недоступны" in info and "BUS INIT" in info:
        print("\n!!! ОШИБКА ИНИЦИАЛИЗАЦИИ ШИНЫ !!!")
        print("Причина: не удалось установить связь с ЭБУ по ISO 9141-2.")
        print("Проверьте:")
        print("  • Зажигание включено (ключ в положении ON)")
        print("  • KKL кабель подключён к OBD-разъёму и к ПК")
        print("  • Верный порт (-p) и скорость (-b 9600 или 10400)")
        print("Для подробных логов запустите: python main.py -p <порт> -v")
        print("")

    print("\n--- Текущие данные ---")

    try:
        while True:
            if clear_screen:
                print("\033[2J\033[H", end="")

            total = ok_count + err_count
            pct = (100.0 * ok_count / total) if total else 100.0
            print(f"OK: {ok_count} | ERR: {err_count} | {pct:.1f}%")
            print("")

            if not pids:
                print("  (нет PID для опроса — все исключены из-за ошибок или пустой список)")
                print("  ---")
                time.sleep(interval_sec)
                continue

            for pid in list(pids):
                pid_norm = pid.replace(" ", "").upper()
                if len(pid_norm) == 2 and all(c in "0123456789ABCDEF" for c in pid_norm):
                    pid_spaced = pid_norm
                elif len(pid_norm) == 4:
                    pid_spaced = f"01 {pid_norm[2:4]}"
                else:
                    pid_spaced = pid

                success, err, lines = emulator.send_obd(pid_spaced)
                if pid_norm in SERVICE_FORMATTERS:
                    name = SERVICE_FORMATTERS[pid_norm][0]
                else:
                    name = OBD_FORMATTERS.get(pid_norm, ("?", lambda _: None))[0]

                if success and lines:
                    ok_count += 1
                    pid_fail_streak[pid_norm] = 0
                    key_fmt = pid_norm if pid_norm in SERVICE_FORMATTERS else pid_spaced.replace(" ", "")
                    text = format_pid_response(key_fmt, lines)
                    if not text and len(pid_norm) == 4:
                        text = format_pid_response(pid_norm, lines)
                    if text:
                        print(f"  {text}")
                    else:
                        print(f"  {name}: —")
                else:
                    err_count += 1
                    pid_fail_streak[pid_norm] = pid_fail_streak.get(pid_norm, 0) + 1
                    if pid_fail_streak[pid_norm] >= 3:
                        logger.warning("PID %s: 3 ошибки подряд — исключён из цикла", pid_norm)
                        if pid in pids:
                            pids.remove(pid)
                        pid_fail_streak.pop(pid_norm, None)
                    print(f"  {name}: нет данных")

            print("  ---")
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\nОстановлено по Ctrl+C")
