#!/bin/bash
# Start robot keyboard control from anywhere
# Usage: bash ~/robot/start.sh
cd ~/robot
echo "Starting robot keyboard control..."
echo "W=forward  S=backward  A=left  D=right  Space=stop  Q=quit"
python3 keyboard_control.py
