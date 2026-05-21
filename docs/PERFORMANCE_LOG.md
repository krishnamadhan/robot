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

## Pre-Upgrade Baselines (before stack upgrades ADR-012 to ADR-018)

> Captured 2026-05-21. Compare against these after each upgrade to measure gain/regression.

| Component | Model | FPS / Latency | RAM | Input Res | Notes |
|---|---|---|---|---|---|
| Person detection | YOLOv8n (ultralytics) | **never actually ran** | — | 320×240 | torch CUDA build was failing silently (libcublasLt missing) — was falling back to HOG |
| Gesture detection | opencv_skin fallback | ~4 FPS | <5MB | full frame | ~80% accuracy in good lighting |
| Face recognition | SFace ONNX | 4 FPS | ~85MB | crop | Madhan 85–97%, Indhu 75% |
| Emotion detection | DeepFace FER | 2 FPS | ~35MB | crop | 7 classes |
| STT | faster-whisper base.en beam=5 | ~1.5s | ~74MB | audio | Indian English tuned |
| TTS | Piper en_US-lessac-medium | ~1.8s gen | ~61MB | text | Streaming sentence-by-sentence |
| Local LLM | Ollama llama3.2:1b | 5–8s warm, 51s cold | ~1.3GB | text | Offline fallback only |
| Full cosmo process | All above | — | ~480MB | — | PM2, idle, no person |

---

## Post-Upgrade Measurements

### YOLO11n — 2026-05-21 (ADR-012)

| Metric | Value | Notes |
|---|---|---|
| avg inference | 98.6ms | 30-frame benchmark, 320×240, Pi 5 CPU |
| FPS capability | **10.1 FPS** | Measured — exceeds 8 FPS pipeline target |
| min/max | 90.7ms / 120.2ms | |
| torch build | 2.11.0+cpu | Replaced broken CUDA build — first working YOLO on this Pi |
| model size | 5.4MB | models/yolo11n.pt |
| PM2 status | ✓ `person_detector (yolo11n)` in logs | Confirmed loading on restart |

**Key finding:** YOLOv8n was never actually running — CUDA torch was failing with `libcublasLt` not found and silently falling back to HOG detector. YOLO11n + CPU torch is the first real YOLO instance on Cosmo.

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
