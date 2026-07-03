#This code was written by Chris Tilton for his Crested Gecko, Buddy, in 2024-2025

import time, sys, network, io
import uasyncio as asyncio
import urequests as requests
import gc as garbage
import getSunriseSunset as gss
from gc import collect as gc
from secrets import ADAFRUIT_AIO_KEY, ADAFRUIT_AIO_USERNAME, ssid, password
from machine import I2C, Pin
from sht31 import SHT31


# Global variable for setpoint
SOFTWARE_VERSION = "v1.2.5"

setpoint = 0
deadband = .25
sht: SHT31 | None = None
current_timestamp: str | None = None
sunrise: str | None = None
sunset: str | None = None
has_sun_times = False
# Initialize relay pin
relay = Pin(4, Pin.OUT)
relay.off()
wlan = None
pending_lamp_status: str | None = None
relay_on_since_ms: int | None = None
last_valid_temp_ms: int | None = None
last_sensor_sample: tuple[float, float | None] | None = None
last_sensor_sample_ms: int | None = None
sensor_i2c: I2C | None = None
sensor_failure_count = 0
last_sensor_reset_ms: int | None = None
last_sensor_alert_ms: int | None = None
heat_lamp_lockout_until_ms: int | None = None
# I2C configuration for controlling NeoPixels
SDA_PIN = 0  # Adjust pins as necessary
SCL_PIN = 1

trinket = I2C(0,scl=Pin(SCL_PIN, Pin.PULL_UP), sda=Pin(SDA_PIN, Pin.PULL_UP))
resetpin = Pin(2, Pin.OUT)
resetpin.high()
TRINKET_ADDRESS = 0x12 

DAY_COLOR = (1, 255, 210, 60, 255)  # Warmer golden yellow
OFF_COLOR = (1, 0, 0, 0, 0)   
STATUS_PIXELS = (57, 58, 59)
MIN_VALID_TEMP_F = 30.0
MAX_VALID_TEMP_F = 120.0
MAX_RELAY_ON_MS = 30 * 60 * 1000
MAX_TEMP_SAMPLE_AGE_MS = 20 * 1000
SENSOR_POLL_INTERVAL_MS = 2 * 1000
SENSOR_RECOVERY_COOLDOWN_MS = 60 * 1000
SENSOR_ALERT_INTERVAL_MS = 15 * 60 * 1000
HEAT_LAMP_RETRY_DELAY_MS = 60 * 1000
CLOCK_REFRESH_INTERVAL_SEC = 60
SUN_REFRESH_INTERVAL_SEC = 10 * 60
HTTP_TIMEOUT_SEC = 8
SHT31_ADDRESS = 0x44
SENSOR_STARTUP_RETRIES = 5
    
#button_pin = Pin(15, Pin.IN)

def reset_i2c():
    global sensor_i2c
    sensor_i2c = I2C(1, scl=Pin(19), sda=Pin(18), freq=100000)
    print("I2C Reset")

def scan_sensor_bus():
    if sensor_i2c is None:
        reset_i2c()
    i2c = sensor_i2c
    if i2c is None:
        return []
    try:
        addresses = i2c.scan()
        print("I2C devices found:", [hex(address) for address in addresses])
        return addresses
    except Exception as e:
        print(f"I2C scan failed: {e}")
        return []

# Function to send RGB color to the Trinket
def send_color(lighttype,r, g, b,brightness, max_retries=5, retry_delay=1):
    color_data = bytearray([lighttype,r, g, b,brightness])
    for _ in range(max_retries):
        try:
            #print(f"Sending color data: {list(color_data)}")
            trinket.writeto(TRINKET_ADDRESS, color_data)
            return True
        except OSError as e:
            print(f"Error sending color: {e}")
            time.sleep(retry_delay)
    print("send_color failed after max retries")
    return False

def reset_trinket():
    resetpin.low()
    time.sleep(.1)
    resetpin.high()
    time.sleep(.1)

