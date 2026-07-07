#!/bin/bash
# Lights routine — cron-driven TV-sync schedule.
#   lights_routine.sh on   → start TV ambilight (strip + Wipro bulb follow TV)
#   lights_routine.sh off  → stop sync and turn all lights off
# Cron: 0 18 * * * (on) · 0 0 * * * (off) — installed 2026-07-06.

set -u
TOKEN=$(grep '^ROBOT_API_TOKEN=' /home/pi/robot/.env | cut -d= -f2)
API="http://127.0.0.1:8000"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
TS=$(date '+%Y-%m-%d %H:%M:%S')

case "${1:-}" in
  on)
    OUT=$(curl -s -m 10 -X POST "$API/led/tv" "${AUTH[@]}" -d '{"on":true}')
    echo "$TS routine ON → $OUT"
    ;;
  off)
    OUT=$(curl -s -m 10 -X POST "$API/led/tv" "${AUTH[@]}" -d '{"on":false}')
    # Also kill any manually-set strip colour left over from the evening.
    OUT2=$(curl -s -m 20 -X POST "$API/led" "${AUTH[@]}" -d '{"cmd":"off"}')
    echo "$TS routine OFF → tv:$OUT strip:$OUT2"
    ;;
  *)
    echo "usage: $0 on|off" >&2
    exit 1
    ;;
esac
