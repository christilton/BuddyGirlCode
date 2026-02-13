#This code was written by Chris Tilton for his Crested Gecko, Buddy, in 2024-2025

import machine, time, sys, network, io
import uasyncio as asyncio
import urequests as requests
import gc as garbage
import getSunriseSunset as gss
from gc import collect as gc
from secrets import ADAFRUIT_AIO_KEY, ADAFRUIT_AIO_USERNAME, ssid, password
from machine import I2C, Pin, WDT
from sen0546 import SEN0546 


# Global variable for setpoint
setpoint = 0
deadband = .25
sunHasRisen = False
sunHasSet = False
connected = False
hasnetwork = False
current_timestamp = 0
offset = 0
sunrise = None
sunset = None
has_sun_times = False
# Initialize relay pin
relay = Pin(4, Pin.OUT)
relay.off()
sht = None
wlan = None
# I2C configuration for controlling NeoPixels
SDA_PIN = 0  # Adjust pins as necessary
SCL_PIN = 1

trinket = I2C(0,scl=Pin(SCL_PIN, Pin.PULL_UP), sda=Pin(SDA_PIN, Pin.PULL_UP))
resetpin = Pin(2, Pin.OUT)
resetpin.high()
TRINKET_ADDRESS = 0x12 

DAY_COLOR = (1,255, 150, 20,255)  # Golden Yellow
OFF_COLOR = (1, 0, 0, 0, 0)   
    
#button_pin = Pin(15, Pin.IN)

def reset_i2c():
    global i2c
    i2c = I2C(1, scl=Pin(19), sda=Pin(18))  # Reinitialize I2C
    print("I2C Reset")

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

async def update_setpoint_feed(new_setpoint):
    global setpoint
    FEED_KEY = 'setpoint-gecko'
    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
    
    data = {'value': new_setpoint}
    headers = {
        'X-AIO-Key': ADAFRUIT_AIO_KEY,
        'Content-Type': 'application/json'
    }
    
    try:
        if new_setpoint != 0:
            gc()
            response = requests.post(url, headers=headers, json=data)
            if response.status_code == 200:
                print(f"Successfully updated setpoint feed.")
            else:
                print(response.text)
            response.close()
    except Exception as e:
        print(f"Failed to update setpoint feed: {e}")
    await asyncio.sleep(1)  # Small delay to prevent CPU overload
        
async def send_setpoint_periodically():
    global setpoint
    while True:
        
        await update_setpoint_feed(setpoint)  # Send the current setpoint to Adafruit IO
        await asyncio.sleep(3600)  # Wait for an hour before sending again

async def manage_setpoint():
    global setpoint
    global current_timestamp
    global sunHasRisen, sunHasSet

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
                response = requests.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    return float(data['value'])
        except Exception as e:
            print(f"Failed to fetch {feed_key}: {e}")
        finally:
            if response is not None:
                response.close()
        return default_value

    while True:
        daytime_setpoint = fetch_last_feed_value('day-setpoint-gecko', 69.0)
        nighttime_setpoint = fetch_last_feed_value('night-setpoint-gecko', 64.0)
        
        # Keep setpoint day/night logic aligned with light-control logic.
        if wlan and wlan.isconnected() and has_sun_times:
            # Update sun state from timestamp when available, then use the same
            # state machine as control_neopixels so lights/setpoint cannot diverge.
            if compare_timestamps(current_timestamp, sunrise, 0):
                sunHasRisen = True
            if compare_timestamps(current_timestamp, sunset, 0):
                sunHasSet = True
            is_daytime = sunHasRisen and not sunHasSet
            new_setpoint = daytime_setpoint if is_daytime else nighttime_setpoint
        else:
            new_setpoint = 67.0
        
        if setpoint != new_setpoint:
            setpoint = new_setpoint
            print(f"Setpoint changed to: {setpoint}°F")
            await update_setpoint_feed(setpoint)
        
        await asyncio.sleep(90)  # Check every minute     

