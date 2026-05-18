"""
STUB — activate when INMP441 mic is wired to GPIO.

Implementation plan when mic is ready:
- Install: pip3 install vosk sounddevice
- Model: vosk-model-small-en-in-0.4 (Indian English, ~50MB)
  Download from https://alphacephei.com/vosk/models
  Place at models/vosk-model-small-en-in-0.4/

- Restricted vocabulary (Vosk grammar mode):
  ["cosmo", "follow", "stop", "sleep", "wake up",
   "dance", "come", "hello", "spin", "look"]

- On keyword match → publish to event_bus:
    COMMAND_FOLLOW, COMMAND_STOP, COMMAND_SLEEP,
    COMMAND_WAKE, COMMAND_DANCE, COMMAND_SPIN

- Architecture: always-listening keyword spotter
  No wake word. No LLM. No TTS.
  Mic → Vosk (restricted vocab) → event → behavior tree.

- Add to BT COMMAND branch (already stubbed as FAILURE):
    COMMAND selector at priority 2 (below SAFETY, above SOCIAL)
    Each command maps to a behavior + sound:
      follow → enter_follow_mode + curious_pip
      sleep  → DoEnterSleep + yawn_sweep
      wake   → DoWakeUp + wake_chime
      dance  → DoDance (spin motors) + excited_trill
      stop   → motors.stop() + beep_ack
"""


class VoiceCommandListener:

    def __init__(self):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    def is_running(self) -> bool:
        return False


voice_command_listener = VoiceCommandListener()