def clear_status_pixels():
    for pixel_index in STATUS_PIXELS:
        send_color(pixel_index, 0, 0, 0, 0)

def print_exception_details(exc, stream=None):
    printer = getattr(sys, "print_exception", None)
    if callable(printer):
        if stream is None:
            printer(exc)
        else:
            printer(exc, stream)
        return

    message = "{}: {}".format(type(exc).__name__, exc)
    if stream is None:
        print(message)
    else:
        stream.write(message)

def set_heatlamp(enabled):
    global relay_on_since_ms
    if enabled:
        relay.on()
        if relay_on_since_ms is None:
            relay_on_since_ms = time.ticks_ms()
    else:
        relay.off()
        relay_on_since_ms = None

def is_heatlamp_on():
    return relay_on_since_ms is not None

def arm_heat_lamp_lockout(duration_ms=HEAT_LAMP_RETRY_DELAY_MS):
    global heat_lamp_lockout_until_ms
    heat_lamp_lockout_until_ms = time.ticks_add(time.ticks_ms(), duration_ms)

def clear_heat_lamp_lockout():
    global heat_lamp_lockout_until_ms
    heat_lamp_lockout_until_ms = None

def heat_lamp_lockout_active():
    lockout_until_ms = heat_lamp_lockout_until_ms
    if lockout_until_ms is None:
        return False
    return time.ticks_diff(lockout_until_ms, time.ticks_ms()) > 0

def queue_lamp_status(state, temperature):
    global pending_lamp_status
    pending_lamp_status = f"{state}, Temp {temperature}"

def is_valid_temperature(temperature):
    if temperature is None:
        return False
    if temperature != temperature:
        return False
    return MIN_VALID_TEMP_F <= temperature <= MAX_VALID_TEMP_F

def init_sensor() -> SHT31:
    global sht, sensor_i2c
    if sensor_i2c is None:
        reset_i2c()
    time.sleep(0.05)
    addresses = scan_sensor_bus()
    if SHT31_ADDRESS not in addresses:
        sht = None
        address_list = [hex(address) for address in addresses]
        raise RuntimeError(f"SHT31 (SEN0385) not detected on I2C bus. Found: {address_list}")
    sht = SHT31(i2c=sensor_i2c)
    if sht.begin() != 0:
        sht = None
        raise RuntimeError("SHT31 (SEN0385) detected on I2C bus but failed to initialize.")
    print("Using SHT31 (SEN0385) sensor driver.")
    return sht

def get_cached_sensor_sample(max_age_ms=None) -> tuple[float | None, float | None]:
    sample = last_sensor_sample
    sample_ms = last_sensor_sample_ms
    if sample is None or sample_ms is None:
        return None, None

    if max_age_ms is not None:
        age_ms = time.ticks_diff(time.ticks_ms(), sample_ms)
        if age_ms < 0 or age_ms > max_age_ms:
            return None, None

    return sample

def should_send_sensor_alert():
    alert_ms = last_sensor_alert_ms
    if alert_ms is None:
        return True
    return time.ticks_diff(time.ticks_ms(), alert_ms) >= SENSOR_ALERT_INTERVAL_MS

def record_sensor_alert():
    global last_sensor_alert_ms
    last_sensor_alert_ms = time.ticks_ms()

async def read_sensor_once(max_retries=2, retry_delay=0.25) -> tuple[float | None, float | None, Exception | None]:
    global sht, last_sensor_sample, last_sensor_sample_ms, last_valid_temp_ms

    if sht is None:
        try:
            init_sensor()
        except Exception as e:
            return None, None, e

    last_error = None
    for attempt in range(max_retries):
        try:
            sensor = sht
            if sensor is None:
                return None, None, Exception("Temperature sensor is not initialized")
            temperature, humidity = sensor.read()
            if temperature is not None and is_valid_temperature(temperature):
                sample = (float(temperature), float(humidity) if humidity is not None else None)
                last_sensor_sample = sample
                last_sensor_sample_ms = time.ticks_ms()
                last_valid_temp_ms = last_sensor_sample_ms
                return sample[0], sample[1], None
            last_error = Exception("Invalid temperature reading")
        except Exception as e:
            last_error = e

        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)

    return None, None, last_error

