"""
LAB 6: Smart RFID System with Cloud & SD Logging
ESP32 + MicroPython (Thonny)

DEBUG VERSION — includes CHECKPOINT prints to isolate where execution
freezes. Once the freeze point is found, these can be removed.
"""

from machine import Pin, SPI, I2C
from mfrc522 import MFRC522
import network
import urequests
import ujson
import time
import os
import sdcard
from machine_i2c_lcd import I2cLcd

print("CHECKPOINT 0 - imports done")

# ─── SD CARD CONFIG ────────────────────────────────────────
SD_SCK = 14
SD_MOSI = 15
SD_MISO = 2
SD_CS = 13
CSV_PATH = "/sd/attendance.csv"

# ─── SD CARD SETUP ─────────────────────────────────────────
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

print("CHECKPOINT 1 - after SD setup")

# ─── WIFI CONFIG ───────────────────────────────────────────
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

print("CHECKPOINT 2 - after WiFi connect")

# ─── TIME SYNC ─────────────────────────────────────────────
TIMEZONE_OFFSET_HOURS = 7  # Phnom Penh = UTC+7

try:
    import ntptime
    ntptime.settime()  # sets RTC to UTC
    print("Time synced via NTP")
except Exception as e:
    print("NTP sync failed:", e)

print("CHECKPOINT 3 - after time sync")


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

print("CHECKPOINT 4 - after firestore config")

# ─── FACE CHECK SERVER CONFIG ──────────────────────────────
# Update to your PC's actual IP address (the one running face_check_server.py)
FACE_CHECK_URL = "http://10.30.0.76:5000/check_face"


def check_face(student_id):
    """Ask the PC server to verify a live camera face against student_id's
    reference photo. Returns True/False. Defaults to False on any error —
    fail closed, not open."""
    try:
        res = urequests.post(FACE_CHECK_URL, json={"student_id": student_id})
        result = res.json()
        res.close()
        print("Face check result:", result)
        return result.get("match", False)
    except Exception as e:
        print("Face check request failed. Type:", type(e), "Args:", e.args)
        return False


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


# ─── RFID SETUP ────────────────────────────────────────────
spi = SPI(1, baudrate=1000000, sck=Pin(18), mosi=Pin(23), miso=Pin(19))
rdr = MFRC522(spi=spi, gpioRst=Pin(22), gpioCs=Pin(16))

print("CHECKPOINT 5 - after RFID setup")

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


print("CHECKPOINT 6 - after buzzer setup")

# ─── LCD SETUP (I2C, 16x2) ─────────────────────────────────
# GPIO 21/22 conflict with RFID pins already in use — moved to 25/26.
LCD_SDA_PIN = 25
LCD_SCL_PIN = 26
LCD_I2C_ADDR = 0x27  # scan with i2c.scan() if display shows nothing

lcd_ok = False
try:
    lcd_i2c = I2C(0, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN), freq=400000)
    found = lcd_i2c.scan()
    print("I2C devices found:", [hex(a) for a in found])
    lcd = I2cLcd(lcd_i2c, LCD_I2C_ADDR, 2, 16)
    lcd.clear()
    lcd.putstr("System Ready")
    lcd_ok = True
    print("LCD initialized")
except Exception as e:
    print("LCD init failed:", e)


def lcd_show(line1, line2=""):
    if not lcd_ok:
        return
    try:
        lcd.clear()
        lcd.move_to(0, 0)
        lcd.putstr(line1[:16])
        if line2:
            lcd.move_to(0, 1)
            lcd.putstr(line2[:16])
    except Exception as e:
        print("LCD write failed:", e)


print("CHECKPOINT 6b - after LCD setup")

# ─── STUDENT DATABASE (UID string -> info) ────────────────
# Scan each card once (Unknown Card will print its UID), then add it here.
STUDENT_DB = {
    "1425411918150": {"name": "Khorn Sokhadom", "student_id": "2023517", "major": "Computer Science"},
    # Add teammate 2's scanned UID below:
    "397102255251": {"name": "Pheng Menghieng", "student_id": "2024188", "major": "CS"},
    # Add teammate 3's scanned UID below:
    "PLACEHOLDER_UID_3": {"name": "Teammate Three", "student_id": "0000000", "major": "Major Here"},
}

print("CHECKPOINT 7 - entering main loop")

# ─── MAIN LOOP ─────────────────────────────────────────────
print("Scan RFID...")

_loop_count = 0

while True:
    _loop_count += 1
    if _loop_count % 50 == 0:
        print("Loop alive, iteration", _loop_count)

    (stat, tag_type) = rdr.request(rdr.REQIDL)

    if stat == rdr.OK:
        (stat, uid) = rdr.anticoll()
        if stat == rdr.OK:
            uid_str = "".join([str(i) for i in uid])
            dt_string = get_datetime_string()
            print("\nUID:", uid_str)

            info = STUDENT_DB.get(uid_str)

            if info:
                print("Valid card:", info["name"])
                lcd_show("Card: Correct", info["name"])
                buzz(VALID_BUZZ_SEC)   # beep #1 — card check result
                time.sleep(0.3)        # brief pause so the two beeps are distinct

                lcd_show("Checking face...")
                face_ok = check_face(info["student_id"])

                if face_ok:
                    print("Face matched:", info["name"])
                    lcd_show("Correct", info["name"])
                    buzz(VALID_BUZZ_SEC)   # beep #2 — face check result
                    if sd_ok:
                        try:
                            with open(CSV_PATH, "a") as f:
                                f.write("{},{},{},{},{}\n".format(
                                    uid_str, info["name"], info["student_id"],
                                    info["major"], dt_string
                                ))
                            print("Saved to SD:", CSV_PATH)
                        except OSError as e:
                            print("SD write failed:", e)
                    else:
                        print("SD not mounted — skipping local save.")
                    send_to_firestore(uid_str, info, dt_string)
                else:
                    print("Face did not match — access denied.")
                    lcd_show("Incorrect", "Face mismatch")
                    buzz(INVALID_BUZZ_SEC)   # beep #2 — face check result
            else:
                print("Unknown Card")
                lcd_show("Incorrect", "Unknown Card")
                buzz(INVALID_BUZZ_SEC)   # beep #1 — card check result (invalid, no face check runs)

            time.sleep(2)
            lcd_show("Scan your card")

