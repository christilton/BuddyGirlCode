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

_last_offset_minutes = -300
_last_utc_unix = None
_last_utc_fetch_ms = None
_last_utc_fetch_failed_ms = None
UTC_CACHE_MS = 5 * 60 * 1000
UTC_RETRY_AFTER_FAILURE_MS = 30 * 1000
HTTP_TIMEOUT_SEC = 8


def _is_rtc_sane(time_tuple):
    return time_tuple is not None and time_tuple[0] >= 2024


def _is_leap_year(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _days_before_month(year, month):
    month_days = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    total = 0
    for index in range(month - 1):
        total += month_days[index]
    if month > 2 and _is_leap_year(year):
        total += 1
    return total


def _day_of_year(year, month, day):
    return _days_before_month(year, month) + day


def _weekday_sunday_first(year, month, day):
    # Sakamoto's algorithm, returns 0=Sunday .. 6=Saturday.
    offsets = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    calc_year = year - 1 if month < 3 else year
    return (calc_year + calc_year // 4 - calc_year // 100 + calc_year // 400 + offsets[month - 1] + day) % 7


def _nth_sunday(year, month, occurrence):
    first_weekday = _weekday_sunday_first(year, month, 1)
    first_sunday = 1 if first_weekday == 0 else 8 - first_weekday
    return first_sunday + ((occurrence - 1) * 7)


def _eastern_dst_offset_minutes_from_utc(utc_unix):
    utc_tuple = time.localtime(utc_unix)
    year = utc_tuple[0]

    march_sunday = _nth_sunday(year, 3, 2)
    november_sunday = _nth_sunday(year, 11, 1)

    # US Eastern DST transitions expressed in UTC:
    # starts 2:00 AM EST => 07:00 UTC
    # ends   2:00 AM EDT => 06:00 UTC
    dst_start_utc = time.mktime((year, 3, march_sunday, 7, 0, 0, 0, 0))
    dst_end_utc = time.mktime((year, 11, november_sunday, 6, 0, 0, 0, 0))

    if dst_start_utc <= utc_unix < dst_end_utc:
        return -240
    return -300


def _parse_clock_parts(clock_text):
    clock = str(clock_text).strip()
    upper_clock = clock.upper()
    am_pm = None
    if "PM" in upper_clock:
        am_pm = "PM"
    elif "AM" in upper_clock:
        am_pm = "AM"

    clock_token_chars = []
    for char in clock:
        if ("0" <= char <= "9") or char == ":":
            clock_token_chars.append(char)
        elif len(clock_token_chars) == 0 and char == " ":
            continue
        else:
            break

    clock_token = "".join(clock_token_chars)
    parts = clock_token.split(":")
    if len(parts) < 2:
        raise ValueError("Timestamp is missing hour/minute fields")

    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) > 2 and parts[2] != "" else 0

    if am_pm == "PM" and hour < 12:
        hour += 12
    elif am_pm == "AM" and hour == 12:
        hour = 0

    return hour, minute, second


def GetTimeTuple(timestamp):
    # Accept ISO-like timestamps with optional trailing 'Z' or fractional seconds.
    ts = str(timestamp).strip()
    if ts.endswith("Z"):
        ts = ts[:-1]
    if "." in ts:
        ts = ts.split(".", 1)[0]

    separator = "T" if "T" in ts else " "
    if separator not in ts:
        raise ValueError("Timestamp missing date/time separator")

    date_text, time_text = ts.split(separator, 1)
    date_parts = date_text.split("-")
    if len(date_parts) != 3:
        raise ValueError("Timestamp date is malformed")

    year = int(date_parts[0])
    month = int(date_parts[1])
    day = int(date_parts[2])
    hour, minute, second = _parse_clock_parts(time_text)
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


def _fetch_utc_unix(force_refresh=False):
    global _last_utc_unix, _last_utc_fetch_ms, _last_utc_fetch_failed_ms

    now_ms = time.ticks_ms()

    if not force_refresh and _last_utc_unix is not None and _last_utc_fetch_ms is not None:
        cache_age_ms = time.ticks_diff(now_ms, _last_utc_fetch_ms)
        if cache_age_ms >= 0 and cache_age_ms <= UTC_CACHE_MS:
            return _last_utc_unix

    if not force_refresh and _last_utc_fetch_failed_ms is not None:
        retry_age_ms = time.ticks_diff(now_ms, _last_utc_fetch_failed_ms)
        if retry_age_ms >= 0 and retry_age_ms < UTC_RETRY_AFTER_FAILURE_MS:
            return _last_utc_unix

    if not HasNetwork():
        return _last_utc_unix

    unixdata = None
    try:
        gc()
        unixdata = requests.get('https://timeapi.io/api/v1/time/current/unix', timeout=HTTP_TIMEOUT_SEC)
        if unixdata.status_code == 200:
            _last_utc_unix = int(unixdata.json()['unix_timestamp'])
            _last_utc_fetch_ms = now_ms
            _last_utc_fetch_failed_ms = None
            return _last_utc_unix
        print(f"Error fetching time: {unixdata.status_code}")
    except Exception as e:
        print(f"Error fetching time: {e}")
    finally:
        if unixdata is not None:
            unixdata.close()

    _last_utc_fetch_failed_ms = now_ms
    return _last_utc_unix


def RefreshTimeCache():
    return _fetch_utc_unix(force_refresh=True)

def GetUtcOffsetMinutes():
    global _last_offset_minutes
    utc_unix = _fetch_utc_unix()
    if utc_unix is not None:
        _last_offset_minutes = _eastern_dst_offset_minutes_from_utc(utc_unix)
    return _last_offset_minutes


def GetTime():
    utc_unix = _fetch_utc_unix()
    if utc_unix is None:
        return None
    offset_minutes = GetUtcOffsetMinutes()
    return utc_unix + (offset_minutes * 60)


def GetLocalTimeTuple():
    eastern_unix = GetTime()
    if eastern_unix is not None:
        return time.localtime(eastern_unix)

    rtc_tuple = time.localtime()
    if _is_rtc_sane(rtc_tuple):
        return rtc_tuple
    return None


def GetDay():
    try:
        time_tuple = GetLocalTimeTuple()
        if time_tuple is None:
            return None
        return time_tuple[7]  # tm_yday is at index 7
    except Exception as e:
        print(f"Error deriving day of the year: {e}")
        return None


def GetEasternDate():
    """Converts Unix time to YYYY-MM-DD (Eastern Time) manually."""
    time_tuple = GetLocalTimeTuple()
    if time_tuple is None:
        return None
    year, month, day = time_tuple[0], time_tuple[1], time_tuple[2]
    return f"{year:04d}-{month:02d}-{day:02d}"


def GetTimeStamp(input_timestamp):
    year, month, day, hour, minute, second = input_timestamp[0:6]
    return f'{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}'


def GetLocalTimestamp():
    time_tuple = GetLocalTimeTuple()
    if time_tuple is None:
        return None
    return GetTimeStamp(time_tuple)


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
        data = requests.get(url, timeout=HTTP_TIMEOUT_SEC)
        if data.status_code == 200:
            results = data.json().get('results', {})

            if not results:
                return "Error fetching sunrise/sunset times"

            # sunrise/sunset are local clock times for the date in America/New_York.
            sunrise = f"{date}T{results['sunrise']}"
            sunset = f"{date}T{results['sunset']}"
            offset = _last_offset_minutes

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
