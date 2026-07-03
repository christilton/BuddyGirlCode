import time
from machine import I2C, Pin

from sht31 import SHT31


sensor_i2c = I2C(1, scl=Pin(19), sda=Pin(18), freq=100000)
sensor = SHT31(i2c=sensor_i2c)

if sensor.begin() != 0:
    raise RuntimeError("SHT31 initialization failed. Check power and I2C wiring.")

print("SHT31 serial number:", sensor.read_serial_number())

while True:
    reading = sensor.read_temperature_and_humidity()
    print(
        "Temperature: {:.2f} C / {:.2f} F, Humidity: {:.2f}%".format(
            reading.TemperatureC,
            reading.TemperatureF,
            reading.Humidity,
        )
    )
    time.sleep(2)