async def recover_sensor():
    global sht, last_sensor_sample, last_sensor_sample_ms, last_sensor_reset_ms

    now_ms = time.ticks_ms()
    last_reset_ms = last_sensor_reset_ms
    if (
        last_reset_ms is not None
        and time.ticks_diff(now_ms, last_reset_ms) < SENSOR_RECOVERY_COOLDOWN_MS
    ):
        return

    sht = None
    last_sensor_sample = None
    last_sensor_sample_ms = None
    await asyncio.sleep(0.1)
    reset_i2c()
    last_sensor_reset_ms = time.ticks_ms()
    await asyncio.sleep(0.1)

    try:
        init_sensor()
    except Exception as e:
        print(f"Sensor reinitialize failed: {e}")

async def update_setpoint_feed(new_setpoint):
    global setpoint
    FEED_KEY = 'setpoint-gecko'
    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
    
    data = {'value': new_setpoint}
    headers = {
        'X-AIO-Key': ADAFRUIT_AIO_KEY,
        'Content-Type': 'application/json'
    }
    response = None
    
    try:
        if new_setpoint != 0:
            gc()
            response = requests.post(url, headers=headers, json=data, timeout=HTTP_TIMEOUT_SEC)
            if response.status_code == 200:
                print(f"Successfully updated setpoint feed.")
            else:
                print(response.text)
    except Exception as e:
        print(f"Failed to update setpoint feed: {e}")
    finally:
        if response is not None:
            response.close()
    await asyncio.sleep(1)  # Small delay to prevent CPU overload
        
async def send_setpoint_periodically():
    global setpoint
    await asyncio.sleep(120)
    while True:
        
        await update_setpoint_feed(setpoint)  # Send the current setpoint to Adafruit IO
        await asyncio.sleep(3600)  # Wait for an hour before sending again

async def manage_setpoint():
    global setpoint
    global current_timestamp

    def fetch_last_feed_value(feed_key, default_value):
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{feed_key}/data/last'
        headers = {
            'X-AIO-Key': ADAFRUIT_AIO_KEY,
            'Content-Type': 'application/json'
        }
        response = None
        try:
            if wlan and wlan.isconnected():
                gc()
                response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_SEC)
                if response.status_code == 200:
                    data = response.json()
                    value = data.get('value')
                    if value is not None:
                        return float(value)
        except Exception as e:
            print(f"Failed to fetch {feed_key}: {e}")
        finally:
            if response is not None:
                response.close()
        return default_value

    await asyncio.sleep(2)
    while True:
        daytime_setpoint = fetch_last_feed_value('day-setpoint-gecko', 69.0)
        await asyncio.sleep(0)
        nighttime_setpoint = fetch_last_feed_value('night-setpoint-gecko', 64.0)
        
        # Keep setpoint day/night logic aligned with light-control logic.
        if wlan and wlan.isconnected() and has_sun_times:
            new_setpoint = daytime_setpoint if is_daytime(current_timestamp) else nighttime_setpoint
        else:
            new_setpoint = 67.0
        
        if setpoint != new_setpoint:
            setpoint = new_setpoint
            print(f"Setpoint changed to: {setpoint}°F")
            await update_setpoint_feed(setpoint)
        
        await asyncio.sleep(90)  # Check every minute     

