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
  :root, [data-theme="dark"] {
    --bg-primary: #0b0f19;
    --bg-secondary: #12121a;
    --bg-card: rgba(20, 25, 45, 0.85);
    --bg-card-hover: #1e1e36;
    --bg: #0b0f19;
    --card-bg: rgba(20, 25, 45, 0.85);
    --card-border: rgba(255, 255, 255, 0.06);
    --text-primary: #e0e4f0;
    --text-secondary: #8892a8;
    --text-muted: #555577;
    --text: #e0e4f0;
    --text-dim: #8892a8;
    --border-color: #2a2a4a;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --accent-2: #a78bfa;
    --success: #22c55e;
    --warning: #f59e0b;
    --danger: #ef4444;
    --table-header: #16162b;
    --table-row-alt: #15152c;
    --input-bg: #0f0f23;
    --input-border: #2a2a4a;
    --shadow: 0 2px 8px rgba(0,0,0,0.4);
    --radius: 12px;
    --gap: 16px;
  }
  [data-theme="light"] {
    --bg-primary: #f5f5f9;
    --bg-secondary: #ffffff;
    --bg-card: #ffffff;
    --bg-card-hover: #f0f0f8;
    --bg: #f5f5f9;
    --card-bg: #ffffff;
    --card-border: #d4d4e8;
    --text-primary: #1a1a2e;
    --text-secondary: #555577;
    --text-muted: #8888aa;
    --text: #1a1a2e;
    --text-dim: #555577;
    --border-color: #d4d4e8;
    --accent: #6366f1;
    --accent-hover: #4f46e5;
    --accent-2: #7c3aed;
    --success: #16a34a;
    --warning: #d97706;
    --danger: #dc2626;
    --table-header: #f0f0f8;
    --table-row-alt: #f5f5fa;
    --input-bg: #ffffff;
    --input-border: #d4d4e8;
    --shadow: 0 2px 8px rgba(0,0,0,0.08);
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

  .cap-badge {
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    font-size: 0.65rem; font-weight: 600; letter-spacing: 0.5px;
    border: 1px solid rgba(167,139,250,0.25);
    margin-right: 6px; vertical-align: middle;
  }

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

  /* ── Inline task form ── */
  .task-form { background: var(--bg-card-hover); border-radius: 8px; padding: 16px;
    position: relative; margin-top: 12px; }
  .task-form textarea {
    width: 100%; min-height: 80px; background: var(--input-bg); color: var(--text);
    border: 1px solid var(--border-color); border-radius: 6px; padding: 10px 12px;
    font-family: inherit; font-size: 0.82rem; resize: vertical; outline: none;
    box-sizing: border-box; margin-bottom: 10px;
  }
  .task-form textarea:focus { border-color: rgba(108,140,255,0.4); }
  .task-form-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .task-form select {
    background: var(--input-bg); color: var(--text); border: 1px solid var(--border-color);
    border-radius: 6px; padding: 8px 12px; font-family: inherit; font-size: 0.82rem;
    cursor: pointer; outline: none; min-width: 120px;
  }
  .task-form select:focus { border-color: rgba(108,140,255,0.4); }
  .task-form .cap-hint {
    font-size: 0.75rem; color: var(--text-dim); line-height: 1.6; flex: 1;
    min-width: 200px;
  }
  .task-form .cap-hint span { margin-right: 8px; white-space: nowrap; }
  .task-form .btn-submit {
    background: var(--accent); color: #fff; border: none; border-radius: 6px;
    padding: 8px 20px; font-family: inherit; font-size: 0.85rem; cursor: pointer;
    transition: filter 0.2s; white-space: nowrap;
  }
  .task-form .btn-submit:hover { filter: brightness(1.15); }
  .task-form .btn-submit:disabled { opacity: 0.5; cursor: not-allowed; }
  .task-form .btn-clear {
    position: absolute; top: 8px; right: 8px;
    background: none; border: none; color: var(--text-dim); font-size: 1rem;
    cursor: pointer; padding: 2px 6px; border-radius: 4px; line-height: 1;
  }
  .task-form .btn-clear:hover { color: var(--danger); background: rgba(248,113,113,0.1); }
  .task-result {
    display: none; background: var(--bg-card-hover); border-left: 3px solid #66bb6a;
    border-radius: 0 6px 6px 0; padding: 12px; margin-top: 10px;
    font-size: 0.78rem; color: var(--text); position: relative;
    max-height: 120px; overflow-y: auto; white-space: pre-wrap; word-break: break-all;
  }
  .task-result .show-full {
    color: var(--accent); cursor: pointer; font-size: 0.75rem;
    display: inline-block; margin-left: 8px;
  }
  .task-result .show-full:hover { text-decoration: underline; }
  .task-form hr { border: none; border-top: 1px solid rgba(255,255,255,0.06); margin: 0 0 12px 0; }

  /* ── Ministers management panel ── */
  .ministers-panel { background: var(--bg-card-hover); border-radius: 8px; padding: 20px; margin-top: 16px; }
  .ministers-panel h3 { margin: 0 0 16px 0; color: var(--text-primary); display: flex; align-items: center; gap: 8px; }
  .minister-count { background: #4fc3f7; color: #0f0f23; border-radius: 12px; padding: 2px 10px; font-size: 13px; font-weight: bold; }
  .add-btn { background: #4fc3f7; color: #0f0f23; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: bold; float: right; }
  .ministers-table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  .ministers-table th { text-align: left; color: var(--text-secondary); font-size: 13px; padding: 8px 12px; border-bottom: 1px solid var(--border-color); }
  .ministers-table td { color: var(--text-primary); padding: 10px 12px; border-bottom: 1px solid #1f1f3a; font-size: 14px; }
  .merit-bar { background: var(--input-bg); border-radius: 4px; height: 20px; overflow: hidden; min-width: 60px; }
  .merit-fill { background: linear-gradient(90deg, #66bb6a, #4caf50); height: 100%; font-size: 11px; line-height: 20px; text-align: center; color: #fff; border-radius: 4px; }
  .action-btn { background: none; border: none; cursor: pointer; font-size: 16px; padding: 4px 8px; }
  .edit-btn { color: #4fc3f7; }
  .delete-btn { color: #e94560; }
  .domain-tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.65rem; font-weight: 600; background: rgba(108,140,255,0.12); color: var(--accent); border: 1px solid rgba(108,140,255,0.2); }

  /* ── Modal (create/edit minister) ── */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.65); z-index: 1000; display: flex; align-items: center; justify-content: center; }
  .modal-overlay.hidden { display: none; }
  .modal-box { background: var(--bg-card-hover); border-radius: 12px; padding: 24px; width: 400px; max-width: 90vw; box-shadow: var(--shadow); border: 1px solid var(--border-color); }
  .modal-box h3 { color: var(--text-primary); margin: 0 0 20px 0; font-size: 1.1rem; }
  .modal-box label { display: block; color: var(--text-secondary); font-size: 0.78rem; margin-bottom: 4px; margin-top: 12px; }
  .modal-box input, .modal-box select { width: 100%; padding: 8px 12px; background: var(--input-bg); color: var(--text-primary); border: 1px solid var(--border-color); border-radius: 6px; font-family: inherit; font-size: 0.85rem; outline: none; box-sizing: border-box; }
  .modal-box input:focus, .modal-box select:focus { border-color: #4fc3f7; }
  .modal-actions { display: flex; gap: 10px; margin-top: 20px; justify-content: flex-end; }
  .modal-actions button { padding: 8px 20px; border-radius: 6px; font-family: inherit; font-size: 0.85rem; cursor: pointer; border: none; }
  .modal-actions .btn-save { background: #4fc3f7; color: #0f0f23; font-weight: bold; }
  .modal-actions .btn-cancel { background: var(--border-color); color: var(--text-secondary); }
  .modal-error { color: var(--danger); font-size: 0.78rem; margin-top: 8px; display: none; }

  /* ── Confirm dialog overlay ── */
  .confirm-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 1001; display: flex; align-items: center; justify-content: center; }
  .confirm-overlay.hidden { display: none; }
  .confirm-box { background: var(--bg-card-hover); border-radius: 12px; padding: 24px; width: 360px; max-width: 90vw; text-align: center; box-shadow: var(--shadow); border: 1px solid var(--danger); }
  .confirm-box p { color: var(--text-primary); font-size: 0.95rem; margin-bottom: 20px; }
  .confirm-box strong { color: var(--danger); }
  .confirm-actions { display: flex; gap: 10px; justify-content: center; }
  .confirm-actions button { padding: 8px 24px; border-radius: 6px; font-family: inherit; font-size: 0.85rem; cursor: pointer; border: none; }
  .confirm-actions .btn-confirm-yes { background: var(--danger); color: #fff; font-weight: bold; }
  .confirm-actions .btn-confirm-no { background: var(--border-color); color: var(--text-secondary); }

  /* ── Scheduler config panel ── */
  .scheduler-config-panel { background: var(--bg-card-hover); border-radius: 8px; padding: 20px; margin-top: 16px; }
  .scheduler-config-panel h3 { margin: 0 0 16px 0; color: var(--text-primary); }
  .config-row { display: flex; align-items: center; margin-bottom: 12px; gap: 12px; }
  .config-row label { color: var(--text-secondary); font-size: 14px; min-width: 80px; }
  .config-row input[type="number"] { background: var(--input-bg); color: var(--text-primary); border: 1px solid var(--border-color); border-radius: 4px; padding: 6px 10px; width: 80px; }
  .config-hint { color: var(--text-muted); font-size: 12px; }
  .toggle-switch { position: relative; width: 48px; height: 24px; cursor: pointer; display: inline-block; }
  .toggle-switch input { display: none; }
  .toggle-slider { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: #3a3a5a; border-radius: 24px; transition: 0.3s; }
  .toggle-slider::before { content: ''; position: absolute; height: 18px; width: 18px; left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: 0.3s; }
  .toggle-switch input:checked + .toggle-slider { background: var(--success); }
  .toggle-switch input:checked + .toggle-slider::before { transform: translateX(24px); }
  .save-btn { background: #4fc3f7; color: #0f0f23; border: none; padding: 8px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; margin-top: 8px; }
  .save-success { color: var(--success); font-size: 13px; margin-left: 12px; display: none; }
  .theme-btn {
    background: none; border: 1px solid var(--border-color); color: var(--text-primary);
    font-size: 18px; cursor: pointer; padding: 6px 10px; border-radius: 6px;
    margin-right: 8px; transition: border-color 0.2s;
  }
  .theme-btn:hover { border-color: var(--accent); }

  /* ── Dashboard Grid Layout ── */
  .dashboard-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--gap);
    margin-bottom: var(--gap);
  }
  .panel-full { grid-column: 1 / -1; }

  /* Adaptive .panel for grid children */
  .panel, .ministers-panel, .scheduler-config-panel {
    margin-bottom: 0;
  }

  /* ── Panel header with collapse button ── */
  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-color);
    flex-shrink: 0;
  }
  .panel-header h2, .panel-header h3 {
    margin: 0;
    font-size: 0.82rem;
    text-transform: uppercase;
    color: var(--text-dim);
    letter-spacing: 1px;
  }
  .panel-collapse-btn {
    background: none;
    border: none;
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 14px;
    padding: 2px 8px;
    border-radius: 4px;
    transition: transform 0.25s;
    line-height: 1;
  }
  .panel-collapse-btn:hover {
    color: var(--text-primary);
    background: var(--bg-card-hover);
  }
  .panel-collapsed .panel-body {
    display: none;
  }
  .panel-collapsed .panel-header {
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
  }
  .panel-collapsed .panel-collapse-btn {
    transform: rotate(-90deg);
  }

  /* ── Health monitoring panel ── */
  .health-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 14px;
    text-align: center;
  }
  .health-label {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    margin-bottom: 4px;
  }
  .health-value {
    font-size: 28px;
    font-weight: 700;
    line-height: 1.1;
  }
  .health-bar {
    margin-top: 6px;
    height: 6px;
    background: var(--bg-primary);
    border-radius: 3px;
    overflow: hidden;
  }
  .health-bar-fill {
    height: 100%;
    border-radius: 3px;
    background: var(--accent);
    width: 0%;
    transition: width 0.5s;
  }
  .health-detail {
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 4px;
  }

  /* ── Responsive layout ── */
  /* Tablet portrait (≤1024px): two-column */
  @media (max-width: 1024px) {
    .dashboard-grid {
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .panel-full {
      grid-column: 1 / -1;
    }
    .stats-row {
      flex-wrap: wrap;
      gap: 8px;
    }
    .stat-card {
      flex: 1 1 calc(50% - 8px);
      min-width: 140px;
    }
  }

  /* Mobile (≤768px): single column */
  @media (max-width: 768px) {
    .dashboard-grid {
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .panel-header {
      flex-direction: column;
      align-items: flex-start;
      gap: 8px;
    }
    .table-wrap {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    .table-wrap table {
      min-width: 600px;
    }
    .minister-actions {
      flex-wrap: wrap;
    }
    .modal-box {
      width: 95vw;
      max-width: 95vw;
      margin: 2vh auto;
      max-height: 90vh;
    }
    .btn, button {
      padding: 6px 12px;
      font-size: 13px;
    }
    .task-form textarea,
    .task-form select {
      width: 100%;
      box-sizing: border-box;
    }
    .grid-stats {
      grid-template-columns: 1fr;
    }
    .grid-charts {
      grid-template-columns: 1fr;
    }
    .health-grid {
      grid-template-columns: repeat(2, 1fr) !important;
    }
  }

  /* Large screen (≥1400px): three-column */
  @media (min-width: 1400px) {
    .dashboard-grid {
      grid-template-columns: 1fr 1fr 1fr;
    }
    .panel-full {
      grid-column: 1 / -1;
    }
  }
</style>
</head>
<body>

<div class="header">
  <h1>Emperor Evolution Dashboard</h1>
  <div>
    <button id="theme-toggle" class="theme-btn" onclick="cycleTheme()" title="切换主题">\u263E</button>
    <span class="badge refresh" id="connectionStatus">Connecting...</span>
    <span class="badge" style="margin-left:8px;" id="lastUpdate"></span>
  </div>
</div>

<div class="dashboard-grid">

<!-- Top stat cards -->
<div class="panel-full grid grid-stats" id="statCards"></div>

<!-- 系统健康面板 -->
<div class="panel panel-full" id="panel-health">
  <div class="panel-header">
    <h2>系统健康</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-health')">▼</button>
    <span class="panel-actions" style="display:flex;gap:8px;">
      <span id="health-uptime" style="color:var(--text-secondary);font-size:13px;">--</span>
    </span>
  </div>
  <div class="panel-body">
    <div class="health-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;">
      <div class="health-card" id="hc-cpu">
        <div class="health-label">CPU</div>
        <div class="health-value">--%</div>
        <div class="health-bar">
          <div class="health-bar-fill" style="width:0%;"></div>
        </div>
      </div>
      <div class="health-card" id="hc-memory">
        <div class="health-label">内存</div>
        <div class="health-value">--%</div>
        <div class="health-bar">
          <div class="health-bar-fill" style="width:0%;"></div>
        </div>
        <div class="health-detail">-- / -- GB</div>
      </div>
      <div class="health-card" id="hc-disk">
        <div class="health-label">磁盘</div>
        <div class="health-value">--%</div>
        <div class="health-bar">
          <div class="health-bar-fill" style="width:0%;"></div>
        </div>
        <div class="health-detail">-- / -- GB</div>
      </div>
      <div class="health-card" id="hc-uptime">
        <div class="health-label">运行时长</div>
        <div class="health-value">--</div>
        <div class="health-detail">Python 3.x</div>
      </div>
    </div>
  </div>
</div>

<!-- 实时天气小部件 -->
<div class="panel" id="panel-weather" style="min-width:0;">
  <div class="panel-header">
    <h2>实时天气</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-weather')">▼</button>
  </div>
  <div class="panel-body" id="weather-body">
    <div style="text-align:center;padding:10px 0;">
      <div id="weather-city" style="font-size:14px;color:var(--text-secondary);margin-bottom:4px;">--</div>
      <div id="weather-temp" style="font-size:40px;font-weight:700;">--°C</div>
      <div id="weather-desc" style="font-size:14px;color:var(--text-secondary);margin-top:2px;">--</div>
      <div style="display:flex;justify-content:center;gap:20px;margin-top:12px;font-size:13px;color:var(--text-secondary);">
        <span>湿度: <span id="weather-humidity">--</span>%</span>
        <span>风力: <span id="weather-wind">--</span></span>
        <span>降水: <span id="weather-precip">--</span>%</span>
      </div>
    </div>
  </div>
</div>

<!-- 新闻头条小部件 -->
<div class="panel" id="panel-news" style="min-width:0;">
  <div class="panel-header">
    <h2>科技新闻</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-news')">▼</button>
    <span class="panel-actions">
      <button onclick="refreshLive()" style="background:none;border:1px solid var(--border-color);color:var(--text-primary);border-radius:4px;padding:2px 8px;font-size:12px;cursor:pointer;">刷新</button>
    </span>
  </div>
  <div class="panel-body" id="news-body">
    <ul id="news-list" style="list-style:none;padding:0;margin:0;">
      <li style="padding:8px 0;color:var(--text-muted);text-align:center;">加载中...</li>
    </ul>
  </div>
</div>

<!-- Control Panel -->
<div class="card panel-full" id="panel-controls">
  <div class="panel-header">
    <h2>控制面板</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-controls')">▼</button>
  </div>
  <div class="panel-body">
  <div style="display:flex;gap:12px;flex-wrap:wrap;">
    <button id="btnEvolve" onclick="triggerEvolve()" style="background:#e94560;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-family:inherit;font-size:0.85rem;cursor:pointer;transition:filter 0.2s;">进化</button>
    <button id="btnHeal" onclick="triggerHeal()" style="background:#e94560;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-family:inherit;font-size:0.85rem;cursor:pointer;transition:filter 0.2s;">自愈检查</button>
    <button id="btnExport" onclick="triggerExport()" style="background:var(--card-bg);color:var(--accent);border:1px solid rgba(108,140,255,0.3);border-radius:8px;padding:10px 20px;font-family:inherit;font-size:0.85rem;cursor:pointer;transition:all 0.2s;">导出数据</button>
  </div>
  <hr>
  <div class="task-form">
    <button class="btn-clear" onclick="clearTaskForm()" title="清空">&times;</button>
    <textarea id="task-prompt" placeholder="输入任务描述...支持自然语言，如：计算圆周率前20位？生成一个UUID" oninput="updateCapHint()"></textarea>
    <div class="task-form-row">
      <select id="task-domain" onchange="updateCapHint()">
        <option value="general">general</option>
        <option value="math">math</option>
        <option value="data">data</option>
        <option value="code">code</option>
        <option value="legal">legal</option>
        <option value="science">science</option>
        <option value="creative">creative</option>
      </select>
      <div class="cap-hint" id="cap-hint"></div>
      <button class="btn-submit" id="task-submit-btn" onclick="submitManualTask()">派遣任务</button>
    </div>
    <div class="task-result" id="task-result"></div>
  </div><!-- .panel-body -->
</div>

<!-- Ministers Management Panel -->
<div class="ministers-panel" id="panel-ministers">
  <div class="panel-header">
    <h2>大臣管理 <span class="minister-count" id="ministerCount">0</span></h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-ministers')">▼</button>
  </div>
  <div class="panel-body">
  <button class="add-btn" onclick="openCreateModal()">新建大臣</button>
  <div style="clear:both;"></div>
  <table class="ministers-table">
    <thead>
      <tr>
        <th>Name</th><th>领域</th><th>功绩(Merit)</th><th>稳定度</th><th>状态</th><th>操作</th>
      </tr>
    </thead>
    <tbody id="ministers-tbody"></tbody>
  </table>
  </div><!-- .panel-body -->
</div>

<!-- Scheduler Config Panel -->
<div class="scheduler-config-panel" id="panel-scheduler">
  <div class="panel-header">
    <h2>调度配置</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-scheduler')">▼</button>
  </div>
  <div class="panel-body">

  <div class="config-row">
    <label for="evolve-interval">进化间隔</label>
    <input type="number" id="evolve-interval" min="1" max="1440" value="5">
    <span class="config-hint">分钟 (1-1440)</span>
  </div>

  <div class="config-row">
    <label for="task-interval">任务间隔</label>
    <input type="number" id="task-interval" min="1" max="1440" value="3">
    <span class="config-hint">分钟 (1-1440)</span>
  </div>

  <div class="config-row">
    <label>自动调度</label>
    <label class="toggle-switch">
      <input type="checkbox" id="auto-schedule-toggle" onchange="updateToggleLabel()">
      <span class="toggle-slider"></span>
    </label>
    <span id="toggle-label" style="font-size:14px;">关</span>
  </div>

  <div style="display:flex;align-items:center;">
    <button class="save-btn" onclick="saveSchedulerConfig()">保存配置</button>
    <span class="save-success" id="save-success">✓ 配置已保存</span>
  </div>
  </div><!-- .panel-body -->
</div>

<!-- Time-series charts row -->
<div class="panel-full" id="panel-charts">
  <div class="panel-header" style="margin-bottom:12px;">
    <h2>进化趋势</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-charts')">▼</button>
  </div>
  <div class="panel-body">
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
  </div><!-- .panel-body -->
</div>

<!-- Minister leaderboard -->
<div class="table-wrap" id="panel-leaderboard">
  <div class="panel-header">
    <h2>大臣排行榜</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-leaderboard')">▼</button>
  </div>
  <div class="panel-body">
  <table>
    <thead>
      <tr>
        <th>Rank</th><th>Minister</th><th>Domain</th><th>Merit</th>
        <th>Confidence</th><th>Tasks</th><th>Success</th><th>Status</th>
      </tr>
    </thead>
    <tbody id="ministerTable"></tbody>
  </table>
  </div><!-- .panel-body -->
</div>

<!-- 能力命中统计饼图 -->
<div class="panel" id="panel-capability-stats" style="min-width:0;">
  <div class="panel-header">
    <h2>能力统计</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-capability-stats')">▼</button>
  </div>
  <div class="panel-body">
    <div id="capability-chart" style="width:100%;height:280px;"></div>
    <div id="capability-legend" style="padding:8px 12px;font-size:12px;color:var(--text-secondary);text-align:center;"></div>
  </div>
</div>

<!-- Recent tasks panel -->
<div class="panel" id="panel-tasks">
  <div class="panel-header">
    <h2>Recent Tasks <span class="count" id="taskCount">0</span></h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-tasks')">▼</button>
  </div>
  <div class="panel-body">
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
  </div><!-- .panel-body -->
</div>

<!-- Alerts panel -->
<div class="panel" id="panel-alerts">
  <div class="panel-header">
    <h2>Active Alerts & Notifications <span class="count" id="alertCount">0</span></h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-alerts')">▼</button>
  </div>
  <div class="panel-body">
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
  </div><!-- .panel-body -->
</div>

<!-- Evals 评测面板 -->
<div class="panel" id="panel-evals">
  <div class="panel-header">
    <h2>Evals 评测</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-evals')">▼</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
      <button onclick="runEvals()" id="evals-run-btn" class="btn btn-sm" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;">Run All Evals</button>
      <span id="evals-status" style="font-size:11px;color:var(--text-muted);"></span>
    </div>
    <div id="evals-summary" style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
      <div class="mini-stat"><span class="mini-stat-val" id="evals-total">--</span><span class="mini-stat-label">Total</span></div>
      <div class="mini-stat"><span class="mini-stat-val" id="evals-passed" style="color:var(--success);">--</span><span class="mini-stat-label">Passed</span></div>
      <div class="mini-stat"><span class="mini-stat-val" id="evals-failed" style="color:var(--danger);">--</span><span class="mini-stat-label">Failed</span></div>
      <div class="mini-stat"><span class="mini-stat-val" id="evals-errored" style="color:var(--warning);">--</span><span class="mini-stat-label">Errored</span></div>
      <div class="mini-stat"><span class="mini-stat-val" id="evals-rate">--</span><span class="mini-stat-label">Pass Rate</span></div>
    </div>
    <div id="evals-suites" style="max-height:360px;overflow-y:auto;font-size:12px;"></div>
  </div><!-- .panel-body -->
</div>

<!-- Audit 审计追踪面板 -->
<div class="panel panel-full" id="panel-audit">
  <div class="panel-header">
    <h2>Audit 审计追踪</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-audit')">▼</button>
  </div>
  <div class="panel-body">
    <div class="tab-bar" style="display:flex;gap:2px;margin-bottom:12px;border-bottom:1px solid var(--card-border);">
      <button class="audit-tab active" onclick="switchAuditTab('recent')" id="audit-tab-recent" style="background:none;border:none;color:var(--accent);padding:6px 14px;cursor:pointer;font-family:inherit;font-size:0.78rem;border-bottom:2px solid var(--accent);margin-bottom:-1px;">Recent Events</button>
      <button class="audit-tab" onclick="switchAuditTab('failures')" id="audit-tab-failures" style="background:none;border:none;color:var(--text-secondary);padding:6px 14px;cursor:pointer;font-family:inherit;font-size:0.78rem;border-bottom:2px solid transparent;margin-bottom:-1px;">Failures</button>
      <button class="audit-tab" onclick="switchAuditTab('stats')" id="audit-tab-stats" style="background:none;border:none;color:var(--text-secondary);padding:6px 14px;cursor:pointer;font-family:inherit;font-size:0.78rem;border-bottom:2px solid transparent;margin-bottom:-1px;">Stats</button>
    </div>
    <div id="audit-tab-content" style="font-size:12px;max-height:400px;overflow-y:auto;">
      <div class="empty">Loading...</div>
    </div>
  </div><!-- .panel-body -->
</div>

<!-- 服务流水线面板 -->
<div class="panel" id="panel-pipelines" style="min-width:0;">
  <div class="panel-header">
    <h2>服务流水线</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-pipelines')">&#9660;</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
      <button onclick="executePipeline('daily_brief')" class="btn btn-sm" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;">每日简报</button>
      <button onclick="executePipeline('health_check')" class="btn btn-sm" style="background:var(--success);color:#fff;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;">健康检查</button>
      <button onclick="executePipeline('search_analyze')" class="btn btn-sm" style="background:var(--warning);color:#000;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;">搜索分析</button>
    </div>
    <div id="pipeline-output" style="font-size:12px;max-height:300px;overflow-y:auto;background:var(--bg-secondary);border-radius:4px;padding:10px;">
      <div style="color:var(--text-muted);text-align:center;">点击上方按钮执行服务流水线</div>
    </div>
    <div style="margin-top:8px;">
      <div style="font-size:11px;color:var(--text-secondary);">执行历史</div>
      <div id="pipeline-history" style="font-size:11px;margin-top:4px;max-height:120px;overflow-y:auto;"></div>
    </div>
    <!-- 定时调度区 -->
    <div style="margin-top:16px;border-top:1px solid var(--border-color);padding-top:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <span style="font-size:12px;font-weight:600;color:var(--text-secondary);">定时调度</span>
        <button onclick="showScheduleDialog()" style="background:var(--accent);color:#fff;border:none;border-radius:3px;padding:2px 10px;font-size:11px;cursor:pointer;">+ 添加</button>
      </div>
      <div id="pipeline-schedules" style="font-size:11px;">
        <div style="color:var(--text-muted);">加载中...</div>
      </div>
    </div>
  </div><!-- .panel-body -->
</div>

</div><!-- .dashboard-grid -->

<div class="footer">
  Emperor Core &middot; Auto-refresh every 3s &middot; <span id="footerCycle">--</span>
</div>

<!-- Create/Edit Minister Modal -->
<div class="modal-overlay hidden" id="ministerModal">
  <div class="modal-box">
    <h3 id="modalTitle">新建大臣</h3>
    <div id="modalBody"></div>
    <div class="modal-error" id="modalError"></div>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeModal()">取消</button>
      <button class="btn-save" id="modalSaveBtn">创建</button>
    </div>
  </div>
</div>

<!-- Delete Confirm Dialog -->
<div class="confirm-overlay hidden" id="confirmDialog">
  <div class="confirm-box">
    <p>确认删除大臣 <strong id="confirmName"></strong> ?</p>
    <p style="font-size:0.78rem;color:#8892b0;">此操作不可撤销</p>
    <div class="confirm-actions">
      <button class="btn-confirm-no" onclick="closeConfirm()">取消</button>
      <button class="btn-confirm-yes" id="confirmYesBtn">确认删除</button>
    </div>
  </div>
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

  // ── Theme management ──
  var currentTheme = 'dark';

  function applyTheme(theme) {
    if (theme === 'auto') {
      var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
    } else {
      document.documentElement.setAttribute('data-theme', theme);
    }
    currentTheme = theme;
    updateThemeButton();
  }

  function updateThemeButton() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var icons = { dark: '\\u263E', light: '\\u2600', auto: '\\u21C5' };
    btn.textContent = icons[currentTheme] || '\\u263E';
    btn.title = '\u4e3b\u9898: ' + currentTheme + ' (\u70b9\u51fb\u5207\u6362)';
  }

  function cycleTheme() {
    var themes = ['dark', 'light', 'auto'];
    var nextIdx = (themes.indexOf(currentTheme) + 1) % themes.length;
    var nextTheme = themes[nextIdx];
    fetch(API + '/theme', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: nextTheme }),
    }).then(function() {
      // success
    }).catch(function() {
      // ignore
    });
    applyTheme(nextTheme);
  }

  // Listen for OS-level theme changes (only in auto mode)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
    if (currentTheme === 'auto') { applyTheme('auto'); }
  });

  function initTheme() {
    fetch(API + '/config')
      .then(function(r) { return r.json(); })
      .then(function(cfg) { applyTheme(cfg.theme || 'dark'); })
      .catch(function() { applyTheme('dark'); });
  }

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

  // ── Capability badge color map ──
  var CAP_COLORS = {
    // 工具类 → 蓝色
    file_info: '#4fc3f7', hash: '#4fc3f7', json_tool: '#4fc3f7', uuid_gen: '#4fc3f7',
    // 计算类 → 绿色
    math: '#66bb6a', random: '#66bb6a',
    // 文本类 → 橙色
    text: '#ffa726', datetime: '#ffa726',
    // 网络类 → 深橙色
    web_search: '#ff6d00', web_fetch: '#ff6d00'
  };

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
      // Check for capability result marker
      var capMatch = (t.result || '').match(/\[能力结果:\s*(\w+)\]/);
      var capBadge = '';
      if (capMatch) {
        var capName = capMatch[1];
        var capColor = CAP_COLORS[capName] || '#a78bfa';
        capBadge = '<span class="cap-badge" style="background:' + capColor + '22;color:' + capColor + ';border-color:' + capColor + '44;">' + capName + '</span>';
      }
      return '<div class="task-row">' +
        '<span class="dot ' + statusClass + '"></span>' +
        '<div>' +
          capBadge +
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

  // ── Inline task form ──
  function updateCapHint() {
    var domain = document.getElementById('task-domain').value;
    var hint = document.getElementById('cap-hint');
    // Map capabilities by domain using CAP_COLORS keys
    var domainCaps = {
      general: ['datetime', 'text', 'uuid_gen', 'web_search', 'web_fetch'],
      math: ['math', 'random'],
      data: ['json_tool', 'hash', 'web_search', 'web_fetch'],
      code: ['file_info', 'hash', 'json_tool', 'uuid_gen', 'web_fetch'],
      legal: [],
      science: [],
      creative: []
    };
    var caps = domainCaps[domain] || [];
    if (caps.length === 0) {
      hint.innerHTML = '<span style="color:#8892a8;">该领域暂无内置能力</span>';
      return;
    }
    hint.innerHTML = caps.map(function(c) {
      var color = CAP_COLORS[c] || '#a78bfa';
      return '<span style="color:' + color + ';">' + c + '</span>';
    }).join('');
  }

  async function submitManualTask() {
    var prompt = document.getElementById('task-prompt').value.trim();
    if (!prompt) return;

    var domain = document.getElementById('task-domain').value;
    var btn = document.getElementById('task-submit-btn');
    btn.disabled = true;
    btn.textContent = '执行中...';

    try {
      var res = await fetch(API + '/api/manual_task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt, domain: domain })
      });
      var data = await res.json();
      if (!res.ok) {
        showTaskResult(data.detail || '执行失败');
      } else {
        showTaskResult(data.report || '');
      }
      // Reload panels after a short delay
      setTimeout(function() { fetchStatus(); fetchMetrics(); fetchTaskHistory(); }, 500);
    } catch (e) {
      showTaskResult('执行失败: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '派遣任务';
    }
  }

  function showTaskResult(report) {
    var container = document.getElementById('task-result');
    if (!report) {
      container.innerHTML = '(空结果)';
      container.style.display = 'block';
      container.dataset.full = '(空结果)';
      return;
    }
    var truncated = report.length > 200
      ? report.slice(0, 200) + '... <span class="show-full" onclick="this.parentElement.innerHTML=this.parentElement.dataset.full;">查看详情 →</span>'
      : report;
    container.innerHTML = truncated;
    container.style.display = 'block';
    container.dataset.full = report;
  }

  function clearTaskForm() {
    document.getElementById('task-prompt').value = '';
    document.getElementById('task-result').style.display = 'none';
    document.getElementById('task-result').innerHTML = '';
    updateCapHint();
  }

  // Initialize cap hint on load
  updateCapHint();

  // ── Ministers management ──
  var ministers = [];
  var _editingMinister = null;
  var _deletingMinister = null;

  async function loadMinisters() {
    try {
      var res = await fetch(API + '/api/ministers');
      if (!res.ok) return;
      var data = await res.json();
      ministers = data.ministers || [];
      renderMinistersTable();
      updateMinisterCount();
    } catch (e) {}
  }

  function renderMinistersTable() {
    var tbody = document.getElementById('ministers-tbody');
    if (!tbody) return;
    if (!ministers.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#8892b0;padding:24px;">暂无大臣</td></tr>';
      return;
    }
    tbody.innerHTML = ministers.map(function(m) {
      var stabColor = (m.stability > 0.8 ? '#66bb6a' : m.stability > 0.5 ? '#ffa726' : '#e94560');
      var statusHtml = '--';
      if (m.success_streak >= 3) {
        statusHtml = '<span style="color:#66bb6a;">\uD83D\uDD25 ' + m.success_streak + '</span>';
      } else if (m.failure_streak >= 3) {
        statusHtml = '<span style="color:#e94560;">\u26A0 ' + m.failure_streak + '</span>';
      }
      return '<tr>' +
        '<td><strong>' + (m.name || '?') + '</strong></td>' +
        '<td><span class="domain-tag">' + (m.domain || 'general') + '</span></td>' +
        '<td><div class="merit-bar"><div class="merit-fill" style="width:' + Math.min(m.merit || 0, 100) + '%">' + (m.merit || 0) + '</div></div></td>' +
        '<td style="color:' + stabColor + '">' + ((m.stability || 0).toFixed(2)) + '</td>' +
        '<td>' + statusHtml + '</td>' +
        '<td>' +
          '<button class="action-btn edit-btn" onclick="openEditModal(\'' + m.name + '\')" title="编辑">\u270E</button>' +
          '<button class="action-btn delete-btn" onclick="confirmDelete(\'' + m.name + '\')" title="删除">\u2715</button>' +
        '</td>' +
      '</tr>';
    }).join('');
  }

  function updateMinisterCount() {
    var el = document.getElementById('ministerCount');
    if (el) el.textContent = ministers.length;
  }

  // ── Create Modal ──
  function openCreateModal() {
    _editingMinister = null;
    document.getElementById('modalTitle').textContent = '新建大臣';
    document.getElementById('modalBody').innerHTML =
      '<label>名称</label>' +
      '<input type="text" id="modalName" placeholder="输入大臣名称..." value="">' +
      '<label>领域</label>' +
      '<select id="modalDomain">' +
        '<option value="general">general</option>' +
        '<option value="math">math</option>' +
        '<option value="data">data</option>' +
        '<option value="code">code</option>' +
        '<option value="legal">legal</option>' +
        '<option value="science">science</option>' +
        '<option value="creative">creative</option>' +
      '</select>';
    document.getElementById('modalSaveBtn').textContent = '创建';
    document.getElementById('modalSaveBtn').onclick = submitCreate;
    document.getElementById('modalError').style.display = 'none';
    document.getElementById('ministerModal').classList.remove('hidden');
  }

  async function submitCreate() {
    var name = document.getElementById('modalName').value.trim();
    var domain = document.getElementById('modalDomain').value;
    var err = document.getElementById('modalError');
    if (!name) { err.textContent = '名称不能为空'; err.style.display = 'block'; return; }
    try {
      var res = await fetch(API + '/api/ministers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, domain: domain })
      });
      var data = await res.json();
      if (!res.ok) { err.textContent = data.detail || '创建失败'; err.style.display = 'block'; return; }
      closeModal();
      await loadMinisters();
    } catch (e) { err.textContent = '创建失败: ' + e.message; err.style.display = 'block'; }
  }

  // ── Edit Modal ──
  function openEditModal(name) {
    var m = ministers.find(function(x) { return x.name === name; });
    if (!m) return;
    _editingMinister = name;
    document.getElementById('modalTitle').textContent = '编辑大臣 - ' + name;
    document.getElementById('modalBody').innerHTML =
      '<label>领域</label>' +
      '<select id="modalDomain">' +
        '<option value="general"' + (m.domain === 'general' ? ' selected' : '') + '>general</option>' +
        '<option value="math"' + (m.domain === 'math' ? ' selected' : '') + '>math</option>' +
        '<option value="data"' + (m.domain === 'data' ? ' selected' : '') + '>data</option>' +
        '<option value="code"' + (m.domain === 'code' ? ' selected' : '') + '>code</option>' +
        '<option value="legal"' + (m.domain === 'legal' ? ' selected' : '') + '>legal</option>' +
        '<option value="science"' + (m.domain === 'science' ? ' selected' : '') + '>science</option>' +
        '<option value="creative"' + (m.domain === 'creative' ? ' selected' : '') + '>creative</option>' +
      '</select>' +
      '<label>功绩 (0-100)</label>' +
      '<input type="number" id="modalMerit" min="0" max="100" value="' + (m.merit || 0) + '">' +
      '<label>稳定度 (0-1)</label>' +
      '<input type="number" id="modalStability" min="0" max="1" step="0.01" value="' + (m.stability || 0.75).toFixed(2) + '">';
    document.getElementById('modalSaveBtn').textContent = '保存';
    document.getElementById('modalSaveBtn').onclick = submitEdit;
    document.getElementById('modalError').style.display = 'none';
    document.getElementById('ministerModal').classList.remove('hidden');
  }

  async function submitEdit() {
    var domain = document.getElementById('modalDomain').value;
    var merit = parseFloat(document.getElementById('modalMerit').value);
    var stability = parseFloat(document.getElementById('modalStability').value);
    var err = document.getElementById('modalError');
    if (!_editingMinister) return;
    try {
      var res = await fetch(API + '/api/ministers/' + _editingMinister, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: domain, merit: merit, stability: stability })
      });
      var data = await res.json();
      if (!res.ok) { err.textContent = data.detail || '保存失败'; err.style.display = 'block'; return; }
      closeModal();
      await loadMinisters();
    } catch (e) { err.textContent = '保存失败: ' + e.message; err.style.display = 'block'; }
  }

  function closeModal() {
    document.getElementById('ministerModal').classList.add('hidden');
    _editingMinister = null;
  }

  // ── Delete Confirm ──
  function confirmDelete(name) {
    _deletingMinister = name;
    document.getElementById('confirmName').textContent = name;
    document.getElementById('confirmYesBtn').onclick = deleteMinister;
    document.getElementById('confirmDialog').classList.remove('hidden');
  }

  function closeConfirm() {
    document.getElementById('confirmDialog').classList.add('hidden');
    _deletingMinister = null;
  }

  async function deleteMinister() {
    if (!_deletingMinister) return;
    try {
      await fetch(API + '/api/ministers/' + _deletingMinister, { method: 'DELETE' });
    } catch (e) {}
    closeConfirm();
    await loadMinisters();
  }

  // ═══ SSE real-time updates ════════════════════════════════════
  var eventSource = null;

  function connectSSE() {
    if (eventSource) {
      eventSource.close();
    }
    eventSource = new EventSource(API + '/api/events');
    eventSource.onmessage = function(event) {
      try {
        var msg = JSON.parse(event.data);
        handleSSEEvent(msg);
      } catch(e) {}
    };
    eventSource.onerror = function() {
      // Connection lost, reconnect after 3s
      setTimeout(connectSSE, 3000);
    };
  }

  function handleSSEEvent(msg) {
    switch(msg.type) {
      case 'task_completed':
        fetchTaskHistory();
        loadMinisters();
        break;
      case 'evolution':
        loadMeritBoard();
        updateCharts();
        loadMinisters();
        break;
      case 'alert':
        fetchAlertHistory();
        break;
      case 'heartbeat':
      case 'connected':
        // Keep-alive, no refresh needed
        break;
    }
  }

  // Load ministers on page load and periodic refresh
  loadMinisters();
  setInterval(loadMinisters, 30000);

  // ── Scheduler configuration ──
  async function loadSchedulerConfig() {
    try {
      var res = await fetch(API + '/api/scheduler/config');
      if (!res.ok) return;
      var cfg = await res.json();
      document.getElementById('evolve-interval').value = cfg.evolve_interval_minutes;
      document.getElementById('task-interval').value = cfg.task_interval_minutes;
      document.getElementById('auto-schedule-toggle').checked = cfg.auto_schedule;
      updateToggleLabel();
    } catch(e) {}
  }

  async function saveSchedulerConfig() {
    var ei = document.getElementById('evolve-interval');
    var ti = document.getElementById('task-interval');
    var cfg = {
      evolve_interval_minutes: parseInt(ei.value),
      task_interval_minutes: parseInt(ti.value),
      auto_schedule: document.getElementById('auto-schedule-toggle').checked
    };

    try {
      var res = await fetch(API + '/api/scheduler/config', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(cfg)
      });
      if (!res.ok) {
        var data = await res.json();
        alert('保存失败: ' + (data.detail || '未知错误'));
        // Revert to current API values
        loadSchedulerConfig();
        return;
      }
      var success = document.getElementById('save-success');
      success.style.display = 'inline';
      setTimeout(function() { success.style.display = 'none'; }, 3000);
    } catch(e) {
      alert('保存失败: ' + e.message);
    }
  }

  function updateToggleLabel() {
    var checked = document.getElementById('auto-schedule-toggle').checked;
    var label = document.getElementById('toggle-label');
    label.textContent = checked ? '开' : '关';
    label.style.color = checked ? '#66bb6a' : '#888';
  }

  loadSchedulerConfig();

  // ═══ Health monitoring ═══════════════════════════════════════

  async function refreshHealth() {
    try {
      var resp = await fetch(API + '/api/health');
      var data = await resp.json();

      // CPU
      var cpuPct = data.cpu_percent;
      updateHealthCard('hc-cpu', cpuPct, cpuPct >= 0 ? cpuPct + '%' : '--%');

      // 内存
      var mem = data.memory;
      updateHealthCard('hc-memory', mem.percent, mem.percent >= 0 ? mem.percent + '%' : '--%');
      var memDetail = document.querySelector('#hc-memory .health-detail');
      if (memDetail && mem.used_gb >= 0) {
        memDetail.textContent = mem.used_gb + ' / ' + mem.total_gb + ' GB';
      }

      // 磁盘
      var disk = data.disk;
      updateHealthCard('hc-disk', disk.percent, disk.percent >= 0 ? disk.percent + '%' : '--%');
      var diskDetail = document.querySelector('#hc-disk .health-detail');
      if (diskDetail && disk.used_gb >= 0) {
        diskDetail.textContent = disk.used_gb + ' / ' + disk.total_gb + ' GB';
      }

      // 运行时长
      var uptimeEl = document.querySelector('#hc-uptime .health-value');
      if (uptimeEl) uptimeEl.textContent = data.uptime || '--';
      var uptimeDetail = document.querySelector('#hc-uptime .health-detail');
      if (uptimeDetail) {
        uptimeDetail.textContent = data.python ? 'Python ' + data.python : '';
      }

      // 顶部运行时长
      var healthUptime = document.getElementById('health-uptime');
      if (healthUptime) healthUptime.textContent = '运行 ' + (data.uptime || '--');

    } catch (e) {
      // 静默失败
    }
  }

  function updateHealthCard(cardId, percent, displayValue) {
    var card = document.getElementById(cardId);
    if (!card) return;

    var valueEl = card.querySelector('.health-value');
    if (valueEl) valueEl.textContent = displayValue;

    var barEl = card.querySelector('.health-bar-fill');
    if (barEl && percent >= 0) {
      barEl.style.width = Math.min(percent, 100) + '%';
      // 根据使用率变色
      if (percent > 90) barEl.style.background = 'var(--danger)';
      else if (percent > 70) barEl.style.background = 'var(--warning)';
      else if (cardId === 'hc-disk') barEl.style.background = 'var(--warning)';
      else if (cardId === 'hc-memory') barEl.style.background = 'var(--success)';
      else barEl.style.background = 'var(--accent)';
    }
  }

  // ═══ Live data (weather + news) ══════════════════════════════

  async function refreshLive() {
    try {
      var resp = await fetch(API + '/api/dashboard/live');
      var data = await resp.json();

      // 天气
      var weather = data.weather || {};
      document.getElementById('weather-city').textContent = weather.city || '--';
      document.getElementById('weather-temp').textContent = (weather.temp_c || '--') + '°C';
      document.getElementById('weather-desc').textContent = weather.weather_desc || '--';
      document.getElementById('weather-humidity').textContent = weather.humidity || '--';
      document.getElementById('weather-wind').textContent = (weather.wind_speed_kmph || '--') + ' km/h';
      document.getElementById('weather-precip').textContent = '--';

      // 新闻
      var newsContainer = document.getElementById('news-list');
      var articles = (data.news && data.news.articles) || [];
      var newsText = data.news_text || '';

      if (articles.length > 0) {
        newsContainer.innerHTML = articles.map(function(item, i) {
          var title = item.title ? item.title.slice(0, 80) : 'Untitled';
          var source = item.source || 'Unknown';
          return '<li style="padding:8px 12px;border-bottom:1px solid var(--border-color);display:flex;align-items:flex-start;gap:8px;">' +
            '<span style="color:var(--accent);font-weight:600;min-width:20px;">' + (i + 1) + '.</span>' +
            '<div>' +
            '<div style="font-size:13px;line-height:1.4;">' + title + '</div>' +
            '<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">' + source + '</div>' +
            '</div>' +
            '</li>';
        }).join('');
      } else if (newsText) {
        var lines = newsText.split('\n').filter(function(l) { return l.trim(); });
        newsContainer.innerHTML = lines.slice(1, 6).map(function(line) {
          var clean = line.replace(/^\d+\.\s*/, '');
          return '<li style="padding:8px 12px;border-bottom:1px solid var(--border-color);font-size:13px;line-height:1.4;">' +
            clean +
            '</li>';
        }).join('');
      } else {
        newsContainer.innerHTML = '<li style="padding:8px;color:var(--text-muted);text-align:center;">暂无新闻</li>';
      }
    } catch (e) {
      // 静默失败
    }
  }

  // ═══ Panel collapse management ════════════════════════════════
  function togglePanel(panelId) {
    var panel = document.getElementById(panelId);
    if (!panel) return;
    panel.classList.toggle('panel-collapsed');
    // Persist to localStorage
    var collapsed = {};
    try { collapsed = JSON.parse(localStorage.getItem('panelCollapsed') || '{}'); } catch(e) {}
    collapsed[panelId] = panel.classList.contains('panel-collapsed');
    localStorage.setItem('panelCollapsed', JSON.stringify(collapsed));
  }

  function restorePanelState() {
    var collapsed = {};
    try { collapsed = JSON.parse(localStorage.getItem('panelCollapsed') || '{}'); } catch(e) {}
    Object.keys(collapsed).forEach(function(id) {
      if (collapsed[id]) {
        var panel = document.getElementById(id);
        if (panel) { panel.classList.add('panel-collapsed'); }
      }
    });
  }

  // Restore panel collapse state on load
  restorePanelState();

  // ═══ Capability pie chart ═══════════════════════════════════

  var capabilityChart = null;

  async function refreshCapabilityStats() {
    var chartDom = document.getElementById('capability-chart');
    if (!chartDom) return;

    try {
      var resp = await fetch(API + '/api/dashboard/capability-stats');
      var data = await resp.json();

      if (capabilityChart) { capabilityChart.dispose(); }

      capabilityChart = echarts.init(chartDom);

      var COLORS = [
        '#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de',
        '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc', '#48b8d0',
        '#d48265', '#c23531'
      ];

      capabilityChart.setOption({
        tooltip: {
          trigger: 'item',
          formatter: '{b}: {c} ({d}%)'
        },
        legend: { show: false },
        series: [{
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['50%', '50%'],
          avoidLabelOverlap: false,
          itemStyle: {
            borderRadius: 4,
            borderColor: 'var(--bg-primary, #1a1a2e)',
            borderWidth: 3
          },
          label: { show: false },
          emphasis: {
            label: { show: true, fontSize: 14, fontWeight: 'bold' }
          },
          color: COLORS,
          data: data.labels.map(function(label, i) {
            return { name: label, value: data.values[i] };
          })
        }]
      });

      var legendEl = document.getElementById('capability-legend');
      if (legendEl) {
        legendEl.innerHTML = '<span style="color:var(--text-primary);">'
          + data.total + ' 次命中</span>';
      }

      window.addEventListener('resize', function() {
        capabilityChart && capabilityChart.resize();
      });
    } catch (e) {
      console.error('Capability stats fetch failed:', e);
    }
  }

  // Initialize theme first (before any rendering)
  initTheme();

  // SSE first, fallback polling at reduced cadence
  connectSSE();

  fetchStatus();
  setInterval(fetchStatus, 15000);
  fetchMetrics();
  setInterval(fetchMetrics, 15000);
  fetchAlerts();
  setInterval(fetchAlerts, 15000);
  // Load persisted history once on page load, then periodically
  fetchTaskHistory();
  setInterval(fetchTaskHistory, 15000);
  fetchAlertHistory();
  setInterval(fetchAlertHistory, 15000);
  refreshHealth();
  setInterval(refreshHealth, 10000);
  refreshLive();
  setInterval(refreshLive, 300000);
  refreshCapabilityStats();
  setInterval(refreshCapabilityStats, 60000);
  refreshPipelineHistory();
  setInterval(refreshPipelineHistory, 30000);
  refreshPipelineSchedules();
  setInterval(refreshPipelineSchedules, 60000);

  // ═══ Pipeline functions ════════════════════════════════════

  async function executePipeline(template) {
    var outputEl = document.getElementById('pipeline-output');
    outputEl.innerHTML = '<div style="color:var(--accent);">执行中...</div>';

    try {
      var resp = await fetch(API + '/api/pipelines/execute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({template: template})
      });
      var data = await resp.json();

      var statusColor = data.status === 'completed' ? 'var(--success)' :
                       data.status === 'failed' ? 'var(--danger)' : 'var(--warning)';

      var html = '<div style="font-weight:600;margin-bottom:8px;">'
        + data.pipeline_name + ' <span style="color:' + statusColor + '">[' + data.status + ']</span> '
        + '<span style="color:var(--text-muted);font-weight:400;">' + data.duration + 's</span>'
        + '</div>';

      if (data.stages) {
        html += data.stages.map(function(s) {
          var icon = s.status === 'success' ? 'OK' : s.status === 'failed' ? 'X' : s.status === 'skipped' ? '-' : '...';
          var color = s.status === 'success' ? 'var(--success)' :
                     s.status === 'failed' ? 'var(--danger)' : 'var(--text-muted)';
          return '<div style="padding:2px 0;color:' + color + ';">  ' + icon + '  ' + s.name + '</div>';
        }).join('');
      }

      outputEl.innerHTML = html;
      refreshPipelineHistory();
    } catch (e) {
      outputEl.innerHTML = '<div style="color:var(--danger);">执行失败: ' + e.message + '</div>';
    }
  }

  async function refreshPipelineHistory() {
    try {
      var resp = await fetch(API + '/api/pipelines/history?limit=10');
      var data = await resp.json();
      var historyEl = document.getElementById('pipeline-history');
      if (!historyEl) return;

      if (!data || data.length === 0) {
        historyEl.innerHTML = '<div style="color:var(--text-muted);">暂无执行历史</div>';
        return;
      }

      historyEl.innerHTML = data.map(function(r) {
        var statusColor = r.status === 'completed' ? 'var(--success)' : 'var(--danger)';
        var dots = (r.stages || []).map(function(s) {
          if (s.status === 'success') return '<span style="color:var(--success);">O</span>';
          if (s.status === 'failed') return '<span style="color:var(--danger);">X</span>';
          return '<span style="color:var(--text-muted);">-</span>';
        }).join('');
        return '<div style="padding:3px 0;display:flex;justify-content:space-between;">'
          + '<span>' + r.pipeline_name + ' <span style="color:' + statusColor + ';font-size:10px;">[' + r.status + ']</span></span>'
          + '<span>' + dots + '  ' + r.duration + 's</span>'
          + '</div>';
      }).join('');
    } catch (e) {
      console.error('Pipeline history fetch failed:', e);
    }
  }

  // ═══ Pipeline scheduler functions ══════════════════════════

  async function refreshPipelineSchedules() {
    try {
      var resp = await fetch(API + '/api/pipelines/schedule');
      var jobs = await resp.json();
      var el = document.getElementById('pipeline-schedules');
      if (!el) return;

      if (!jobs || jobs.length === 0) {
        el.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:8px;">暂无定时调度</div>';
        return;
      }

      el.innerHTML = jobs.map(function(j) {
        var statusColor = j.enabled ? 'var(--success)' : 'var(--text-muted)';
        var nextRun = j.next_run ? new Date(j.next_run * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}) : '--';
        return '<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border-color);">'
          + '<div style="flex:1;min-width:0;">'
          + '<div style="font-weight:500;">' + j.template + ' <span style="color:' + statusColor + ';font-size:10px;">[' + (j.enabled ? '启用' : '禁用') + ']</span></div>'
          + '<div style="color:var(--text-muted);font-size:10px;">下次: ' + nextRun + ' | 已执行: ' + j.run_count + '次</div>'
          + '</div>'
          + '<div style="display:flex;gap:4px;">'
          + '<button onclick="toggleSchedule(\'' + j.job_id + '\', ' + !j.enabled + ')" style="background:none;border:1px solid var(--border-color);color:var(--text-primary);border-radius:2px;padding:1px 6px;font-size:10px;cursor:pointer;">' + (j.enabled ? '暂停' : '启用') + '</button>'
          + '<button onclick="deleteSchedule(\'' + j.job_id + '\')" style="background:none;border:1px solid var(--border-color);color:var(--danger);border-radius:2px;padding:1px 6px;font-size:10px;cursor:pointer;">删除</button>'
          + '</div></div>';
      }).join('');
    } catch (e) {
      console.error('Schedule refresh failed:', e);
    }
  }

  async function toggleSchedule(jobId, enable) {
    try {
      await fetch(API + '/api/pipelines/schedule/' + jobId + '/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: enable})
      });
      refreshPipelineSchedules();
    } catch (e) {
      console.error('Toggle schedule failed:', e);
    }
  }

  async function deleteSchedule(jobId) {
    try {
      await fetch(API + '/api/pipelines/schedule/' + jobId, {method: 'DELETE'});
      refreshPipelineSchedules();
    } catch (e) {
      console.error('Delete schedule failed:', e);
    }
  }

  async function showScheduleDialog() {
    var template = prompt('选择流水线模板:\\n1. daily_brief (每日简报)\\n2. health_check (健康检查)\\n3. search_analyze (搜索分析)', 'daily_brief');
    if (!template) return;

    var interval = prompt('执行间隔（分钟），默认 1440（每天）:', '1440');
    if (!interval) return;

    try {
      var resp = await fetch(API + '/api/pipelines/schedule', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          template: template,
          interval_minutes: parseInt(interval)
        })
      });
      var data = await resp.json();
      if (data.job_id) {
        refreshPipelineSchedules();
      } else {
        alert('添加失败: ' + (data.error || '未知错误'));
      }
    } catch (e) {
      alert('请求失败: ' + e.message);
    }
  }

  // ═══ Evals panel functions ════════════════════════════════════

  async function refreshEvals() {
    try {
      var res = await fetch(API + '/api/dashboard/evals/report');
      var data = await res.json();
      renderEvals(data);
    } catch (e) {
      console.error('Evals fetch failed:', e);
    }
  }

  function renderEvals(report) {
    document.getElementById('evals-total').textContent = report.total_cases || 0;
    document.getElementById('evals-passed').textContent = report.passed || 0;
    document.getElementById('evals-failed').textContent = report.failed || 0;
    document.getElementById('evals-errored').textContent = report.errored || 0;
    var rate = report.pass_rate != null ? (report.pass_rate * 100).toFixed(1) + '%' : '--';
    document.getElementById('evals-rate').textContent = rate;

    var suitesHtml = '';
    var suites = report.suites || [];
    if (suites.length === 0) {
      suitesHtml = '<div class="empty">No eval results yet. Click "Run All Evals" to start.</div>';
    } else {
      suites.forEach(function(s) {
        var suitePassRate = s.pass_rate != null ? (s.pass_rate * 100).toFixed(0) + '%' : '--';
        suitesHtml += '<div style="border-bottom:1px solid var(--card-border);padding:6px 0;">';
        suitesHtml += '<div style="display:flex;justify-content:space-between;cursor:pointer;" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display===\'none\'?\'\':\'none\'">';
        suitesHtml += '<strong>' + s.suite_name + '</strong>';
        suitesHtml += '<span style="font-size:11px;color:var(--text-muted);">' + s.passed + '/' + s.failed + '/' + s.errored + ' | ' + suitePassRate + '</span>';
        suitesHtml += '</div>';
        suitesHtml += '<div style="display:none;margin-top:4px;padding-left:12px;font-size:11px;">';
        (s.results || []).forEach(function(r) {
          var icon = r.status === 'pass' ? '<span style="color:var(--success);">PASS</span>' :
                    r.status === 'fail' ? '<span style="color:var(--danger);">FAIL</span>' :
                    r.status === 'error' ? '<span style="color:var(--warning);">ERR</span>' :
                    '<span style="color:var(--text-muted);">' + r.status + '</span>';
          suitesHtml += '<div style="padding:1px 0;">' + icon + ' ' + r.case + '</div>';
        });
        suitesHtml += '</div></div>';
      });
    }
    document.getElementById('evals-suites').innerHTML = suitesHtml;
  }

  async function runEvals() {
    var btn = document.getElementById('evals-run-btn');
    var status = document.getElementById('evals-status');
    btn.disabled = true;
    btn.textContent = 'Running...';
    status.textContent = 'Executing all suites...';
    try {
      var res = await fetch(API + '/api/dashboard/evals/run', { method: 'POST' });
      if (!res.ok) {
        status.textContent = 'Failed: ' + res.status;
        return;
      }
      var data = await res.json();
      renderEvals(data);
      status.textContent = 'Completed at ' + new Date().toLocaleTimeString();
    } catch (e) {
      status.textContent = 'Error: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Run All Evals';
    }
  }

  // ═══ Audit panel functions ════════════════════════════════════

  var _auditTab = 'recent';

  function switchAuditTab(tab) {
    _auditTab = tab;
    document.querySelectorAll('.audit-tab').forEach(function(btn) {
      btn.style.color = 'var(--text-secondary)';
      btn.style.borderBottomColor = 'transparent';
      btn.classList.remove('active');
    });
    var activeBtn = document.getElementById('audit-tab-' + tab);
    if (activeBtn) {
      activeBtn.style.color = 'var(--accent)';
      activeBtn.style.borderBottomColor = 'var(--accent)';
      activeBtn.classList.add('active');
    }
    refreshAudit();
  }

  async function refreshAudit() {
    var container = document.getElementById('audit-tab-content');
    try {
      if (_auditTab === 'recent') {
        var res = await fetch(API + '/api/dashboard/audit/recent?limit=50');
        var data = await res.json();
        renderAuditTable(data.entries || [], container);
      } else if (_auditTab === 'failures') {
        var res = await fetch(API + '/api/dashboard/audit/failures?limit=50');
        var data = await res.json();
        renderAuditTable(data.entries || [], container);
      } else if (_auditTab === 'stats') {
        var res = await fetch(API + '/api/dashboard/audit/stats');
        var data = await res.json();
        renderAuditStats(data, container);
      }
    } catch (e) {
      container.innerHTML = '<div class="empty">Fetch error: ' + e.message + '</div>';
    }
  }

  function renderAuditTable(entries, container) {
    if (!entries || entries.length === 0) {
      container.innerHTML = '<div class="empty">No audit records found.</div>';
      return;
    }
    var html = '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:11px;">';
    html += '<thead><tr style="border-bottom:1px solid var(--card-border);color:var(--text-secondary);text-align:left;">';
    html += '<th style="padding:4px 8px;">Trace ID</th>';
    html += '<th style="padding:4px 8px;">Phase</th>';
    html += '<th style="padding:4px 8px;">Action</th>';
    html += '<th style="padding:4px 8px;">Success</th>';
    html += '<th style="padding:4px 8px;">Time</th>';
    html += '</tr></thead><tbody>';
    entries.forEach(function(e) {
      var successColor = e.success ? 'var(--success)' : 'var(--danger)';
      var successText = e.success ? 'OK' : 'FAIL';
      html += '<tr style="border-bottom:1px solid var(--card-border);">';
      html += '<td style="padding:3px 8px;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (e.trace_id || '') + '">' + (e.trace_id || '').slice(0, 12) + '</td>';
      html += '<td style="padding:3px 8px;">' + (e.phase || '') + '</td>';
      html += '<td style="padding:3px 8px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (e.action || '') + '">' + (e.action || '') + '</td>';
      html += '<td style="padding:3px 8px;color:' + successColor + ';">' + successText + '</td>';
      html += '<td style="padding:3px 8px;color:var(--text-muted);white-space:nowrap;">' + (e.created_at || '') + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    container.innerHTML = html;
  }

  function renderAuditStats(data, container) {
    var rate = data.total_entries > 0 ? (data.success_rate || 0).toFixed(1) + '%' : '--';
    var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;">';
    html += '<div class="mini-stat"><span class="mini-stat-val">' + (data.total_entries || 0) + '</span><span class="mini-stat-label">Total Entries</span></div>';
    html += '<div class="mini-stat"><span class="mini-stat-val" style="color:var(--success);">' + (data.successes || 0) + '</span><span class="mini-stat-label">Successes</span></div>';
    html += '<div class="mini-stat"><span class="mini-stat-val" style="color:var(--danger);">' + (data.failures || 0) + '</span><span class="mini-stat-label">Failures</span></div>';
    html += '<div class="mini-stat"><span class="mini-stat-val">' + rate + '</span><span class="mini-stat-label">Success Rate</span></div>';
    html += '<div class="mini-stat"><span class="mini-stat-val">' + formatBytes(data.db_size_bytes || 0) + '</span><span class="mini-stat-label">DB Size</span></div>';
    html += '</div>';

    var actions = data.top_actions || [];
    if (actions.length > 0) {
      html += '<div style="font-size:11px;"><strong style="color:var(--text-secondary);">Top Actions:</strong></div>';
      html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;">';
      actions.forEach(function(a) {
        html += '<span style="background:var(--bg-secondary);padding:2px 8px;border-radius:4px;font-size:10px;">' + a.action + ': ' + a.count + '</span>';
      });
      html += '</div>';
    }

    container.innerHTML = html;
  }

  function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    var k = 1024;
    var sizes = ['B', 'KB', 'MB', 'GB'];
    var i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  // Auto-refresh Evals and Audit
  refreshEvals();
  refreshAudit();
  setInterval(refreshAudit, 60000);
</script>
</body>
</html>"""
