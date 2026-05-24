#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <LittleFS.h>
#include "html_content.h"

ESP8266WebServer server(80);

// Forward declarations
void handleApiRelaysConfig();
void handleApiRelayToggle();

// Config
struct RelayConfig {
  bool state;
  String purpose;
  String trigger;
};

RelayConfig relays[16];
int max_relays = 2;
String wifi_ssid = "";
String wifi_password = "";
int ow_pin = 10;
DynamicJsonDocument sensors_config(1024);

OneWire* oneWire = nullptr;
DallasTemperature* sensors = nullptr;

const char* config_file = "/config.json";

void loadConfig() {
  if (LittleFS.exists(config_file)) {
    File file = LittleFS.open(config_file, "r");
    if (file) {
      DynamicJsonDocument doc(2048);
      DeserializationError error = deserializeJson(doc, file);
      if (!error) {
        wifi_ssid = doc["wifi_ssid"].as<String>();
        wifi_password = doc["wifi_password"].as<String>();
        max_relays = doc["max_relays"] | 2;
        ow_pin = doc["ow_pin"] | 10;

        if (doc.containsKey("sensors_config")) {
          sensors_config = doc["sensors_config"];
        } else {
          sensors_config.to<JsonObject>();
        }

        JsonObject rels = doc["relays"].as<JsonObject>();
        for (JsonPair kv : rels) {
          String key = kv.key().c_str();
          if (key.startsWith("relay_")) {
            int idx = key.substring(6).toInt() - 1;
            if (idx >= 0 && idx < 16) {
              JsonObject rObj = kv.value().as<JsonObject>();
              relays[idx].state = rObj["state"] | false;
              relays[idx].purpose = rObj["purpose"].as<String>();
              relays[idx].trigger = rObj["trigger"] | "high";
            }
          }
        }
      }
      file.close();
    }
  } else {
    // Default setup
    for (int i=0; i<16; i++) {
      relays[i].state = false;
      relays[i].purpose = "";
      relays[i].trigger = "high";
    }
    sensors_config.to<JsonObject>();
  }
}

void saveConfig() {
  DynamicJsonDocument doc(2048);
  doc["wifi_ssid"] = wifi_ssid;
  doc["wifi_password"] = wifi_password;
  doc["max_relays"] = max_relays;
  doc["ow_pin"] = ow_pin;
  doc["sensors_config"] = sensors_config;

  JsonObject rels = doc.createNestedObject("relays");
  for (int i=0; i<16; i++) {
    String key = "relay_" + String(i + 1);
    JsonObject rObj = rels.createNestedObject(key);
    rObj["state"] = relays[i].state;
    rObj["purpose"] = relays[i].purpose;
    rObj["trigger"] = relays[i].trigger;
  }

  File file = LittleFS.open(config_file, "w");
  if (file) {
    serializeJson(doc, file);
    file.close();
  }
}

void setupOnewire() {
  if (sensors != nullptr) {
    delete sensors;
    delete oneWire;
  }
  oneWire = new OneWire(ow_pin);
  sensors = new DallasTemperature(oneWire);
  sensors->begin();
}

void handleRoot() {
  server.send(200, "text/html", index_html);
}

void handleConfigHtml() {
  server.send(200, "text/html", config_html);
}

void handleCss() {
  server.send(200, "text/css", main_css);
}

void handleApiState() {
  DynamicJsonDocument doc(4096);
  doc["type"] = "controller";
  doc["ip"] = WiFi.localIP().toString();
  doc["udid"] = String(ESP.getChipId(), HEX);
  doc["ssid"] = wifi_ssid;
  doc["mode"] = WiFi.getMode() == WIFI_AP ? "AP" : "Wi-Fi";
  doc["max_relays"] = max_relays;
  doc["ow_pin"] = ow_pin;
  doc["sensors_config"] = sensors_config;

  JsonObject rel_copy = doc.createNestedObject("relays");
  JsonObject pur_copy = doc.createNestedObject("purposes");
  JsonObject trig_copy = doc.createNestedObject("triggers");

  for (int i=0; i<16; i++) {
    String key = "relay_" + String(i + 1);
    rel_copy[key] = relays[i].state;
    pur_copy[key] = relays[i].purpose;
    trig_copy[key] = relays[i].trigger;
  }

  JsonObject sd = doc.createNestedObject("sensors");
  JsonArray ds_temps = sd.createNestedArray("ds_temps");

  if (sensors != nullptr) {
    int count = sensors->getDeviceCount();
    for (int i=0; i<count; i++) {
      DeviceAddress addr;
      if (sensors->getAddress(addr, i)) {
        String rid = "";
        for (uint8_t j=0; j<8; j++) {
          if (addr[j] < 16) rid += "0";
          rid += String(addr[j], HEX);
        }
        float t = sensors->getTempC(addr);

        JsonArray sensorArr = ds_temps.createNestedArray();
        sensorArr.add(rid);
        sensorArr.add(t);
        if (sensors_config.containsKey(rid)) {
          sensorArr.add(sensors_config[rid].as<String>());
        } else {
          sensorArr.add("");
        }
      }
    }
  }

  String response;
  serializeJson(doc, response);
  server.send(200, "application/json", response);
}