async def read_sensor():
    global sensor_failure_count
    while True:
        try:
            temperature, _, sensor_error = await read_sensor_once()
            lamp_is_on = is_heatlamp_on()
            relay_started_ms = relay_on_since_ms
            if temperature is None or not is_valid_temperature(temperature):
                sensor_failure_count += 1
                if sensor_failure_count == 1:
                    print(f"Temperature sensor unavailable: {sensor_error}")

                set_heatlamp(False)
                if lamp_is_on:
                    queue_lamp_status("OFF", "Sensor error")

                if should_send_sensor_alert():
                    await send_status_notification(f"Temperature Sensor Error: {sensor_error}")
                    record_sensor_alert()

                if sensor_failure_count >= 3:
                    await recover_sensor()

                await asyncio.sleep(2)
                continue

            if sensor_failure_count > 0:
                print("Temperature sensor recovered.")
            sensor_failure_count = 0
            valid_temperature = float(temperature)

            if (
                lamp_is_on
                and relay_started_ms is not None
                and last_valid_temp_ms is not None
                and time.ticks_diff(last_valid_temp_ms, relay_started_ms) >= MAX_RELAY_ON_MS
            ):
                print("Heat lamp exceeded max runtime, forcing OFF.")
                set_heatlamp(False)
                arm_heat_lamp_lockout()
                queue_lamp_status("OFF", valid_temperature)
                await send_status_notification("Heat lamp max runtime exceeded, forcing OFF.")
                await asyncio.sleep(1)
                continue
            # Bang-bang controller logic
            if valid_temperature < setpoint - deadband:
                if heat_lamp_lockout_active():
                    set_heatlamp(False)
                else:
                    set_heatlamp(True)
                if not lamp_is_on and is_heatlamp_on():
                    queue_lamp_status("ON", valid_temperature)
                    print("Heat Lamp turned ON.")
            elif valid_temperature >= setpoint:
                set_heatlamp(False)
                clear_heat_lamp_lockout()
                if lamp_is_on:
                    queue_lamp_status("OFF", valid_temperature)
                    print("Heat Lamp turned OFF.")
        except Exception as e:
            set_heatlamp(False)
            print(f"read_sensor error: {e}")
            await send_status_notification(f"Sensor loop error, heat lamp OFF: {e}")
        await asyncio.sleep_ms(SENSOR_POLL_INTERVAL_MS)
async def process_lamp_status_updates():
    global pending_lamp_status
    FEED_KEY = 'lamp-gecko'
    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
    while True:
        relay_started_ms = relay_on_since_ms
        valid_temp_ms = last_valid_temp_ms
        if relay_started_ms is not None and valid_temp_ms is not None:
            if time.ticks_diff(time.ticks_ms(), valid_temp_ms) >= MAX_TEMP_SAMPLE_AGE_MS:
                set_heatlamp(False)
                arm_heat_lamp_lockout()
                pending_lamp_status = "OFF, Temp stale sample"
        if pending_lamp_status is None:
            await asyncio.sleep(2)
            continue
        if not (wlan and wlan.isconnected()):
            await asyncio.sleep(2)
            continue
        reply = None
        try:
            data = {'value': pending_lamp_status}
            headers = {
                'X-AIO-Key': ADAFRUIT_AIO_KEY,
                'Content-Type': 'application/json'
            }
            reply = requests.post(url, headers=headers, json=data, timeout=HTTP_TIMEOUT_SEC)
            pending_lamp_status = None
        except Exception as e:
            print(f"Failed to send lamp status: {e}")
        finally:
            if reply is not None:
                reply.close()
        await asyncio.sleep(2)
async def send_temp():
    FEED_KEY = 'temperature-gecko'
    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'

    await asyncio.sleep(4)
    while True:
        if not (wlan and wlan.isconnected()):
            await asyncio.sleep(10)
            continue

        temperature, _ = get_cached_sensor_sample(MAX_TEMP_SAMPLE_AGE_MS)
        if temperature is None or not is_valid_temperature(temperature):
            await asyncio.sleep(10)
            continue

        reply = None
        try:
            data = {'value': temperature}
            headers = {
                'X-AIO-Key': ADAFRUIT_AIO_KEY,
                'Content-Type': 'application/json'
            }

            # Send data to Adafruit IO
            gc()
            reply = requests.post(url, headers=headers, json=data, timeout=HTTP_TIMEOUT_SEC)
            if reply.status_code != 200:
                print(reply.status_code)
                print(reply.text)
        except Exception as e:
            print("Failed to send data (T):", e)
        finally:
            if reply is not None:
                reply.close()  # Close the response to free up resources

        await asyncio.sleep(10)  # Send data every 10 seconds

