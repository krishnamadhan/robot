"""
TTS speech output.

Engine priority:
  1. Piper TTS (neural, natural voice — en_US-lessac-medium)
  2. espeak-ng fallback (robotic but always available)

Audio output: pw-play → @DEFAULT_SINK@ (JBL Flip 5 via PipeWire BT)
"""

import asyncio
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

_PW_ENV = {
    **os.environ,
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
}

BT_SINK_NAME = "bluez_output.28_FA_19_C1_73_F8.1"
PW_DEFAULT_SINK = "@DEFAULT_SINK@"
PIPER_MODEL = Path.home() / ".robot" / "models" / "piper" / "en_US-lessac-medium.onnx"
_PREBAKE_DIR = Path("/tmp/cosmo_sounds")

# Instant Tanglish voice lines — pre-generated at startup, played with zero LLM latency
_INSTANT_LINES: dict[str, str] = {
    # Greetings per person
    "greet_Madhan_0": "Hey Madhan! Good to see you!",
    "greet_Madhan_1": "Madhan! I was wondering where you went.",
    "greet_Madhan_2": "Oh hey Madhan! Finally!",
    "greet_Madhan_3": "Madhan is here! Now things get interesting.",
    "greet_Madhan_4": "Hey, you're back! Missed you.",
    "greet_Indhu_0":  "Hi Indhu! So good to see you!",
    "greet_Indhu_1":  "Indhu! Welcome back!",
    "greet_Indhu_2":  "Hey Indhu, I'm happy you're here.",
    "greet_Indhu_3":  "Oh Indhu! I was getting a bit lonely.",
    "greet_Indhu_4":  "Hi Indhu! Come on in.",
    "greet_stranger_0": "Oh, someone new! Hello there.",
    "greet_stranger_1": "Hi! I don't think we've met.",
    # Touch reactions
    "touch_head":  "Ooh, that feels nice!",
    "touch_belly": "Hey, that tickles!",
    # Lonely
    "alone_0": "It's pretty quiet around here.",
    "alone_1": "Anyone home? Starting to miss the company.",
    "alone_2": "All by myself again. Hope someone comes back soon.",
}


