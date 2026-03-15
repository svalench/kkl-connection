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


def send_5_baud_init(port_name: str, address: int = OBD2_ADDRESS) -> None:
    """Инициализация ISO 9141-2 на скорости 5 бод (slow init)."""
    ser_init = serial.Serial(port_name, 5, timeout=2)
    ser_init.write(bytes([address]))
    ser_init.flush()
    ser_init.close()
    time.sleep(0.1)


def iso_9141_handshake(ser: serial.Serial, address: int = OBD2_ADDRESS) -> bool:
    """
    После 5-baud init ЭБУ присылает 0x55 (Sync), KW1, KW2.
    Тестер отправляет inv(KW2), ЭБУ отвечает inv(address).
    """
    ser.timeout = 1.5
    sync = ser.read(1)
    if sync != b'\x55':
        return False

    kw1 = ser.read(1)
    kw2 = ser.read(1)
    if not kw1 or not kw2:
        return False

    inv_kw2 = bytes([kw2[0] ^ 0xFF])
    time.sleep(0.05)
    ser.write(inv_kw2)

    ack = ser.read(1)
    expected_ack = address ^ 0xFF
    return ack is not None and len(ack) > 0 and ack[0] == expected_ack


def open_kkl(port: str, baud: int = DEFAULT_BAUD) -> serial.Serial:
    """Открыть порт KKL на рабочей скорости."""
    return serial.Serial(port, baud, timeout=0.5)


def init_bus(port: str, baud: int = DEFAULT_BAUD, address: int = OBD2_ADDRESS) -> serial.Serial | None:
    """
    Полная инициализация: 5-baud init, переключение на рабочую скорость, handshake.
    Возвращает открытый serial или None при ошибке.
    """
    send_5_baud_init(port, address)
    ser = open_kkl(port, baud)
    ser.reset_input_buffer()

    if iso_9141_handshake(ser, address):
        return ser

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
