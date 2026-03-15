"""
Программная эмуляция ELM323.
Реализует протокол и тайминги по ELM323DS для работы поверх VAG KKL.
"""
from __future__ import annotations

from typing import TypedDict

from kkl import (
    init_bus,
    send_frame,
    read_response,
    close,
    DEFAULT_BAUD,
    OBD2_ADDRESS,
)


# ISO 9141 заголовок по умолчанию: 68 6A F1
DEFAULT_HEADER_ISO9141 = (0x68, 0x6A, 0xF1)


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
        self.echo = False
        self.headers = False
        self.linefeed = True
        self.timeout_ms = 200  # AT ST default ~200ms (32 hex * 4ms)
        self._header = DEFAULT_HEADER_ISO9141
        self._protocol = "iso9141"  # или "kwp2000"

    def _ensure_bus_init(self) -> tuple[bool, str]:
        """
        Инициализировать шину при первом обращении.
        Возвращает (success, error_reason).
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
            print("[ELM323] Запуск инициализации шины...")
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
            if self.verbose:
                print("[ELM323] Шина инициализирована (%s)" % self._protocol)
            return True, ""
        if self.verbose:
            print("[ELM323] ОШИБКА: %s" % err)
        return False, err

    def _build_frame(self, data_bytes: list[int]) -> bytes:
        """Собрать кадр: ISO 9141 или KWP2000."""
        if self._protocol == "kwp2000":
            # KWP2000: [0xC0|len] 33 F1 data... checksum
            length_byte = 0xC0 | len(data_bytes)
            all_bytes = [length_byte, 0x33, 0xF1] + data_bytes
        else:
            # ISO 9141: 68 6A F1 data... checksum (sum=0)
            h0, h1, h2 = self._header
            all_bytes = [h0, h1, h2] + data_bytes

        s = sum(all_bytes) & 0xFF
        checksum = (0x100 - s) & 0xFF
        return bytes(all_bytes + [checksum])

    def _parse_response(self, raw: bytes | None) -> tuple[bool, str, list[str]]:
        """
        Разбор ответа ЭБУ (ISO 9141 или KWP2000).
        Возвращает (success, error_msg, data_lines).
        """
        if not raw or len(raw) < 5:
            return False, "NO DATA", []

        if sum(raw) & 0xFF != 0:
            if self._protocol == "kwp2000":
                data = raw[3:-1] if len(raw) > 4 else []
            else:
                data = raw[3:-1]
            data_hex = " ".join(f"{b:02X}" for b in data)
            return False, "<DATA ERROR", [data_hex] if self.headers else []

        if self._protocol == "kwp2000":
            # KWP: [len] [target] [source] [data...] [checksum]
            data = raw[3:-1]
        else:
            # ISO 9141: [h0 h1 h2] [data...] [checksum]
            data = raw[3:-1]

        if not data:
            return False, "NO DATA", []

        data_hex = " ".join(f"{b:02X}" for b in data)
        if self.headers:
            header_hex = " ".join(f"{raw[i]:02X}" for i in range(min(3, len(raw))))
            lines = [f"{header_hex} {data_hex}"]
        else:
            lines = [data_hex]
        return True, "", lines

    def send_obd(self, hex_str: str) -> tuple[bool, str | None, list[str]]:
        """
        Отправить OBD-команду (hex, например "01 0C" или "010C").
        Возвращает (success, error_message, data_lines).
        При success=True data_lines содержат данные; при False — error_message.
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
            print("[ELM323] OBD >> %s (кадр: %s)" % (hex_str, frame.hex()))
        send_frame(self._serial, frame)

        timeout_sec = self.timeout_ms / 1000.0
        self._serial.timeout = max(0.5, timeout_sec)
        raw = self._serial.read(64)

        if self.verbose:
            r = raw.hex() if raw else "нет данных (таймаут)"
            print("[ELM323] OBD << %s" % r)
        if not raw or len(raw) < 4:
            return False, "NO DATA", []

        success, err, lines = self._parse_response(raw)
        if not success and err == "NO DATA":
            return False, "NO DATA", []
        if not success and err == "<DATA ERROR":
            return False, "<DATA ERROR", lines
        return True, None, lines

    def close(self) -> None:
        """Закрыть соединение с KKL."""
        if self._serial:
            close(self._serial)
            self._serial = None
            self._bus_init_done = False
