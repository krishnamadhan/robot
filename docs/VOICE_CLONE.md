# Voice Clone V1

Cosmo's default speech engine is Piper. Cloned voice output is optional and
remote-only in V1. If the remote Voicebox service is missing, slow, or errors,
speech falls back to Piper.

## Runtime Model

- Local default: `PiperVoiceEngine`
  - binary: `/usr/local/bin/piper`
  - model: `~/.robot/models/piper/en_US-lessac-medium.onnx`
  - output: raw mono `s16le` at 22050 Hz
- Optional clone engine: `RemoteVoiceboxEngine`
  - enabled only when `VOICEBOX_URL` is set
  - requires a locally consented profile
  - calls `POST /generate`
- Optional Replicate clone engine: `ReplicateVoiceEngine`
  - enabled with `VOICE_ENGINE=replicate` and `REPLICATE_API_TOKEN`
  - model slug: `lucataco/xtts-v2`
  - API base: `https://api.replicate.com/v1`
  - if `REPLICATE_API_TOKEN` is unset, startup continues with Piper fallback
- Consent profile:
  - `~/.robot/memory/voices/<name>/reference.wav`
  - `~/.robot/memory/voices/<name>/consent.json`

`expression.speech.tts` keeps the existing app API:

```python
tts.set_voice_profile("Madhan")  # no-op unless consent metadata exists
await tts.speak("Vanakkam da")
```

## Voicebox HTTP Contract

The real server does not exist yet. Tests use a local HTTP mock and pin this V1
contract so the robot does not block on future server work.

`POST /generate`

```json
{
  "text": "Hello",
  "voice": "Madhan",
  "reference_wav": "/home/pi/.robot/memory/voices/madhan/reference.wav"
}
```

Response may be `audio/wav`, raw bytes, or JSON:

```json
{
  "audio_base64": "...",
  "sample_rate": 22050,
  "encoding": "wav"
}
```

`POST /transcribe`

```json
{
  "audio_base64": "...",
  "sample_rate": 22050,
  "encoding": "wav"
}
```

Response:

```json
{ "text": "transcript" }
```

## Enrollment

Enrollment is explicitly consent-gated. The script refuses to record unless the
operator types the exact consent phrase shown on screen.

```bash
PYTHONPATH=/home/pi/robot python3 tools/enroll_voice.py --name Madhan
```

Optional future/mock Voicebox transcript check:

```bash
VOICEBOX_URL=http://127.0.0.1:8088 \
PYTHONPATH=/home/pi/robot python3 tools/enroll_voice.py --name Madhan
```

Replicate cloned synthesis:

```bash
VOICE_ENGINE=replicate \
REPLICATE_API_TOKEN=... \
PYTHONPATH=/home/pi/robot python3 tools/voice_test.py --name Madhan
```

To revoke a profile, remove its directory:

```bash
rm -r ~/.robot/memory/voices/madhan
```

Run destructive commands only after confirming the target path.

## Test Tools

Speak through the active engine:

```bash
VOICEBOX_URL=http://127.0.0.1:8088 \
PYTHONPATH=/home/pi/robot python3 tools/voice_test.py --name Madhan
```

Force the remote path to fail and verify Piper still speaks:

```bash
PYTHONPATH=/home/pi/robot python3 tools/voice_fallback_test.py --name Madhan
```

If the profile is missing or unconsented, both tools use Piper.

## Safety Rules

- Piper stays the default and final fallback.
- The robot never waits for the real Voicebox server to exist.
- A cloned voice requires local `consent.json` with `consent_granted: true`.
- No model larger than the existing Piper model is loaded into the robot process.
- Voicebox failures are logged as warnings and do not make Cosmo mute.
