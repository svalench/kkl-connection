"""
Программная эмуляция ELM323.
Реализует протокол и тайминги по ELM323DS для работы поверх VAG KKL.
"""
from __future__ import annotations

import logging
import time
from typing import TypedDict

import serial

import kkl
from kkl import (
    init_bus,
    send_frame,
    close,
    DEFAULT_BAUD,
    OBD2_ADDRESS,
)

logger = logging.getLogger("elm323")

# ISO 9141 заголовок по умолчанию: 68 6A F1
DEFAULT_HEADER_ISO9141 = (0x68, 0x6A, 0xF1)


def build_iso9141_checksum_for_payload(header_and_data: list[int]) -> int:
    """
    Контрольная сумма ISO 9141 (сумма всех байт кадра mod 256 = 0).
    Явные скобки: (0x100 - (sum & 0xFF)) & 0xFF.
    """
    s = sum(header_and_data) & 0xFF
    return (0x100 - s) & 0xFF


class EmulatorState(TypedDict, total=False):
    echo: bool
    headers: bool
    linefeed: bool
    timeout_ms: int
    header_bytes: tuple[int, int, int]


class ELM323Emulator:
    """
    Эмулятор ELM323: принимает OBD-команды (hex), собирает кадр,
    обменивается с авто через KKL, возвращает форматированный ответ.
    """

    def __init__(
        self,
        port: str,
        baud: int = DEFAULT_BAUD,
        address: int = OBD2_ADDRESS,
        verbose: bool = False,
        try_fast_init: bool = True,
    ):
        self.port = port
        self.baud = baud
        self.address = address
        self.verbose = verbose
        self.try_fast_init = try_fast_init
        self._serial = None
        self._bus_init_done = False
        self._last_command = ""
        self._error_count = 0
        self.echo = False
        self.headers = False
        self.linefeed = True
        self.timeout_ms = 200
        self._header = DEFAULT_HEADER_ISO9141
        self._protocol = "iso9141"
        kkl.set_disconnect_handler(self._on_serial_disconnect)

    def _on_serial_disconnect(self) -> None:
        """Сброс при SerialException из kkl."""
        self._bus_init_done = False
        s = self._serial
        self._serial = None
        if s is not None:
            try:
                close(s)
            except Exception:
                pass

    def _ensure_bus_init(self) -> tuple[bool, str]:
        """
        Инициализировать шину при первом обращении.
        После 3 последовательных ошибок — принудительное переподключение.
        """
        if self._bus_init_done and self._serial and self._serial.is_open:
            return True, ""
        if self._serial:
            try:
                close(self._serial)
            except Exception:
                pass
            self._serial = None
        if self.verbose:
            logger.info("Запуск инициализации шины...")
        ser, err, proto = init_bus(
            self.port,
            self.baud,
            self.address,
            verbose=self.verbose,
            try_fast_first=self.try_fast_init,
        )
        if ser:
            self._serial = ser
            self._bus_init_done = True
            self._protocol = proto or "iso9141"
            self._error_count = 0
            if self.verbose:
                logger.info("Шина инициализирована (%s)", self._protocol)
            return True, ""
        self._error_count += 1
        logger.warning("Ошибка инициализации шины: %s (подряд ошибок: %d)", err, self._error_count)
        if self.verbose:
            logger.info("ОШИБКА: %s", err)
        if self._error_count >= 3:
            logger.warning("3 ошибки подряд — принудительная переинициализация порта")
            if self.reconnect():
                return True, ""
        return False, err

    def reconnect(self) -> bool:
        """Явное переподключение: закрыть порт, пауза 3 с, повторная инициализация."""
        kkl.set_disconnect_handler(None)
        if self._serial:
            try:
                close(self._serial)
            except Exception:
                pass
            self._serial = None
        self._bus_init_done = False
        self._error_count = 0
        time.sleep(3.0)
        kkl.set_disconnect_handler(self._on_serial_disconnect)
        ser, err, proto = init_bus(
            self.port,
            self.baud,
            self.address,
            verbose=self.verbose,
            try_fast_first=self.try_fast_init,
        )
        if ser:
            self._serial = ser
            self._bus_init_done = True
            self._protocol = proto or "iso9141"
            logger.info("Переподключение OK (%s)", self._protocol)
            return True
        logger.error("Переподключение не удалось: %s", err)
        return False

    def _build_frame(self, data_bytes: list[int]) -> bytes:
        """Собрать кадр: ISO 9141 или KWP2000."""
        if self._protocol == "kwp2000":
            length_byte = 0xC0 | len(data_bytes)
            all_bytes = [length_byte, 0x33, 0xF1] + data_bytes
        else:
            h0, h1, h2 = self._header
            all_bytes = [h0, h1, h2] + data_bytes

        checksum = (0x100 - (sum(all_bytes) & 0xFF)) & 0xFF
        return bytes(all_bytes + [checksum])

    def _parse_response(
        self,
        raw: bytes | None,
        sent_frame: bytes | None = None,
        strip_echo: bool = True,
    ) -> tuple[bool, str, list[str]]:
        """
        Разбор ответа ЭБУ (ISO 9141 или KWP2000).
        strip_echo: убрать префикс, совпадающий с отправленным кадром (эхо FTDI/KKL).
        """
        if not raw or len(raw) < 5:
            return False, "NO DATA", []

        data_raw = raw
        if strip_echo and sent_frame and len(sent_frame) > 0 and len(raw) >= len(sent_frame):
            if raw[: len(sent_frame)] == sent_frame:
                data_raw = raw[len(sent_frame) :]
                logger.debug("Снято эхо (%d байт)", len(sent_frame))
                if len(data_raw) < 5:
                    return False, "NO DATA", []

        if sum(data_raw) & 0xFF != 0:
            if self._protocol == "kwp2000":
                data = data_raw[3:-1] if len(data_raw) > 4 else []
            else:
                data = data_raw[3:-1]
            data_hex = " ".join(f"{b:02X}" for b in data)
            return False, "<DATA ERROR", [data_hex] if self.headers else []

        if self._protocol == "kwp2000":
            data = data_raw[3:-1]
        else:
            data = data_raw[3:-1]

        if not data:
            return False, "NO DATA", []

        data_hex = " ".join(f"{b:02X}" for b in data)
        if self.headers:
            header_hex = " ".join(f"{data_raw[i]:02X}" for i in range(min(3, len(data_raw))))
            lines = [f"{header_hex} {data_hex}"]
        else:
            lines = [data_hex]
        return True, "", lines

    def send_obd(self, hex_str: str) -> tuple[bool, str | None, list[str]]:
        """
        Отправить OBD-команду (hex, например "01 0C" или "010C").
        Возвращает (success, error_message, data_lines).
        """
        hex_str = hex_str.replace(" ", "").strip().upper()
        if not hex_str:
            return False, "?", []
        if len(hex_str) % 2 != 0:
            return False, "?", []

        try:
            data_bytes = [int(hex_str[i : i + 2], 16) for i in range(0, len(hex_str), 2)]
        except ValueError:
            return False, "?", []

        if len(data_bytes) > 7:
            return False, "?", []

        self._last_command = hex_str

        ok, init_err = self._ensure_bus_init()
        if not ok:
            return False, "BUS INIT: " + init_err, []

        frame = self._build_frame(data_bytes)
        if self.verbose:
            logger.info("OBD >> %s (кадр: %s)", hex_str, frame.hex())
        try:
            send_frame(self._serial, frame)
        except serial.SerialException:
            logger.exception("Ошибка записи в порт")
            return False, "SERIAL ERROR", []

        timeout_sec = self.timeout_ms / 1000.0
        self._serial.timeout = max(0.5, timeout_sec)
        try:
            raw = self._serial.read(64)
        except serial.SerialException:
            logger.exception("Ошибка чтения из порта")
            return False, "SERIAL ERROR", []

        if self.verbose:
            r = raw.hex() if raw else "нет данных (таймаут)"
            logger.info("OBD << %s", r)
        if not raw or len(raw) < 4:
            return False, "NO DATA", []

        success, err, lines = self._parse_response(raw, sent_frame=frame, strip_echo=True)
        if not success and err == "NO DATA":
            return False, "NO DATA", []
        if not success and err == "<DATA ERROR":
            return False, "<DATA ERROR", lines
        return True, None, lines

    def close(self) -> None:
        """Закрыть соединение с KKL."""
        kkl.set_disconnect_handler(None)
        if self._serial:
            close(self._serial)
            self._serial = None
        self._bus_init_done = False
