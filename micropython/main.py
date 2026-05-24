# MicroPython Pico W - 16 Relays + OLED + WiFi + System Menu
# Architecture: MAIN thread = web server + sensors (90s) + buttons + logic
# Display THREAD = OLED-only; wakes only when screen_dirty is set

import _thread, machine, network, time, json, os, ubinascii, usocket as socket
from machine import I2C, Pin, WDT
from ssd1306 import SSD1306_I2C
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

# --------------- I2C busses & display ---------------
# i2c1: OLED (ESP32-C3: SDA=8, SCL=9)
i2c1 = I2C(0, scl=Pin(9), sda=Pin(8), freq=400_000)
oled = SSD1306_I2C(128, 64, i2c1)

# ------------ Locks for concurrency safety ------------
i2c1_lock = _thread.allocate_lock()   # for OLED (i2c1)
relay_state_lock = _thread.allocate_lock()
sensor_lock = _thread.allocate_lock()
screen_lock = _thread.allocate_lock()  # protects screen_dirty and snapshot

# event-driven flag
screen_dirty = True  # initial draw

# --------------- Helpers ---------------
def oled_print(line, text, invert=False, align="left"):
    y = line * FONT_H
    bg = 1 if invert else 0
    fg = 0 if invert else 1
    oled.fill_rect(0, y, 128, FONT_H, bg)
    text = str(text)[:21]
    if align == "center":
        x = (128 - len(text) * 6) // 2
    else:
        x = 2
    oled.text(text, x, y, fg)

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

# Relays (Pin 2 and 3)
relay1 = Pin(2, Pin.OUT, value=0)
relay2 = Pin(3, Pin.OUT, value=0)
# LEDs (Pin 12 and 13) indicating relay status
led1 = Pin(12, Pin.OUT, value=0)
led2 = Pin(13, Pin.OUT, value=0)
print(f"Relays initialized. Relay1: {relay1.value()}, Relay2: {relay2.value()}")

# ---------------- Config / runtime state ----------------
relay_state = {}
relay_purposes = {}

# safer set_relay replacement for GPIO
def set_relay(ch, on):
    # ch is 0-based index (0 for relay_1, 1 for relay_2)
    try:
        key = f"relay_{ch+1}"
        trigger = "high"
        if key in cfg["relays"]:
            rel_data = cfg["relays"][key]
            if isinstance(rel_data, dict) and "trigger" in rel_data:
                trigger = rel_data["trigger"]

        val = 1 if on else 0
        if trigger == "low":
            val = 0 if on else 1

        print("set_relay ch={} on={} trigger={} -> val={}".format(ch, on, trigger, val))
        if ch == 0:
            relay1.value(val)
            led1.value(1 if on else 0)
        elif ch == 1:
            relay2.value(val)
            led2.value(1 if on else 0)
    except Exception as e:
        print("set_relay error:", e)

# ------------- Buttons -------------
btn_up = Pin(4, Pin.IN, Pin.PULL_UP)
btn_down = Pin(5, Pin.IN, Pin.PULL_UP)
btn_hash = Pin(7, Pin.IN, Pin.PULL_UP)
btn_star = Pin(6, Pin.IN, Pin.PULL_UP)
def pressed(b): return not b.value()

