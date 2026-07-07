"""Frame-builder tests for the LEDDMX strip driver.

Byte layouts verified against github user154lt/LEDDMX-00 (Dmx00Data.kt).
The 0x04 power opcode is intentionally absent — it latches this unit.
"""

from hardware.led_strip import (
    CUSTOM_MODES,
    MUSIC_MODES,
    PATTERNS,
    _bright_frame,
    _color_frame,
    _custom_color_frame,
    _custom_direction_frame,
    _custom_mode_frame,
    _mic_frame,
    _pattern_frame,
    _temp_frame,
)


def test_color_frame():
    assert _color_frame(255, 128, 0) == bytes(
        [0x7B, 0xFF, 0x07, 0xFF, 0x80, 0x00, 0x00, 0xFF, 0xBF])


def test_bright_frame_scaling():
    # AA byte = pct*32//100, PP byte = pct
    assert _bright_frame(100) == bytes(
        [0x7B, 0xFF, 0x01, 32, 100, 0x00, 0xFF, 0xFF, 0xBF])
    assert _bright_frame(50)[3] == 16
    assert _bright_frame(0)[3:5] == bytes([0, 0])
    assert _bright_frame(150)[4] == 100   # clamped


def test_pattern_frame():
    assert _pattern_frame(3) == bytes(
        [0x7B, 0xFF, 0x03, 3, 0xFF, 0xFF, 0xFF, 0xFF, 0xBF])
    assert _pattern_frame(999)[3] == 210  # clamped to table size
    assert _pattern_frame(-5)[3] == 0


def test_mic_frame_is_8_bytes():
    # The mic/EQ frame is one byte SHORTER than the others — per reference.
    f = _mic_frame(4)
    assert f == bytes([0x7B, 0xFF, 0x0B, 4, 0x00, 0xFF, 0xFF, 0xBF])
    assert len(f) == 8
    assert _mic_frame(0)[3] == 0          # 0 = mic off


def test_temp_frame():
    assert _temp_frame(100) == bytes(
        [0x7B, 0xFF, 0x09, 32, 100, 0xFF, 0xFF, 0xFF, 0xBF])
    assert _temp_frame(0)[3:5] == bytes([0, 0])


def test_custom_pattern_frames():
    # Colour list entry: position is byte 1 (not 0xFF), list starts at 1.
    f = _custom_color_frame(1, 255, 0, 128, 3)
    assert f == bytes([0x7B, 0x01, 0x0E, 0xFD, 0xFF, 0x00, 0x80, 0x03, 0xBF])
    assert _custom_mode_frame(CUSTOM_MODES["flow"])[3] == 2
    assert _custom_direction_frame(True)[3] == 0
    assert _custom_direction_frame(False)[3] == 1


def test_no_power_opcode_anywhere():
    # Guard: opcode 0x04 (native power) latches this unit into blackout.
    frames = [
        _color_frame(1, 2, 3), _bright_frame(50), _pattern_frame(3),
        _mic_frame(1), _temp_frame(50), _custom_color_frame(1, 1, 1, 1, 2),
        _custom_mode_frame(0), _custom_direction_frame(True),
    ]
    for f in frames:
        assert f[2] != 0x04, f"opcode 0x04 must never be sent: {f.hex()}"


def test_lookup_tables_sane():
    assert PATTERNS["off"] == 0
    assert all(0 <= v <= 210 for v in PATTERNS.values())
    assert MUSIC_MODES["off"] == 0
    assert CUSTOM_MODES["off"] == 4   # AC = off in the mode enum
