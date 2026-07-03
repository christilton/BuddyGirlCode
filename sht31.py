import time
from machine import I2C, Pin


def _sleep_ms(milliseconds):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000.0)


class SHT31Reading:
    __slots__ = ("TemperatureC", "TemperatureF", "Humidity", "ERR")

    def __init__(self, temperature_c: float, humidity: float, err: int = 0):
        self.TemperatureC = temperature_c
        self.TemperatureF = (temperature_c * 9.0 / 5.0) + 32.0
        self.Humidity = humidity
        self.ERR = err


class SHT31:
    DEFAULT_ADDRESS = 0x44

    REPEATABILITY_HIGH = "high"
    REPEATABILITY_MEDIUM = "medium"
    REPEATABILITY_LOW = "low"

    eRepeatability_High = REPEATABILITY_HIGH
    eRepeatability_Medium = REPEATABILITY_MEDIUM
    eRepeatability_Low = REPEATABILITY_LOW

    MEASURE_FREQ_0_5HZ = 0.5
    MEASURE_FREQ_1HZ = 1
    MEASURE_FREQ_2HZ = 2
    MEASURE_FREQ_4HZ = 4
    MEASURE_FREQ_10HZ = 10

    eMeasureFreq_Hz5 = MEASURE_FREQ_0_5HZ
    eMeasureFreq_1Hz = MEASURE_FREQ_1HZ
    eMeasureFreq_2Hz = MEASURE_FREQ_2HZ
    eMeasureFreq_4Hz = MEASURE_FREQ_4HZ
    eMeasureFreq_10Hz = MEASURE_FREQ_10HZ

    _SERIAL_NUMBER_COMMAND = 0x3780
    _SOFT_RESET_COMMAND = 0x30A2
    _HEATER_ENABLE_COMMAND = 0x306D
    _HEATER_DISABLE_COMMAND = 0x3066
    _READ_STATUS_COMMAND = 0xF32D
    _CLEAR_STATUS_COMMAND = 0x3041
    _FETCH_DATA_COMMAND = 0xE000
    _STOP_PERIODIC_COMMAND = 0x3093

    _STATUS_ALERT_PENDING = 1 << 15
    _STATUS_HEATER_ENABLED = 1 << 13

    _SINGLE_COMMANDS = {
        REPEATABILITY_HIGH: (0x2400, 15),
        REPEATABILITY_MEDIUM: (0x240B, 6),
        REPEATABILITY_LOW: (0x2416, 4),
    }

    _PERIODIC_COMMANDS = {
        (MEASURE_FREQ_0_5HZ, REPEATABILITY_HIGH): 0x2032,
        (MEASURE_FREQ_0_5HZ, REPEATABILITY_MEDIUM): 0x2024,
        (MEASURE_FREQ_0_5HZ, REPEATABILITY_LOW): 0x202F,
        (MEASURE_FREQ_1HZ, REPEATABILITY_HIGH): 0x2130,
        (MEASURE_FREQ_1HZ, REPEATABILITY_MEDIUM): 0x2126,
        (MEASURE_FREQ_1HZ, REPEATABILITY_LOW): 0x212D,
        (MEASURE_FREQ_2HZ, REPEATABILITY_HIGH): 0x2236,
        (MEASURE_FREQ_2HZ, REPEATABILITY_MEDIUM): 0x2220,
        (MEASURE_FREQ_2HZ, REPEATABILITY_LOW): 0x222B,
        (MEASURE_FREQ_4HZ, REPEATABILITY_HIGH): 0x2334,
        (MEASURE_FREQ_4HZ, REPEATABILITY_MEDIUM): 0x2322,
        (MEASURE_FREQ_4HZ, REPEATABILITY_LOW): 0x2329,
        (MEASURE_FREQ_10HZ, REPEATABILITY_HIGH): 0x2737,
        (MEASURE_FREQ_10HZ, REPEATABILITY_MEDIUM): 0x2721,
        (MEASURE_FREQ_10HZ, REPEATABILITY_LOW): 0x272A,
    }

    def __init__(
        self,
        i2c=None,
        scl_pin=None,
        sda_pin=None,
        bus=1,
        freq=100000,
        address=DEFAULT_ADDRESS,
    ):
        self.address = address
        if i2c is not None:
            self.i2c = i2c
        else:
            if scl_pin is None or sda_pin is None:
                raise ValueError("Provide either an I2C instance or both scl_pin and sda_pin.")
            self.i2c = I2C(bus, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=freq)

        self._command_buffer = bytearray(2)
        self._data_buffer = bytearray(6)
        self._periodic_mode = False
        self._repeatability = self.REPEATABILITY_HIGH
        self._measure_frequency = self.MEASURE_FREQ_1HZ

    def _normalize_repeatability(self, repeatability):
        if repeatability is None:
            return self._repeatability

        if isinstance(repeatability, str):
            normalized = repeatability.lower()
        else:
            normalized = repeatability

        if normalized not in self._SINGLE_COMMANDS:
            raise ValueError("Unsupported repeatability: {}".format(repeatability))
        return normalized

    def _normalize_measure_frequency(self, measure_frequency):
        if measure_frequency not in (
            self.MEASURE_FREQ_0_5HZ,
            self.MEASURE_FREQ_1HZ,
            self.MEASURE_FREQ_2HZ,
            self.MEASURE_FREQ_4HZ,
            self.MEASURE_FREQ_10HZ,
        ):
            raise ValueError("Unsupported periodic measurement frequency: {}".format(measure_frequency))
        return measure_frequency

    def _write_command(self, command):
        self._command_buffer[0] = (command >> 8) & 0xFF
        self._command_buffer[1] = command & 0xFF
        self.i2c.writeto(self.address, self._command_buffer)

    def _read_bytes(self, count):
        return self.i2c.readfrom(self.address, count)

    def _read_data_with_crc(self) -> SHT31Reading:
        raw = self._read_bytes(6)
        if len(raw) != 6:
            raise RuntimeError("Expected 6 bytes from SHT31, received {}.".format(len(raw)))

        temp_bytes = raw[0:2]
        humidity_bytes = raw[3:5]

        if self._crc8(temp_bytes) != raw[2]:
            raise RuntimeError("SHT31 temperature CRC check failed.")
        if self._crc8(humidity_bytes) != raw[5]:
            raise RuntimeError("SHT31 humidity CRC check failed.")

        raw_temp = (temp_bytes[0] << 8) | temp_bytes[1]
        raw_humidity = (humidity_bytes[0] << 8) | humidity_bytes[1]

        temperature_c = -45.0 + (175.0 * raw_temp / 65535.0)
        humidity = 100.0 * raw_humidity / 65535.0
        humidity = max(0.0, min(100.0, humidity))

        return SHT31Reading(temperature_c=temperature_c, humidity=humidity, err=0)

    def begin(self):
        try:
            self.read_status()
            return 0
        except Exception:
            return 1

    def read_status(self):
        self._write_command(self._READ_STATUS_COMMAND)
        _sleep_ms(1)
        raw = self._read_bytes(3)
        if len(raw) != 3:
            raise RuntimeError("Expected 3 bytes from SHT31 status register, received {}.".format(len(raw)))
        if self._crc8(raw[0:2]) != raw[2]:
            raise RuntimeError("SHT31 status CRC check failed.")
        return (raw[0] << 8) | raw[1]

    def read_serial_number(self):
        self._write_command(self._SERIAL_NUMBER_COMMAND)
        _sleep_ms(1)
        raw = self._read_bytes(6)
        if len(raw) != 6:
            raise RuntimeError("Expected 6 bytes for SHT31 serial number, received {}.".format(len(raw)))
        if self._crc8(raw[0:2]) != raw[2]:
            raise RuntimeError("SHT31 serial number CRC check failed for word 1.")
        if self._crc8(raw[3:5]) != raw[5]:
            raise RuntimeError("SHT31 serial number CRC check failed for word 2.")
        return (raw[0] << 24) | (raw[1] << 16) | (raw[3] << 8) | raw[4]

    def soft_reset(self):
        self._write_command(self._SOFT_RESET_COMMAND)
        self._periodic_mode = False
        _sleep_ms(2)
        self.read_status()
        return True

    def heater_enable(self):
        self._write_command(self._HEATER_ENABLE_COMMAND)
        _sleep_ms(1)
        return bool(self.read_status() & self._STATUS_HEATER_ENABLED)

    def heater_disable(self):
        self._write_command(self._HEATER_DISABLE_COMMAND)
        _sleep_ms(1)
        return not bool(self.read_status() & self._STATUS_HEATER_ENABLED)

    def clear_status_register(self):
        self._write_command(self._CLEAR_STATUS_COMMAND)
        _sleep_ms(1)

    def read_alert_state(self):
        return bool(self.read_status() & self._STATUS_ALERT_PENDING)

    def _single_measurement(self, repeatability=None):
        resolved_repeatability = self._normalize_repeatability(repeatability)
        command, delay_ms = self._SINGLE_COMMANDS[resolved_repeatability]
        self._write_command(command)
        _sleep_ms(delay_ms)
        return self._read_data_with_crc()

    def start_periodic_mode(self, measure_frequency, repeatability=REPEATABILITY_HIGH):
        resolved_frequency = self._normalize_measure_frequency(measure_frequency)
        resolved_repeatability = self._normalize_repeatability(repeatability)
        command = self._PERIODIC_COMMANDS[(resolved_frequency, resolved_repeatability)]
        self._write_command(command)
        self._periodic_mode = True
        self._measure_frequency = resolved_frequency
        self._repeatability = resolved_repeatability
        _sleep_ms(2)
        return True

    def stop_periodic_mode(self):
        self._write_command(self._STOP_PERIODIC_COMMAND)
        self._periodic_mode = False
        _sleep_ms(2)
        return True

    def _fetch_periodic_measurement(self):
        self._write_command(self._FETCH_DATA_COMMAND)
        _sleep_ms(1)
        return self._read_data_with_crc()

    def read_temperature_and_humidity(self, repeatability=None):
        if self._periodic_mode and repeatability is None:
            return self._fetch_periodic_measurement()
        return self._single_measurement(repeatability=repeatability)

    def get_temperature_c(self) -> float:
        return self.read_temperature_and_humidity().TemperatureC

    def get_temperature_f(self) -> float:
        return self.read_temperature_and_humidity().TemperatureF

    def get_humidity_rh(self) -> float:
        return self.read_temperature_and_humidity().Humidity

    def read(self) -> tuple[float, float]:
        reading = self.read_temperature_and_humidity()
        return (reading.TemperatureF, reading.Humidity)

    def temp(self) -> float:
        temperature, _ = self.read()
        return round(temperature, 1)

    def humidity(self) -> float:
        _, humidity = self.read()
        return round(humidity, 1)

    @staticmethod
    def _crc8(buffer):
        crc = 0xFF
        for byte in buffer:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    readSerialNumber = read_serial_number
    softReset = soft_reset
    heaterEnable = heater_enable
    heaterDisable = heater_disable
    clearStatusRegister = clear_status_register
    readAlertState = read_alert_state
    startPeriodicMode = start_periodic_mode
    stopPeriodicMode = stop_periodic_mode
    readTemperatureAndHumidity = read_temperature_and_humidity
    getTemperatureC = get_temperature_c
    getTemperatureF = get_temperature_f
    getHumidityRH = get_humidity_rh


SEN0385 = SHT31
