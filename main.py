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
current_timestamp = 0
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
def send_color(lighttype,r, g, b,brightness):
    color_data = bytearray([lighttype,r, g, b,brightness])
    while True:
        try:
            #print(f"Sending color data: {list(color_data)}")
            trinket.writeto(TRINKET_ADDRESS, color_data)
            break
        except OSError as e:
            print(f"Error sending color: {e}")
            time.sleep(5)
            continue

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
    while True:
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/day-setpoint-gecko/data/last'
        headers = {
        'X-AIO-Key': ADAFRUIT_AIO_KEY,
        'Content-Type': 'application/json'
        }
        gc()
        if wlan and wlan.isconnected():
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                #print(data)
                daytime_setpoint = float(data['value'])
                response.close()
            else:
                daytime_setpoint = 69.0 #HARDCODE HERE
        else:
            daytime_setpoint = 69.0 #HARDCODE HERE
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/night-setpoint-gecko/data/last'
        headers = {
        'X-AIO-Key': ADAFRUIT_AIO_KEY,
        'Content-Type': 'application/json'
        }
        gc()
        if wlan and wlan.isconnected():
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                nighttime_setpoint = float(data['value'])
            else:
                nighttime_setpoint = 64.0 #HARDCODE HERE
        else:
            nighttime_setpoint = 64.0
        response.close()
        
        #HANDLE TIME HERE
        if wlan and wlan.isconnected():
            new_setpoint = nighttime_setpoint if compare_timestamps(current_timestamp, sunset,1800) or not compare_timestamps(current_timestamp, sunrise,3600) else daytime_setpoint
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
        if retries < max_retries:
            while retries < max_retries:
                try:
                    temperature = sht.temp()
                    humidity = sht.humidity()
                    break  # Successful read, exit retry loop
                except OSError as e:
                    retries += 1
                    await asyncio.sleep(0.5)
        else:
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
    if wlan and wlan.isconnected():
        FEED_KEY = 'temperature-gecko'
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
        global current_timestamp
        
        while True:
            
            if sht is not None:
                temperature = sht.temp()
            #print(temperature)
            data = {'value': temperature}
            headers = {
                'X-AIO-Key': ADAFRUIT_AIO_KEY,
                'Content-Type': 'application/json'
            }
            
            try:
                # Send data to Adafruit IO
                gc()
                reply = requests.post(url, headers=headers, json=data)
                #print(reply.text)
                data = reply.json()
                timestamp_str = data['created_at']
                #print(timestamp_str)
                sse = time.mktime(gss.GetTimeTuple(timestamp_str)) # type: ignore
                current_timestamp = gss.GetTimeStamp((time.localtime(sse+(offset*60))))# type: ignore
                
                #print(f"Current Time: {current_timestamp} ET")
                if reply.status_code != 200:
                    print(reply.status_code)
                    print(reply.text)
                reply.close()  # Close the response to free up resources
            except Exception as e:
                print("Failed to send data (T):", e)
            
            await asyncio.sleep(10)  # Send data every 10 seconds

async def send_humidity():
    if wlan and wlan.isconnected():
        FEED_KEY = 'humidity-gecko'
        url = f'https://io.adafruit.com/api/v2/{ADAFRUIT_AIO_USERNAME}/feeds/{FEED_KEY}/data'
        
        while True:
            if sht is not None:
                if sht.humidity() is not None:
                    humidity = sht.humidity()
            
            data = {'value': humidity}
            headers = {
                'X-AIO-Key': ADAFRUIT_AIO_KEY,
                'Content-Type': 'application/json'
            }
            
            try:
                # Send data to Adafruit IO
                gc()
                reply = requests.post(url, headers=headers, json=data)
                if reply.status_code != 200:
                    print(reply.status_code)
                    print(reply.text)
                reply.close()  # Close the response to free up resources
            except Exception as e:
                print("Failed to send data: (H)", e)
            
            await asyncio.sleep(10)  # Send data every 10 seconds
        
