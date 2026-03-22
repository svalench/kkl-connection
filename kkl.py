"""
Низкоуровневый драйвер VAG KKL кабеля.
ISO 9141-2 (5-baud slow init) и KWP2000/ISO 14230 (fast init).
По стандарту: 10400 бод для K-Line.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable

import serial

# Стандартный адрес OBD2
OBD2_ADDRESS = 0x33

# K-Line работает на 10400 бод (ISO 9141-2, KWP2000)
DEFAULT_BAUD = 10400

# KWP2000 StartCommunication: C1 33 F1 81 66 (format byte, target, source, 0x81=startDiag, checksum)
KWP_FAST_INIT_FRAME = bytes([0xC1, 0x33, 0xF1, 0x81, 0x66])

# ISO 9141-2: W4 (inv(KW2) до inv(addr)) 25–50 ms
W4_DELAY_SEC = 0.040

# P3 / keepalive: до 5 с без кадра — сессия может оборваться; опрос чаще
KEEPALIVE_INTERVAL_SEC = 2.5

logger = logging.getLogger("kkl")

_connection_lost = False
_disconnect_handler: Callable[[], None] | None = None


def connection_lost() -> bool:
    """Флаг: зафиксирован обрыв по SerialException."""
    return _connection_lost


def set_disconnect_handler(handler: Callable[[], None] | None) -> None:
    """Обработчик при обрыве (например сброс состояния эмулятора)."""
    global _disconnect_handler
    _disconnect_handler = handler


def reset_connection_state() -> None:
    """Сброс флага обрыва (после успешного переподключения)."""
    global _connection_lost
    _connection_lost = False


def _handle_disconnect() -> None:
    global _connection_lost
    _connection_lost = True
    h = _disconnect_handler
    if h is not None:
        try:
            h()
        except Exception:
            logger.exception("Ошибка в обработчике disconnect")


def _log_verbose(verbose: bool, msg: str, *args) -> None:
    if verbose:
        text = msg % args if args else msg
        logger.info(text)


def configure_ftdi_port(port: str) -> None:
    """
    Оптимальные настройки FTDI FT232RL для K-Line 10400 baud.
    Latency timer 1 ms (не 16 ms по умолчанию). Вызвать до open_kkl().
    """
    if sys.platform.startswith("linux"):
        port_name = os.path.basename(port)
        latency_path = f"/sys/bus/usb-serial/devices/{port_name}/latency_timer"
        try:
            with open(latency_path, "w", encoding="ascii") as f:
                f.write("1")
            logger.debug("FTDI latency_timer=1 для %s", port_name)
        except (PermissionError, FileNotFoundError, OSError):
            logger.debug("Не удалось записать latency_timer для %s (нужны права или не FTDI)", port_name)
    elif sys.platform == "win32":
        try:
            import winreg

            # Попытка: параметры сервиса FTSER2K (типичный VCP FTDI)
            key_path = r"SYSTEM\CurrentControlSet\Services\FTSER2K\Parameters"
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as _:
                    logger.debug("FTDI FTSER2K найден; LatencyTimer задаётся на уровне устройства в диспетчере")
            except OSError:
                logger.debug("Реестр FTSER2K не найден — пропуск настройки latency")
        except ImportError:
            logger.debug("winreg недоступен")


def _reset_port_buffers(port: str, baud: int) -> None:
    """Кратко открыть порт и сбросить буферы между попытками init."""
    try:
        s = serial.Serial(port, baud, timeout=0.1, rtscts=False, dsrdtr=False)
        try:
            s.reset_input_buffer()
            s.reset_output_buffer()
        finally:
            s.close()
    except (serial.SerialException, OSError, ValueError):
        pass


def _kwp_verify_response(raw: bytes) -> bool:
    """Проверить ответ KWP: длина >= 4, сумма всех байт mod 256 = 0."""
    if not raw or len(raw) < 4:
        return False
    return (sum(raw) & 0xFF) == 0


def send_kwp_fast_init(port: str, baud: int = DEFAULT_BAUD, verbose: bool = False) -> tuple[serial.Serial | None, str, str]:
    """
    KWP2000 fast init: wake-up импульс + StartCommunication.
    Межбайтовая пауза 5 ms (ISO 14230), после кадра P2 min 30 ms, чтение до 2 с.
    Возвращает (serial, "", "kwp2000"), при ошибке (None, "причина", "").
    """
    _log_verbose(verbose, "Fast init (KWP2000): открытие порта %s на %d бод", port, baud)
    try:
        ser = serial.Serial(port, baud, timeout=2, rtscts=False, dsrdtr=False)
    except (serial.SerialException, OSError, ValueError) as e:
        return None, "Порт %s: %s" % (port, str(e)), ""

    try:
        ser.reset_input_buffer()
    except serial.SerialException:
        _handle_disconnect()
        try:
            ser.close()
        except Exception:
            pass
        return None, "KWP fast init: порт недоступен", ""

    _log_verbose(verbose, "Fast init: wake-up импульс (DTR)...")
    try:
        ser.dtr = False
        time.sleep(0.025)
        ser.dtr = True
        time.sleep(0.025)
    except (serial.SerialException, AttributeError):
        pass

    _log_verbose(verbose, "Fast init: отправка StartCommunication по байтам с паузой 5 ms")
    try:
        for i, b in enumerate(KWP_FAST_INIT_FRAME):
            ser.write(bytes([b]))
            if i < len(KWP_FAST_INIT_FRAME) - 1:
                time.sleep(0.005)
        ser.flush()
    except serial.SerialException:
        _handle_disconnect()
        try:
            ser.close()
        except Exception:
            pass
        return None, "KWP fast init: ошибка записи", ""

    time.sleep(0.030)
    ser.timeout = 2.0
    try:
        raw = ser.read(32)
    except serial.SerialException:
        _handle_disconnect()
        try:
            ser.close()
        except Exception:
            pass
        return None, "KWP fast init: ошибка чтения", ""

    _log_verbose(verbose, "Fast init: ответ %s", raw.hex() if raw else "нет данных")

    if _kwp_verify_response(raw):
        _log_verbose(verbose, "=== BUS INIT: OK (KWP2000 fast) ===")
        return ser, "", "kwp2000"
    if raw:
        _log_verbose(verbose, "Fast init: неверный ответ (checksum)")
        try:
            ser.close()
        except Exception:
            pass
        return None, "KWP fast init: invalid response", ""
    try:
        ser.close()
    except Exception:
        pass
    return None, "KWP fast init: no response", ""


def send_5_baud_init(port_name: str, address: int = OBD2_ADDRESS, verbose: bool = False) -> None:
    """Инициализация ISO 9141-2 на скорости 5 бод (slow init)."""
    _log_verbose(verbose, "5-baud init: открытие порта %s на 5 бод", port_name)
    try:
        ser_init = serial.Serial(port_name, 5, timeout=3, rtscts=False, dsrdtr=False)
    except (serial.SerialException, OSError, ValueError) as e:
        _log_verbose(verbose, "5-baud init: ОШИБКА открытия порта: %s", str(e))
        raise
    try:
        _log_verbose(verbose, "5-baud init: отправка адреса 0x%02X (ждём 2 сек по стандарту)", address)
        ser_init.write(bytes([address]))
        ser_init.flush()
    except serial.SerialException:
        _handle_disconnect()
        try:
            ser_init.close()
        except Exception:
            pass
        raise
    ser_init.close()
    time.sleep(2.0)
    _log_verbose(verbose, "5-baud init: пауза 2 сек, переоткрытие на рабочей скорости")


def _detect_protocol_from_keywords(kw1: bytes, kw2: bytes) -> str:
    """Эвристика по keyword bytes (логирование, не замена handshake)."""
    if not kw1 or not kw2:
        return "unknown"
    k1, k2 = kw1[0], kw2[0]
    if k1 == 0x08 and k2 == 0x08:
        return "ISO 9141-2 (типично VAG)"
    if k1 == 0x8F:
        return "возможен KWP2000 / нетипичные KW"
    return "ISO 9141-2 / прочее"


def iso_9141_handshake(
    ser: serial.Serial,
    address: int = OBD2_ADDRESS,
    verbose: bool = False,
    w4_delay_ms: int = 40,
) -> tuple[bool, str]:
    """
    После 5-baud init ЭБУ присылает 0x55 (Sync), KW1, KW2.
    Тестер отправляет inv(KW2), ЭБУ отвечает inv(address).
    W4: 25–50 ms перед inv(KW2); по умолчанию w4_delay_ms=40 (как W4_DELAY_SEC).
    """
    ser.timeout = 3.0
    _log_verbose(verbose, "Handshake: ожидание Sync (0x55)...")
    try:
        sync = ser.read(1)
    except serial.SerialException:
        _handle_disconnect()
        return False, "Serial error on read (Sync)"

    if not sync:
        _log_verbose(verbose, "Handshake: таймаут — Sync не получен (нет данных)")
        return False, "Sync timeout (ЭБУ не ответил 0x55)"
    _log_verbose(verbose, "Handshake: получен байт 0x%02X (ожидался 0x55)", sync[0])
    if sync != b"\x55":
        _log_verbose(verbose, "Handshake: ОШИБКА — неверный Sync, ожидался 0x55")
        return False, "Invalid Sync 0x%02X (ожидался 0x55)" % sync[0]

    _log_verbose(verbose, "Handshake: ожидание KW1...")
    try:
        kw1 = ser.read(1)
    except serial.SerialException:
        _handle_disconnect()
        return False, "Serial error on read (KW1)"
    if not kw1:
        _log_verbose(verbose, "Handshake: таймаут — KW1 не получен")
        return False, "KW1 timeout"
    _log_verbose(verbose, "Handshake: KW1 = 0x%02X", kw1[0])

    _log_verbose(verbose, "Handshake: ожидание KW2...")
    try:
        kw2 = ser.read(1)
    except serial.SerialException:
        _handle_disconnect()
        return False, "Serial error on read (KW2)"
    if not kw2:
        _log_verbose(verbose, "Handshake: таймаут — KW2 не получен")
        return False, "KW2 timeout"
    _log_verbose(verbose, "Handshake: KW2 = 0x%02X", kw2[0])

    hint = _detect_protocol_from_keywords(kw1, kw2)
    logger.info("Keyword bytes: KW1=0x%02X KW2=0x%02X — %s", kw1[0], kw2[0], hint)
    _log_verbose(verbose, "Handshake: по KW определено: %s", hint)

    if kw1[0] == 0x08 and kw2[0] == 0x08:
        logger.info("Валидация KW: признак ISO 9141-2 VAG (0x08/0x08)")
    elif kw1[0] == 0x8F:
        logger.info("Валидация KW: 0x8F — вероятен ответ KWP2000, не классический ISO 9141-2")

    inv_kw2 = bytes([kw2[0] ^ 0xFF])
    # ISO 9141-2 W4: 25–50 ms; по умолчанию W4_DELAY_SEC (40 ms)
    w4_sec = max(0.025, min(0.050, w4_delay_ms / 1000.0))
    _log_verbose(verbose, "Handshake: пауза W4 %.3f с перед inv(KW2) = 0x%02X", w4_sec, inv_kw2[0])
    time.sleep(w4_sec)
    try:
        ser.write(inv_kw2)
    except serial.SerialException:
        _handle_disconnect()
        return False, "Serial error on write (inv KW2)"

    _log_verbose(verbose, "Handshake: ожидание ACK (0x%02X)...", address ^ 0xFF)
    try:
        ack = ser.read(1)
    except serial.SerialException:
        _handle_disconnect()
        return False, "Serial error on read (ACK)"
    expected_ack = address ^ 0xFF
    if not ack:
        _log_verbose(verbose, "Handshake: таймаут — ACK не получен")
        return False, "ACK timeout"
    _log_verbose(verbose, "Handshake: получен ACK 0x%02X (ожидался 0x%02X)", ack[0], expected_ack)
    if ack[0] != expected_ack:
        _log_verbose(verbose, "Handshake: ОШИБКА — неверный ACK")
        return False, "Invalid ACK 0x%02X (ожидался 0x%02X)" % (ack[0], expected_ack)
    _log_verbose(verbose, "Handshake: OK — связь установлена (ISO 9141-2)")
    return True, ""


def open_kkl(port: str, baud: int = DEFAULT_BAUD, verbose: bool = False) -> serial.Serial:
    """Открыть порт KKL на рабочей скорости."""
    _log_verbose(verbose, "Открытие порта %s на %d бод", port, baud)
    ser = serial.Serial(port, baud, timeout=0.5, rtscts=False, dsrdtr=False)
    ser.reset_input_buffer()
    _log_verbose(verbose, "Порт открыт, буфер ввода очищен")
    return ser


def init_bus(
    port: str,
    baud: int = DEFAULT_BAUD,
    address: int = OBD2_ADDRESS,
    verbose: bool = False,
    try_fast_first: bool = True,
    max_retries: int = 3,
    retry_delay_sec: float = 2.5,
) -> tuple[serial.Serial | None, str, str]:
    """
    Инициализация шины. По умолчанию: сначала KWP2000 fast init, при неудаче — ISO 9141-2 slow init.
    Повтор до max_retries с паузой retry_delay_sec и сбросом буферов между попытками.
    Возвращает (serial, error_reason, protocol).
    """
    reset_connection_state()
    last_err = ""
    _log_verbose(verbose, "=== Инициализация K-Line (ISO 9141-2 / KWP2000) ===")
    _log_verbose(verbose, "Порт: %s, скорость: %d, адрес: 0x%02X", port, baud, address)

    for attempt in range(1, max_retries + 1):
        logger.info("Инициализация шины: попытка %d из %d", attempt, max_retries)
        _log_verbose(verbose, "Попытка %d из %d", attempt, max_retries)

        if try_fast_first:
            _log_verbose(verbose, "KWP2000 fast init")
            ser, err, proto = send_kwp_fast_init(port, baud, verbose=verbose)
            if ser:
                return ser, "", proto
            last_err = err or last_err
            _log_verbose(verbose, "Fast init не удался: %s", err)
            time.sleep(2.5)

        _log_verbose(verbose, "ISO 9141-2 slow init (5-baud)")
        try:
            send_5_baud_init(port, address, verbose=verbose)
        except Exception as e:
            last_err = "Порт %s: %s" % (port, str(e))
            logger.warning("5-baud init: %s", last_err)
            _log_verbose(verbose, "init_bus: ОШИБКА 5-baud init: %s", str(e))
            if attempt < max_retries:
                _reset_port_buffers(port, baud)
                time.sleep(retry_delay_sec)
            continue

        ser_slow: serial.Serial | None = None
        handshake_ok = False
        try:
            ser_slow = open_kkl(port, baud, verbose=verbose)
            try:
                ser_slow.reset_input_buffer()
                ser_slow.reset_output_buffer()
            except serial.SerialException:
                _handle_disconnect()
                raise
            _log_verbose(verbose, "Запуск handshake...")
            handshake_ok, err = iso_9141_handshake(ser_slow, address, verbose=verbose)
            if handshake_ok:
                _log_verbose(verbose, "=== BUS INIT: OK (ISO 9141-2) ===")
                return ser_slow, "", "iso9141"
            last_err = err
            logger.warning("Handshake не прошёл: %s", err)
            _log_verbose(verbose, "init_bus: handshake не прошёл — %s", err)
        except serial.SerialException:
            last_err = "Ошибка последовательного порта при инициализации"
            logger.warning("%s", last_err)
        finally:
            if ser_slow is not None and not handshake_ok:
                try:
                    ser_slow.close()
                except Exception:
                    pass

        if attempt < max_retries:
            _reset_port_buffers(port, baud)
            time.sleep(retry_delay_sec)

    return None, "Превышено число попыток: %s" % last_err, ""


def send_keepalive(ser: serial.Serial, protocol: str, verbose: bool = False) -> bool:
    """
    Тестовый кадр / Tester Present для удержания сессии.
    iso9141: PID 01 00; kwp2000: 0x3E. True, если получен любой непустой ответ.
    """
    protocol = (protocol or "").lower()
    if protocol == "iso9141":
        body = [0x68, 0x6A, 0xF1, 0x01, 0x00]
        s = sum(body) & 0xFF
        chk = (0x100 - s) & 0xFF
        frame = bytes(body + [chk])
    elif protocol == "kwp2000":
        body = [0xC1, 0x33, 0xF1, 0x3E]
        s = sum(body) & 0xFF
        chk = (0x100 - s) & 0xFF
        frame = bytes(body + [chk])
    else:
        logger.debug("send_keepalive: неизвестный протокол %s", protocol)
        return False

    _log_verbose(verbose, "Keepalive >> %s", frame.hex())
    try:
        ser.write(frame)
        ser.flush()
    except serial.SerialException:
        _handle_disconnect()
        return False

    prev_timeout = ser.timeout
    try:
        ser.timeout = 0.5
        raw = ser.read(64)
    except serial.SerialException:
        _handle_disconnect()
        return False
    finally:
        ser.timeout = prev_timeout

    _log_verbose(verbose, "Keepalive << %s", raw.hex() if raw else "нет данных")
    return bool(raw)


def send_frame(ser: serial.Serial, data: bytes) -> None:
    """Отправить кадр на K-Line."""
    try:
        ser.write(data)
        ser.flush()
    except serial.SerialException:
        _handle_disconnect()
        raise


def read_response(ser: serial.Serial, timeout_ms: int = 200) -> bytes | None:
    """Читать ответ с K-Line до таймаута."""
    try:
        ser.timeout = timeout_ms / 1000.0
        data = ser.read(64)
    except serial.SerialException:
        _handle_disconnect()
        return None
    return data if data else None


def close(ser: serial.Serial) -> None:
    """Закрыть соединение."""
    try:
        ser.close()
    except Exception:
        pass
