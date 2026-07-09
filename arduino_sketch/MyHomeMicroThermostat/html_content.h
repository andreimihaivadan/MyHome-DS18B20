#ifndef HTML_CONTENT_H
#define HTML_CONTENT_H

#include <Arduino.h>

const char index_html[] PROGMEM = R"=====(
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Transylvanian Elysium Nest</title>
  <link rel="stylesheet" href="/main.css">
</head>
<body>
  <h1>Transylvanian Elysium Nest</h1>
  <div id="sensors"></div>
    <div style="margin-top: 20px;"><a href="/config.html">Configure Device</a></div>
  <div class="section" id="device-info">
    <h2>Device Info</h2>
    <div style="margin: 5px 0;"><strong>Serial:</strong> <span id="serial">--</span></div>
    <div style="margin: 5px 0;"><strong>IP:</strong> <span id="ip">--</span></div>
    <div style="margin: 5px 0;"><strong>Mode:</strong> <span id="mode">--</span></div>
    <div style="margin: 5px 0;"><strong>Firmware: v1.0 (Arduino)</strong></div>
  </div>

  <div class="section" id="wifi-info">
    <h2>Wi-Fi information</h2>
    <div style="margin: 5px 0;"><strong>Signal strength:</strong> <span id="rssi_dbm">--</span> dBm</div>
    <div style="margin: 5px 0;"><strong>Signal strength:</strong> <span id="rssi_human">--</span></div>
  </div>
  <script>
  async function load() {
    const r = await fetch('/api/state');
    const d = await r.json();
    document.getElementById('serial').textContent = d.udid || '--';
    document.getElementById('ip').textContent = d.ip || '--';
    if (document.getElementById('mode')) document.getElementById('mode').textContent = d.mode || '--';

    let rssi_dbm = '--';
    let rssi_human = '--';
    if (d.rssi !== undefined) {
      rssi_dbm = d.rssi;
      if (d.rssi > -50) rssi_human = 'Very strong';
      else if (d.rssi > -60) rssi_human = 'Strong';
      else if (d.rssi > -70) rssi_human = 'Ok';
      else if (d.rssi > -80) rssi_human = 'Low';
      else rssi_human = 'Very low';
    }

    if (document.getElementById('rssi_dbm')) document.getElementById('rssi_dbm').textContent = rssi_dbm;
    if (document.getElementById('rssi_human')) document.getElementById('rssi_human').textContent = rssi_human;

    const sContainer = document.getElementById('sensors');
    if (sContainer) {
        sContainer.innerHTML = '<h2>Sensors</h2>';
        if (d.sensors && d.sensors.ds_temps) {
          d.sensors.ds_temps.forEach(sensor => {
            const div = document.createElement('div');
            div.style.fontSize = '1.2em';
            div.style.margin = '10px 0';
            div.innerHTML = `<strong>${sensor[2] || sensor[0]}:</strong> ${sensor[1]} &deg;C`;
            sContainer.appendChild(div);
          });
        }
    }

  }
  load(); setInterval(load, 3000);
  </script>
</body>
</html>
)=====";

const char config_html[] PROGMEM = R"=====(
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Transylvanian Elysium Nest – Config</title>
  <link rel="stylesheet" href="/main.css">