DEFAULT_CONFIG = {
    "relays": {f"relay_{i}": {"state": False, "purpose": ""} for i in range(1, 17)},
    "max_relays": 2,
    "wifi_ssid": "",
    "wifi_password": "",
    "ow_pin": 10,
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
        if "relays" not in data: data["relays"] = {}
        for i in range(1, 17):
            k = f"relay_{i}"
            if k not in data["relays"]:
                data["relays"][k] = {"state": False, "purpose": "", "trigger": "high"}
            else:
                if isinstance(data["relays"][k], bool):
                    data["relays"][k] = {"state": data["relays"][k], "purpose": "", "trigger": "high"}
                elif "state" not in data["relays"][k]:
                    data["relays"][k]["state"] = False
                if "purpose" not in data["relays"][k]:
                    data["relays"][k]["purpose"] = ""
                if "trigger" not in data["relays"][k]:
                    data["relays"][k]["trigger"] = "high"
        if "max_relays" not in data: data["max_relays"] = 2
        if "wifi_ssid" not in data: data["wifi_ssid"] = ""
        if "wifi_password" not in data: data["wifi_password"] = ""
        if "ow_pin" not in data: data["ow_pin"] = 10
        if "sensors_config" not in data: data["sensors_config"] = {}
        return data
    except Exception as e:
        print("Config load error:", e)
        return DEFAULT_CONFIG

cfg = load_config()
relay_state = {k: v["state"] for k, v in cfg["relays"].items()}
relay_purposes = {k: v["purpose"] for k, v in cfg["relays"].items()}
for i in range(16):
    set_relay(i, relay_state.get(f"relay_{i+1}", False))

init_onewire(cfg.get("ow_pin", 10))

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
menu_items = ["Sensors Data", "Network Info", "About", "Reboot", "Reset Wi-Fi", "Reset relays", "Factory reset", "Exit"]
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


def draw_main_screen():
    oled.fill(0)
    oled_print(0, "T-NEST Controller", align="center")
    oled_print(2, wifi_ip, align="center")
    oled_print(4, "Relays: " + str(cfg.get("max_relays", 2)))
    oled.show()

def draw_menu_screen():
    oled.fill(0)
    oled_print(0, "Menu", invert=True)
    for i, item in enumerate(menu_items):
        if i == menu_index:
            oled_print(i + 1, "> " + item)
        else:
            oled_print(i + 1, "  " + item)
    oled.show()

def draw_sensors_screen():
    oled.fill(0)
    oled_print(0, "Sensors Data", invert=True)
    with sensor_lock:
        temps = sensor_data.get("ds_temps", [])

    if not temps:
        oled_print(2, "No sensors found", align="center")
    else:
        # Display up to 6 sensors
        for i, sensor in enumerate(temps[:6]):
            rid = sensor[0]
            t = sensor[1]
            scope = sensor[2] if len(sensor) > 2 else ""
            label = scope[:6] if scope else (rid[-6:] if rid and len(rid) >= 6 else str(i+1))
            oled_print(i + 1, "{}: {:.1f} C".format(label, t))

def draw_network_info():
    oled.fill(0)
    oled_print(0, "Network information", invert=True)
    oled_print(2, "Mode: %s" % wifi_mode[0])
    oled_print(3, "IP: %s" % wifi_ip)
    oled_print(4, "SSID:")
    oled_print(5, "%s" % (cfg.get("wifi_ssid") or "None"))

def draw_about():
    uid = ubinascii.hexlify(machine.unique_id()).decode()
    oled.fill(0)
    oled_print(2, "UDID:", align="center")
    oled_print(3, uid[:16], align="center")
    oled_print(4, uid[16:], align="center")

# display thread: only manipulates OLED and i2c1

def display_thread():
    global screen_dirty, _last_draw_snapshot, oled, i2c1
    while True:
        with screen_lock:
            dirty = screen_dirty
            # snapshot relevant state to draw without holding other locks
            mode = screen_mode
            sel = selected
            midx = menu_index
            footer = FOOTER_TEXT
            # sensors snapshot
            with sensor_lock:
                sensors_snap = sensor_data.copy()
            # relays snapshot
            with relay_state_lock:
                rel_snapshot = {k: relay_state[k] for k in relay_state}
            # clear dirty to avoid redraw storms
            if dirty:
                screen_dirty = False
        if not dirty:
            # sleep briefly — display thread is event-driven but polls
            time.sleep(0.12)
            try: wdt.feed()
            except: pass
            continue

        # perform actual draw under i2c1_lock
        try:
            i2c1_lock.acquire()
            try:
                if mode == "main":
                    draw_main_screen()

                elif mode == "menu":
                    # decide sub-screens (Sensors Data, Network Info, About) - but main 'menu' shows menu list
                    draw_menu_screen()
                elif mode == "sensors":
                    draw_sensors_screen()
                elif mode == "netinfo":
                    draw_network_info()
                elif mode == "about":
                    draw_about()
                else:
                    draw_main_screen()
                oled.show()
            finally:
                i2c1_lock.release()
        except Exception as e:
            # try re-init on error
            print("OLED show error:", e)
            try:
                i2c1 = I2C(0, scl=Pin(9), sda=Pin(8), freq=400_000)
                oled = SSD1306_I2C(128, 64, i2c1)
            except Exception as e2:
                print("Failed to re-init OLED:", e2)
        # feed watchdog after drawing
        try: wdt.feed()
        except: pass

# start display thread
_thread.start_new_thread(display_thread, ())

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
SENSOR_INTERVAL = 30

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

# mark screen dirty helper that avoids grabbing many locks in caller
def mark_dirty():
    global screen_dirty
    with screen_lock:
        screen_dirty = True

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
                        with relay_state_lock:
                            rel_copy = relay_state.copy()
                            pur_copy = relay_purposes.copy()
                            trig_copy = {k: cfg["relays"][k].get("trigger", "high") for k in cfg["relays"]}
                        with sensor_lock:
                            sd = sensor_data.copy()

                        therm_on = rel_copy.get("relay_1", False)
                        payload = {
                            "type": "thermostat",
                            "relays": rel_copy,
                            "purposes": pur_copy,
                            "triggers": trig_copy,
                            "ip": wifi_ip,
                            "udid": ubinascii.hexlify(machine.unique_id()).decode(),
                            "ssid": cfg.get("wifi_ssid", ""),
                            "mode": wifi_mode[0],
                            "sensors": sd,
                                                        "temp_check_interval": cfg.get("temp_check_interval", 30),
                            "max_relays": cfg.get("max_relays", 2),
                                                        }
                        send_headers(conn, "application/json")
                        try: conn.send(json.dumps(payload).encode())
                        except: pass
                    elif path == "/api/wifi" and method == "POST":
                        try:
                            payload = req.split("\r\n\r\n", 1)[1]
                            data = json.loads(payload)
                            ssid = data.get("ssid", "").strip()
                            pwd = data.get("password", "").strip()
                            with relay_state_lock:
                                cfg["wifi_ssid"] = ssid
                                cfg["wifi_password"] = pwd
                            save_config(cfg)
                            send_headers(conn, "application/json")
                            try: conn.send(b'{"status":"saved"}')
                            except: pass
                            mark_dirty()
                        except Exception as e:
                            print("WiFi save error:", e)
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
                    elif path.startswith("/api/relays/relay_") and method == "POST":
                        parts = path.split("/")
                        if len(parts) >= 5 and parts[3] in relay_state and parts[4] in ("on", "off"):
                            key = parts[3]
                            state = parts[4] == "on"
                            relay_idx = int(key.split("_")[1]) - 1
                            with relay_state_lock:
                                relay_state[key] = state
                                cfg["relays"][key]["state"] = state
                                # hardware write protected by i2c0_lock inside set_relay
                                set_relay(relay_idx, state)
                            save_config(cfg)
                            mark_dirty()
                            send_headers(conn, "application/json")
                            try: conn.send(json.dumps({"relay": key, "state": state}).encode())
                            except: pass
                        else:
                            try: conn.send(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                            except: pass
                    elif path == "/api/reset_relays" and method == "POST":
                        with relay_state_lock:
                            for k in relay_state:
                                relay_state[k] = False
                                cfg["relays"][k]["state"] = False
                                relay_idx = int(k.split("_")[1]) - 1
                                set_relay(relay_idx, False)
                        save_config(cfg)
                        mark_dirty()
                        send_headers(conn, "application/json")
                        try: conn.send(b'{"status":"all_off"}')
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

                            mark_dirty()
                            send_headers(conn, "application/json")
                            try: conn.send(b'{"status":"ok"}')
                            except: pass
                        except Exception as e:
                            print("Sensors config save error:", e)
                            try: conn.send(b"HTTP/1.1 500\r\n\r\n")
                            except: pass
                    elif path == "/api/relays/config" and method == "POST":
                        try:
                            payload = req.split("\r\n\r\n", 1)[1]
                            data = json.loads(payload)
                            with relay_state_lock:
                                for k, v in data.items():
                                    if k in cfg["relays"]:
                                        if "purpose" in v:
                                            cfg["relays"][k]["purpose"] = v["purpose"].strip()
                                            relay_purposes[k] = v["purpose"].strip()
                                        if "trigger" in v:
                                            cfg["relays"][k]["trigger"] = v["trigger"]
                                            set_relay(int(k.split("_")[1])-1, relay_state[k])
                            save_config(cfg)
                            mark_dirty()
                            send_headers(conn, "application/json")
                            try: conn.send(b'{"status":"ok"}')
                            except: pass
                        except Exception as e:
                            print("Relay config save error:", e)
                            try: conn.send(b"HTTP/1.1 500\r\n\r\n")
                            except: pass
                    elif path == "/api/purposes" and method == "POST":
                        try:
                            payload = req.split("\r\n\r\n", 1)[1]
                            new_purposes = json.loads(payload)
                            with relay_state_lock:
                                for k, purpose in new_purposes.items():
                                    if k in cfg["relays"]:
                                        cfg["relays"][k]["purpose"] = purpose.strip()
                                        relay_purposes[k] = purpose.strip()
                            save_config(cfg)
                            mark_dirty()
                            send_headers(conn, "application/json")
                            try: conn.send(b'{"status":"ok"}')
                            except: pass
                        except Exception as e:
                            print("Purpose save error:", e)
                            try: conn.send(b"HTTP/1.1 500\r\n\r\n")
                            except: pass
                    elif path == "/api/manual_relay" and method == "POST":
                        try:
                            payload = req.split("\r\n\r\n", 1)[1]
                            data = json.loads(payload)
                            # Expect {"relay": 1, "state": true}
                            r_id = data.get("relay")
                            r_state = data.get("state")
                            if r_id in (1, 2) and isinstance(r_state, bool):
                                k = f"relay_{r_id}"
                                with relay_state_lock:
                                    relay_state[k] = r_state
                                    cfg["relays"][k]["state"] = r_state
                                    set_relay(r_id - 1, r_state)
                                save_config(cfg)
                                mark_dirty()
                                send_headers(conn, "application/json")
                                try: conn.send(b'{"status":"ok"}')
                                except: pass
                            else:
                                try: conn.send(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                                except: pass
                        except Exception as e:
                            print("Manual relay error:", e)
                            try: conn.send(b"HTTP/1.1 500\r\n\r\n")
                            except: pass
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

        # 2) sensors & thermostat logic
        now_sec = time.time()
        check_int = 30
        if now_sec - last_sensor_read >= check_int:
            last_sensor_read = now_sec
            read_sensors()

            mark_dirty()

        # 3) buttons handling (stable edge detection) - main thread

        # UP Button
        up_now = is_pressed_stable(btn_up)
        if up_now and not btn_states["up"]:
            btn_states["up"] = True
            if screen_mode == "main":
                screen_mode = "menu"
                menu_index = 0
                menu_start_time = time.time()
            elif screen_mode == "menu":
                menu_index = (menu_index - 1) % len(menu_items)
                menu_start_time = time.time()
            mark_dirty()
        elif not up_now:
            btn_states["up"] = False

        # DOWN Button
        down_now = is_pressed_stable(btn_down)
        if down_now and not btn_states["down"]:
            btn_states["down"] = True
            if screen_mode == "main":
                screen_mode = "menu"
                menu_index = 0
                menu_start_time = time.time()

        elif not down_now:
            btn_states["down"] = False

        # HASH Button
        hash_now = is_pressed_stable(btn_hash)
        if hash_now and not btn_states["hash"]:
            btn_states["hash"] = True
            if screen_mode == "main":
                screen_mode = "menu"
                menu_index = 0
                menu_start_time = time.time()

        elif not hash_now:
            btn_states["hash"] = False

        # btn_star independent handling for long press
        if pressed(btn_star):
            if star_press_start == 0:
                star_press_start = time.ticks_ms()
            else:
                if time.ticks_diff(time.ticks_ms(), star_press_start) > 2000 and not star_long_handled:
                    # Long Press Action (> 2s)
                    star_long_handled = True
                    if screen_mode == "main":
                        # Removed thermostat mode toggle
                        pass
        else:
            # Button released
            if star_press_start > 0:
                if not star_long_handled:
                    # Short press action
                    if time.ticks_diff(time.ticks_ms(), last_press) > DEBOUNCE_MS: # Apply debounce to short press too
                        last_press = time.ticks_ms()
                        if screen_mode == "main":
                            # Added fallback navigation (same as Hash)
                            screen_mode = "menu"
                            menu_index = 0
                            menu_start_time = time.time()
                            mark_dirty()

                        elif screen_mode in ("sensors", "netinfo", "about"):
                            screen_mode = "menu"
                            menu_start_time = time.time()
                            mark_dirty()
                        else:
                            # Menu handling
                            choice = menu_items[menu_index]
                            if choice == "Sensors Data":
                                screen_mode = "sensors"
                                view_start_time = time.time()
                                mark_dirty()
                            elif choice == "Network Info":
                                screen_mode = "netinfo"
                                view_start_time = time.time()
                                mark_dirty()
                            elif choice == "About":
                                screen_mode = "about"
                                view_start_time = time.time()
                                mark_dirty()
                            elif choice == "Reboot":
                                screen_mode = "menu"
                                mark_dirty()
                                time.sleep(0.3)
                                machine.reset()
                            elif choice == "Reset Wi-Fi":
                                with relay_state_lock:
                                    cfg["wifi_ssid"] = cfg["wifi_password"] = ""
                                save_config(cfg)
                                mark_dirty()
                            elif choice == "Reset relays":
                                with relay_state_lock:
                                    for k in relay_state:
                                        relay_state[k] = False
                                        cfg["relays"][k]["state"] = False
                                        relay_idx = int(k.split("_")[1]) - 1
                                        set_relay(relay_idx, False)
                                save_config(cfg)
                                mark_dirty()
                            elif choice == "Factory reset":
                                try: os.remove(CONFIG_PATH)
                                except: pass
                                time.sleep(0.3)
                                machine.reset()
                            elif choice == "Exit":
                                screen_mode = "main"
                                mark_dirty()

                star_press_start = 0
                star_long_handled = False

        # Auto-revert from temporary screens
        if screen_mode == "menu" and (time.time() - menu_start_time > 10):
            screen_mode = "main"
            mark_dirty()
        elif screen_mode == "sensors" and (time.time() - view_start_time > 10):
            screen_mode = "menu"
            menu_start_time = time.time()
            mark_dirty()
        elif screen_mode == "netinfo" and (time.time() - view_start_time > 6):
            screen_mode = "menu"
            menu_start_time = time.time()
            mark_dirty()
        elif screen_mode == "about" and (time.time() - view_start_time > 4):
            screen_mode = "menu"
            menu_start_time = time.time()
            mark_dirty()

        # always feed watchdog in main loop
        try: wdt.feed()
        except: pass
        # small sleep to avoid 100% CPU
        time.sleep(0.05)

except KeyboardInterrupt:
    print("Main loop stopped by KeyboardInterrupt")
