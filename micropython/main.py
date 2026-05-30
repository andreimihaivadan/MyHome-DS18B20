# MicroPython Pico W - Sensors + WiFi
# Architecture: MAIN thread = web server + sensors (90s) + buttons + logic
# Display THREAD = OLED-only; wakes only when screen_dirty is set

import _thread, machine, network, time, json, os, ubinascii, usocket as socket
from machine import I2C, Pin, WDT
import onewire, ds18x20
import ubinascii

# Safety delay to allow breaking into REPL
time.sleep(3)

CONFIG_PATH = "config.json"
FONT_H = 8

# ---------------- Watchdog ----------------
wdt = WDT(timeout=8000)  # 8s watchdog
def feed_wdt():
    try:
        wdt.feed()
    except:
        pass


# ------------ Locks for concurrency safety ------------
sensor_lock = _thread.allocate_lock()


# --------------- Helpers ---------------

# ---------------- Hardware ----------------
# OneWire
ow_pin = None
ds = None
ds_roms = []

def init_onewire(pin_num):
    global ow_pin, ds, ds_roms
    try:
        ow_pin = Pin(pin_num)
        ds = ds18x20.DS18X20(onewire.OneWire(ow_pin))
        ds_roms = ds.scan()
        print(f"OneWire on pin {pin_num}. Found DS18x20 devices: {ds_roms}")
    except Exception as e:
        print(f"OneWire init/scan error on pin {pin_num}:", e)
        ds = None
        ds_roms = []




DEFAULT_CONFIG = {
    "wifi_ssid": "",
    "wifi_password": "",
    "ow_pin": 4,
    "sensors_config": {}
}

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            f.write(json.dumps(cfg))
            f.flush()
            try: os.sync()
            except: pass
    except Exception as e:
        print("Save error:", e)

def load_config():
    if CONFIG_PATH not in os.listdir():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        data = json.load(open(CONFIG_PATH))
        if "wifi_ssid" not in data: data["wifi_ssid"] = ""
        if "wifi_password" not in data: data["wifi_password"] = ""
        if "ow_pin" not in data: data["ow_pin"] = 10
        if "sensors_config" not in data: data["sensors_config"] = {}
        return data
    except Exception as e:
        print("Config load error:", e)
        return DEFAULT_CONFIG

cfg = load_config()

init_onewire(cfg.get("ow_pin", 4))

# ---------- WiFi / AP ----------
wlan = network.WLAN(network.STA_IF)
ap = network.WLAN(network.AP_IF)
AP_SSID = "T_NEST_" + ubinascii.hexlify(machine.unique_id()).decode()[:6]
AP_PASSWORD = "password123"
wifi_ip = "0.0.0.0"

if cfg.get("wifi_ssid") and cfg.get("wifi_password"):
    wlan.active(True)
    wlan.connect(cfg["wifi_ssid"], cfg["wifi_password"])
    for _ in range(20):
        if wlan.isconnected():
            print("Connected to ", cfg.get("wifi_ssid"))
            break
        time.sleep(0.5)
        feed_wdt()
if wlan.isconnected():
    wifi_ip = wlan.ifconfig()[0]
    wifi_mode = ("Wi-Fi", wifi_ip)
else:
    wlan.active(False)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASSWORD)
    wifi_ip = ap.ifconfig()[0]
    wifi_mode = ("AP", wifi_ip)

# ---------------- Sensor storage ----------------
sensor_data = {"ds_temps": []}
last_sensor_read = 0

# ------------- UI state -------------
selected = 0
screen_mode = "main"   # 'main', 'menu', 'set_temp', 'sensors', 'netinfo', 'about'
edit_start_time = 0
menu_index = 0
star_press_start = 0
star_long_handled = False
view_start_time = 0  # for temporary screens like sensors/about
menu_start_time = 0  # for menu timeout
FOOTER_TEXT = ""
scroll_pos = 0
scroll_delay = 0
SCROLL_SPEED = 0.15

# helper to mark screen dirty
def mark_screen_dirty():
    global screen_dirty
    with screen_lock:
        screen_dirty = True

# helper for stable button reading (filters noise/OneWire pulses)
def is_pressed_stable(btn):
    if btn.value() == 0:
        time.sleep(0.01)
        if btn.value() == 0:
            time.sleep(0.01)
            if btn.value() == 0:
                return True
    return False

# button state tracking for edge detection
btn_states = {"up": False, "down": False, "hash": False}

# ------------- Drawing functions (OLED-only, used by display thread) -------------

def update_footer():
    global FOOTER_TEXT, scroll_pos, scroll_delay
    FOOTER_TEXT = f"{wifi_mode[0]}: {wifi_mode[1]}"
    if len(FOOTER_TEXT) * 6 <= 128:
        scroll_pos = 0
        return
    scroll_delay += 0.05
    if scroll_delay >= SCROLL_SPEED:
        scroll_delay = 0
        scroll_pos = (scroll_pos + 1) % (len(FOOTER_TEXT) * 6 + 20)

# minimal snapshot to avoid unnecessary drawing logic on display thread
_last_draw_snapshot = {"selected": None, "prev": None, "next": None, "mode": None, "menu_idx": None, "footer": None}




# display thread: only manipulates OLED and i2c1


# ------------- Web server & main logic (runs on MAIN thread) -------------

def send_headers(conn, ct="text/html"):
    try:
        conn.send(b"HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: *\r\nContent-Type: %s\r\n\r\n" % ct.encode())
    except Exception:
        pass