</head>
<body>
  <h1>Transylvanian Elysium Nest – Config</h1>

  <!-- 0. Sensors Settings -->
  <div class="section">
    <h2>Sensors Settings</h2>
    <div>
      <label>OneWire GPIO:</label>
      <input type="number" id="ow_pin" placeholder="4">
    </div>
    <div style="margin-top: 10px;">
      <table style="width: 100%;">
        <thead>
          <tr>
            <th>Sensor ID (ROM)</th>
            <th>Current Temp</th>
            <th>Scope (e.g. Room1)</th>
          </tr>
        </thead>
        <tbody id="sensors_body"></tbody>
      </table>
    </div>
    <div style="margin-top: 10px;">
      <button onclick="saveSensors()">Save Sensors Config</button>
    </div>
  </div>



  <!-- 2. WiFi -->
  <div class="section">
    <h2>WiFi Settings</h2>
    <input type="text" id="ssid" placeholder="SSID">
    <input type="password" id="pass" placeholder="Password">
    <button onclick="saveWiFi()">Save & Reboot</button>
  </div>

  <!-- 3. Device Info -->
  <div class="section">
    <h2>Device Info</h2>
    <pre id="info">Loading...</pre>
    <button onclick="reboot()">Reboot</button>
    <button onclick="factoryReset()">Factory Reset</button>
  </div>

  <div class="back"><a href="/">Back to Control</a></div>

  <script>
    async function load() {
      const r = await fetch('/api/state');
      const d = await r.json();

      const owEl = document.getElementById('ow_pin');
      if (owEl) owEl.value = d.ow_pin !== undefined ? d.ow_pin : 4;

      const stbl = document.getElementById('sensors_body');
      if (stbl) {
        stbl.innerHTML = '';
        if (d.sensors && d.sensors.ds_temps) {
          d.sensors.ds_temps.forEach((sensor) => {
             const tr = document.createElement('tr');
             tr.innerHTML = `
               <td>${sensor[0]}</td>
               <td>${sensor[1]} °C</td>
               <td><input class="sensor-scope-input" data-rom="${sensor[0]}" value="${sensor[2] || ''}"></td>
             `;
             stbl.appendChild(tr);
          });
        }
      }


      document.getElementById('ssid').value = d.ssid || '';
      document.getElementById('info').textContent =
        `Serial: ${d.udid}\nIP: ${d.ip}\nMode: ${d.mode}\nFirmware: v1.0 (Arduino)`;
    }

    async function saveSensors() {
      const ow_pin = parseInt(document.getElementById('ow_pin').value, 10) || 4;
      const inputs = document.querySelectorAll('.sensor-scope-input');
      const sensors_config = {};
      inputs.forEach(inp => {
        sensors_config[inp.dataset.rom] = inp.value.trim();
      });

      await fetch('/api/sensors/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ow_pin, sensors_config})
      });
      alert('Sensors config saved!');
    }

    async function saveWiFi() {
      const ssid = document.getElementById('ssid').value;
      const pass = document.getElementById('pass').value;
      await fetch('/api/wifi', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ssid,password:pass})});
      alert('WiFi saved!');
      setTimeout(()=>location.reload(),2000);
      reboot();
    }

    async function reboot() { if(confirm('Reboot?')) await fetch('/api/reboot',{method:'POST'}); }
    async function factoryReset() { if(confirm('Factory reset?')) await fetch('/api/factory_reset',{method:'POST'}); }

    load();
  </script>
</body>
</html>
)=====";

const char main_css[] PROGMEM = R"=====(
body {
  background: repeating-linear-gradient(135deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(45deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(67.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(135deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(45deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(112.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(112.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(45deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(22.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(45deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(22.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(135deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(157.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(67.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              repeating-linear-gradient(67.5deg, hsla(264,0%,88%,0.03) 0px, hsla(264,0%,88%,0.03) 1px,transparent 1px, transparent 12px),
              linear-gradient(90deg, rgb(26,169,210),rgb(57,59,205));
  color: white;
}
h1 {
  color: #2c3e50;
  text-align: center;
}
.section {
  background: white;
  padding: 15px;
  border-radius: 8px;
  margin-bottom: 20px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
h2 {
  margin-top: 0;
  font-size: 1.2em;
  color: #34495e;
}
button {
  padding: 10px 15px;
  border: none;
  border-radius: 4px;
  background-color: #3498db;
  color: white;
  cursor: pointer;
  font-size: 1em;
  margin: 5px 2px;
}
button:hover {
  background-color: #2980b9;
}
button.on {
  background-color: #e74c3c;
}
button.off {
  background-color: #95a5a6;
}
input, select {
  padding: 8px;
  margin: 5px 0;
  border: 1px solid #ccc;
  border-radius: 4px;
  width: calc(100% - 18px);
}
.footer {
  text-align: center;
  font-size: 0.8em;
  color: #7f8c8d;
  margin-top: 20px;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th, td {
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid #ddd;
}
.category {
  font-weight: bold;
  margin-top: 15px;
  color: #2c3e50;
  border-bottom: 2px solid #3498db;
  padding-bottom: 5px;
}
.back {
  margin-top: 20px;
  text-align: center;
}
.back a {
  text-decoration: none;
  color: #3498db;
  font-weight: bold;
}
)=====";

#endif