async def read_sensor(sht):
    lamp_status = 0
    max_retries = 5
    while True:
        retries = 0
        temperature = None
        humidity = None
        while retries < max_retries:
            try:
                temperature = sht.temp()
                humidity = sht.humidity()
                break  # Successful read, exit retry loop
            except OSError as e:
                retries += 1
                await asyncio.sleep(0.5)

        if retries >= max_retries or temperature is None:
            print("Max retries reached, skipping this cycle.")
            await send_status_notification("Temperature Sensor Error. Resetting...")
            reset_i2c()
            relay.off()
            continue
            # Skip to next loop iteration
        #print("Temperature: {}°F, Humidity: {}%".format(temperature, humidity))

        # Bang-bang controller logic
        if temperature < setpoint - deadband:
            relay.on()  # Turn on the heat lamp
            if lamp_status == 0 :
                if wlan and wlan.isconnected():
                    data = {'value': f'ON, Temp {temperature}'}
                    FEED_KEY = 'lamp-gecko'
                    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
                    headers = {
                        'X-AIO-Key': ADAFRUIT_AIO_KEY,
                        'Content-Type': 'application/json'
                    }
                    reply = requests.post(url, headers=headers, json=data)
                    reply.close()
                lamp_status = 1
                print("Heat Lamp turned ON.")
    
        elif temperature >= setpoint:
            relay.off()  # Turn off the heat lamp
            if lamp_status == 1:
                if wlan and wlan.isconnected():
                    data = {'value': f'OFF, Temp {temperature}'}
                    FEED_KEY = 'lamp-gecko'
                    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
                    headers = {
                        'X-AIO-Key': ADAFRUIT_AIO_KEY,
                        'Content-Type': 'application/json'
                    }
                    reply = requests.post(url, headers=headers, json=data)
                    reply.close()

                lamp_status = 0
                print("Heat Lamp turned OFF.")
    
        await asyncio.sleep(1)  # Read sensor values every second

async def send_temp():
    FEED_KEY = 'temperature-gecko'
    url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
    global current_timestamp

    while True:
        if not (wlan and wlan.isconnected()):
            await asyncio.sleep(10)
            continue

        if sht is None:
            await asyncio.sleep(10)
            continue

        temperature = sht.temp()
        data = {'value': temperature}
        headers = {
            'X-AIO-Key': ADAFRUIT_AIO_KEY,
            'Content-Type': 'application/json'
        }

        reply = None
        try:
            # Send data to Adafruit IO
            gc()
            reply = requests.post(url, headers=headers, json=data)
            data = reply.json()
            timestamp_str = data['created_at']
            sse = time.mktime(gss.GetTimeTuple(timestamp_str)) # type: ignore
            current_timestamp = gss.GetTimeStamp((time.localtime(sse+(offset*60))))# type: ignore

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

    while True:
        if not (wlan and wlan.isconnected()):
            await asyncio.sleep(10)
            continue

        if sht is None:
            await asyncio.sleep(10)
            continue

        humidity = sht.humidity()
        if humidity is None:
            await asyncio.sleep(10)
            continue

        data = {'value': humidity}
        headers = {
            'X-AIO-Key': ADAFRUIT_AIO_KEY,
            'Content-Type': 'application/json'
        }

        reply = None
        try:
            # Send data to Adafruit IO
            gc()
            reply = requests.post(url, headers=headers, json=data)
            if reply.status_code != 200:
                print(reply.status_code)
                print(reply.text)
        except Exception as e:
            print("Failed to send data: (H)", e)
        finally:
            if reply is not None:
                reply.close()  # Close the response to free up resources

        await asyncio.sleep(10)  # Send data every 10 seconds
        