class TTSEngine:
    """Text-to-speech: Piper (neural) → espeak-ng fallback → pw-play."""

    def __init__(self) -> None:
        self._espeak_available = self._check_espeak()
        self._piper_available = self._check_piper()
        self._voice = "en+f3"     # female voice, warm (espeak fallback)
        self._speed = 150         # words per minute (espeak fallback)
        self._pitch = 55          # 0-99 (espeak fallback)
        self._playing = False
        self._player = self._detect_player()
        self._prebaked: dict[str, str] = {}
        self._prebake_all()
        # Lazy-init: asyncio.Lock must be created inside a running loop
        self.__audio_lock: Optional[asyncio.Lock] = None
        log.info("tts.init", player=self._player, piper=self._piper_available,
                  espeak=self._espeak_available, prebaked=len(self._prebaked))

    @property
    def _audio_lock(self) -> asyncio.Lock:
        if self.__audio_lock is None:
            self.__audio_lock = asyncio.Lock()
        return self.__audio_lock

    def _prebake_all(self) -> None:
        """Generate all sounds + instant voice lines to disk at startup."""
        _PREBAKE_DIR.mkdir(exist_ok=True)
        for name in ["beep_ack", "boot_chime", "chirp_curious", "chirp_happy",
                     "whimper_sad", "purr_content", "happy_trill"]:
            path = _PREBAKE_DIR / f"{name}.wav"
            if not path.exists():
                try:
                    ok = self._generate_sound_to_file(name, str(path))
                    if not ok:
                        continue
                except Exception as e:
                    log.warning("tts.prebake_failed", sound=name, error=str(e))
                    continue
            self._prebaked[name] = str(path)

        # Pre-generate Tanglish voice lines via Piper (skipped if Piper not available)
        if self._piper_available:
            for key, text in _INSTANT_LINES.items():
                path = _PREBAKE_DIR / f"line_{key}.wav"
                if not path.exists():
                    try:
                        self._tts_to_file(text, str(path))
                    except Exception as e:
                        log.warning("tts.voice_line_prebake_failed", key=key, error=str(e))
                        continue
                if path.exists():
                    self._prebaked[f"line_{key}"] = str(path)
        log.info("tts.prebaked", sounds=len(self._prebaked))

    def _generate_sound_to_file(self, sound_type: str, out_path: str) -> bool:
        """Generate a sound and save at 44100 Hz stereo WAV. Returns True on success."""
        import numpy as np, wave as wave_module
        sr = 22050
        sounds = {
            "boot_chime":    self._chime([523, 659, 784], sr),
            "beep_ack":      self._tone(880, 0.15, sr),
            "chirp_curious": self._chirp(600, 1200, 0.2, sr),
            "chirp_happy":   self._chirp(800, 1600, 0.25, sr),
            "whimper_sad":   self._chirp(500, 250, 0.4, sr),
            "purr_content":  self._purr(120, 2.0, sr),
            "happy_trill":   self._trill(700, 0.4, sr),
        }
        samples = sounds.get(sound_type)
        if samples is None:
            return False
        tmp = out_path + ".tmp.wav"
        with wave_module.open(tmp, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(samples.tobytes())
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp, "-ar", "44100", "-ac", "2", out_path],
            capture_output=True, timeout=10,
        )
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return r.returncode == 0

    def _tts_to_file(self, text: str, out_path: str) -> bool:
        """Run Piper TTS on text, upsample, save to out_path. Returns True on success."""
        import tempfile
        tmp_raw = out_path + ".raw.wav"
        try:
            r = subprocess.run(
                ["piper", "--model", str(PIPER_MODEL),
                 "--length_scale", "0.9", "--output_file", tmp_raw],
                input=text.encode(), capture_output=True, timeout=20,
            )
            if r.returncode != 0:
                return False
            up = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_raw, "-ar", "44100", "-ac", "2", out_path],
                capture_output=True, timeout=10,
            )
            return up.returncode == 0
        except Exception:
            return False
        finally:
            try:
                import os as _os; _os.unlink(tmp_raw)
            except Exception:
                pass

    def _check_piper(self) -> bool:
        try:
            r = subprocess.run(["piper", "--help"], capture_output=True, timeout=3)
            return r.returncode in (0, 1) and PIPER_MODEL.exists()
        except Exception:
            return False

    def _check_espeak(self) -> bool:
        try:
            r = subprocess.run(["espeak-ng", "--version"],
                               capture_output=True, timeout=2)
            return r.returncode == 0
        except Exception:
            return False

    def _detect_player(self) -> str:
        """Return best available audio player."""
        # pw-play: native PipeWire, handles BT natively
        try:
            r = subprocess.run(["pw-play", "--help"],
                               capture_output=True, timeout=2)
            if r.returncode in (0, 1):
                return "pw-play"
        except Exception:
            pass
        # paplay: PulseAudio/PipeWire compat
        try:
            r = subprocess.run(["paplay", "--version"],
                               capture_output=True, timeout=2)
            if r.returncode == 0:
                return "paplay"
        except Exception:
            pass
        return "aplay"

    def _bt_sink_active(self) -> bool:
        """Check if BT speaker is connected and available."""
        try:
            r = subprocess.run(
                ["pactl", "list", "sinks", "short"],
                capture_output=True, text=True, timeout=3, env=_PW_ENV
            )
            return BT_SINK_NAME in r.stdout
        except Exception:
            return False

    def _play_wav(self, wav_path: str) -> bool:
        """Play WAV file, return True on success."""
        bt_active = self._bt_sink_active()

        if self._player == "pw-play":
            # Always route to @DEFAULT_SINK@ — lets PipeWire pick BT or HDMI
            cmd = ["pw-play", "--target", PW_DEFAULT_SINK, wav_path]
            r = subprocess.run(cmd, capture_output=True, timeout=30,
                               env=_PW_ENV)
            return r.returncode == 0

        if self._player == "paplay":
            cmd = ["paplay"]
            if bt_active:
                cmd += ["--device", BT_SINK_NAME]
            cmd.append(wav_path)
            r = subprocess.run(cmd, capture_output=True, timeout=30,
                               env=_PW_ENV)
            return r.returncode == 0

        # aplay fallback
        r = subprocess.run(["aplay", "-q", wav_path],
                           capture_output=True, timeout=30)
        return r.returncode == 0

    async def speak(self, text: str, interrupt: bool = True) -> None:
        if not (self._piper_available or self._espeak_available) or not text.strip():
            return
        if self._audio_lock.locked():
            log.debug("tts.speak_dropped", reason="audio_busy", text_preview=text[:40])
            return
        cleaned = self._clean_text(text)
        async with self._audio_lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._speak_sync, cleaned)

    def _speak_sync(self, text: str) -> None:
        self._playing = True
        t0 = time.monotonic()
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name

            if self._piper_available:
                gen = subprocess.run(
                    ["piper",
                     "--model", str(PIPER_MODEL),
                     "--length_scale", "0.9",
                     "--output_file", wav_path],
                    input=text.encode(),
                    capture_output=True,
                    timeout=15,
                )
                engine = "piper"
            else:
                gen = subprocess.run(
                    ["espeak-ng", "-v", self._voice,
                     "-s", str(self._speed), "-p", str(self._pitch),
                     "-w", wav_path, text],
                    capture_output=True, timeout=10,
                )
                engine = "espeak"

            gen_ms = int((time.monotonic() - t0) * 1000)

            if gen.returncode != 0:
                log.warning("tts.generation_failed", engine=engine,
                             stderr=gen.stderr.decode()[:200])
                # Piper failed — try espeak fallback
                if engine == "piper" and self._espeak_available:
                    subprocess.run(
                        ["espeak-ng", "-v", self._voice, "-s", str(self._speed),
                         "-p", str(self._pitch), "-w", wav_path, text],
                        capture_output=True, timeout=10,
                    )
                else:
                    return

            # Upsample to 44100 Hz stereo for BT speaker quality
            # Piper outputs 22050 Hz mono — PipeWire resampling causes grain
            upsampled = wav_path.replace(".wav", "_44k.wav")
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path,
                 "-ar", "44100", "-ac", "2", upsampled],
                capture_output=True, timeout=10,
            )
            play_path = upsampled if r.returncode == 0 else wav_path

            ok = self._play_wav(play_path)
            total_ms = int((time.monotonic() - t0) * 1000)
            log.info("tts.spoke", engine=engine, gen_ms=gen_ms, total_ms=total_ms,
                      audio_ok=ok, player=self._player,
                      text_preview=text[:50])

        except subprocess.TimeoutExpired:
            log.warning("tts.timeout")
        except Exception as e:
            log.error("tts.error", error=str(e))
        finally:
            self._playing = False
            for p in [wav_path, locals().get("upsampled")]:
                if p:
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\*+([^*]+)\*+", r"\1", text)
        text = re.sub(r"`[^`]+`", "", text)
        text = re.sub(r"https?://\S+", "a link", text)
        text = re.sub(r"[#>]", "", text)
        text = text.replace("—", " — ").replace("...", "... ")
        return text.strip()

    def set_mood_params(self, mood: float, energy: float) -> None:
        self._speed = int(130 + energy * 40)    # 130–170 WPM
        self._pitch = int(50 + mood * 15)        # 35–65

    async def speak_instant(self, key: str) -> bool:
        """
        Play a pre-baked Tanglish voice line instantly (no LLM, no TTS generation).
        key: one of the _INSTANT_LINES keys, e.g. 'greet_Madhan_0'.
        Returns True if played, False if line not available (caller can fall back to TTS).
        """
        wav = self._prebaked.get(f"line_{key}")
        if not wav:
            return False
        if self._audio_lock.locked():
            log.debug("tts.instant_dropped", key=key, reason="audio_busy")
            return False
        async with self._audio_lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._play_wav, wav)
        log.debug("tts.instant_played", key=key)
        return True

    async def play_sound(self, sound_type: str) -> None:
        """Play a non-speech sound. Skipped (not queued) if any audio is playing."""
        if self._audio_lock.locked():
            log.debug("tts.sound_dropped", reason="audio_busy", sound=sound_type)
            return
        async with self._audio_lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._play_sound_sync, sound_type)

    def _play_sound_sync(self, sound_type: str) -> None:
        # Use pre-baked file (no generation overhead — critical for <200ms beep_ack)
        if sound_type in self._prebaked:
            self._play_wav(self._prebaked[sound_type])
            return

        # On-the-fly fallback for sounds not in prebake cache
        import numpy as np, wave, struct
        sr = 22050
        sounds = {
            "boot_chime":      self._chime([523, 659, 784], sr),
            "beep_ack":        self._tone(880, 0.15, sr),
            "chirp_curious":   self._chirp(600, 1200, 0.2, sr),
            "chirp_happy":     self._chirp(800, 1600, 0.25, sr),
            "whimper_sad":     self._chirp(500, 250, 0.4, sr),
            "purr_content":    self._purr(120, 2.0, sr),
            "happy_trill":     self._trill(700, 0.4, sr),
        }
        samples = sounds.get(sound_type)
        if samples is None:
            return
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            with wave.open(wav_path, "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(samples.tobytes())
            self._play_wav(wav_path)
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

    @staticmethod
    def _tone(freq: float, dur: float, sr: int) -> "np.ndarray":
        import numpy as np
        t = np.linspace(0, dur, int(sr * dur))
        wave = np.sin(2 * np.pi * freq * t)
        env = np.exp(-t * 4)
        return (wave * env * 20000).astype(np.int16)

    @staticmethod
    def _chirp(f0: float, f1: float, dur: float, sr: int) -> "np.ndarray":
        import numpy as np
        t = np.linspace(0, dur, int(sr * dur))
        freq = np.linspace(f0, f1, len(t))
        phase = np.cumsum(freq / sr * 2 * np.pi)
        env = np.exp(-t * 3)
        return (np.sin(phase) * env * 20000).astype(np.int16)

    @staticmethod
    def _purr(fund: float, dur: float, sr: int) -> "np.ndarray":
        import numpy as np
        t = np.linspace(0, dur, int(sr * dur))
        mod = 0.5 + 0.5 * np.sin(2 * np.pi * 25 * t)
        w = np.sin(2 * np.pi * fund * t) * mod
        for h in [2, 3, 4]:
            w += np.sin(2 * np.pi * fund * h * t) * mod / h
        return (w / w.max() * 12000).astype(np.int16)

    @staticmethod
    def _trill(freq: float, dur: float, sr: int) -> "np.ndarray":
        import numpy as np
        t = np.linspace(0, dur, int(sr * dur))
        vibrato = freq * (1 + 0.1 * np.sin(2 * np.pi * 12 * t))
        phase = np.cumsum(vibrato / sr * 2 * np.pi)
        env = np.sin(np.pi * t / dur)
        return (np.sin(phase) * env * 20000).astype(np.int16)

    @staticmethod
    def _chime(freqs, sr: int) -> "np.ndarray":
        import numpy as np
        parts = []
        for i, f in enumerate(freqs):
            dur = 0.5
            t = np.linspace(0, dur, int(sr * dur))
            env = np.exp(-t * 5)
            parts.append(np.sin(2 * np.pi * f * t) * env * 18000)
        out = np.zeros(len(parts[0]) + (len(freqs) - 1) * int(sr * 0.15))
        for i, p in enumerate(parts):
            offset = i * int(sr * 0.15)
            out[offset:offset + len(p)] += p
        return (out / out.max() * 20000).astype(np.int16)

    async def speak_streaming(self, sentence_gen) -> str:
        """
        Pipeline: synthesize sentence N+1 while sentence N is playing.
        Reduces perceived latency by ~40-60% on multi-sentence responses.
        Returns full concatenated text.
        """
        if not (self._piper_available or self._espeak_available):
            full = []
            async for s in sentence_gen:
                full.append(s)
            return " ".join(full)

        loop = asyncio.get_event_loop()
        play_queue: asyncio.Queue = asyncio.Queue()
        full_text: list = []

        async def _player() -> None:
            while True:
                item = await play_queue.get()
                if item is None:
                    break
                wav_path = item
                try:
                    await loop.run_in_executor(None, self._play_wav, wav_path)
                finally:
                    try:
                        os.unlink(wav_path)
                    except Exception:
                        pass

        player_task = asyncio.create_task(_player())

        async for sentence in sentence_gen:
            sentence = self._clean_text(sentence.strip())
            if not sentence:
                continue
            full_text.append(sentence)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name

            # Synthesize in executor — player_task runs concurrently
            ok = await loop.run_in_executor(
                None, lambda p=wav_path, s=sentence: self._tts_to_file(s, p)
            )
            if ok:
                await play_queue.put(wav_path)
            else:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

        await play_queue.put(None)  # sentinel — tell player to exit
        await player_task

        result = " ".join(full_text)
        if result:
            log.info("tts.streaming_done", sentences=len(full_text),
                     preview=result[:60])
        return result

    @property
    def is_available(self) -> bool:
        return self._piper_available or self._espeak_available

    @property
    def is_speaking(self) -> bool:
        return self._audio_lock.locked()


tts = TTSEngine()
