#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <LittleFS.h>
#include <ArduinoOTA.h>
#include "html_content.h"

ESP8266WebServer server(80);

// Forward declarations / Global variables
String wifi_ssid = "";
String wifi_password = "";
int ow_pin = 4;
JsonDocument sensors_config;

OneWire* oneWire = nullptr;
DallasTemperature* sensors = nullptr;

const char* config_file = "/config.json";

// =============================================================
// Load configuration
// =============================================================
void loadConfig() {
  Serial.println("Loading config from LittleFS...");

  if (LittleFS.exists(config_file)) {
    File file = LittleFS.open(config_file, "r");
    if (file) {
      JsonDocument doc;
      DeserializationError error = deserializeJson(doc, file);
      if (!error) {
        wifi_ssid = doc["wifi_ssid"].as<String>();
        wifi_password = doc["wifi_password"].as<String>();
        ow_pin = doc["ow_pin"] | 4;

        if (doc.containsKey("sensors_config")) {
          sensors_config.set(doc["sensors_config"]);
        } else {
          sensors_config.to<JsonObject>();
        }
        Serial.println("Config loaded successfully");
      } else {
        Serial.println("Failed to parse config.json");
      }
      file.close();
    } else {
      Serial.println("Failed to open config file");
    }
  } else {
    Serial.println("No config file found - using defaults");
    sensors_config.to<JsonObject>();
  }
}

// =============================================================
// Save configuration
// =============================================================
void saveConfig() {
  JsonDocument doc;
  doc["wifi_ssid"] = wifi_ssid;
  doc["wifi_password"] = wifi_password;
  doc["ow_pin"] = ow_pin;
  doc["sensors_config"] = sensors_config;

  File file = LittleFS.open(config_file, "w");
  if (file) {
    serializeJson(doc, file);
    file.close();
    Serial.println("Config saved successfully");
  } else {
    Serial.println("Failed to save config file");
  }
}

// =============================================================
// Setup OneWire + DS18B20
// =============================================================
void setupOnewire() {
  if (sensors != nullptr) {
    delete sensors;
    delete oneWire;
  }


  oneWire = new OneWire(ow_pin);
  sensors = new DallasTemperature(oneWire);
  sensors->begin();
  sensors->setWaitForConversion(false); // Non-blocking to prevent WDT resets
  Serial.printf("OneWire initialized on pin %d\n", ow_pin);
}

