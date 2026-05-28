#!/usr/bin/env bash
# cosmo_doctor.sh — one-shot health snapshot (<5s)
# Usage: bash tools/cosmo_doctor.sh

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
hdr()  { echo -e "\n${BOLD}$1${NC}"; }

echo -e "${BOLD}╔══════════════════════════════╗"
echo -e "║   Cosmo Doctor — $(date '+%H:%M:%S')   ║"
echo -e "╚══════════════════════════════╝${NC}"

# ── PM2 ──────────────────────────────────────────────────────────────────────
hdr "PM2 Processes"
if command -v pm2 &>/dev/null; then
  pm2 jlist 2>/dev/null | python3 -c "
import json,sys
procs = json.load(sys.stdin)
for p in procs:
    name   = p.get('name','?')
    status = p.get('pm2_env',{}).get('status','?')
    mem    = p.get('monit',{}).get('memory',0) // 1024 // 1024
    restarts = p.get('pm2_env',{}).get('restart_time',0)
    sym = '✓' if status=='online' else '✗'
    print(f'  {sym} {name:<20} {status:<10} {mem}MB  restarts={restarts}')
" 2>/dev/null || pm2 list --no-color 2>/dev/null | grep -E "online|stopped|errored" | head -10
else
  fail "pm2 not found"
fi

# ── RAM ───────────────────────────────────────────────────────────────────────
hdr "RAM"
read total used free <<< $(free -m | awk '/^Mem:/{print $2,$3,$4}')
headroom=$free
if [ "$headroom" -ge 1024 ]; then
  ok "Free: ${free}MB / ${total}MB  (headroom OK)"
elif [ "$headroom" -ge 512 ]; then
  warn "Free: ${free}MB / ${total}MB  (headroom LOW)"
else
  fail "Free: ${free}MB / ${total}MB  (CRITICAL — <512MB)"
fi

# ── Disk ──────────────────────────────────────────────────────────────────────
hdr "Disk"
pct=$(df / | awk 'NR==2{gsub(/%/,"",$5); print $5}')
avail=$(df -h / | awk 'NR==2{print $4}')
if [ "$pct" -lt 85 ]; then
  ok "/ is ${pct}% full  (${avail} free)"
elif [ "$pct" -lt 95 ]; then
  warn "/ is ${pct}% full  (${avail} free) — consider cleanup"
else
  fail "/ is ${pct}% full  (${avail} free) — CRITICAL"
fi

# ── CPU temp ─────────────────────────────────────────────────────────────────
hdr "CPU"
temp=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//;s/.C//')
throttled=$(vcgencmd get_throttled 2>/dev/null | sed 's/throttled=//')
if [ -n "$temp" ]; then
  t_int=${temp%.*}
  if [ "$t_int" -lt 70 ]; then
    ok "Temp: ${temp}°C"
  elif [ "$t_int" -lt 80 ]; then
    warn "Temp: ${temp}°C (warm)"
  else
    fail "Temp: ${temp}°C (HOT)"
  fi
fi
[ "$throttled" = "0x0" ] && ok "No throttling" || warn "Throttle flags: $throttled"

# ── Camera ───────────────────────────────────────────────────────────────────
hdr "Camera"
if [ -e /dev/video0 ]; then
  ok "/dev/video0 present"
else
  fail "/dev/video0 missing — C920 not connected?"
fi

# ── BT Speaker ───────────────────────────────────────────────────────────────
hdr "Bluetooth Speaker (JBL Flip 5)"
MAC="28:FA:19:C1:73:F8"
if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; then
  ok "JBL Flip 5 connected"
else
  warn "JBL Flip 5 NOT connected — run: bluetoothctl connect $MAC"
fi

# ── I2C devices ──────────────────────────────────────────────────────────────
hdr "I2C Bus (expected: 0x23=BH1750, 0x36=UPS HAT, 0x3C/3D=OLEDs)"
if command -v i2cdetect &>/dev/null; then
  detected=$(i2cdetect -y 1 2>/dev/null | grep -oP '(?<= )[0-9a-f]{2}(?= |$)' | sort -u)
  for addr in 0x23 0x36; do
    hex=$(echo $addr | sed 's/0x//')
    if echo "$detected" | grep -qi "^$hex$"; then
      ok "I2C $addr found"
    else
      warn "I2C $addr NOT found"
    fi
  done
  # OLEDs optional until wired
  for addr in 0x3c 0x3d; do
    hex=$(echo $addr | sed 's/0x//')
    if echo "$detected" | grep -qi "^$hex$"; then
      ok "I2C $addr found (OLED)"
    else
      warn "I2C $addr not found (OLED not wired yet)"
    fi
  done
else
  warn "i2cdetect not available (install i2c-tools)"
fi

# ── Token budget ─────────────────────────────────────────────────────────────
hdr "Token Budget (100K/day limit)"
LOG_DIR="$HOME/.robot/logs"
today=$(date '+%Y-%m-%d')
if [ -d "$LOG_DIR" ]; then
  tokens=$(grep -h "cosmo_mind.tokens\|cosmo_mind.daily_summary" "$LOG_DIR"/*.log 2>/dev/null | \
    python3 -c "
import sys,json,re
total=0
for line in sys.stdin:
    try:
        m = re.search(r'\"call_tokens\":\s*(\d+)', line)
        if m: total += int(m.group(1))
    except: pass
print(total)
" 2>/dev/null)
  tokens=${tokens:-0}
  budget=100000
  pct=$((tokens * 100 / budget))
  if [ "$pct" -lt 50 ]; then
    ok "Today: ~${tokens} tokens used (${pct}% of ${budget})"
  elif [ "$pct" -lt 85 ]; then
    warn "Today: ~${tokens} tokens used (${pct}% of ${budget})"
  else
    fail "Today: ~${tokens} tokens used (${pct}% of ${budget}) — near limit"
  fi
else
  warn "Log dir not found: $LOG_DIR"
fi

# ── Cosmo API ping ────────────────────────────────────────────────────────────
hdr "Cosmo API (port 8000)"
if curl -sf --max-time 2 http://localhost:8000/health >/dev/null 2>&1; then
  ok "API responding on :8000"
else
  warn "API not responding on :8000 (cosmo may not be running)"
fi

# ── Pin conflict check ────────────────────────────────────────────────────────
hdr "GPIO Pin Registry"
cd "$(dirname "$0")/.." && python3 -c "
try:
    from hardware.pin_registry import pin_registry
    conflicts = pin_registry.check_conflicts()
    if conflicts:
        for c in conflicts: print(f'  ✗ {c}')
    else:
        print('  ✓ No pin conflicts detected')
except Exception as e:
    print(f'  ⚠  Could not check pin registry: {e}')
" 2>/dev/null

echo -e "\n${BOLD}Doctor done.${NC}\n"
