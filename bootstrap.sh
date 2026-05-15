#!/bin/bash
echo "=============================="
echo "  ROBOT SESSION START — $(date)"
echo "=============================="
echo ""
echo "--- Memory Repo ---"
cd /home/pi/claude-memory && git pull --ff-only 2>/dev/null || echo "[WARN] git pull failed"
cat STATUS.md
echo ""
cat ACTIVE_TASK.md
echo ""
echo "--- Pi Health ---"
free -h | grep Mem
df -h /home/pi | tail -1
vcgencmd measure_temp 2>/dev/null || cat /sys/class/thermal/thermal_zone0/temp | awk '{print $1/1000 "°C"}'
echo ""
echo "--- Services ---"
pm2 status 2>/dev/null | grep -E "name|banteragent|cosmo|battery|scheduler|monitor"
echo ""
systemctl --user list-units 'robot-*' 2>/dev/null || echo "No robot systemd units yet"
echo ""
echo "--- Robot Services Dir ---"
ls /home/pi/robot/services/ 2>/dev/null || echo "services/ not created yet"
echo ""
echo "=============================="
echo "  Ready."
echo "=============================="