async def send_humidity():
    FEED_KEY = 'humidity-gecko'
    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'

    await asyncio.sleep(6)
    while True:
        if not (wlan and wlan.isconnected()):
            await asyncio.sleep(10)
            continue

        reply = None
        try:
            _, humidity = get_cached_sensor_sample(MAX_TEMP_SAMPLE_AGE_MS)
            if humidity is None:
                await asyncio.sleep(10)
                continue

            data = {'value': humidity}
            headers = {
                'X-AIO-Key': ADAFRUIT_AIO_KEY,
                'Content-Type': 'application/json'
            }

            # Send data to Adafruit IO
            gc()
            reply = requests.post(url, headers=headers, json=data, timeout=HTTP_TIMEOUT_SEC)
            if reply.status_code != 200:
                print(reply.status_code)
                print(reply.text)
        except Exception as e:
            print("Failed to send data: (H)", e)
        finally:
            if reply is not None:
                reply.close()  # Close the response to free up resources

        await asyncio.sleep(10)  # Send data every 10 seconds

def refresh_current_timestamp():
    global current_timestamp

    if hasattr(gss, "GetLocalTimestamp"):
        timestamp = gss.GetLocalTimestamp()
        if timestamp is not None:
            current_timestamp = timestamp
            return timestamp
    return None

def load_sun_schedule(keep_existing=False):
    global sunrise, sunset, has_sun_times

    refresh_current_timestamp()

    if not (wlan and wlan.isconnected()):
        if not keep_existing:
            has_sun_times = False
        return False, "No network connection"

    if not hasattr(gss, "GetSunriseSunset"):
        if not keep_existing:
            has_sun_times = False
        return False, "getSunriseSunset module missing GetSunriseSunset()"

    result = gss.GetSunriseSunset()
    if result and isinstance(result, (tuple, list)) and len(result) == 3:
        _, sunrise_value, sunset_value = result
        sunrise = sunrise_value
        sunset = sunset_value
        has_sun_times = True
        return True, result

    if not keep_existing:
        has_sun_times = False
    return False, result

async def refresh_time_state():
    await asyncio.sleep(3)
    while True:
        refresh_current_timestamp()
        await asyncio.sleep(CLOCK_REFRESH_INTERVAL_SEC)

async def maintain_sun_schedule():
    loaded_day = gss.GetDay() if has_sun_times and hasattr(gss, "GetDay") else None

    await asyncio.sleep(8)
    while True:
        current_day = gss.GetDay() if hasattr(gss, "GetDay") else None
        if current_day is not None and current_day != loaded_day:
            refreshed, result = load_sun_schedule(keep_existing=loaded_day is not None)
            if refreshed:
                loaded_day = current_day
                print(f"Sun schedule updated for day {current_day}.")
            else:
                print(f"Sun schedule refresh failed: {result}")
        await asyncio.sleep(SUN_REFRESH_INTERVAL_SEC)
         