// =============================================================
// Web Handlers
// =============================================================
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
  JsonDocument doc;
  doc["type"] = "controller";
  doc["ip"] = WiFi.localIP().toString();
  doc["udid"] = String(ESP.getChipId(), HEX);
  doc["ssid"] = wifi_ssid;
  doc["mode"] = (WiFi.getMode() == WIFI_AP) ? "AP" : "STA";
  if (WiFi.status() == WL_CONNECTED) {
    doc["rssi"] = WiFi.RSSI();
  }
  doc["ow_pin"] = ow_pin;
  doc["sensors_config"] = sensors_config;

  JsonObject sd = doc.createNestedObject("sensors");
  JsonArray ds_temps = sd.createNestedArray("ds_temps");

  if (sensors != nullptr) {
    int count = sensors->getDeviceCount();
    for (int i = 0; i < count; i++) {
      DeviceAddress addr;
      if (sensors->getAddress(addr, i)) {
        String rid = "";
        for (uint8_t j = 0; j < 8; j++) {
          if (addr[j] < 16) rid += "0";
          rid += String(addr[j], HEX);
        }
        float t = sensors->getTempC(addr);

        // === FIXED PART ===
        JsonArray sensorArr = ds_temps.createNestedArray();
        sensorArr.add(rid);
        sensorArr.add(t);

        if (sensors_config.containsKey(rid)) {
          sensorArr.add(sensors_config[rid].as<String>());
        } else {
          sensorArr.add("");        // empty name/label
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

  JsonDocument doc;
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
    sensors_config.set(doc["sensors_config"]);
  }

  saveConfig();

  if (pinChanged) {
    setupOnewire();
  }

  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleApiWifi() {
  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method Not Allowed");
    return;
  }

  JsonDocument doc;
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

void handleApiFactoryReset() {
  LittleFS.remove(config_file);
  server.send(200, "application/json", "{\"status\":\"ok\"}");
  delay(1000);
  ESP.restart();
}

void registerExtraRoutes() {
  // Add future routes here
}

// =============================================================
// SETUP
// =============================================================
void setup() {
  Serial.begin(115200);
  delay(800);                                 // Important for ESP8266

  Serial.println("\n\n=================================");
  Serial.println("       T_NEST Controller");
  Serial.println("=================================\n");

  // LittleFS
  if (!LittleFS.begin()) {
    Serial.println("LittleFS mount failed. Formatting...");
    if (LittleFS.format()) {
      Serial.println("LittleFS formatted successfully.");
      LittleFS.begin();
    } else {
      Serial.println("LittleFS format FAILED! System halted.");
      while (true) delay(1000);
    }
  } else {
    Serial.println("LittleFS mounted successfully");
  }

  loadConfig();
  setupOnewire();

  // WiFi Connection
  if (wifi_ssid.length() > 0) {
    Serial.println("Connecting to WiFi: " + wifi_ssid);
    WiFi.mode(WIFI_STA);
    WiFi.begin(wifi_ssid.c_str(), wifi_password.c_str());

    int retries = 0;
    while (WiFi.status() != WL_CONNECTED && retries < 25) {
      delay(500);
      Serial.print(".");
      retries++;
    }
    Serial.println();
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi connection failed → Starting Access Point");
    WiFi.mode(WIFI_AP);
    String apName = "T_NEST_" + String(ESP.getChipId(), HEX);
    WiFi.softAP(apName.c_str(), "password123");
    Serial.println("AP Started: " + apName);
    Serial.print("AP IP Address: ");
    Serial.println(WiFi.softAPIP());
  } else {
    Serial.println("Connected to WiFi!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
  }

  // Web Server Routes
  server.on("/", handleRoot);
  server.on("/index.html", handleRoot);
  server.on("/config.html", handleConfigHtml);
  server.on("/main.css", handleCss);
  server.on("/api/state", handleApiState);
  server.on("/api/sensors/config", handleApiSensorsConfig);
  server.on("/api/wifi", handleApiWifi);
  server.on("/api/reboot", handleApiReboot);
  server.on("/api/factory_reset", handleApiFactoryReset);

  registerExtraRoutes();

  server.begin();
  Serial.println("Web server started on port 80");

  ArduinoOTA.begin();
  Serial.println("System ready!\n");
}

// =============================================================
// LOOP
// =============================================================
unsigned long lastTempRequest = 0;

bool conversionPending = false;
unsigned long conversionStartTime = 0;

void loop() {
  server.handleClient();
  ArduinoOTA.handle();

  if (sensors != nullptr) {
    // 1. Request temperatures every 5 seconds
    if (!conversionPending && millis() - lastTempRequest > 5000) {
      sensors->requestTemperatures();
      conversionPending = true;
      conversionStartTime = millis();
      lastTempRequest = millis();
    }

    // 2. Read temperatures 750ms after requesting (non-blocking)
    if (conversionPending && millis() - conversionStartTime > 750) {
      conversionPending = false;
      Serial.println("---- DS18B20 Sensors ----");
      int count = sensors->getDeviceCount();
      for (int i = 0; i < count; i++) {
        DeviceAddress addr;
        if (sensors->getAddress(addr, i)) {
          String rid = "";
          for (uint8_t j = 0; j < 8; j++) {
            if (addr[j] < 16) rid += "0";
            rid += String(addr[j], HEX);
          }
          float tempC = sensors->getTempC(addr);
          Serial.printf("Sensor %d [%s] = %.2f °C\n", i, rid.c_str(), tempC);
        }
        yield(); // Feed WDT
      }
      Serial.println("------------------------");
    }
  }
}
