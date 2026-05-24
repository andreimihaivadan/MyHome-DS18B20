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
  <div id="relays"></div>
  <div style="margin-top: 20px;"><a href="/config.html">Configure Device</a></div>
  <div class="footer">
    Serial: <span id="serial"></span> | IP: <span id="ip"></span>
  </div>
  <script>
  async function load() {
    const r = await fetch('/api/state');
    const d = await r.json();
    document.getElementById('serial').textContent = d.udid;
    document.getElementById('ip').textContent = d.ip;

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

    const container = document.getElementById('relays');
    container.innerHTML = '';
    const maxRelays = d.max_relays || 16;
    const sorted = Object.keys(d.relays).filter(k => {
        const n = parseInt(k.split('_')[1]);
        return n <= maxRelays;
    }).sort((a,b) => {
      const na = parseInt(a.split('_')[1]);
      const nb = parseInt(b.split('_')[1]);
      return na - nb;
    }).map(k => [k, d.relays[k]]);

    let cur = '';
    sorted.forEach(([k, state]) => {
      const purpose = d.purposes[k] || 'Unknown';
      if (purpose !== cur) {
        cur = purpose;
        const cat = document.createElement('div');
        cat.className = 'category';
        cat.textContent = purpose;
        container.appendChild(cat);
      }
      const i = k.split('_')[1];
      const b = document.createElement('button');
      b.textContent = `Relay ${i}: ${state?'ON':'OFF'}`;
      b.className = state ? 'on' : 'off';
      b.onclick = () => fetch(`/api/relays/${k}/${state?'off':'on'}`, {method:'POST'}).then(load);
      container.appendChild(b);
    });
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
      <input type="number" id="ow_pin" placeholder="10">
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

  <!-- 1. Relay Purposes -->
  <div class="section">
    <h2>Relay Configuration</h2>
    <table>
      <thead>
        <tr>
          <th>Relay</th>
          <th>Purpose</th>
          <th>Trigger</th>
        </tr>
      </thead>
      <tbody id="relays_body"></tbody>
    </table>
    <button onclick="saveRelays()">Save All</button>
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
    <button onclick="resetRelays()">All Relays OFF</button>
    <button onclick="factoryReset()">Factory Reset</button>
  </div>

  <div class="back"><a href="/">Back to Control</a></div>

  <script>
    let currentMaxRelays = 16;

    async function load() {
      const r = await fetch('/api/state');
      const d = await r.json();
      currentMaxRelays = d.max_relays || 16;

      const owEl = document.getElementById('ow_pin');
      if (owEl) owEl.value = d.ow_pin !== undefined ? d.ow_pin : 10;

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

      // Purposes
      const tbl = document.getElementById('relays_body');
      if (tbl) {
          tbl.innerHTML = '';
          for (let i=1;i<=currentMaxRelays;i++) {
            const k = 'relay_'+i;
            const trig = (d.triggers && d.triggers[k]) ? d.triggers[k] : 'high';
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td>Relay ${i}</td>
              <td><input id="p${i}" value="${d.purposes[k]||''}" placeholder="e.g. Lights, Pump"></td>
              <td>
                <select id="t${i}">
                  <option value="high" ${trig=='high'?'selected':''}>High (Norm)</option>
                  <option value="low" ${trig=='low'?'selected':''}>Low (Inv)</option>
                </select>
              </td>`;
            tbl.appendChild(tr);
          }
      }

      document.getElementById('ssid').value = d.ssid || '';
      document.getElementById('info').textContent =
        `Serial: ${d.udid}\nIP: ${d.ip}\nMode: ${d.mode}\nFirmware: v1.0 (Arduino)`;
    }

    async function saveSensors() {
      const ow_pin = parseInt(document.getElementById('ow_pin').value, 10);
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

    async function saveRelays() {
      const p = {};
      for (let i=1;i<=currentMaxRelays;i++) {
        const pEl = document.getElementById('p'+i);
        const tEl = document.getElementById('t'+i);
        if (pEl && tEl) {
          p['relay_'+i] = {
            purpose: pEl.value.trim(),
            trigger: tEl.value
          };
        }
      }
      await fetch('/api/relays/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
      alert('Saved!');
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
    async function resetRelays() { if(confirm('All OFF?')) await fetch('/api/reset_relays',{method:'POST'}).then(()=>location.href='/'); }
    async function factoryReset() { if(confirm('Factory reset?')) await fetch('/api/factory_reset',{method:'POST'}); }

    load();
  </script>
</body>
</html>
)=====";

const char main_css[] PROGMEM = R"=====(
body {
  font-family: Arial, sans-serif;
  margin: 0;
  padding: 20px;
  background-color: #f0f0f0;
  color: #333;
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
