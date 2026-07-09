"""
LAB 6: Smart RFID System with Cloud & SD Logging
ESP32 + MicroPython (Thonny)

Uses your existing mfrc522.py driver (MFRC522(spi=, gpioRst=, gpioCs=)).

Flow:
 1. Read UID from RFID card
 2. Match UID against student database
 3. Generate current datetime (YYYY-MM-DD HH:MM:SS)
 4. If valid: buzz 0.3s, save to SD (CSV), send to Firestore
 5. If invalid: buzz 3s, print "Unknown Card", do not save/send
"""

from machine import Pin, SPI
from mfrc522 import MFRC522
import network
import urequests
import ujson
import time
import os
import sdcard

# ─── WIFI CONFIG ───────────────────────────────────────────



SD_SCK = 14
SD_MOSI = 15
SD_MISO = 2
SD_CS = 13

CSV_PATH = "/sd/attendance.csv"

sd_spi = SPI(2, baudrate=1000000, sck=Pin(SD_SCK), mosi=Pin(SD_MOSI), miso=Pin(SD_MISO))
sd_cs = Pin(SD_CS)

sd_ok = False
try:
    sd = sdcard.SDCard(sd_spi, sd_cs)
    vfs = os.VfsFat(sd)
    os.mount(vfs, "/sd")
    sd_ok = True
    print("SD card mounted at /sd")
    try:
        os.stat(CSV_PATH)
    except OSError:
        with open(CSV_PATH, "w") as f:
            f.write("UID,Name,StudentID,Major,DateTime\n")
        print("Created new CSV with header")
except OSError as e:
    print("SD card mount failed:", e)
    
SSID = "Robotic WIFI"
PASSWORD = "rbtWIFI@2025"

wifi = network.WLAN(network.STA_IF)
wifi.active(True)
wifi.connect(SSID, PASSWORD)
print("Connecting WiFi", end="")
while not wifi.isconnected():
    print(".", end="")
    time.sleep(0.5)
print("\nConnected:", wifi.ifconfig())

# ─── TIME SYNC ─────────────────────────────────────────────
TIMEZONE_OFFSET_HOURS = 7  # Phnom Penh = UTC+7

try:
    import ntptime
    ntptime.settime()  # sets RTC to UTC
    print("Time synced via NTP")
except Exception as e:
    print("NTP sync failed:", e)


def get_datetime_string():
    t = time.localtime(time.time() + TIMEZONE_OFFSET_HOURS * 3600)
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )


# ─── FIRESTORE CONFIG ──────────────────────────────────────
PROJECT_ID = "rfid-g2"
FIRESTORE_COLLECTION = "rfid_logs"
url = "https://firestore.googleapis.com/v1/projects/{}/databases/(default)/documents/{}".format(
    PROJECT_ID, FIRESTORE_COLLECTION
)


def send_to_firestore(uid, info, dt_string):
    data = {
        "fields": {
            "UID": {"stringValue": uid},
            "Name": {"stringValue": info["name"]},
            "StudentID": {"stringValue": info["student_id"]},
            "Major": {"stringValue": info["major"]},
            "DateTime": {"stringValue": dt_string},
        }
    }
    try:
        res = urequests.post(url, json=data)
        print("Sent to Firestore:", res.status_code)
        res.close()
        return True
    except Exception as e:
        print("Error sending to Firestore:", e)
        return False


# ─── RFID SETUP (unchanged from your working code) ────────
spi = SPI(1, baudrate=1000000, sck=Pin(18), mosi=Pin(23), miso=Pin(19))
rdr = MFRC522(spi=spi, gpioRst=Pin(22), gpioCs=Pin(16))

# ─── SD CARD SETUP ─────────────────────────────────────────
# Confirmed working pins (tested standalone):


# Separate SPI bus (SPI(2)) so it doesn't conflict with the RFID
# reader, which is already using SPI(1).



def save_to_sd(uid, info, dt_string):
    if not sd_ok:
        print("SD not mounted — skipping local save.")
        return
    try:
        with open(CSV_PATH, "a") as f:
            f.write("{},{},{},{},{}\n".format(
                uid, info["name"], info["student_id"], info["major"], dt_string
            ))
        print("Saved to SD:", CSV_PATH)
    except OSError as e:
        print("SD write failed:", e)


# ─── BUZZER SETUP ──────────────────────────────────────────
BUZZER_PIN = 4
VALID_BUZZ_SEC = 0.3
INVALID_BUZZ_SEC = 3.0

buzzer = Pin(BUZZER_PIN, Pin.OUT)
buzzer.value(0)


def buzz(seconds):
    buzzer.value(1)
    time.sleep(seconds)
    buzzer.value(0)
