"""
Низкоуровневый драйвер VAG KKL кабеля.
5-baud init (ISO 9141-2), handshake, отправка/приём кадров по K-Line.
"""
import serial
import time


# Стандартный адрес OBD2 по ISO 9141-2
OBD2_ADDRESS = 0x33

# Рабочая скорость для ISO 9141 (после 5-baud init)
DEFAULT_BAUD = 9600


def _log(verbose: bool, msg: str, *args) -> None:
    if verbose:
        text = msg % args if args else msg
        print(f"[KKL] {text}")


def send_5_baud_init(port_name: str, address: int = OBD2_ADDRESS, verbose: bool = False) -> None:
    """Инициализация ISO 9141-2 на скорости 5 бод (slow init)."""
    _log(verbose, "5-baud init: открытие порта %s на 5 бод", port_name)
    try:
        ser_init = serial.Serial(port_name, 5, timeout=2)
    except Exception as e:
        _log(verbose, "5-baud init: ОШИБКА открытия порта: %s", str(e))
        raise
    _log(verbose, "5-baud init: отправка адреса 0x%02X", address)
    ser_init.write(bytes([address]))
    ser_init.flush()
    ser_init.close()
    time.sleep(0.1)
    _log(verbose, "5-baud init: порт закрыт, пауза 100ms перед переоткрытием")


def iso_9141_handshake(
    ser: serial.Serial, address: int = OBD2_ADDRESS, verbose: bool = False
) -> bool:
    """
    После 5-baud init ЭБУ присылает 0x55 (Sync), KW1, KW2.
    Тестер отправляет inv(KW2), ЭБУ отвечает inv(address).
    """
    ser.timeout = 1.5
    _log(verbose, "Handshake: ожидание Sync (0x55)...")
    sync = ser.read(1)
    if not sync:
        _log(verbose, "Handshake: таймаут — Sync не получен (нет данных)")
        return False
    _log(verbose, "Handshake: получен байт 0x%02X (ожидался 0x55)", sync[0])
    if sync != b'\x55':
        _log(verbose, "Handshake: ОШИБКА — неверный Sync, ожидался 0x55")
        return False

    _log(verbose, "Handshake: ожидание KW1...")
    kw1 = ser.read(1)
    if not kw1:
        _log(verbose, "Handshake: таймаут — KW1 не получен")
        return False
    _log(verbose, "Handshake: KW1 = 0x%02X", kw1[0])

    _log(verbose, "Handshake: ожидание KW2...")
    kw2 = ser.read(1)
    if not kw2:
        _log(verbose, "Handshake: таймаут — KW2 не получен")
        return False
    _log(verbose, "Handshake: KW2 = 0x%02X", kw2[0])

    inv_kw2 = bytes([kw2[0] ^ 0xFF])
    _log(verbose, "Handshake: отправка inv(KW2) = 0x%02X", inv_kw2[0])
    time.sleep(0.05)
    ser.write(inv_kw2)

    _log(verbose, "Handshake: ожидание ACK (0x%02X)...", address ^ 0xFF)
    ack = ser.read(1)
    expected_ack = address ^ 0xFF
    if not ack:
        _log(verbose, "Handshake: таймаут — ACK не получен")
        return False
    _log(verbose, "Handshake: получен ACK 0x%02X (ожидался 0x%02X)", ack[0], expected_ack)
    if ack[0] != expected_ack:
        _log(verbose, "Handshake: ОШИБКА — неверный ACK")
        return False
    _log(verbose, "Handshake: OK — связь установлена")
    return True


def open_kkl(port: str, baud: int = DEFAULT_BAUD, verbose: bool = False) -> serial.Serial:
    """Открыть порт KKL на рабочей скорости."""
    _log(verbose, "Открытие порта %s на %d бод", port, baud)
    ser = serial.Serial(port, baud, timeout=0.5)
    ser.reset_input_buffer()
    _log(verbose, "Порт открыт, буфер ввода очищен")
    return ser


def init_bus(
    port: str,
    baud: int = DEFAULT_BAUD,
    address: int = OBD2_ADDRESS,
    verbose: bool = False,
) -> serial.Serial | None:
    """
    Полная инициализация: 5-baud init, переключение на рабочую скорость, handshake.
    Возвращает открытый serial или None при ошибке.
    """
    _log(verbose, "=== Инициализация шины ISO 9141-2 ===")
    _log(verbose, "Порт: %s, скорость: %d, адрес: 0x%02X", port, baud, address)

    try:
        send_5_baud_init(port, address, verbose=verbose)
    except Exception as e:
        _log(verbose, "init_bus: ОШИБКА 5-baud init: %s", str(e))
        return None

    ser = open_kkl(port, baud, verbose=verbose)

    _log(verbose, "Запуск handshake...")
    if iso_9141_handshake(ser, address, verbose=verbose):
        _log(verbose, "=== BUS INIT: OK ===")
        return ser

    _log(verbose, "init_bus: handshake не прошёл — шина не инициализирована")
    ser.close()
    return None


def send_frame(ser: serial.Serial, data: bytes) -> None:
    """Отправить кадр на K-Line."""
    ser.write(data)
    ser.flush()


def read_response(ser: serial.Serial, timeout_ms: int = 200) -> bytes | None:
    """
    Читать ответ с K-Line до таймаута.
    timeout_ms приблизительно (4 * value как в AT ST).
    """
    ser.timeout = timeout_ms / 1000.0
    data = ser.read(64)
    return data if data else None


def close(ser: serial.Serial) -> None:
    """Закрыть соединение."""
    try:
        ser.close()
    except Exception:
        pass
