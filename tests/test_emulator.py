"""Тесты эмулятора ELM323."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from elm323_emulator import ELM323Emulator


def test_send_obd_hex_parsing():
    emu = ELM323Emulator(port="/dev/fake")
    emu._protocol = "iso9141"
    emu._bus_init_done = True
    emu._serial = MagicMock()
    # Ответ: 68 6A F1 41 0D 3C + checksum (скорость 60 км/ч)
    resp_body = [0x68, 0x6A, 0xF1, 0x41, 0x0D, 0x3C]
    chk = (0x100 - (sum(resp_body) & 0xFF)) & 0xFF
    full = bytes(resp_body + [chk])
    assert (sum(full) & 0xFF) == 0
    emu._serial.read.return_value = full

    with patch.object(ELM323Emulator, "_ensure_bus_init", return_value=(True, "")):
        with patch("elm323_emulator.send_frame"):
            ok, err, lines = emu.send_obd("010D")
    assert ok is True
    assert err is None
    assert lines


def test_send_obd_invalid_hex():
    emu = ELM323Emulator(port="COM1")
    ok, err, lines = emu.send_obd("01")
    assert ok is False
    assert lines == []


def test_parse_response_strip_echo():
    emu = ELM323Emulator.__new__(ELM323Emulator)
    emu._protocol = "iso9141"
    emu.headers = False
    # Отправленный кадр для 01 0C
    sb = [0x68, 0x6A, 0xF1, 0x01, 0x0C]
    chk_s = (0x100 - (sum(sb) & 0xFF)) & 0xFF
    sent = bytes(sb + [chk_s])
    rb = [0x68, 0x6A, 0xF1, 0x41, 0x0C, 0x01, 0x02]
    chk_r = (0x100 - (sum(rb) & 0xFF)) & 0xFF
    inner = bytes(rb + [chk_r])
    assert (sum(inner) & 0xFF) == 0
    raw = sent + inner
    ok, err, lines = emu._parse_response(raw, sent_frame=sent, strip_echo=True)
    assert ok is True
    assert not err
    assert lines


def test_parse_response_no_strip_when_mismatch():
    emu = ELM323Emulator.__new__(ELM323Emulator)
    emu._protocol = "iso9141"
    emu.headers = False
    rb = [0x68, 0x6A, 0xF1, 0x41, 0x0C, 0x10, 0x20]
    chk_r = (0x100 - (sum(rb) & 0xFF)) & 0xFF
    inner = bytes(rb + [chk_r])
    assert (sum(inner) & 0xFF) == 0
    ok, _, _ = emu._parse_response(inner, sent_frame=b"\xaa\xbb", strip_echo=True)
    assert ok is True