async def control_neopixels():
    global current_timestamp
    lights_on = None  # Track light status to avoid redundant notifications

    await asyncio.sleep(1)
    while True:
        if not has_sun_times:
            if lights_on != False:
                await send_lights_notification("Nighttime, Lights OFF")
                lights_on = False
            send_color(1, 0, 0, 0, 0)
            await asyncio.sleep(5)
            continue
        
        daytime = is_daytime(current_timestamp)

        if daytime:
            if lights_on != True:  
                send_color(*DAY_COLOR)
                await send_lights_notification("Daytime, Lights ON")
                lights_on = True  # Update state

        else:
            if lights_on != False:  # Notify only if status changes
                await send_lights_notification("Nighttime, Lights OFF")
                lights_on = False  # Update state
            if not send_color(*OFF_COLOR):  # Ensure lights are off
                reset_trinket()
                time.sleep(0.2)
                send_color(*OFF_COLOR)

        await asyncio.sleep(5)  # Prevent CPU overload

async def send_status_notification(message):
    if wlan and wlan.isconnected():
        FEED_KEY = 'status-gecko'
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
        data = {'value': str(message)}
        headers = {
            'X-AIO-Key': ADAFRUIT_AIO_KEY,
            'Content-Type': 'application/json'
        }
        response = None
        try:
            response = requests.post(url, headers=headers, json=data, timeout=HTTP_TIMEOUT_SEC)
        except Exception as e:
            print(f"Failed to send error notification: {e}")
        finally:
            if response is not None:
                response.close()

async def send_lights_notification(message):
    if wlan and wlan.isconnected():
        FEED_KEY = 'lights-gecko'
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
        data = {'value': str(message)}
        headers = {
            'X-AIO-Key': ADAFRUIT_AIO_KEY,
            'Content-Type': 'application/json'
        }
        response = None
        try:
            response = requests.post(url, headers=headers, json=data, timeout=HTTP_TIMEOUT_SEC)
        except Exception as e:
            print(f"Failed to send error notification: {e}")
        finally:
            if response is not None:
                response.close()

async def check_connection():
    global wlan
    while True:
        if not wlan or not wlan.isconnected():
            print("Wi-Fi disconnected! Attempting to reconnect...")
            # Fail-safe: don't leave heater latched ON while networking is unstable.
            set_heatlamp(False)
            send_color(59, 255, 255, 255, 50)  # White = Reconnecting
            wlan = connectWifi()

        await asyncio.sleep(60)  # Check every minute

async def periodic_status_report():
    await asyncio.sleep(300)
    while True:
         free_mem = garbage.mem_free()/1024
         total_mem = free_mem + garbage.mem_alloc()/1024
         await send_status_notification(f"System is running smoothly. Free Memory: {free_mem} KB, Total Memory:{total_mem} KB")
         await asyncio.sleep(3600)  # Every hour
        
def connectWifi():
    global wlan
    send_color(59, 255, 255, 255, 5)
    print('Attempting to Connect to WiFi...')

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
    new_wlan = wlan
    new_wlan.active(True)

    # Prevent re-connecting an already connected interface.
    if new_wlan.isconnected():
        print(f'Already connected to {ssid}.')
        send_color(59, 0, 255, 0, 5)
        return new_wlan

    backoff = 1  # Start with 1 second backoff
    max_attempts = 3

    for _ in range(max_attempts):
        new_wlan.connect(ssid, password)

        # Wait up to 5 seconds for this attempt.
        for _ in range(5):
            if new_wlan.isconnected():
                wlan = new_wlan
                send_color(59, 0, 255, 0, 25)
                print(f'Connected to {ssid}.')
                return wlan
            time.sleep(1)

        backoff = min(backoff * 2, 5)  # Keep reconnect blocking window short
        print(f"Retrying WiFi in {backoff} seconds...")
        time.sleep(backoff)

    send_color(59, 255, 0, 0, 5)
    return None

def is_daytime(timestamp):
    if (
        not has_sun_times
        or timestamp is None
        or not isinstance(timestamp, str)
        or "T" not in timestamp
        or sunrise is None
        or sunset is None
    ):
        return False
    try:
        current_seconds = time.mktime(gss.GetTimeTuple(timestamp)) # type: ignore
        sunrise_seconds = time.mktime(gss.GetTimeTuple(sunrise)) # type: ignore
        sunset_seconds = time.mktime(gss.GetTimeTuple(sunset)) # type: ignore
        return sunrise_seconds <= current_seconds < sunset_seconds
    except Exception as e:
        print(f"is_daytime error: {e}")
        return False
  
