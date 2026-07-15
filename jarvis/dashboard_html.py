"""Dashboard HTML template — self-contained, zero-dependency monitoring UI.

Contains a single generate_html() function that returns the full HTML
for the Emperor dashboard. No external CSS/JS — everything is inline.

Features:
- Dark theme with glassmorphism cards
- Auto-refresh every 3 seconds via SSE-like polling
- Minister ranking table with merit bars
- Evolution cycle counter + history sparklines
- Task success rate meter
- Scheduler status (if connected)
"""

from __future__ import annotations


def generate_html(api_base: str = "http://127.0.0.1:9020") -> str:
    """Return the complete dashboard HTML page.

    Args:
        api_base: Base URL of the Emperor API (e.g. http://127.0.0.1:9020).
    """
    return DASHBOARD_HTML.replace("{{API_BASE}}", api_base)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Emperor Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0b0f19;
    --card-bg: rgba(20, 25, 45, 0.85);
    --card-border: rgba(255, 255, 255, 0.06);
    --text: #e0e4f0;
    --text-dim: #8892a8;
    --accent: #6c8cff;
    --accent-glow: rgba(108, 140, 255, 0.25);
    --success: #4ade80;
    --warning: #facc15;
    --danger: #f87171;
    --radius: 12px;
    --gap: 16px;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 'Helvetica Neue', Arial, sans-serif;
    background: var(--bg);
    background-image:
      radial-gradient(ellipse at 20% 50%, rgba(108,140,255,0.06) 0%, transparent 60%),
      radial-gradient(ellipse at 80% 20%, rgba(74,222,128,0.04) 0%, transparent 50%);
    color: var(--text);
    min-height: 100vh;
    padding: 32px 24px;
  }
  .header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 32px;
    flex-wrap: wrap; gap: 12px;
  }
  .header h1 {
    font-size: 1.75rem; font-weight: 700; letter-spacing: -0.5px;
    background: linear-gradient(135deg, var(--accent), #a78bfa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .badge {
    font-size: 0.8rem; padding: 6px 14px; border-radius: 20px;
    background: rgba(108,140,255,0.12); color: var(--accent);
    border: 1px solid rgba(108,140,255,0.2);
  }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: var(--gap); margin-bottom: var(--gap); }
  .card {
    background: var(--card-bg); backdrop-filter: blur(16px);
    border: 1px solid var(--card-border); border-radius: var(--radius);
    padding: 20px 24px; transition: border-color 0.3s;
  }
  .card:hover { border-color: rgba(255,255,255,0.12); }
  .card-label { font-size: 0.75rem; text-transform: uppercase; color: var(--text-dim); letter-spacing: 1px; margin-bottom: 8px; }
  .card-value { font-size: 2rem; font-weight: 700; line-height: 1.1; }
  .card-value.accent { color: var(--accent); }
  .card-value.success { color: var(--success); }
  .card-value.warning { color: var(--warning); }
  .card-value.danger { color: var(--danger); }
  .card-sub { font-size: 0.8rem; color: var(--text-dim); margin-top: 4px; }

  .table-wrap { background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: var(--radius); overflow-x: auto; backdrop-filter: blur(16px);
    margin-bottom: var(--gap);
  }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th { text-align: left; padding: 14px 20px; color: var(--text-dim); font-weight: 600;
       font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px;
       border-bottom: 1px solid var(--card-border); background: rgba(255,255,255,0.02); }
  td { padding: 12px 20px; border-bottom: 1px solid rgba(255,255,255,0.03); }
  tr:last-child td { border-bottom: none; }
  .merit-bar {
    display: inline-block; height: 6px; border-radius: 3px;
    background: linear-gradient(90deg, var(--accent), #a78bfa);
    vertical-align: middle; margin-right: 8px;
  }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.online { background: var(--success); box-shadow: 0 0 8px rgba(74,222,128,0.5); }
  .dot.offline { background: var(--danger); }

  .meter {
    width: 100%; height: 8px; background: rgba(255,255,255,0.05);
    border-radius: 4px; overflow: hidden; margin-top: 8px;
  }
  .meter-fill {
    height: 100%; border-radius: 4px; transition: width 0.6s ease;
    background: linear-gradient(90deg, var(--success), var(--accent));
  }

  .footer {
    text-align: center; padding: 24px; color: var(--text-dim);
    font-size: 0.75rem; margin-top: 16px;
  }
  .refresh { animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }

  .alert-panel { background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: var(--radius); padding: 20px 24px; backdrop-filter: blur(16px);
    margin-bottom: var(--gap);
  }
  .alert-panel h3 { font-size: 0.8rem; text-transform: uppercase; color: var(--text-dim);
    letter-spacing: 1px; margin-bottom: 12px;
  }
  .alert-item { padding: 8px 12px; border-radius: 8px; margin-bottom: 6px;
    font-size: 0.82rem; display: flex; align-items: center; gap: 10px; }
  .alert-item.info { background: rgba(108,140,255,0.08); border: 1px solid rgba(108,140,255,0.15); }
  .alert-item.warning { background: rgba(250,204,21,0.08); border: 1px solid rgba(250,204,21,0.15); }
  .alert-item.critical { background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.15); }
  .alert-sev { font-size: 0.7rem; text-transform: uppercase; font-weight: 700; min-width: 60px; }
  .alert-sev.info { color: var(--accent); }
  .alert-sev.warning { color: var(--warning); }
  .alert-sev.critical { color: var(--danger); }
  .alert-msg { color: var(--text); flex: 1; }
  .alert-time { color: var(--text-dim); font-size: 0.7rem; white-space: nowrap; }
  .alert-empty { color: var(--text-dim); font-size: 0.8rem; padding: 8px 0; }
