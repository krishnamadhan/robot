import network
import time

SSID = "02135_2.4G"
PASSWORD = "1234567890"

def connect_wifi():
    w = network.WLAN(network.STA_IF)
    w.active(False)
    time.sleep(0.3)
    w.active(True)
    time.sleep(0.5)
    if w.isconnected():
        return w
    w.connect(SSID, PASSWORD)
    for _ in range(20):
        if w.isconnected():
            break
        time.sleep(0.5)
    return w

wlan = connect_wifi()
if wlan.isconnected():
    print("WiFi OK:", wlan.ifconfig()[0])
else:
    print("WiFi FAILED")