async def main():
    # Start the tasks
    try:
        await asyncio.gather(
            read_sensor(),
            process_lamp_status_updates(),
            send_temp(),
            send_humidity(),
            manage_setpoint(),
            refresh_time_state(),
            maintain_sun_schedule(),
            control_neopixels(),
            check_connection(),
            periodic_status_report(),
            send_setpoint_periodically(),
            #button_checker(),
            #manage_pump()
        ) # type: ignore
    except Exception as e:
        set_heatlamp(False)
        print(f"Exception occurred in line: {e}")
        print_exception_details(e)
        send_color(59,255,0,0,255)
        await send_status_notification(f"Error in main:{e}")
        #time.sleep(5)
# Run the asyncio event loop
try:
    print("Inizializing...")
    reset_trinket()
    time.sleep(5)
    wlan = connectWifi()
    print(wlan)
    asyncio.run(send_status_notification(f"Connected to Wifi. Software Version: {SOFTWARE_VERSION}"))
    print('Connecting to Temperature Sensor...')
    send_color(58,255,255,255,5)
    retries = 0
    sensor_connected = False
    while retries < SENSOR_STARTUP_RETRIES:
        try:
            sht = init_sensor() #SHT TEMPERATURE SENSOR
            if sht is not None:
                startup_temperature, _ = sht.read()
                if startup_temperature is None or not is_valid_temperature(startup_temperature):
                    raise RuntimeError("Startup sensor read was invalid")
                asyncio.run(send_status_notification("Temperature Sensor Connected"))
                send_color(58,0,255,0,5)
                sensor_connected = True
                break
            retries += 1
        except Exception as e:
            print(f"Error: {e}")
            send_color(58,255,0,0,5)
            retries += 1
            time.sleep(1)
            continue
    if not sensor_connected:
        print("Temperature sensor not detected at startup; continuing in offline mode.")
        send_color(58,255,0,0,5)
        asyncio.run(send_status_notification("Temperature sensor not detected at startup"))
    print('Getting Sunrise and Sunset Times...')
    send_color(57,255,255,255,5)
    refresh_current_timestamp()
    loaded_sun_schedule, result = load_sun_schedule()
    if loaded_sun_schedule:
        is_day = is_daytime(current_timestamp)
        print(f"Risen: {is_day}, Set: {not is_day}")
        startup_timestamp = current_timestamp if current_timestamp is not None else "unknown"
        asyncio.run(send_status_notification(f"Uptime Date: {startup_timestamp}, Sunrise = {sunrise}, Sunset = {sunset}"))
    else:
        print(f"Error getting sunrise/sunset: {result}")
        asyncio.run(send_status_notification("Error fetching sunrise/sunset times at startup"))
    send_color(57,0,255,0,5)
    asyncio.run(send_status_notification(f"Initialization complete, System ON. Software Version: {SOFTWARE_VERSION}"))
    time.sleep(2)
    clear_status_pixels()
    print("Starting main tasks...")
    #MAIN LOOP
    asyncio.run(main())
except Exception as e:
    set_heatlamp(False)
    print(f"System Error: {e}")
    buf = io.StringIO()
    print_exception_details(e, buf)
    traceback_text = buf.getvalue()
    print(traceback_text)
    time.sleep(1)
    send_color(57,255,1,1,5)
    asyncio.run(send_status_notification(f"System Stopped by Exception: {e}"))
except KeyboardInterrupt:
    print("System Stopped")
    if wlan is not None:
        wlan.disconnect()
    asyncio.run(send_status_notification("System Stopped by Keyboard Interrupt"))
finally:
    set_heatlamp(False)
    send_color(1,0,0,0,0)