void handleApiSensorsConfig() {
  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method Not Allowed");
    return;
  }

  DynamicJsonDocument doc(1024);
  DeserializationError error = deserializeJson(doc, server.arg("plain"));
  if (error) {
    server.send(400, "text/plain", "Bad Request");
    return;
  }

  bool pinChanged = false;
  if (doc.containsKey("ow_pin")) {
    int new_pin = doc["ow_pin"];
    if (new_pin != ow_pin) {
      ow_pin = new_pin;
      pinChanged = true;
    }
  }

  if (doc.containsKey("sensors_config")) {
    sensors_config = doc["sensors_config"];
  }

  saveConfig();

  if (pinChanged) {
    setupOnewire();
  }

  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

// ... Additional API handlers for relays, WiFi, reboot, etc ...

void registerExtraRoutes() {
  server.on("/api/relays/config", handleApiRelaysConfig);
  // Using generic handler for relay toggle
  server.onNotFound([]() {
    if (server.uri().startsWith("/api/relays/")) {
      handleApiRelayToggle();
    } else {
      server.send(404, "text/plain", "Not Found");
    }
  });
}


void handleApiWifi() {
  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method Not Allowed");
    return;
  }
  DynamicJsonDocument doc(1024);
  DeserializationError error = deserializeJson(doc, server.arg("plain"));
  if (!error) {
    wifi_ssid = doc["ssid"].as<String>();
    wifi_password = doc["password"].as<String>();
    saveConfig();
    server.send(200, "application/json", "{\"status\":\"ok\"}");
  } else {
    server.send(400, "text/plain", "Bad Request");
  }
}

void handleApiReboot() {
  server.send(200, "application/json", "{\"status\":\"ok\"}");
  delay(1000);
  ESP.restart();
}

void handleApiResetRelays() {
  for (int i = 0; i < 16; i++) {
    relays[i].state = false;
    setRelay(i, false);
  }
  saveConfig();
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleApiFactoryReset() {
  LittleFS.remove(config_file);
  server.send(200, "application/json", "{\"status\":\"ok\"}");
  delay(1000);
  ESP.restart();
}

void setup() {
  Serial.begin(115200);
  LittleFS.begin();
  loadConfig();
  setupOnewire();

  if (wifi_ssid != "") {
    WiFi.mode(WIFI_STA);
    WiFi.begin(wifi_ssid.c_str(), wifi_password.c_str());
    int retries = 0;
    while (WiFi.status() != WL_CONNECTED && retries < 20) {
      delay(500);
      Serial.print(".");
      retries++;
    }
  }

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_AP);
    String apName = "T_NEST_" + String(ESP.getChipId(), HEX);
    WiFi.softAP(apName.c_str(), "password123");
    Serial.println("Started AP: " + apName);
  }

  server.on("/", handleRoot);
  server.on("/index.html", handleRoot);
  server.on("/config.html", handleConfigHtml);
  server.on("/main.css", handleCss);
  server.on("/api/state", handleApiState);
  server.on("/api/sensors/config", handleApiSensorsConfig);
  server.on("/api/wifi", handleApiWifi);
  server.on("/api/reboot", handleApiReboot);
  server.on("/api/reset_relays", handleApiResetRelays);
  server.on("/api/factory_reset", handleApiFactoryReset);
  registerExtraRoutes();


  for (int i=0; i<16; i++) {
    if (relay_pins[i] != -1) {
      pinMode(relay_pins[i], OUTPUT);
      setRelay(i, relays[i].state);
    }
  }

  server.begin();
}

unsigned long lastTempRequest = 0;
void loop() {
  server.handleClient();

  if (sensors != nullptr && millis() - lastTempRequest > 30000) {
    sensors->requestTemperatures();
    lastTempRequest = millis();
  }
}


// Default relay pins (modify according to actual hardware)
int relay_pins[16] = {5, 4, 14, 12, 13, 15, 16, 2, -1, -1, -1, -1, -1, -1, -1, -1};

void setRelay(int idx, bool state) {
  if (idx < 0 || idx > 15 || relay_pins[idx] == -1) return;
  bool trigger_high = (relays[idx].trigger == "high");
  digitalWrite(relay_pins[idx], state == trigger_high ? HIGH : LOW);
}

void handleApiRelaysConfig() {
  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method Not Allowed");
    return;
  }
  DynamicJsonDocument doc(2048);
  DeserializationError error = deserializeJson(doc, server.arg("plain"));
  if (error) {
    server.send(400, "text/plain", "Bad Request");
    return;
  }

  for (JsonPair kv : doc.as<JsonObject>()) {
    String key = kv.key().c_str();
    if (key.startsWith("relay_")) {
      int idx = key.substring(6).toInt() - 1;
      if (idx >= 0 && idx < 16) {
        JsonObject rObj = kv.value().as<JsonObject>();
        if (rObj.containsKey("purpose")) relays[idx].purpose = rObj["purpose"].as<String>();
        if (rObj.containsKey("trigger")) relays[idx].trigger = rObj["trigger"].as<String>();
      }
    }
  }
  saveConfig();
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleApiRelayToggle() {
  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method Not Allowed");
    return;
  }
  String uri = server.uri(); // /api/relays/relay_1/on
  int p1 = uri.indexOf("/api/relays/");
  if (p1 != -1) {
    String rest = uri.substring(12);
    int p2 = rest.indexOf("/");
    if (p2 != -1) {
      String key = rest.substring(0, p2);
      String action = rest.substring(p2 + 1);

      if (key.startsWith("relay_")) {
        int idx = key.substring(6).toInt() - 1;
        if (idx >= 0 && idx < 16) {
          bool state = (action == "on");
          relays[idx].state = state;
          setRelay(idx, state);
          saveConfig();
          server.send(200, "application/json", "{\"status\":\"ok\"}");
          return;
        }
      }
    }
  }
  server.send(400, "text/plain", "Bad Request");
}
