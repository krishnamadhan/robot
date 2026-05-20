#!/bin/bash
# update_docs.sh — regenerate live status tables in ARCHITECTURE.md
# Run manually or add to cron: 0 6 * * * /home/pi/robot/tools/update_docs.sh

set -euo pipefail
ROBOT_DIR="/home/pi/robot"
PYTHONPATH="$ROBOT_DIR"
DOCS="$ROBOT_DIR/docs"

echo "=== Cosmo Doc Update === $(date)"

# 1. Pull hardware registry status from live API
HW_STATUS=$(curl -s --max-time 3 http://localhost:8000/hardware 2>/dev/null || echo "{}")
HEALTH=$(curl -s --max-time 3 http://localhost:8000/health 2>/dev/null || echo "{}")
STATE=$(curl -s --max-time 3 http://localhost:8000/state 2>/dev/null || echo "{}")

# 2. Generate status snapshot
python3 - <<PYEOF
import json, time, sys

hw = json.loads('''$HW_STATUS''') or {}
health = json.loads('''$HEALTH''') or {}
state = json.loads('''$STATE''') or {}

lines = []
lines.append(f"# Cosmo Live Status Snapshot")
lines.append(f"> Generated: {time.strftime('%Y-%m-%d %H:%M')}")
lines.append("")

# Health
uptime = health.get('uptime_s', 0)
h, m = divmod(uptime // 60, 60)
lines.append(f"**Uptime:** {h}h {m}m | "
             f"**CPU Temp:** {health.get('cpu_temp_c', '?')}°C | "
             f"**Free RAM:** {health.get('free_ram_mb', '?')}MB | "
             f"**Mood:** {health.get('mood', '?')} | "
             f"**Energy:** {health.get('energy', '?')}")
lines.append("")

# Hardware
lines.append("## Hardware Status")
lines.append("| Component | Status | Reason |")
lines.append("|-----------|--------|--------|")
for name, info in hw.get('components', {}).items():
    s = info.get('status', '?')
    r = info.get('reason', '')
    emoji = '✅' if s == 'real' else '⚠️' if s == 'mock' else '❌'
    lines.append(f"| {name} | {emoji} {s} | {r[:60]} |")
lines.append("")

# Behavior
behavior = state.get('behavior', {})
lines.append(f"**Listen state:** {behavior.get('listen_state', '?')} | "
             f"**Last response:** {str(behavior.get('last_response', '—'))[:80]}")

print('\n'.join(lines))
PYEOF

# 3. Write snapshot file
python3 - <<PYEOF > "$DOCS/LIVE_STATUS.md"
import json, time, subprocess, sys

hw = json.loads('''$HW_STATUS''') or {}
health = json.loads('''$HEALTH''') or {}

lines = []
lines.append("# Cosmo Live Status")
lines.append(f"> Auto-generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
lines.append("> Run \`tools/update_docs.sh\` to refresh")
lines.append("")
lines.append("## System Health")

uptime = health.get('uptime_s', 0)
h, m = divmod(uptime // 60, 60)
lines.append(f"| Metric | Value |")
lines.append(f"|--------|-------|")
lines.append(f"| Uptime | {h}h {m}m |")
lines.append(f"| CPU Temp | {health.get('cpu_temp_c', '?')}°C |")
lines.append(f"| Free RAM | {health.get('free_ram_mb', '?')} MB |")
lines.append(f"| Mood | {health.get('mood', '?')} |")
lines.append(f"| Energy | {health.get('energy', '?')} |")
lines.append("")

lines.append("## Hardware Components")
lines.append("| Component | Status | Reason |")
lines.append("|-----------|--------|--------|")
for name, info in hw.get('components', {}).items():
    s = info.get('status', '?')
    r = info.get('reason', '')
    emoji = '✅' if s == 'real' else '⚠️' if s == 'mock' else '❌'
    lines.append(f"| {name} | {emoji} {s} | {r[:70]} |")

lines.append("")
lines.append(f"**Real components:** {', '.join(hw.get('real', [])) or 'none'}")
lines.append(f"**Mocked:** {', '.join(hw.get('mocked', [])) or 'none'}")
lines.append(f"**Errors:** {', '.join(hw.get('errors', [])) or 'none'}")

print('\n'.join(lines))
PYEOF

# 4. Run performance review
echo ""
echo "=== Performance Review ==="
python3 "$ROBOT_DIR/tools/perf_review.py" 2>/dev/null || echo "(perf_review.py not available)"

# 5. Git commit docs if changed
cd "$ROBOT_DIR"
if ! git diff --quiet docs/; then
    git add docs/
    git commit -m "docs: auto-update $(date +%Y-%m-%d_%H:%M)" --quiet
    echo "✅ Docs committed"
else
    echo "✅ No doc changes"
fi

echo "=== Done ==="