async def control_neopixels():
    global current_timestamp, sunHasRisen, sunHasSet

    brightness = 255  # Default to full brightness
    lights_on = None  # Track light status to avoid redundant notifications

    while True:
        if not has_sun_times:
            if lights_on != False:
                await send_lights_notification("Nighttime, Lights OFF")
                lights_on = False
            send_color(1, 0, 0, 0, 0)
            await asyncio.sleep(5)
            continue
        
        if compare_timestamps (current_timestamp, sunrise, 0):
            sunHasRisen = True
        if compare_timestamps (current_timestamp, sunset, 0):
            sunHasSet = True
        if sunHasRisen and not sunHasSet:
            if lights_on != True:  
                send_color(*DAY_COLOR)
                await send_lights_notification("Daytime, Lights ON")
                lights_on = True  # Update state

        else:
            if lights_on != False:  # Notify only if status changes
                await send_lights_notification("Nighttime, Lights OFF")
                lights_on = False  # Update state
            send_color(1, 0, 0, 0, 0)  # Ensure lights are off
            brightness = 0  # Reset brightness

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
        try:
            response = requests.post(url, headers=headers, json=data)
            response.close()
        except Exception as e:
            print(f"Failed to send error notification: {e}")

async def send_lights_notification(message):
    if wlan and wlan.isconnected():
        FEED_KEY = 'lights-gecko'
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
        data = {'value': str(message)}
        headers = {
            'X-AIO-Key': ADAFRUIT_AIO_KEY,
            'Content-Type': 'application/json'
        }
        try:
            response = requests.post(url, headers=headers, json=data)
            response.close()
        except Exception as e:
            print(f"Failed to send error notification: {e}")

async def check_reboot(upday):
    while True:
        if not (wlan and wlan.isconnected()):
            await asyncio.sleep(600)
            continue

        current_day = gss.GetDay()
        print(f'upday = {upday}, current_day = {current_day}')
       # await send_status_notification(f'Checking Reboot: upday = {upday}, current_day = {current_day}')

        if current_day is None:
            print("Error: Unable to fetch current day")
            await send_status_notification("System unable to verify date, skipping reboot check.")
            await asyncio.sleep(600)
            continue

        if current_day != upday:
            await send_status_notification("System Resetting")
            relay.off()
            reset_trinket()
            machine.reset()

        await asyncio.sleep(600)

async def check_connection():
    global wlan, connected, hasnetwork
    while True:
        if not wlan or not wlan.isconnected():
            print("Wi-Fi disconnected! Attempting to reconnect...")
            # Fail-safe: don't leave heater latched ON while networking is unstable.
            relay.off()
            send_color(59, 255, 255, 255, 50)  # White = Reconnecting
            wlan = connectWifi()
            hasnetwork = wlan is not None
            connected = hasnetwork
        else:
            hasnetwork = True
            connected = True

        await asyncio.sleep(60)  # Check every minute

async def periodic_status_report():
    while True:
         free_mem = garbage.mem_free()/1024
         total_mem = free_mem + garbage.mem_alloc()/1024
         await send_status_notification(f"System is running smoothly. Free Memory: {free_mem} KB, Total Memory:{total_mem} KB")
         await asyncio.sleep(3600)  # Every hour
        
def connectWifi():
    global wlan, connected, hasnetwork
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
        connected = True
        hasnetwork = True
        return new_wlan

    backoff = 1  # Start with 1 second backoff
    max_attempts = 3

    for _ in range(max_attempts):
        new_wlan.connect(ssid, password)

        # Wait up to 5 seconds for this attempt.
        for _ in range(5):
            if new_wlan.isconnected():
                wlan = new_wlan
                connected = True
                hasnetwork = True
                send_color(59, 0, 255, 0, 25)
                print(f'Connected to {ssid}.')
                return wlan
            time.sleep(1)

        backoff = min(backoff * 2, 5)  # Keep reconnect blocking window short
        print(f"Retrying WiFi in {backoff} seconds...")
        time.sleep(backoff)

    send_color(59, 255, 0, 0, 5)
    connected = False
    hasnetwork = False
    return None

