import network
import time
import json
import sys

SSID = "02125 Ext"
PASSWORD = "1234567890"

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(False)
    time.sleep(0.5)
    wlan.active(True)
    time.sleep(0.5)
    if wlan.isconnected():
        print("Already connected:", wlan.ifconfig())
        return wlan

    print("Connecting to", SSID, "...")
    wlan.connect(SSID, PASSWORD)  # positional only in MicroPython ESP32-S3
    for i in range(20):
        if wlan.isconnected():
            break
        time.sleep(0.5)
        print(".", end="", flush=True)
    print()

    if wlan.isconnected():
        ip, mask, gw, dns = wlan.ifconfig()
        print("Connected!")
        print("  IP  :", ip)
        print("  GW  :", gw)
        try:
            print("  RSSI:", wlan.status("rssi"), "dBm")
        except:
            pass
        return wlan
    else:
        print("FAILED to connect. Status:", wlan.status())
        return None

connect_wifi()
