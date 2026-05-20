# PERFORMANCE_LOG.md — Resource Measurements

> Pi 5 8GB — measure before and after significant changes.  
> Target: total idle CPU < 30%, total idle RAM < 3GB, single service idle CPU < 5%.

---

## How to Measure

```bash
# Full system snapshot
free -h && top -bn1 | head -20

# Per-process CPU + RAM
pm2 status   # shows basic CPU/memory
ps aux --sort=-%mem | head -20  # top memory consumers

# Temperature + throttle
vcgencmd measure_temp
vcgencmd get_throttled   # 0x0 = healthy, anything else = problem

# Per-loop timing in vision pipeline
pm2 logs cosmo_demo --lines 100 --nostream | grep "fps\|loop\|latency"

# Audio pipeline latency
pm2 logs cosmo_demo --lines 50 --nostream | grep "stt\|tts\|latency\|pipeline"

# Claude API token usage check
pm2 logs cosmo_demo --lines 100 --nostream | grep "token\|api_call\|budget"
```

---

## Baseline Measurements

### System Idle — 2026-05-20 (all services running, person not present)

| Metric | Value | Date | Notes |
|---|---|---|---|
| Total RAM used | ~1.6GB | 2026-05-20 | PM2 + OS + all services |
| Free RAM | 6044MB | 2026-05-20 | Plenty of headroom |
| Pi temperature (idle) | 55.4°C | 2026-05-20 | Active cooler running |
| Throttle status | TBD | — | Run vcgencmd get_throttled |

### Per-Service Resource (2026-05-20)

| Service | CPU % | RAM | FPS/Rate | Date |
|---|---|---|---|---|
| cosmo (full, idle) | ~0% | 480MB | — | 2026-05-20 |
| banteragent | ~0% | 47MB | event | 2026-05-20 |
| battery-monitor | ~0% | 23MB | poll | 2026-05-20 |
| pi-monitor | ~0% | 25MB | poll | 2026-05-20 |
| pi-scheduler | ~0% | 56MB | cron | 2026-05-20 |
| Vision capture loop | TBD | TBD | 30 fps | — |
| Vision detection loop | TBD | TBD | 15 fps | — |
| Vision recognition loop | TBD | TBD | 4 fps | — |
| Vision emotion loop | TBD | TBD | 2 fps | — |
| Audio wake word listen | TBD | TBD | always-on | — |

### Latency Measurements

| Pipeline | Target | Measured | Date | Notes |
|---|---|---|---|---|
| Wake word detection | <100ms | ~80ms | Phase 1 | OpenWakeWord |
| STT (speech→text) | <2s | ~1.5s | Phase 1 | faster-whisper base.en beam=5 |
| Claude Haiku response | <3s | ~1-2s | Phase 1 | Including network round-trip |
| Piper TTS generation | <2s | ~1.8s | Phase 1 | Per sentence |
| Full pipeline (hear→speak) | <5s | ~2-3s | Phase 1 | End-to-end |
| Face recognition | <300ms | TBD | — | SFace ONNX at 4 FPS |
| Person detection | <100ms | TBD | — | YOLOv8n at 320×240 |
| Emotion detection | <500ms | TBD | — | DeepFace at 2 FPS |

---

## Performance Regressions

> Log here if a change caused CPU/RAM/latency to get worse

| Date | Change | Before | After | Action |
|---|---|---|---|---|
| — | — | — | — | — |

---

## Optimization Notes

- Vision: detection loop at 15 FPS is the right balance. Do not increase — CPU spikes.
- Recognition loop at 4 FPS is sufficient for real-time face ID. Do not increase.
- Emotion loop at 2 FPS is plenty — emotions don't change faster than that.
- Wake word must stay always-on at low CPU. Do not add processing to that loop.
- Claude API calls must never be triggered more than once per cooldown window (face_seen=45s, emotion=60s, alone=180s).
