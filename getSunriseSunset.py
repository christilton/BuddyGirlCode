try:
    import urequests as requests
except ImportError:
    import requests

import time
from gc import collect as gc

try:
    import network
except ImportError:
    network = None


def GetTimeTuple(timestamp):
    # Accept ISO-like timestamps with optional trailing 'Z' or fractional seconds.
    ts = str(timestamp).strip()
    if ts.endswith("Z"):
        ts = ts[:-1]
    if "." in ts:
        ts = ts.split(".", 1)[0]

    year = int(ts[0:4])
    month = int(ts[5:7])
    day = int(ts[8:10])
    hour = int(ts[11:13])
    minute = int(ts[14:16])
    second = int(ts[17:19])
    timetuple = (year, month, day, hour, minute, second, 0, 0)
    return timetuple


def HasNetwork():
    if network is None:
        return True
    try:
        wlan = network.WLAN(network.STA_IF)
        return wlan.active() and wlan.isconnected()
    except Exception:
        # If network state cannot be checked, allow request layer to decide.
        return True


def _get_dst_active():
    data = None
    try:
        if not HasNetwork():
            return None
        gc()
        data = requests.get('https://timeapi.io/api/v1/timezone/zone?timeZone=America%2FNew_York')
        if data.status_code == 200:
            payload = data.json()
            return bool(payload.get('dst_active', False))
    except Exception as e:
        print(f"Error fetching DST state: {e}")
    finally:
        if data is not None:
            data.close()
    return None


def GetUtcOffsetMinutes():
    dst_active = _get_dst_active()
    if dst_active is None:
        # Conservative fallback for Eastern Standard Time.
        return -300
    return -240 if dst_active else -300


def GetTime():
    unixdata = None
    try:
        if not HasNetwork():
            return None
        gc()
        unixdata = requests.get('https://timeapi.io/api/v1/time/current/unix')
        if unixdata.status_code == 200:
            utc_unix = int(unixdata.json()['unix_timestamp'])
            offset_minutes = GetUtcOffsetMinutes()
            return utc_unix + (offset_minutes * 60)
        else:
            print(f"Error fetching time: {unixdata.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching time: {e}")
        return None
    finally:
        if unixdata is not None:
            unixdata.close()


def GetDay():
    try:
        eastern_unix = GetTime()
        if eastern_unix is None:
            return None
        time_tuple = time.localtime(eastern_unix)
        return time_tuple[7]  # tm_yday is at index 7
    except Exception as e:
        print(f"Error deriving day of the year: {e}")
        return None


def GetEasternDate():
    """Converts Unix time to YYYY-MM-DD (Eastern Time) manually."""
    unix_time = GetTime()
    if unix_time is None:
        return None
    time_tuple = time.localtime(unix_time)  # Convert Unix time to a time tuple
    year, month, day = time_tuple[0], time_tuple[1], time_tuple[2]
    return f"{year:04d}-{month:02d}-{day:02d}"


def GetTimeStamp(input_timestamp):
    year, month, day, hour, minute, second = input_timestamp[0:6]
    return f'{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}'


def GetSunriseSunset():
    """Fetches sunrise and sunset for the correct Eastern Time date."""
    if not HasNetwork():
        return "No network connection"

    date = GetEasternDate()
    if date is None:
        return "Error fetching date in Get Sunrise/Sunset"

    lat, lon = 42.385408, -71.113114
    url = f"https://api.sunrisesunset.io/json?lat={lat}&lng={lon}&time_format=24&timezone=America%2FNew_York&date={date}"

    data = None
    try:
        gc()
        data = requests.get(url)
        if data.status_code == 200:
            results = data.json().get('results', {})

            if not results:
                return "Error fetching sunrise/sunset times"

            # sunrise/sunset are local clock times for the date in America/New_York.
            sunrise = f"{date}T{results['sunrise']}"
            sunset = f"{date}T{results['sunset']}"
            offset = GetUtcOffsetMinutes()

            return offset, sunrise, sunset
        else:
            print(f"Error fetching sunrise/sunset times: {data.status_code}")
            return "Error fetching sunrise/sunset times"
    except Exception as e:
        print(f"Error fetching sunrise/sunset times: {e}")
        return "Error fetching sunrise/sunset times"
    finally:
        if data is not None:
            data.close()


if __name__ == "__main__":
    print("HasNetwork:", HasNetwork())
    print("GetTime:", GetTime())
    print("GetDay:", GetDay())
    print("GetSunriseSunset:", GetSunriseSunset())
    print("GetTimeTuple:", GetTimeTuple("2022-01-01T00:00:00"))
