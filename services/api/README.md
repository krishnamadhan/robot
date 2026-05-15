# services/api — Debug HTTP API

FastAPI server on port 8000. Runs inside the cosmo process as an async task.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /state | Emotional state, attention target, active behavior, last 3 events |
| GET | /memory/recent | Last 20 episodic memory rows |
| GET | /health | Uptime, CPU temp, free RAM |
| POST | /trigger/describe | Capture frame + Claude vision describe (logs token cost) |
| GET | /docs | FastAPI auto-docs |

## Events Consumed
None — reads state via injected references (`wire_state()`)

## Events Emitted
None

## Starting
Called from `tools/cosmo_demo.py` at startup:
```python
from services.api import service as api_service
api_service.wire_state(_state, _state["events"])
await api_service.start()
```

## Known Issues
- /trigger/describe uses claude-sonnet-4-6 (vision model) — ~$0.003/call
- Camera capture opens /dev/video0 briefly — may conflict if vision_loop holds it
