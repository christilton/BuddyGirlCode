import sys

script_path = __file__
if "/" in script_path:
    script_dir = script_path.rsplit("/", 1)[0]
    if "/" in script_dir:
        repo_root = script_dir.rsplit("/", 1)[0]
        if repo_root and repo_root not in sys.path:
            sys.path.append(repo_root)
elif "\\" in script_path:
    script_dir = script_path.rsplit("\\", 1)[0]
    if "\\" in script_dir:
        repo_root = script_dir.rsplit("\\", 1)[0]
        if repo_root and repo_root not in sys.path:
            sys.path.append(repo_root)

try:
    import getSunriseSunset as gss
except ImportError:
    # MicroPython fallback when executing from /testscripts.
    if ".." not in sys.path:
        sys.path.append("..")
    if "/" not in sys.path:
        sys.path.append("/")
    import getSunriseSunset as gss


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def close(self):
        return None


class FakeRequests:
    def __init__(self, route_map):
        self.route_map = route_map

    def get(self, url):
        for key, value in self.route_map.items():
            if key in url:
                return FakeResponse(value[0], value[1])
        return FakeResponse(404, {})


def assert_equal(actual, expected, name):
    if actual != expected:
        raise AssertionError(f"{name} failed. Expected {expected}, got {actual}")
    print(f"PASS: {name}")


def run_tests():
    original_requests = gss.requests
    original_has_network = gss.HasNetwork
    original_get_date = gss.GetEasternDate
    original_get_offset = gss.GetUtcOffsetMinutes
    original_get_dst = gss._get_dst_active

    try:
        assert_equal(gss.GetTimeTuple("2026-02-13T09:08:07Z"), (2026, 2, 13, 9, 8, 7, 0, 0), "GetTimeTuple handles trailing Z")
        assert_equal(gss.GetTimeTuple("2026-02-13T09:08:07.123"), (2026, 2, 13, 9, 8, 7, 0, 0), "GetTimeTuple handles fractional seconds")
        assert_equal(gss.GetTimeStamp((2026, 2, 3, 4, 5, 6, 0, 0)), "2026-02-03T04:05:06", "GetTimeStamp formatting")

        gss._get_dst_active = lambda: True
        assert_equal(gss.GetUtcOffsetMinutes(), -240, "GetUtcOffsetMinutes DST")
        gss._get_dst_active = lambda: False
        assert_equal(gss.GetUtcOffsetMinutes(), -300, "GetUtcOffsetMinutes standard")
        gss._get_dst_active = lambda: None
        assert_equal(gss.GetUtcOffsetMinutes(), -300, "GetUtcOffsetMinutes fallback")

        gss.HasNetwork = lambda: False
        assert_equal(gss.GetSunriseSunset(), "No network connection", "GetSunriseSunset network guard")

        gss.HasNetwork = lambda: True
        gss.GetEasternDate = lambda: "2026-02-13"
        gss.GetUtcOffsetMinutes = lambda: -300
        gss.requests = FakeRequests(
            {
                "sunrisesunset.io": (200, {"results": {"sunrise": "06:45:00", "sunset": "17:21:00"}})
            }
        )
        assert_equal(
            gss.GetSunriseSunset(),
            (-300, "2026-02-13T06:45:00", "2026-02-13T17:21:00"),
            "GetSunriseSunset success path",
        )

        print("All getSunriseSunset standalone tests passed.")
    finally:
        gss.requests = original_requests
        gss.HasNetwork = original_has_network
        gss.GetEasternDate = original_get_date
        gss.GetUtcOffsetMinutes = original_get_offset
        gss._get_dst_active = original_get_dst


if __name__ == "__main__":
    run_tests()
