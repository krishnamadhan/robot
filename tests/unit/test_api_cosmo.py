"""
Phase 5 — WhatsApp control surface endpoints on the debug API.

banteragent's !cosmo command proxies to these; they must hold their
contract (shapes + error codes) since the proxy is deployed dormant.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.capabilities import Capability, registry
from services.api import service


@pytest.fixture
def client():
    return TestClient(service.app)


@pytest.fixture
def _restore_caps():
    saved = {c: registry.state(c) for c in Capability}
    yield
    for c, s in saved.items():
        registry.set_state(c, s, "test restore")


class TestCaps:

    def test_caps_snapshot_shape(self, client):
        r = client.get("/caps")
        assert r.status_code == 200
        body = r.json()
        assert "locomotion" in body
        assert all(isinstance(v, str) for v in body.values())


class TestSim:

    def test_sim_valid_cap(self, client, _restore_caps):
        r = client.post("/cosmo/sim", json={"cap": "locomotion"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["state"] == "simulated"
        assert registry.has(Capability.LOCOMOTION)

    def test_sim_unknown_cap_400_with_valid_list(self, client):
        r = client.post("/cosmo/sim", json={"cap": "jetpack"})
        assert r.status_code == 400
        assert "locomotion" in r.json()["valid"]

    def test_sim_case_insensitive(self, client, _restore_caps):
        r = client.post("/cosmo/sim", json={"cap": "  LOCOMOTION "})
        assert r.status_code == 200


class TestSay:

    def test_say_empty_400(self, client):
        r = client.post("/cosmo/say", json={"text": "  "})
        assert r.status_code == 400

    def test_say_speaks(self, client):
        with patch("expression.speech.tts") as tts:
            tts.speak = AsyncMock()
            r = client.post("/cosmo/say", json={"text": "vanakkam da"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "text": "vanakkam da"}
        tts.speak.assert_called_once_with("vanakkam da")

    def test_say_truncates_to_300(self, client):
        with patch("expression.speech.tts") as tts:
            tts.speak = AsyncMock()
            r = client.post("/cosmo/say", json={"text": "x" * 500})
        assert len(r.json()["text"]) == 300


class TestLast:

    def test_last_shape(self, client):
        service.wire_state({"last_response": "hi"}, [{"e": 1}, {"e": 2}])
        try:
            r = client.get("/cosmo/last?n=1")
            body = r.json()
            assert body["events"] == [{"e": 2}]
            assert body["last_response"] == "hi"
        finally:
            service.wire_state({}, [])

    def test_last_n_clamped(self, client):
        r = client.get("/cosmo/last?n=999")
        assert r.status_code == 200


class TestLed:

    def test_led_pattern_named_stops_animation_and_ambilight(self, client):
        with patch("hardware.led_strip.strip") as strip, patch("behavior.ambilight.ambilight") as ambilight:
            strip.stop_animation = AsyncMock()
            strip.set_pattern = AsyncMock(return_value=True)
            strip.state = {"mode": "pattern:rainbow", "on": True}
            ambilight.active = True
            ambilight.stop = AsyncMock()
            r = client.post("/led", json={"cmd": "pattern", "value": "rainbow"})
        assert r.status_code == 200
        assert r.json()["mode"] == "pattern:rainbow"
        strip.stop_animation.assert_awaited_once()
        ambilight.stop.assert_awaited_once()
        strip.set_pattern.assert_awaited_once_with("rainbow")

    def test_led_music_off_maps_to_zero(self, client):
        with patch("hardware.led_strip.strip") as strip, patch("behavior.ambilight.ambilight") as ambilight:
            strip.stop_animation = AsyncMock()
            strip.set_music = AsyncMock(return_value=True)
            strip.state = {"mode": None, "on": True}
            ambilight.active = False
            ambilight.stop = AsyncMock()
            r = client.post("/led", json={"cmd": "music", "value": "off"})
        assert r.status_code == 200
        strip.stop_animation.assert_awaited_once()
        ambilight.stop.assert_not_awaited()
        strip.set_music.assert_awaited_once_with(0)

    def test_led_temp_accepts_range_value(self, client):
        with patch("hardware.led_strip.strip") as strip, patch("behavior.ambilight.ambilight") as ambilight:
            strip.stop_animation = AsyncMock()
            strip.set_color_temp = AsyncMock(return_value=True)
            strip.state = {"mode": "temp:72", "on": True}
            ambilight.active = False
            ambilight.stop = AsyncMock()
            r = client.post("/led", json={"cmd": "temp", "value": 72})
        assert r.status_code == 200
        strip.stop_animation.assert_awaited_once()
        strip.set_color_temp.assert_awaited_once_with(72)

    def test_led_unknown_cmd_lists_new_modes(self, client):
        r = client.post("/led", json={"cmd": "sparkle", "value": "x"})
        assert r.status_code == 400
        body = r.json()
        assert "pattern" in body["error"]
        assert "music" in body["error"]
        assert "temp" in body["error"]
