"""Тесты kkl и связанных утилит."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import kkl
from elm323_emulator import ELM323Emulator, build_iso9141_checksum_for_payload
from obd_display import (
    fmt_pid_05,
    fmt_pid_0D,
    format_pid_response,
)


def test_kwp_verify_response_valid():
    # Сумма mod 256 = 0 (кадр StartCommunication в прошивке имеет другую длину/состав)
    raw = bytes([0x01, 0x02, 0x03, 0xFA])
    assert kkl._kwp_verify_response(raw) is True


def test_kwp_verify_response_invalid():
    raw = bytes([0x01, 0x02, 0x03, 0x04])
    assert kkl._kwp_verify_response(raw) is False
    assert kkl._kwp_verify_response(b"") is False


def test_build_iso9141_checksum():
    # ISO 9141: сумма всех байт кадра mod 256 = 0 (не 0x06 — типичная опечатка в примерах)
    payload = [0x68, 0x6A, 0xF1, 0x01, 0x00]
    chk = build_iso9141_checksum_for_payload(payload)
    assert chk == 0x3C
    assert (sum(payload + [chk]) & 0xFF) == 0


def test_emulator_build_frame_iso():
    emu = ELM323Emulator.__new__(ELM323Emulator)
    emu._protocol = "iso9141"
    emu._header = (0x68, 0x6A, 0xF1)
    frame = ELM323Emulator._build_frame(emu, [0x01, 0x00])
    assert (sum(frame) & 0xFF) == 0


def test_emulator_build_frame_kwp():
    emu = ELM323Emulator.__new__(ELM323Emulator)
    emu._protocol = "kwp2000"
    emu._header = (0x68, 0x6A, 0xF1)
    frame = ELM323Emulator._build_frame(emu, [0x01, 0x0C])
    assert (sum(frame) & 0xFF) == 0


def test_format_pid_response_0105():
    lines = ["41 05 7B"]
    s = format_pid_response("0105", lines)
    assert s is not None
    assert "ОЖ" in s or "°C" in s


def test_fmt_pid_05_direct():
    data = [0x41, 0x05, 0x7B]
    s = fmt_pid_05(data)
    assert s is not None
    assert "83" in s  # 0x7B=123, 123-40=83


def test_fmt_pid_0d():
    s = fmt_pid_0D([0x41, 0x0D, 0x3C])
    assert s and "60" in s


@patch("kkl.time.sleep", lambda *a, **k: None)
@patch("kkl.iso_9141_handshake")
@patch("kkl.open_kkl")
@patch("kkl.send_5_baud_init")
@patch("kkl.send_kwp_fast_init")
def test_init_bus_three_attempts_on_handshake_fail(mock_fast, mock_slow, mock_open, mock_hs):
    mock_fast.return_value = (None, "fast fail", "")
    mock_slow.side_effect = lambda *a, **k: None
    mock_open.return_value = MagicMock()
    mock_hs.return_value = (False, "ACK timeout")

    ser, err, proto = kkl.init_bus(
        "/dev/fake",
        max_retries=3,
        retry_delay_sec=0.0,
        try_fast_first=True,
        verbose=False,
    )
    assert ser is None
    assert "Превышено число попыток" in err
    assert mock_hs.call_count == 3