async def control_neopixels():
    global current_timestamp, sunHasRisen, sunHasSet

    brightness = 255  # Default to full brightness
    lights_on = None  # Track light status to avoid redundant notifications

    while True:
        
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
    if wlan and wlan.isconnected():
        while True:
            
            current_day = gss.GetDay()
            print(f'upday = {upday}, current_day = {current_day}')
           # await send_status_notification(f'Checking Reboot: upday = {upday}, current_day = {current_day}')
            
            if current_day is None:
                print("Error: Unable to fetch current day")
                await send_status_notification("System unable to verify date, skipping reboot check.")
                continue

            if current_day != upday:
                await send_status_notification("System Resetting")
                relay.off()
                reset_trinket()
                machine.reset()
                
            await asyncio.sleep(900)

async def check_connection():
    global wlan, connected
    while True:
        if not wlan or not wlan.isconnected():
            print("Wi-Fi disconnected! Attempting to reconnect...")
            send_color(59, 255, 255, 255, 50)  # White = Reconnecting
            
            wlan = connectWifi()  # ✅ This ensures wlan is updated globally
            
            if wlan:
                connected = True
            else:
                connected = False
        else:
            connected = True
        
        await asyncio.sleep(60)  # Check every minute

async def periodic_status_report():
    while True:
         free_mem = garbage.mem_free()/1024
         total_mem = free_mem + garbage.mem_alloc()/1024
         await send_status_notification(f"System is running smoothly. Free Memory: {free_mem} KB, Total Memory:{total_mem} KB")
         await asyncio.sleep(3600)  # Every hour
        
def connectWifi():
    global wlan, connected
    send_color(59, 255, 255, 255, 5)
    print('Attempting to Connect to WiFi...')
    if wlan is None:
        new_wlan = network.WLAN(network.STA_IF)
        new_wlan.active(True)
    
    elif wlan.isconnected():
        print(f'Already connected to {ssid}.')
        send_color(59, 0, 255, 0, 5)  # Green = already connected
        connected = True
        return wlan

    new_wlan.connect(ssid, password)
    timeout = 0
    backoff = 1  # Start with 1 second backoff

    while not new_wlan.isconnected():
        new_wlan.connect(ssid, password)
        for _ in range(10):  # 1 second increments
            if new_wlan.isconnected():
                wlan = new_wlan
                connected = True
                send_color(59, 0, 255, 0, 25)
                print(f'Connected to {ssid}.')
                return wlan
            time.sleep(1)
        timeout += 1
        backoff = min(backoff * 2, 60)  # Double backoff up to 60 seconds
        print(f"Retrying WiFi in {backoff} seconds...")
        time.sleep(backoff)
    send_color(59, 255, 0, 0, 5)
    connected = False
    return None

def compare_timestamps(currenttime,eventtime,newoffset):
    ct = time.mktime(gss.GetTimeTuple(currenttime))-newoffset # type: ignore
    et = time.mktime(gss.GetTimeTuple(eventtime))-newoffset # type: ignore
    if int(ct) >= int(et):
        return True
    else:
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
        print(f"Exception occurred: {e}")
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
    if connected:
        offset,sunrise,sunset = gss.GetSunriseSunset()
        upday = gss.GetDay()
        uptime2 = gss.GetTime()
        uptime2_timestamp = gss.GetTimeStamp(time.localtime(uptime2))
        asyncio.run(send_status_notification(f"Uptime Date: {uptime2_timestamp}, Upday: {upday}, Sunrise = {sunrise}, Sunset = {sunset}"))
        if compare_timestamps(uptime2_timestamp,sunrise,0):
            sunHasRisen = True
        if compare_timestamps(uptime2_timestamp,sunset,0):
            sunHasSet = True
        print(f"Risen: {sunHasRisen}, Set: {sunHasSet}")
    else: 
        upday = 0
    send_color(57,0,255,0,5)
    asyncio.run(send_status_notification("Initialization complete, System ON"))
    time.sleep(2)
    send_color(1,0,0,0,0)
    #MAIN LOOP
    asyncio.run(main())
except Exception as e:
    print(f"System Error: {e}")
    time.sleep(1)
    send_color(57,255,1,1,5)
    asyncio.run(send_status_notification(f"System Stopped by Exception: {e}"))
except KeyboardInterrupt:
    print("System Stopped")
    asyncio.run(send_status_notification("System Stopped by Keyboard Interrupt"))
finally:
    relay.off()
    send_color(1,0,0,0,0)