def serve_file(conn, path):
    try:
        with open(path, "rb") as f:
            send_headers(conn, "text/css" if path.endswith(".css") else "text/html")
            while True:
                try:
                    data = f.read(512)
                    if not data: break
                    conn.send(data)
                except Exception:
                    break
    except OSError:
        try:
            conn.send(b"HTTP/1.1 404 Not Found\r\n\r\n")
        except Exception:
            pass

# open listening socket (non-blocking accept by setting timeout)
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 80))
s.listen(1)
s.settimeout(0.6)
print("Web server socket ready on port 80; entering main loop. IP:", wifi_ip)

DEBOUNCE_MS = 150
last_press = time.ticks_ms()

# sensor read interval
SENSOR_INTERVAL = cfg.get("temp_check_interval", 30)

# helper: read sensors
def read_sensors():
    global sensor_data
    ds_list = []
    if ds is None:
        return
    try:
        ds.convert_temp()
        time.sleep(0.75)
        sensors_cfg = cfg.get("sensors_config", {})
        for rom in ds_roms:
            t = ds.read_temp(rom)
            if t is not None:
                try:
                    rid = ubinascii.hexlify(rom).decode()
                    scope = sensors_cfg.get(rid, "")
                    ds_list.append((rid, t, scope))
                    print(f"Sensor {rid} ({scope}): {t} C")
                except:
                    ds_list.append(("Unknown", t, ""))
    except Exception as e:
        print("OneWire read error:", e)

    with sensor_lock:
        sensor_data["ds_temps"] = ds_list
    print("Sensors data: ", sensor_data)

# main loop: handles web connections, buttons, sensors, and logic
last_sensor_read = time.time() - (SENSOR_INTERVAL + 1)

try:
    while True:
        # 1) accept incoming connection (non-blocking due to timeout)
        conn = None
        try:
            try:
                conn, addr = s.accept()
            except OSError:
                conn = None
            if conn:
                try:
                    conn.settimeout(1.0)
                except Exception:
                    pass
                try:
                    req = conn.recv(4096).decode()
                except Exception:
                    try: conn.close()
                    except: pass
                    conn = None
                if conn and req:
                    path = req.split(" ")[1].split("?")[0]
                    method = req.split(" ")[0]
                    if path == "/":
                        serve_file(conn, "index.html")
                    elif path == "/config.html":
                        serve_file(conn, "config.html")
                    elif path == "/main.css":
                        serve_file(conn, "main.css")
                    elif path == "/api/state":
                        with sensor_lock:
                            sd = sensor_data.copy()


                        payload = {
                            "type": "sensor_node",



                            "ip": wifi_ip,
                            "udid": ubinascii.hexlify(machine.unique_id()).decode(),
                            "ssid": cfg.get("wifi_ssid", ""),
                            "mode": wifi_mode[0],
                            "sensors": sd,
}
                        if wlan.isconnected():
                            payload["rssi"] = wlan.status("rssi")
                        send_headers(conn, "application/json")
                        try: conn.send(json.dumps(payload).encode())
                        except: pass
                    elif path == "/api/wifi" and method == "POST":
                        try:
                            payload = req.split("\r\n\r\n", 1)[1]
                            data = json.loads(payload)
                            ssid = data.get("ssid", "").strip()
                            pwd = data.get("password", "").strip()
                            cfg["wifi_ssid"] = ssid
                            cfg["wifi_password"] = pwd
                            save_config(cfg)
                            send_headers(conn, "application/json")
                            try: conn.send(b'{"status":"saved"}')
                            except: pass
                        except Exception as e:
                            print("WiFi save error:", e)
                            try: conn.send(b"HTTP/1.1 500\r\n\r\n")
                            except: pass
                    elif path == "/api/sensors/config" and method == "POST":
                        try:
                            payload = req.split("\r\n\r\n", 1)[1]
                            data = json.loads(payload)

                            changed_pin = False
                            if "ow_pin" in data:
                                try:
                                    new_pin = int(data["ow_pin"])
                                    if new_pin != cfg.get("ow_pin"):
                                        cfg["ow_pin"] = new_pin
                                        changed_pin = True
                                except: pass

                            if "sensors_config" in data:
                                cfg["sensors_config"] = data["sensors_config"]

                            save_config(cfg)

                            if changed_pin:
                                init_onewire(cfg["ow_pin"])

                            send_headers(conn, "application/json")
                            try: conn.send(b'{"status":"ok"}')
                            except: pass
                        except Exception as e:
                            print("Sensors config save error:", e)
                            try: conn.send(b"HTTP/1.1 500\r\n\r\n")
                            except: pass
                    elif path == "/api/reboot" and method == "POST":
                        send_headers(conn, "application/json")
                        try: conn.send(b'{"status":"rebooting"}')
                        except: pass
                        time.sleep(1)
                        machine.reset()
                    elif path == "/api/factory_reset" and method == "POST":
                        try: os.remove(CONFIG_PATH)
                        except: pass
                        send_headers(conn, "application/json")
                        try: conn.send(b'{"status":"reset"}')
                        except: pass
                        time.sleep(1)
                        machine.reset()
                    else:
                        try: conn.send(b"HTTP/1.1 404 Not Found\r\n\r\n")
                        except: pass
                try: conn.close()
                except: pass
        except Exception as e:
            print("Main accept/serve error:", e)
            try:
                if conn: conn.close()
            except: pass

        # 2) sensors logic
        now_sec = time.time()
        check_int = 30
        if now_sec - last_sensor_read >= check_int:
            last_sensor_read = now_sec
            read_sensors()

        # always feed watchdog in main loop
        try: wdt.feed()
        except: pass
        # small sleep to avoid 100% CPU
        time.sleep(0.05)

except KeyboardInterrupt:
    print("Main loop stopped by KeyboardInterrupt")