def compare_timestamps(currenttime,eventtime,newoffset):
    if currenttime is None or eventtime is None:
        return False
    try:
        ct = time.mktime(gss.GetTimeTuple(currenttime)) # type: ignore
        et = time.mktime(gss.GetTimeTuple(eventtime))-newoffset # type: ignore
        if int(ct) >= int(et):
            return True
        else:
            return False
    except Exception as e:
        print(f"compare_timestamps error: {e}")
        return False
  
async def main():
    # Start the tasks
    try:
        await asyncio.gather(
            read_sensor(sht),
            send_temp(),
            send_humidity(),
            manage_setpoint(),
            control_neopixels(),
            check_reboot(upday),
            check_connection(),
            periodic_status_report(),
            send_setpoint_periodically(),
            #button_checker(),
            #manage_pump()
        ) # type: ignore
    except Exception as e:
        relay.off()
        print(f"Exception occurred in line: {e}")
        sys.print_exception(e)
        send_color(59,255,0,0,255)
        await send_status_notification(f"Error in main:{e}")
        #time.sleep(5)
        #machine.reset()
# Run the asyncio event loop
try:
    print("Inizializing...")
    reset_trinket()
    time.sleep(5)
    wlan = connectWifi()
    hasnetwork = wlan is not None
    print(wlan)
    asyncio.run(send_status_notification(f"Connected to Wifi"))
    print('Connecting to Temperature Sensor...')
    send_color(58,255,255,255,5)
    retries = 0
    while retries < 5:
        try:
            sht = SEN0546(scl_pin=19,sda_pin=18) #SHT TEMPERATURE SENSOR
            if sht is not None:
                asyncio.run(send_status_notification("Temperature Sensor Connected"))
                send_color(58,0,255,0,5)
                break
            else:
                retries+= 1
        except OSError as e:
            print(f"Error: {e}")
            send_color(58,255,0,0,5)
            retries += 1
            time.sleep(1)
            continue
    print('Getting Sunrise and Sunset Times...')
    send_color(57,255,255,255,5)
    if hasnetwork:
        if not hasattr(gss, "GetSunriseSunset"):
            print("getSunriseSunset module missing GetSunriseSunset()")
            print("Available attributes:", dir(gss))
            asyncio.run(send_status_notification("gss missing GetSunriseSunset; check uploaded module"))
            result = None
        else:
            result = gss.GetSunriseSunset()
        # Validate result is a tuple/list with three items (offset, sunrise, sunset)
        if result and isinstance(result, (tuple, list)) and len(result) == 3:
            offset, sunrise, sunset = result
            has_sun_times = True
            upday = gss.GetDay() if hasattr(gss, "GetDay") else 0
            uptime2 = gss.GetTime() if hasattr(gss, "GetTime") else None
            if uptime2 is not None and hasattr(gss, "GetTimeStamp"):
                uptime2_timestamp = gss.GetTimeStamp(time.localtime(uptime2))
            else:
                uptime2_timestamp = "unknown"
            asyncio.run(send_status_notification(f"Uptime Date: {uptime2_timestamp}, Upday: {upday}, Sunrise = {sunrise}, Sunset = {sunset}"))
            if compare_timestamps(uptime2_timestamp, sunrise, 0):
                sunHasRisen = True
            if compare_timestamps(uptime2_timestamp, sunset, 0):
                sunHasSet = True
            print(f"Risen: {sunHasRisen}, Set: {sunHasSet}")
            print(f"Risen: {sunHasRisen}, Set: {sunHasSet}")
        else:
            print(f"Error getting sunrise/sunset: {result}")
            asyncio.run(send_status_notification("Error fetching sunrise/sunset times at startup"))
            upday = 0
            has_sun_times = False
    else: 
        upday = 0
        has_sun_times = False
    send_color(57,0,255,0,5)
    asyncio.run(send_status_notification("Initialization complete, System ON"))
    time.sleep(2)
    send_color(1,0,0,0,0)
    #MAIN LOOP
    asyncio.run(main())
except Exception as e:
    relay.off()
    print(f"System Error: {e}")
    buf = io.StringIO()
    sys.print_exception(e, buf)
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
    relay.off()
    send_color(1,0,0,0,0)