</style>
</head>
<body>

<div class="header">
  <h1>Emperor Evolution Dashboard</h1>
  <div>
    <span class="badge refresh" id="connectionStatus">Connecting...</span>
    <span class="badge" style="margin-left:8px;" id="lastUpdate"></span>
  </div>
</div>

<div class="grid" id="statCards"></div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>#</th><th>Minister</th><th>Domain</th><th>Merit</th>
        <th>Confidence</th><th>Tasks</th><th>Success Rate</th>
      </tr>
    </thead>
    <tbody id="ministerTable"></tbody>
  </table>
</div>

<div class="grid" id="detailCards"></div>

<div class="alert-panel" id="alertsPanel">
  <h3>Alerts & Notifications</h3>
  <div id="alertsList"><span class="alert-empty">No alerts</span></div>
</div>

<div class="footer">
  Emperor Core &middot; Auto-refresh every 3s &middot; <span id="footerCycle">--</span>
</div>

<script>
  var API = "{{API_BASE}}";

  function fmt(n, dec) {
    if (n == null || isNaN(n)) return '--';
    return Number(n).toFixed(dec || 0);
  }

  function fmtTs(ts) {
    var d = new Date(ts * 1000);
    return d.toLocaleTimeString();
  }

  function renderStatCards(d) {
    var c = d.court || {};
    var t = d.tasks || {};
    var rate = t.success_rate != null ? (t.success_rate * 100).toFixed(1) : '--';

    document.getElementById('statCards').innerHTML = [
      '<div class="card">' +
        '<div class="card-label">Active Ministers</div>' +
        '<div class="card-value accent">' + (c.active_ministers || 0) + '</div>' +
        '<div class="card-sub">Evolution Cycle #' + (c.cycle || 0) + '</div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Total Tasks</div>' +
        '<div class="card-value">' + (t.total || 0) + '</div>' +
        '<div class="card-sub">' + (t.completed || 0) + ' completed / ' + (t.failed || 0) + ' failed</div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Success Rate</div>' +
        '<div class="card-value ' + (rate > 80 ? 'success' : rate > 50 ? 'warning' : 'danger') + '">' + rate + '%</div>' +
        '<div class="meter"><div class="meter-fill" style="width:' + (rate === '--' ? 0 : rate) + '%"></div></div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Avg Merit</div>' +
        '<div class="card-value accent">' + fmt(t.avg_merit, 1) + '</div>' +
        '<div class="card-sub">Top: ' + (c.top_minister || '--') + '</div>' +
      '</div>',
    ].join('');
  }

  function renderMinisterTable(d) {
    var ministers = d.ministers || [];
    if (!ministers.length) {
      document.getElementById('ministerTable').innerHTML =
        '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:32px;">No ministers registered yet</td></tr>';
      return;
    }
    var maxMerit = Math.max.apply(null, ministers.map(function(m){ return m.merit || 0; })) || 1;
    document.getElementById('ministerTable').innerHTML = ministers.map(function(m, i){
      var barW = ((m.merit || 0) / maxMerit * 120).toFixed(0);
      var rate = m.success_rate != null ? (m.success_rate * 100).toFixed(0) : '--';
      return '<tr>' +
        '<td style="color:var(--text-dim);">' + (i+1) + '</td>' +
        '<td><strong>' + (m.name || '?') + '</strong></td>' +
        '<td style="color:var(--text-dim);">' + (m.domain || '--') + '</td>' +
        '<td><span class="merit-bar" style="width:' + barW + 'px;"></span>' + fmt(m.merit, 2) + '</td>' +
        '<td>' + fmt(m.confidence, 3) + '</td>' +
        '<td style="color:var(--text-dim);">' + (m.tasks_completed || 0) + '</td>' +
        '<td>' + rate + '%</td>' +
      '</tr>';
    }).join('');
  }

  function renderDetailCards(d) {
    var c = d.court || {};
    var t = d.tasks || {};
    var s = d.config || {};
    document.getElementById('detailCards').innerHTML = [
      '<div class="card">' +
        '<div class="card-label">Configuration</div>' +
        '<div style="font-size:0.85rem;color:var(--text-dim);">' +
          'Min Ministers: ' + (s.min_ministers || '--') + '<br>' +
          'Max Ministers: ' + (s.max_ministers || '--') + '<br>' +
          'Crossover Rate: ' + fmt(s.crossover_rate, 2) + '<br>' +
          'API Port: ' + (s.api_port || '--') +
        '</div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Scheduler</div>' +
        '<div style="font-size:0.85rem;">' +
          '<span class="dot ' + (d.scheduler_running ? 'online' : 'offline') + '"></span>' +
          (d.scheduler_running ? 'Running' : 'Stopped') + '<br>' +
          '<span style="color:var(--text-dim);">Jobs: ' + (d.scheduler_jobs || 0) +
          ' &middot; Runs: ' + (d.scheduler_total_runs || 0) + '</span>' +
        '</div>' +
      '</div>',
    ].join('');
  }

  function fetchData() {
    fetch(API + '/dashboard/status')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        document.getElementById('connectionStatus').textContent = 'Live';
        document.getElementById('connectionStatus').style.color = 'var(--success)';
        document.getElementById('lastUpdate').textContent = fmtTs(Date.now() / 1000);
        document.getElementById('footerCycle').textContent = 'Cycle #' + ((d.court||{}).cycle || 0);
        renderStatCards(d);
        renderMinisterTable(d);
        renderDetailCards(d);
      })
      .catch(function(e) {
        document.getElementById('connectionStatus').textContent = 'Disconnected';
        document.getElementById('connectionStatus').style.color = 'var(--danger)';
      });
  }

  function fetchAlerts() {
    fetch(API + '/dashboard/alerts')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var history = d.history || [];
        var el = document.getElementById('alertsList');
        if (!history.length) {
          el.innerHTML = '<span class="alert-empty">No alerts</span>';
          return;
        }
        el.innerHTML = history.slice(0, 10).map(function(a) {
          var t = new Date(a.timestamp * 1000);
          return '<div class="alert-item ' + (a.severity || 'info') + '">' +
            '<span class="alert-sev ' + (a.severity || 'info') + '">' + (a.severity || '').toUpperCase() + '</span>' +
            '<span class="alert-msg">' + (a.message || a.rule_name || '?') +
            ' <span style="color:var(--text-dim);font-size:0.7rem">(' +
            (a.metric||'') + ' ' + (a.operator||'') + ' ' + fmt(a.threshold,2) +
            ', current: ' + fmt(a.current_value,3) + ')</span></span>' +
            '<span class="alert-time">' + t.toLocaleTimeString() + '</span>' +
          '</div>';
        }).join('');
      })
      .catch(function() {});
  }

  fetchData();
  setInterval(fetchData, 3000);
  fetchAlerts();
  setInterval(fetchAlerts, 5000);
</script>
</body>
</html>"""
