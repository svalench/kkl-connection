"""
VAG KWP1281 (KW1281) — проприетарный протокол VAG до ~2004 года.
Инициализация: 5-baud slow init с адресом блока (01=двигатель, 03=КПП, 08=климат...).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import serial

from kkl import DEFAULT_BAUD, send_5_baud_init

logger = logging.getLogger("kwp1281")

KWP1281_BLOCK_ENGINE = 0x01
KWP1281_BLOCK_GEARBOX = 0x03
KWP1281_BLOCK_ABS = 0x06
KWP1281_BLOCK_AIRBAG = 0x15
KWP1281_BLOCK_CLIMATE = 0x08
KWP1281_BLOCK_CLUSTER = 0x17
KWP1281_BLOCK_IMMO = 0x25
KWP1281_BLOCK_COMFORT = 0x46

# Интервал keepalive < 1500 ms (VAG)
KWP1281_KEEPALIVE_INTERVAL_SEC = 1.5


def _kw1281_checksum(block_without_checksum: bytes) -> int:
    """Контрольная сумма блока KW1281: сумма байт mod 256 (распространённый вариант VAG)."""
    return (-sum(block_without_checksum)) & 0xFF


def _read_block(
    ser: serial.Serial,
    timeout_sec: float = 2.0,
    verbose: bool = False,
) -> tuple[int | None, bytes]:
    """
    Чтение одного блока: [counter][title][length][payload...][checksum].
    Упрощённая схема для типичных ответов ЭБУ.
    """
    ser.timeout = timeout_sec
    head = ser.read(3)
    if len(head) < 3:
        if verbose:
            logger.debug("Блок: нет заголовка (%s)", head.hex())
        return None, head
    counter, title, length = head[0], head[1], head[2]
    rest = ser.read(length + 1)
    if len(rest) < length + 1:
        if verbose:
            logger.debug("Блок неполный: title=0x%02X", title)
        return title, head + rest
    block = head + rest
    if verbose:
        logger.debug("Блок title=0x%02X len=%d: %s", title, length, block.hex())
    return title, block


def _send_ack(ser: serial.Serial, counter: int, verbose: bool = False) -> None:
    """ACK 0x09 после приёма блока (типично для KW1281)."""
    body = bytes([counter, 0x09])
    chk = _kw1281_checksum(body)
    frame = body + bytes([chk])
    if verbose:
        logger.debug("ACK >> %s", frame.hex())
    ser.write(frame)
    ser.flush()


def kwp1281_init(
    port: str,
    block_address: int,
    baud: int = 9600,
    verbose: bool = False,
) -> tuple[serial.Serial | None, str, bytes | None]:
    """
    Slow init с адресом блока, переход на baud, чтение приветственных блоков до 0xF6 (ASCII имя ЭБУ).
    Возвращает (serial, "", welcome_payload) или (None, ошибка, None).
    """
    log: Callable[..., None] = logger.info if verbose else logger.debug
    try:
        send_5_baud_init(port, block_address, verbose=verbose)
    except Exception as e:
        return None, "5-baud init: %s" % e, None

    try:
        ser = serial.Serial(port, baud, timeout=2.0, rtscts=False, dsrdtr=False)
    except (serial.SerialException, OSError, ValueError) as e:
        return None, str(e), None

    ser.reset_input_buffer()
    welcome: bytes | None = None
    end = time.monotonic() + 5.0
    while time.monotonic() < end:
        title, block = _read_block(ser, timeout_sec=1.0, verbose=verbose)
        if title is None:
            break
        if title == 0xF6:
            welcome = block
            log("Получен блок приветствия 0xF6")
            _send_ack(ser, block[0], verbose=verbose)
            break
        _send_ack(ser, block[0], verbose=verbose)
    if welcome is None:
        ser.close()
        return None, "Нет приветственного блока 0xF6", None
    return ser, "", welcome


def kwp1281_read_faults(ser: serial.Serial, verbose: bool = False) -> tuple[bytes | None, str]:
    """Запрос кодов неисправностей (блок заголовка 0x07)."""
    return _kwp1281_request_simple(ser, 0x07, verbose=verbose)


def kwp1281_clear_faults(ser: serial.Serial, verbose: bool = False) -> tuple[bytes | None, str]:
    """Сброс кодов неисправностей (блок 0x05)."""
    return _kwp1281_request_simple(ser, 0x05, verbose=verbose)


def _kwp1281_request_simple(
    ser: serial.Serial,
    title: int,
    verbose: bool = False,
) -> tuple[bytes | None, str]:
    """Отправка однобайтовой команды как блока title без данных."""
    counter = (time.monotonic_ns() // 10_000_000) & 0xFF
    payload = b""
    length = len(payload)
    body = bytes([counter, title, length]) + payload
    chk = _kw1281_checksum(body)
    frame = body + bytes([chk])
    if verbose:
        logger.info("KW1281 >> %s", frame.hex())
    try:
        ser.write(frame)
        ser.flush()
    except serial.SerialException as e:
        return None, str(e)
    ser.timeout = 2.0
    t, block = _read_block(ser, timeout_sec=2.0, verbose=verbose)
    if t is None:
        return None, "Нет ответа"
    _send_ack(ser, block[0], verbose=verbose)
    return block, ""


def kwp1281_read_measuring_block(
    ser: serial.Serial,
    block_num: int,
    verbose: bool = False,
) -> tuple[bytes | None, str]:
    """Чтение измерительного блока (команда 0x29 + номер блока)."""
    counter = (time.monotonic_ns() // 10_000_000) & 0xFF
    payload = bytes([block_num & 0xFF])
    length = len(payload)
    body = bytes([counter, 0x29, length]) + payload
    chk = _kw1281_checksum(body)
    frame = body + bytes([chk])
    if verbose:
        logger.info("Измерительный блок %d >> %s", block_num, frame.hex())
    try:
        ser.write(frame)
        ser.flush()
    except serial.SerialException as e:
        return None, str(e)
    t, block = _read_block(ser, timeout_sec=2.0, verbose=verbose)
    if t is None:
        return None, "Нет ответа"
    _send_ack(ser, block[0], verbose=verbose)
    return block, ""


def kwp1281_send_keepalive(ser: serial.Serial, counter: int, verbose: bool = False) -> bool:
    """
    Keepalive: 0x03 counter 0x09 + checksum (каждые ~1.5 с).
    True, если после команды прочитан хоть какой ответ.
    """
    body = bytes([0x03, counter & 0xFF, 0x09])
    chk = _kw1281_checksum(body)
    frame = body + bytes([chk])
    if verbose:
        logger.debug("Keepalive >> %s", frame.hex())
    try:
        ser.write(frame)
        ser.flush()
    except serial.SerialException:
        return False
    prev = ser.timeout
    try:
        ser.timeout = 0.5
        raw = ser.read(32)
    finally:
        ser.timeout = prev
    if verbose:
        logger.debug("Keepalive << %s", raw.hex() if raw else "—")
    return bool(raw)
