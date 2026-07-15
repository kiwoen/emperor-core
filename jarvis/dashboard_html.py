"""Dashboard HTML template — self-contained, zero-dependency monitoring UI.

Contains a single generate_html() function that returns the full HTML
for the Emperor dashboard. No external CSS/JS — everything is inline.

Features:
- Dark theme with glassmorphism cards
- Auto-refresh every 3 seconds via polling
- Minister ranking table with merit bars
- Real-time task success-rate timeseries (inline SVG line chart)
- Confidence + execution-time sparkline charts
- Evolution cycle timeline
- Active alert history with severity colors
- Self-healing action log
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
    --accent-2: #a78bfa;
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
    margin-bottom: 24px;
    flex-wrap: wrap; gap: 12px;
  }
  .header h1 {
    font-size: 1.75rem; font-weight: 700; letter-spacing: -0.5px;
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .badge {
    font-size: 0.8rem; padding: 6px 14px; border-radius: 20px;
    background: rgba(108,140,255,0.12); color: var(--accent);
    border: 1px solid rgba(108,140,255,0.2);
  }
  .grid { display: grid; gap: var(--gap); margin-bottom: var(--gap); }
  .grid-stats  { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
  .grid-charts { grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); }
  .card {
    background: var(--card-bg); backdrop-filter: blur(16px);
    border: 1px solid var(--card-border); border-radius: var(--radius);
    padding: 20px 24px; transition: border-color 0.3s;
  }
  .card:hover { border-color: rgba(255,255,255,0.12); }
  .card-label { font-size: 0.72rem; text-transform: uppercase; color: var(--text-dim);
    letter-spacing: 1px; margin-bottom: 8px; display: flex; justify-content: space-between; }
  .card-label .badge-mini { font-size: 0.65rem; padding: 2px 8px; border-radius: 10px;
    background: rgba(255,255,255,0.05); text-transform: none; letter-spacing: 0; }
  .card-value { font-size: 1.85rem; font-weight: 700; line-height: 1.1; }
  .card-value.accent { color: var(--accent); }
  .card-value.success { color: var(--success); }
  .card-value.warning { color: var(--warning); }
  .card-value.danger { color: var(--danger); }
  .card-sub { font-size: 0.75rem; color: var(--text-dim); margin-top: 4px; }

  .table-wrap { background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: var(--radius); overflow-x: auto; backdrop-filter: blur(16px);
    margin-bottom: var(--gap);
  }
  table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  th { text-align: left; padding: 12px 16px; color: var(--text-dim); font-weight: 600;
       font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px;
       border-bottom: 1px solid var(--card-border); background: rgba(255,255,255,0.02); }
  td { padding: 10px 16px; border-bottom: 1px solid rgba(255,255,255,0.03); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,0.02); }
  .merit-bar {
    display: inline-block; height: 6px; border-radius: 3px;
    background: linear-gradient(90deg, var(--accent), var(--accent-2));
    vertical-align: middle; margin-right: 8px;
  }
  .rank { display: inline-block; width: 24px; height: 24px; border-radius: 6px;
    text-align: center; line-height: 24px; font-size: 0.75rem; font-weight: 700;
    background: rgba(255,255,255,0.05); color: var(--text-dim); margin-right: 8px; }
  .rank.gold { background: linear-gradient(135deg, #facc15, #f59e0b); color: #0b0f19; }
  .rank.silver { background: linear-gradient(135deg, #cbd5e1, #94a3b8); color: #0b0f19; }
  .rank.bronze { background: linear-gradient(135deg, #d97706, #b45309); color: #0b0f19; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.online { background: var(--success); box-shadow: 0 0 8px rgba(74,222,128,0.5); }
  .dot.offline { background: var(--danger); }
  .status-pill { display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.7rem; font-weight: 600; }
  .status-pill.active { background: rgba(74,222,128,0.12); color: var(--success); }
  .status-pill.idle { background: rgba(250,204,21,0.12); color: var(--warning); }
  .status-pill.failed { background: rgba(248,113,113,0.12); color: var(--danger); }

  .meter { width: 100%; height: 6px; background: rgba(255,255,255,0.05);
    border-radius: 3px; overflow: hidden; margin-top: 8px; }
  .meter-fill { height: 100%; border-radius: 3px; transition: width 0.6s ease;
    background: linear-gradient(90deg, var(--success), var(--accent)); }

  .chart-wrap { position: relative; height: 180px; }
  .chart-svg { width: 100%; height: 100%; display: block; }
  .chart-empty { position: absolute; inset: 0; display: flex; align-items: center;
    justify-content: center; color: var(--text-dim); font-size: 0.85rem; }

  .panel { background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: var(--radius); padding: 18px 20px; backdrop-filter: blur(16px);
    margin-bottom: var(--gap); }
  .panel h3 { font-size: 0.78rem; text-transform: uppercase; color: var(--text-dim);
    letter-spacing: 1px; margin-bottom: 12px; display: flex; justify-content: space-between; }
  .panel h3 .count { background: rgba(255,255,255,0.05); padding: 2px 8px;
    border-radius: 10px; font-size: 0.7rem; }
  .alert-item { padding: 8px 12px; border-radius: 8px; margin-bottom: 6px;
    font-size: 0.82rem; display: flex; align-items: center; gap: 10px; }
  .alert-item.info { background: rgba(108,140,255,0.08); border-left: 3px solid var(--accent); }
  .alert-item.warning { background: rgba(250,204,21,0.08); border-left: 3px solid var(--warning); }
  .alert-item.critical { background: rgba(248,113,113,0.08); border-left: 3px solid var(--danger); }
  .alert-sev { font-size: 0.7rem; text-transform: uppercase; font-weight: 700; min-width: 60px; }
  .alert-sev.info { color: var(--accent); }
  .alert-sev.warning { color: var(--warning); }
  .alert-sev.critical { color: var(--danger); }
  .alert-msg { color: var(--text); flex: 1; }
  .alert-time { color: var(--text-dim); font-size: 0.7rem; white-space: nowrap; }
  .empty { color: var(--text-dim); font-size: 0.8rem; padding: 12px 0; text-align: center; }

  .task-row { display: grid; grid-template-columns: 14px 1fr auto auto; gap: 10px;
    align-items: center; padding: 6px 10px; border-radius: 6px; font-size: 0.78rem;
    margin-bottom: 3px; }
  .task-row:hover { background: rgba(255,255,255,0.02); }
  .task-row .dot { margin: 0; }
  .task-domain { color: var(--text-dim); font-size: 0.7rem; }
  .task-time { color: var(--text-dim); font-size: 0.7rem; font-variant-numeric: tabular-nums; }

  .footer { text-align: center; padding: 20px; color: var(--text-dim);
    font-size: 0.72rem; margin-top: 8px; }
  .refresh { animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
  .tabs { display: flex; gap: 4px; margin-bottom: 12px; }
  .tab { padding: 6px 14px; border-radius: 8px; font-size: 0.75rem; cursor: pointer;
    background: rgba(255,255,255,0.03); color: var(--text-dim); border: 1px solid transparent;
    transition: all 0.2s; }
  .tab.active { background: rgba(108,140,255,0.15); color: var(--accent);
    border-color: rgba(108,140,255,0.3); }
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

<!-- Top stat cards -->
<div class="grid grid-stats" id="statCards"></div>

<!-- Control Panel -->
<div class="card" style="background:#16213e;border:1px solid #0f3460;margin-bottom:var(--gap);">
  <div class="card-label" style="font-size:0.78rem;text-transform:uppercase;color:var(--text-dim);letter-spacing:1px;margin-bottom:12px;">Control Panel</div>
  <div style="display:flex;gap:12px;flex-wrap:wrap;">
    <button id="btnEvolve" onclick="triggerEvolve()" style="background:#e94560;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-family:inherit;font-size:0.85rem;cursor:pointer;transition:filter 0.2s;">进化</button>
    <button id="btnExecute" onclick="triggerExecute()" style="background:#e94560;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-family:inherit;font-size:0.85rem;cursor:pointer;transition:filter 0.2s;">执行任务</button>
    <button id="btnHeal" onclick="triggerHeal()" style="background:#e94560;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-family:inherit;font-size:0.85rem;cursor:pointer;transition:filter 0.2s;">自愈检查</button>
    <button id="btnExport" onclick="triggerExport()" style="background:var(--card-bg);color:var(--accent);border:1px solid rgba(108,140,255,0.3);border-radius:8px;padding:10px 20px;font-family:inherit;font-size:0.85rem;cursor:pointer;transition:all 0.2s;">导出数据</button>
  </div>
</div>

<!-- Time-series charts row -->
<div class="grid grid-charts" id="chartsRow">
  <div class="card">
    <div class="card-label">Task Success Rate <span class="badge-mini" id="successRateBadge">--</span></div>
    <div class="chart-wrap" id="successChart">
      <svg class="chart-svg" viewBox="0 0 400 160" preserveAspectRatio="none" id="successSvg"></svg>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Average Confidence <span class="badge-mini" id="confidenceBadge">--</span></div>
    <div class="chart-wrap" id="confidenceChart">
      <svg class="chart-svg" viewBox="0 0 400 160" preserveAspectRatio="none" id="confidenceSvg"></svg>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Task Execution Time (ms) <span class="badge-mini" id="execTimeBadge">--</span></div>
    <div class="chart-wrap" id="execTimeChart">
      <svg class="chart-svg" viewBox="0 0 400 160" preserveAspectRatio="none" id="execTimeSvg"></svg>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Evolution Cycles <span class="badge-mini" id="evolutionBadge">--</span></div>
    <div class="chart-wrap" id="evolutionChart">
      <svg class="chart-svg" viewBox="0 0 400 160" preserveAspectRatio="none" id="evolutionSvg"></svg>
    </div>
  </div>
</div>
<div style="text-align:center;font-size:0.72rem;color:var(--text-dim);margin-bottom:var(--gap);padding:4px 0;">
  历史数据已持久化，重启不丢失
</div>

<!-- Minister leaderboard -->
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Rank</th><th>Minister</th><th>Domain</th><th>Merit</th>
        <th>Confidence</th><th>Tasks</th><th>Success</th><th>Status</th>
      </tr>
    </thead>
    <tbody id="ministerTable"></tbody>
  </table>
</div>

<!-- Recent tasks panel -->
<div class="panel">
  <h3>Recent Tasks <span class="count" id="taskCount">0</span></h3>
  <div class="filter-bar" style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
    <input type="text" id="taskSearch" placeholder="搜索任务..." oninput="debounceFilterTasks()"
      style="flex:1;min-width:140px;padding:6px 10px;border-radius:6px;border:1px solid var(--card-border);background:rgba(255,255,255,0.03);color:var(--text);font-family:inherit;font-size:0.78rem;outline:none;">
    <select id="taskMinisterFilter" onchange="filterTasks()"
      style="padding:6px 10px;border-radius:6px;border:1px solid var(--card-border);background:rgba(255,255,255,0.03);color:var(--text);font-family:inherit;font-size:0.78rem;cursor:pointer;">
      <option value="">全部大臣</option>
    </select>
    <select id="taskStatusFilter" onchange="filterTasks()"
      style="padding:6px 10px;border-radius:6px;border:1px solid var(--card-border);background:rgba(255,255,255,0.03);color:var(--text);font-family:inherit;font-size:0.78rem;cursor:pointer;">
      <option value="">全部状态</option>
      <option value="completed">完成</option>
      <option value="failed">失败</option>
    </select>
  </div>
  <div id="taskList" class="panel-scroll">
    <div class="empty">No tasks executed yet</div>
  </div>
</div>

<!-- Alerts panel -->
<div class="panel">
  <h3>Active Alerts & Notifications <span class="count" id="alertCount">0</span></h3>
  <div class="filter-bar" style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
    <input type="text" id="alertSearch" placeholder="搜索告警..." oninput="debounceFilterAlerts()"
      style="flex:1;min-width:140px;padding:6px 10px;border-radius:6px;border:1px solid var(--card-border);background:rgba(255,255,255,0.03);color:var(--text);font-family:inherit;font-size:0.78rem;outline:none;">
    <select id="alertLevelFilter" onchange="filterAlerts()"
      style="padding:6px 10px;border-radius:6px;border:1px solid var(--card-border);background:rgba(255,255,255,0.03);color:var(--text);font-family:inherit;font-size:0.78rem;cursor:pointer;">
      <option value="">全部级别</option>
      <option value="WARNING">WARNING</option>
      <option value="ERROR">ERROR</option>
      <option value="INFO">INFO</option>
    </select>
  </div>
  <div id="alertsList"><div class="empty">No alerts</div></div>
</div>

<div class="footer">
  Emperor Core &middot; Auto-refresh every 3s &middot; <span id="footerCycle">--</span>
</div>

<script>
  var API = "{{API_BASE}}";
  var MAX_POINTS = 40;   // rolling window for charts

  function fmt(n, dec) {
    if (n == null || isNaN(n)) return '--';
    return Number(n).toFixed(dec || 0);
  }
  function fmtTs(ts) {
    if (!ts) return '--';
    var d = new Date(ts * 1000);
    return d.toLocaleTimeString();
  }
  function fmtRel(ts) {
    if (!ts) return '--';
    var s = (Date.now() / 1000) - ts;
    if (s < 60) return Math.floor(s) + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    return Math.floor(s / 3600) + 'h ago';
  }

  // ── State buffers for charts ──
  var buf = {
    success: [],       // 0..1
    confidence: [],    // 0..1
    execTime: [],      // ms
    evolution: []      // cycle count
  };

  // ── SVG line chart helper ──
  function drawLineChart(svgId, values, opts) {
    opts = opts || {};
    var svg = document.getElementById(svgId);
    if (!svg) return;
    if (!values || values.length === 0) {
      svg.innerHTML = '<text x="200" y="80" text-anchor="middle" fill="#8892a8" font-size="12">Awaiting data...</text>';
      return;
    }
    var W = 400, H = 160, P = 20;
    var min = opts.min != null ? opts.min : Math.min.apply(null, values);
    var max = opts.max != null ? opts.max : Math.max.apply(null, values);
    if (min === max) { min -= 0.5; max += 0.5; }
    var range = max - min;
    var stepX = (W - 2 * P) / Math.max(values.length - 1, 1);

    var pts = values.map(function(v, i) {
      var x = P + i * stepX;
      var y = H - P - ((v - min) / range) * (H - 2 * P);
      return [x, y];
    });

    var linePath = 'M ' + pts.map(function(p){ return p[0].toFixed(1)+','+p[1].toFixed(1); }).join(' L ');
    var areaPath = linePath + ' L ' + pts[pts.length-1][0].toFixed(1) + ',' + (H - P) +
                   ' L ' + pts[0][0].toFixed(1) + ',' + (H - P) + ' Z';

    var color = opts.color || '#6c8cff';
    var gid = 'g' + svgId;
    var html = '';
    html += '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="' + color + '" stop-opacity="0.4"/>' +
            '<stop offset="100%" stop-color="' + color + '" stop-opacity="0"/></linearGradient></defs>';
    html += '<path d="' + areaPath + '" fill="url(#' + gid + ')" />';
    html += '<path d="' + linePath + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" />';
    // dots
    pts.forEach(function(p) {
      html += '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) +
              '" r="2.5" fill="' + color + '"/>';
    });
    // y-axis label
    if (opts.label) {
      html += '<text x="6" y="14" fill="#8892a8" font-size="9">' + opts.label + '</text>';
    }
    // x-axis ticks (min/max)
    html += '<text x="' + P + '" y="' + (H - 4) + '" fill="#8892a8" font-size="9" text-anchor="middle">' + fmt(min, opts.dec || 1) + '</text>';
    html += '<text x="' + (W - P) + '" y="' + (H - 4) + '" fill="#8892a8" font-size="9" text-anchor="middle">' + fmt(max, opts.dec || 1) + '</text>';
    svg.innerHTML = html;
  }

  // ── Stat cards ──
  function renderStatCards(d) {
    var c = d.court || {};
    var t = d.tasks || {};
    var m = d.metrics || {};
    var rate = t.success_rate != null ? (t.success_rate * 100).toFixed(1) : '--';
    var avgConf = m.avg_confidence != null ? m.avg_confidence.toFixed(3) : '--';
    var avgExec = m.avg_execution_time_ms != null ? m.avg_execution_time_ms.toFixed(0) : '--';
    var evoCount = m.total_evolution_cycles != null ? m.total_evolution_cycles : 0;

    document.getElementById('statCards').innerHTML = [
      '<div class="card">' +
        '<div class="card-label">Active Ministers <span class="badge-mini">cycle #' + (c.cycle || 0) + '</span></div>' +
        '<div class="card-value accent">' + (c.active_ministers || 0) + '</div>' +
        '<div class="card-sub">top: ' + (c.top_minister || '--') + '</div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Tasks <span class="badge-mini">' + (m.samples_in_buffer || 0) + ' samples</span></div>' +
        '<div class="card-value">' + (t.total || 0) + '</div>' +
        '<div class="card-sub">' + (t.completed || 0) + ' done / ' + (t.failed || 0) + ' failed</div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Success Rate</div>' +
        '<div class="card-value ' + (rate > 80 ? 'success' : rate > 50 ? 'warning' : 'danger') + '">' + rate + '%</div>' +
        '<div class="meter"><div class="meter-fill" style="width:' + (rate === '--' ? 0 : rate) + '%"></div></div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Avg Confidence</div>' +
        '<div class="card-value accent">' + avgConf + '</div>' +
        '<div class="card-sub">higher = more reliable</div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Avg Exec Time</div>' +
        '<div class="card-value">' + avgExec + ' <span style="font-size:0.9rem;color:var(--text-dim);">ms</span></div>' +
        '<div class="card-sub">per task</div>' +
      '</div>',
      '<div class="card">' +
        '<div class="card-label">Evolution Cycles</div>' +
        '<div class="card-value accent">' + evoCount + '</div>' +
        '<div class="card-sub">' + (m.total_evolutions || 0) + ' sessions</div>' +
      '</div>',
    ].join('');
  }

  // ── Minister leaderboard ──
  function renderMinisterTable(d) {
    var ministers = d.ministers || [];
    if (!ministers.length) {
      document.getElementById('ministerTable').innerHTML =
        '<tr><td colspan="8" style="text-align:center;color:var(--text-dim);padding:32px;">No ministers registered yet</td></tr>';
      return;
    }
    var maxMerit = Math.max.apply(null, ministers.map(function(m){ return m.merit || 0; })) || 1;
    document.getElementById('ministerTable').innerHTML = ministers.map(function(m, i){
      var barW = ((m.merit || 0) / maxMerit * 110).toFixed(0);
      var rate = m.success_rate != null ? (m.success_rate * 100).toFixed(0) : '--';
      var rankClass = i === 0 ? 'gold' : i === 1 ? 'silver' : i === 2 ? 'bronze' : '';
      var status = m.status || 'unknown';
      return '<tr>' +
        '<td><span class="rank ' + rankClass + '">' + (i+1) + '</span></td>' +
        '<td><strong>' + (m.name || '?') + '</strong></td>' +
        '<td style="color:var(--text-dim);">' + (m.domain || '--') + '</td>' +
        '<td><span class="merit-bar" style="width:' + barW + 'px;"></span>' + fmt(m.merit, 2) + '</td>' +
        '<td>' + fmt(m.confidence, 3) + '</td>' +
        '<td style="color:var(--text-dim);">' + (m.tasks_completed || 0) + '</td>' +
        '<td>' + rate + '%</td>' +
        '<td><span class="status-pill ' + (status === 'active' ? 'active' : 'idle') + '">' + status + '</span></td>' +
      '</tr>';
    }).join('');
  }

  // ── Recent task list (prefer DB history, fallback to metrics) ──
  var taskHistory = null;
  function renderTaskList(metrics) {
    // Only update taskHistory from metrics if DB history is not available
    if (!taskHistory || !taskHistory.length) {
      if (metrics && metrics.tasks && metrics.tasks.length) {
        taskHistory = metrics.tasks.map(function(t) {
          return {
            task_id: t.task_id,
            prompt: t.prompt || '',
            minister: t.domain || 'general',
            result: '',
            confidence: t.confidence || 0,
            status: t.success ? 'completed' : 'failed',
            created_at: t.timestamp ? new Date(t.timestamp * 1000).toISOString() : null,
            id: null
          };
        });
      }
    }
    filterTasks();
  }

  // ── Charts: update from metrics ──
  function updateCharts(metrics) {
    var tasks = metrics.tasks || [];
    var evos = metrics.evolutions || [];
    var summary = metrics.summary || {};

    // Build time-ordered series
    var sPoints = tasks.map(function(t){ return t.success ? 1 : 0; });
    var cPoints = tasks.map(function(t){ return t.confidence || 0; });
    var ePoints = tasks.map(function(t){ return t.execution_time_ms || 0; });
    var vPoints = evos.map(function(e){ return e.cycles || 0; });

    // Keep last MAX_POINTS
    buf.success = sPoints.slice(-MAX_POINTS);
    buf.confidence = cPoints.slice(-MAX_POINTS);
    buf.execTime = ePoints.slice(-MAX_POINTS);
    buf.evolution = vPoints.slice(-MAX_POINTS);

    drawLineChart('successSvg', buf.success, {min: 0, max: 1,
      color: '#4ade80', label: 'success'});
    drawLineChart('confidenceSvg', buf.confidence, {min: 0, max: 1,
      color: '#6c8cff', label: 'confidence'});
    drawLineChart('execTimeSvg', buf.execTime, {min: 0,
      color: '#facc15', label: 'ms', dec: 0});
    drawLineChart('evolutionSvg', buf.evolution, {min: 0,
      color: '#a78bfa', label: 'cycles', dec: 0});

    document.getElementById('successRateBadge').textContent =
      summary.success_rate != null ? (summary.success_rate * 100).toFixed(1) + '%' : '--';
    document.getElementById('confidenceBadge').textContent =
      summary.avg_confidence != null ? summary.avg_confidence.toFixed(3) : '--';
    document.getElementById('execTimeBadge').textContent =
      summary.avg_execution_time_ms != null ? summary.avg_execution_time_ms.toFixed(0) + 'ms' : '--';
    document.getElementById('evolutionBadge').textContent =
      (summary.total_evolution_cycles || 0) + ' cycles';
  }

  // ── Alerts (prefer DB history, fallback to real-time) ──
  var alertHistory = null;
  function renderAlerts(alertsData) {
    // Only update alertHistory from real-time if DB history not available
    if (!alertHistory || !alertHistory.length) {
      if (alertsData && alertsData.history && alertsData.history.length) {
        alertHistory = alertsData.history.map(function(a) {
          return {
            rule_name: a.rule_name,
            level: a.severity || 'info',
            message: a.message,
            created_at: a.timestamp ? new Date(a.timestamp * 1000).toISOString() : null
          };
        });
      }
    }
    filterAlerts();
  }

  // ── Fetchers ──
  function fetchStatus() {
    fetch(API + '/dashboard/status')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        document.getElementById('connectionStatus').textContent = 'Live';
        document.getElementById('connectionStatus').style.color = 'var(--success)';
        document.getElementById('lastUpdate').textContent = fmtTs(Date.now() / 1000);
        document.getElementById('footerCycle').textContent = 'Cycle #' + ((d.court||{}).cycle || 0);
        // Store minister list for dropdown
        window._lastMinisters = d.ministers || [];
        if (d.metrics) { d.tasks.success_rate = d.metrics.success_rate; }
        renderStatCards(d);
        renderMinisterTable(d);
        populateMinisterDropdown();
      })
      .catch(function() {
        document.getElementById('connectionStatus').textContent = 'Disconnected';
        document.getElementById('connectionStatus').style.color = 'var(--danger)';
      });
  }

  function fetchMetrics() {
    fetch(API + '/dashboard/metrics')
      .then(function(r) { return r.json(); })
      .then(function(m) {
        updateCharts(m);
        renderTaskList(m);
      })
      .catch(function() {});
  }

  function fetchAlerts() {
    fetch(API + '/dashboard/alerts')
      .then(function(r) { return r.json(); })
      .then(function(d) { renderAlerts(d); })
      .catch(function() {});
  }

  function fetchTaskHistory() {
    fetch(API + '/dashboard/task-history?limit=50')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        taskHistory = d.history || [];
        renderTaskList(null);
        populateMinisterDropdown();
      })
      .catch(function() {});
  }

  function fetchAlertHistory() {
    fetch(API + '/dashboard/alert-history?limit=50')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        alertHistory = d.history || [];
        renderAlerts(null);
      })
      .catch(function() {});
  }

  // ── Filtering helpers ──
  var _taskFilterTimer = null;
  var _alertFilterTimer = null;

  function debounceFilterTasks() {
    if (_taskFilterTimer) clearTimeout(_taskFilterTimer);
    _taskFilterTimer = setTimeout(filterTasks, 300);
  }

  function debounceFilterAlerts() {
    if (_alertFilterTimer) clearTimeout(_alertFilterTimer);
    _alertFilterTimer = setTimeout(filterAlerts, 300);
  }

  function filterTasks() {
    var search = (document.getElementById('taskSearch').value || '').toLowerCase();
    var minister = document.getElementById('taskMinisterFilter').value;
    var status = document.getElementById('taskStatusFilter').value;

    // Build filtered list from cached taskHistory
    var raw = taskHistory || [];
    var filtered = raw.filter(function(t) {
      if (search) {
        var prompt = (t.prompt || '').toLowerCase();
        var result = (t.result || '').toLowerCase();
        if (prompt.indexOf(search) === -1 && result.indexOf(search) === -1) return false;
      }
      if (minister && t.minister !== minister) return false;
      if (status && t.status !== status) return false;
      return true;
    });

    document.getElementById('taskCount').textContent = filtered.length;
    var el = document.getElementById('taskList');
    if (!filtered.length) {
      el.innerHTML = '<div class="empty">No matching tasks</div>';
      return;
    }
    el.innerHTML = filtered.slice(0, 50).map(function(t) {
      var statusClass = (t.status === 'completed') ? 'online' : 'offline';
      var conf = (t.confidence != null ? t.confidence : 0).toFixed(2);
      var displayId = t.task_id || '#' + t.id;
      return '<div class="task-row">' +
        '<span class="dot ' + statusClass + '"></span>' +
        '<div>' +
          '<code style="font-size:0.78rem;">' + displayId + '</code>' +
          ' &middot; <span class="task-domain">' + (t.minister || 'general') + '</span>' +
          ' &middot; conf=' + conf +
        '</div>' +
        '<span style="color:var(--text-dim);font-size:0.7rem;">' + (t.prompt || '').substring(0, 30) + '</span>' +
        '<span class="task-time">' + (t.created_at ? new Date(t.created_at).toLocaleTimeString() : '--') + '</span>' +
      '</div>';
    }).join('');
  }

  function filterAlerts() {
    var search = (document.getElementById('alertSearch').value || '').toLowerCase();
    var level = document.getElementById('alertLevelFilter').value;

    var raw = alertHistory || [];
    var filtered = raw.filter(function(a) {
      if (search) {
        var msg = (a.message || '').toLowerCase();
        var name = (a.rule_name || '').toLowerCase();
        if (msg.indexOf(search) === -1 && name.indexOf(search) === -1) return false;
      }
      if (level && a.level !== level) return false;
      return true;
    });

    document.getElementById('alertCount').textContent = filtered.length;
    var el = document.getElementById('alertsList');
    if (!filtered.length) {
      el.innerHTML = '<div class="empty">No matching alerts</div>';
      return;
    }
    el.innerHTML = filtered.slice(0, 30).map(function(a) {
      var timeStr = '--';
      if (a.timestamp) {
        timeStr = new Date(a.timestamp * 1000).toLocaleTimeString();
      } else if (a.created_at) {
        timeStr = new Date(a.created_at).toLocaleTimeString();
      }
      var sev = (a.severity || a.level || 'info').toLowerCase();
      var name = a.rule_name || 'alert';
      var msg = a.message || '';
      return '<div class="alert-item ' + sev + '">' +
        '<span class="alert-sev ' + sev + '">' + sev.toUpperCase() + '</span>' +
        '<span class="alert-msg"><strong>' + name + '</strong> &middot; ' + msg + '</span>' +
        '<span class="alert-time">' + timeStr + '</span>' +
      '</div>';
    }).join('');
  }

  function populateMinisterDropdown() {
    var sel = document.getElementById('taskMinisterFilter');
    if (!sel) return;
    var current = sel.value;
    // Collect unique minister names from taskHistory
    var names = [];
    var seen = {};
    (taskHistory || []).forEach(function(t) {
      if (t.minister && !seen[t.minister]) {
        seen[t.minister] = true;
        names.push(t.minister);
      }
    });
    // Also collect from dashboard status ministers
    if (window._lastMinisters) {
      window._lastMinisters.forEach(function(m) {
        if (m.name && !seen[m.name]) {
          seen[m.name] = true;
          names.push(m.name);
        }
      });
    }
    names.sort();
    var html = '<option value="">全部大臣</option>';
    names.forEach(function(n) {
      html += '<option value="' + n + '"' + (n === current ? ' selected' : '') + '>' + n + '</option>';
    });
    sel.innerHTML = html;
  }

  function triggerExport() {
    var fmt = window.confirm('选择导出格式：\\n\\n确定 = JSON\\n取消 = CSV');
    var format = fmt ? 'json' : 'csv';
    fetch(API + '/dashboard/export?format=' + format + '&what=all')
      .then(function(r) {
        var contentType = r.headers.get('Content-Type') || '';
        if (contentType.indexOf('text/csv') !== -1 || format === 'csv') {
          return r.text().then(function(text) {
            var blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'emperor_export.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
          });
        }
        return r.text().then(function(text) {
          var blob = new Blob([text], { type: 'application/json;charset=utf-8' });
          var url = URL.createObjectURL(blob);
          var a = document.createElement('a');
          a.href = url;
          a.download = 'emperor_export.json';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(url);
        });
      })
      .catch(function() { alert('导出失败，请检查服务是否运行'); });
  }

  // ── Control Panel actions ──
  function triggerEvolve() {
    fetch(API + '/dashboard/evolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cycles: 1 })
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.ok) { location.reload(); }
        else { alert('Evolution failed'); }
      })
      .catch(function() { alert('Evolution request failed'); });
  }

  function triggerExecute() {
    var prompt = window.prompt('Enter task prompt:');
    if (prompt === null || prompt.trim() === '') return;
    var domain = window.prompt('Enter domain (optional, default: general):', 'general');
    if (domain === null) domain = 'general';
    fetch(API + '/dashboard/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: prompt, domain: domain })
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.ok) { location.reload(); }
        else { alert('Task execution failed'); }
      })
      .catch(function() { alert('Task execution request failed'); });
  }

  function triggerHeal() {
    fetch(API + '/dashboard/heal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.ok) {
          var n = (d.actions || []).length;
          alert('Healing check complete — ' + n + ' action(s) executed');
          location.reload();
        } else {
          alert('Healing check failed');
        }
      })
      .catch(function() { alert('Healing request failed'); });
  }

  fetchStatus();
  setInterval(fetchStatus, 3000);
  fetchMetrics();
  setInterval(fetchMetrics, 3000);
  fetchAlerts();
  setInterval(fetchAlerts, 5000);
  // Load persisted history once on page load, then periodically
  fetchTaskHistory();
  setInterval(fetchTaskHistory, 10000);
  fetchAlertHistory();
  setInterval(fetchAlertHistory, 10000);
</script>
</body>
</html>"""
