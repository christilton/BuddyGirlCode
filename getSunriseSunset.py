import urequests as requests
import time
from gc import collect as gc
from main import connectWifi


def GetTimeTuple(timestamp):
    year = int(timestamp[0:4])
    month = int(timestamp[5:7])
    day = int(timestamp[8:10])
    hour = int(timestamp[11:13])
    minute = int(timestamp[14:16])
    second = int(timestamp[17:19])
    timetuple = (year, month, day, hour, minute, second, 0, 0)
    return timetuple

def GetTime():
    try:
        gc()
        unixdata = requests.get('https://timeapi.io/api/v1/time/current/unix')
        dstdata = requests.get('https://timeapi.io/api/v1/timezone/zone?timeZone=America%2FNew_York')
        #print(unixdata.status_code)
        #Sprint(dstdata.status_code)
        if unixdata.status_code == 200:
            if dstdata.json()['dst_active'] == True:
                time = unixdata.json()['unix_timestamp'] -18000 + 3600
                unixdata.close()
                dstdata.close()
            else:
                time = unixdata.json()['unix_timestamp'] - 18000
                unixdata.close()
                dstdata.close()
            return time
        else:
            print(f"Error fetching time: {unixdata.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching time: {e}")
        return None

def GetDay():
    try:
        gc()
        data = requests.get('https://timeapi.io/api/v1/time/current/unix')
        if data.status_code == 200:
            unix_timestamp = data.json()['unix_timestamp']
            data.close()
            time_tuple = time.localtime(unix_timestamp)
            day_of_year = time_tuple[7]  # tm_yday is at index 7
            return day_of_year
        else:
            print(f"Error fetching day of the year: {data.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching day of the year: {e}")
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
    timestamp = []
    for item in input_timestamp:
        if item < 10:
            timestamp.append(f'0{item}')
        else:
            timestamp.append(item)
    timestamp_final = f'{timestamp[0]}-{timestamp[1]}-{timestamp[2]}T{timestamp[3]}:{timestamp[4]}:{timestamp[5]}'
    return timestamp_final

def GetSunriseSunset():
    """Fetches sunrise and sunset for the correct Eastern Time date."""
    date = GetEasternDate()
    if date is None:
        return "Error fetching date in Get Sunrise/Sunset"
    
    lat, lon = 42.385408, -71.113114
    url = f"https://api.sunrisesunset.io/json?lat={lat}&lng={lon}&time_format=24&timezone=EST&date={date}"

    try:
        gc()
        data = requests.get(url)
        if data.status_code == 200:
            results = data.json().get('results', {})
            data.close()

            if not results:
                return "Error fetching sunrise/sunset times"

            sunrise = f"{date}T{results['sunrise']}Z"
            sunset = f"{date}T{results['sunset']}Z"
            offset = -13400

            return offset, sunrise, sunset
        else:
            print(f"Error fetching sunrise/sunset times: {data.status_code}")
            return "Error fetching sunrise/sunset times"
    except Exception as e:
        print(f"Error fetching sunrise/sunset times: {e}")
        return "Error fetching sunrise/sunset times"

if __name__ == "__main__":
    print(GetTime())
    print(GetSunriseSunset())
    print(GetDay())
    print(GetTimeTuple("2022-01-01T00:00:00"))
