"""
Низкоуровневый драйвер VAG KKL кабеля.
ISO 9141-2 (5-baud slow init) и KWP2000/ISO 14230 (fast init).
По стандарту: 10400 бод для K-Line.
"""
import serial
import time


# Стандартный адрес OBD2
OBD2_ADDRESS = 0x33

# K-Line работает на 10400 бод (ISO 9141-2, KWP2000)
DEFAULT_BAUD = 10400

# KWP2000 StartCommunication: C1 33 F1 81 66 (format byte, target, source, 0x81=startDiag, checksum)
KWP_FAST_INIT_FRAME = bytes([0xC1, 0x33, 0xF1, 0x81, 0x66])


def _log(verbose: bool, msg: str, *args) -> None:
    if verbose:
        text = msg % args if args else msg
        print(f"[KKL] {text}")


def _kwp_verify_response(raw: bytes) -> bool:
    """Проверить ответ KWP: длина >= 4, сумма всех байт mod 256 = 0."""
    if not raw or len(raw) < 4:
        return False
    return (sum(raw) & 0xFF) == 0


def send_kwp_fast_init(port: str, baud: int = DEFAULT_BAUD, verbose: bool = False) -> tuple[serial.Serial | None, str, str]:
    """
    KWP2000 fast init: wake-up импульс + StartCommunication.
    Возвращает (serial, "", "kwp2000"), при ошибке (None, "причина", "").
    """
    _log(verbose, "Fast init (KWP2000): открытие порта %s на %d бод", port, baud)
    try:
        ser = serial.Serial(port, baud, timeout=2)
    except Exception as e:
        return None, "Порт %s: %s" % (port, str(e)), ""

    ser.reset_input_buffer()

    # Wake-up: 25ms low, 25ms high на K-Line (через DTR/RTS, если кабель использует)
    _log(verbose, "Fast init: wake-up импульс (DTR)...")
    try:
        ser.dtr = False
        time.sleep(0.025)
        ser.dtr = True
        time.sleep(0.025)
    except Exception:
        pass

    _log(verbose, "Fast init: отправка StartCommunication %s", KWP_FAST_INIT_FRAME.hex())
    ser.write(KWP_FAST_INIT_FRAME)
    ser.flush()

    ser.timeout = 1.0
    raw = ser.read(32)
    _log(verbose, "Fast init: ответ %s", raw.hex() if raw else "нет данных")

    if _kwp_verify_response(raw):
        _log(verbose, "=== BUS INIT: OK (KWP2000 fast) ===")
        return ser, "", "kwp2000"
    if raw:
        _log(verbose, "Fast init: неверный ответ (checksum)")
        ser.close()
        return None, "KWP fast init: invalid response", ""
    ser.close()
    return None, "KWP fast init: no response", ""


def send_5_baud_init(port_name: str, address: int = OBD2_ADDRESS, verbose: bool = False) -> None:
    """Инициализация ISO 9141-2 на скорости 5 бод (slow init)."""
    _log(verbose, "5-baud init: открытие порта %s на 5 бод", port_name)
    try:
        ser_init = serial.Serial(port_name, 5, timeout=3)
    except Exception as e:
        _log(verbose, "5-baud init: ОШИБКА открытия порта: %s", str(e))
        raise
    _log(verbose, "5-baud init: отправка адреса 0x%02X (ждём 2 сек по стандарту)", address)
    ser_init.write(bytes([address]))
    ser_init.flush()
    ser_init.close()
    # ISO 9141-2: пауза ~2000 мс перед переключением на рабочую скорость
    time.sleep(2.0)
    _log(verbose, "5-baud init: пауза 2 сек, переоткрытие на рабочей скорости")


