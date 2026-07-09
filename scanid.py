from machine import Pin, SPI
from mfrc522 import MFRC522
import time

spi = SPI(1, baudrate=1000000, polarity=0, phase=0,
          sck=Pin(18), mosi=Pin(23), miso=Pin(19))

rdr = MFRC522(spi=spi, gpioRst=Pin(22), gpioCs=Pin(16))

print("Scan RFID...")

while True:
    (stat, tag_type) = rdr.request(rdr.REQIDL)

    if stat == rdr.OK:
        (stat, uid) = rdr.anticoll()

        if stat == rdr.OK:
            uid_str = "".join([str(i) for i in uid])
            print("UID:", uid_str)

            time.sleep(1)

