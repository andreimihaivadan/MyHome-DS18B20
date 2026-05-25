# MicroPython Pico W - 16 Relays + OLED + WiFi + System Menu
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
i2c1_lock = _thread.allocate_lock()   # for OLED (i2c1)
relay_state_lock = _thread.allocate_lock()
sensor_lock = _thread.allocate_lock()
screen_lock = _thread.allocate_lock()  # protects screen_dirty and snapshot

# event-driven flag
screen_dirty = True  # initial draw

# --------------- Helpers ---------------


def draw_menu_screen():
    oled.fill(0)
    oled_print(0, "Menu", invert=True)
    for i, item in enumerate(menu_items):
        if i == menu_index:
            oled_print(i + 1, "> " + item)
        else:
            oled_print(i + 1, "  " + item)
    oled.show()





def draw_about():
    uid = ubinascii.hexlify(machine.unique_id()).decode()
    oled.fill(0)
    oled_print(2, "UDID:", align="center")
    oled_print(3, uid[:16], align="center")
    oled_print(4, uid[16:], align="center")

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


        # always feed watchdog in main loop
        try: wdt.feed()
        except: pass
        # small sleep to avoid 100% CPU
        time.sleep(0.05)

except KeyboardInterrupt:
    print("Main loop stopped by KeyboardInterrupt")