def iso_9141_handshake(
    ser: serial.Serial, address: int = OBD2_ADDRESS, verbose: bool = False
) -> tuple[bool, str]:
    """
    После 5-baud init ЭБУ присылает 0x55 (Sync), KW1, KW2.
    Тестер отправляет inv(KW2), ЭБУ отвечает inv(address).
    """
    ser.timeout = 3.0  # W1 до 300ms, некоторые ЭБУ медленнее
    _log(verbose, "Handshake: ожидание Sync (0x55)...")
    sync = ser.read(1)
    if not sync:
        _log(verbose, "Handshake: таймаут — Sync не получен (нет данных)")
        return False, "Sync timeout (ЭБУ не ответил 0x55)"
    _log(verbose, "Handshake: получен байт 0x%02X (ожидался 0x55)", sync[0])
    if sync != b'\x55':
        _log(verbose, "Handshake: ОШИБКА — неверный Sync, ожидался 0x55")
        return False, "Invalid Sync 0x%02X (ожидался 0x55)" % sync[0]

    _log(verbose, "Handshake: ожидание KW1...")
    kw1 = ser.read(1)
    if not kw1:
        _log(verbose, "Handshake: таймаут — KW1 не получен")
        return False, "KW1 timeout"
    _log(verbose, "Handshake: KW1 = 0x%02X", kw1[0])

    _log(verbose, "Handshake: ожидание KW2...")
    kw2 = ser.read(1)
    if not kw2:
        _log(verbose, "Handshake: таймаут — KW2 не получен")
        return False, "KW2 timeout"
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
        return False, "ACK timeout"
    _log(verbose, "Handshake: получен ACK 0x%02X (ожидался 0x%02X)", ack[0], expected_ack)
    if ack[0] != expected_ack:
        _log(verbose, "Handshake: ОШИБКА — неверный ACK")
        return False, "Invalid ACK 0x%02X (ожидался 0x%02X)" % (ack[0], expected_ack)
    _log(verbose, "Handshake: OK — связь установлена (ISO 9141-2)")
    return True, ""


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
    try_fast_first: bool = True,
) -> tuple[serial.Serial | None, str, str]:
    """
    Инициализация шины. По умолчанию: сначала KWP2000 fast init, при неудаче — ISO 9141-2 slow init.
    Возвращает (serial, error_reason, protocol). При успехе: (ser, "", "kwp2000"|"iso9141"), при ошибке: (None, "причина", "").
    """
    _log(verbose, "=== Инициализация K-Line (ISO 9141-2 / KWP2000) ===")
    _log(verbose, "Порт: %s, скорость: %d, адрес: 0x%02X", port, baud, address)

    # 1. Пробуем fast init (KWP2000) — часто лучше работает с USB-адаптерами
    if try_fast_first:
        _log(verbose, "Попытка 1: KWP2000 fast init")
        ser, err, proto = send_kwp_fast_init(port, baud, verbose=verbose)
        if ser:
            return ser, "", proto
        _log(verbose, "Fast init не удался: %s. Пробуем slow init через 2.5 сек...", err)
        time.sleep(2.5)

    # 2. Slow init (ISO 9141-2) 5-baud
    _log(verbose, "Попытка 2: ISO 9141-2 slow init (5-baud)")
    try:
        send_5_baud_init(port, address, verbose=verbose)
    except Exception as e:
        err = "Порт %s: %s" % (port, str(e))
        _log(verbose, "init_bus: ОШИБКА 5-baud init: %s", str(e))
        return None, err, ""

    ser = open_kkl(port, baud, verbose=verbose)

    _log(verbose, "Запуск handshake...")
    ok, err = iso_9141_handshake(ser, address, verbose=verbose)
    if ok:
        _log(verbose, "=== BUS INIT: OK (ISO 9141-2) ===")
        return ser, "", "iso9141"

    _log(verbose, "init_bus: handshake не прошёл — %s", err)
    ser.close()
    return None, err, ""


def send_frame(ser: serial.Serial, data: bytes) -> None:
    """Отправить кадр на K-Line."""
    ser.write(data)
    ser.flush()


def read_response(ser: serial.Serial, timeout_ms: int = 200) -> bytes | None:
    """Читать ответ с K-Line до таймаута."""
    ser.timeout = timeout_ms / 1000.0
    data = ser.read(64)
    return data if data else None


def close(ser: serial.Serial) -> None:
    """Закрыть соединение."""
    try:
        ser.close()
    except Exception:
        pass
