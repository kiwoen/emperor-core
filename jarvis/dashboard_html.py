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

  /* ── Summary bar ── */
  @keyframes alertPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.4); }
    50%      { box-shadow: 0 0 0 8px rgba(239,68,68,0); }
  }
  .summary-bar {
    display: flex; gap: 12px; margin-bottom: var(--gap);
    flex-wrap: wrap;
  }
  .summary-card {
    flex: 1 1 0; min-width: 110px;
    background: var(--card-bg); backdrop-filter: blur(16px);
    border: 1px solid var(--card-border); border-radius: var(--radius);
    padding: 16px 18px; text-align: center;
    position: relative; overflow: hidden;
    background-image: linear-gradient(135deg, rgba(108,140,255,0.04) 0%, transparent 100%);
    transition: transform 0.2s, border-color 0.3s;
  }
  .summary-card:hover {
    transform: translateY(-2px);
    border-color: rgba(255,255,255,0.14);
  }
  .summary-card.alert-pulse {
    animation: alertPulse 2s infinite;
    background-image: linear-gradient(135deg, rgba(239,68,68,0.08) 0%, transparent 100%);
    border-color: rgba(239,68,68,0.35);
  }
  .summary-icon {
    font-size: 1.1rem; margin-bottom: 4px;
  }
  .summary-value {
    font-size: 1.65rem; font-weight: 700; line-height: 1.1;
  }
  .summary-value.accent { color: var(--accent); }
  .summary-value.success { color: var(--success); }
  .summary-value.warning { color: var(--warning); }
  .summary-value.danger { color: var(--danger); }
  .summary-label {
    font-size: 0.68rem; color: var(--text-dim);
    margin-top: 2px; letter-spacing: 0.3px;
  }

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

  /* ── Model Cost panel ── */
  .ring-progress {
    position: relative; width: 120px; height: 120px; margin: 0 auto 12px;
  }
  .ring-progress svg { transform: rotate(-90deg); }
  .ring-progress .ring-bg {
    fill: none; stroke: rgba(255,255,255,0.06); stroke-width: 10;
  }
  .ring-progress .ring-fill {
    fill: none; stroke: var(--success); stroke-width: 10;
    stroke-linecap: round; transition: stroke-dashoffset 0.8s ease;
  }
  .ring-progress .ring-center {
    position: absolute; inset: 0; display: flex;
    flex-direction: column; align-items: center; justify-content: center;
  }
  .ring-progress .ring-pct {
    font-size: 1.4rem; font-weight: 700; color: var(--success);
  }
  .ring-progress .ring-label {
    font-size: 0.6rem; color: var(--text-dim); text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  /* Evals donut chart */
  .evals-donut { position: relative; width: 140px; height: 140px; margin: 0 auto 10px; }
  .evals-donut svg { transform: rotate(-90deg); }
  .evals-donut-center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  .evals-donut-pct { font-size: 1.6rem; font-weight: 700; color: var(--text-primary); line-height: 1; }
  .evals-donut-label { font-size: 0.55rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
  .evals-donut-legend { display: flex; justify-content: center; gap: 18px; margin-bottom: 14px; font-size: 0.68rem; }
  .evals-donut-legend span { display: flex; align-items: center; gap: 5px; }
  .evals-donut-legend .leg-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .evals-donut-legend .leg-dot.pass { background: var(--success); }
  .evals-donut-legend .leg-dot.fail { background: var(--danger); }
  .evals-donut-legend .leg-dot.warn { background: var(--warning); }
  /* Evals suite accordion */
  .evals-toggle-bar { display: flex; justify-content: flex-end; margin-bottom: 6px; }
  .evals-toggle-bar button { font-size: 0.65rem; background: rgba(255,255,255,0.04); color: var(--text-dim); border: 1px solid var(--border-color); border-radius: 4px; padding: 2px 10px; cursor: pointer; }
  .evals-toggle-bar button:hover { color: var(--text-primary); border-color: var(--text-dim); }
  .evals-suite-item { border-bottom: 1px solid var(--card-border); }
  .evals-suite-header { display: flex; justify-content: space-between; align-items: center; padding: 7px 4px; cursor: pointer; user-select: none; }
  .evals-suite-header:hover { background: rgba(255,255,255,0.02); }
  .evals-suite-header .suite-title { font-weight: 600; font-size: 0.78rem; color: var(--text-primary); }
  .evals-suite-header .suite-meta { display: flex; align-items: center; gap: 10px; font-size: 0.68rem; color: var(--text-dim); }
  .evals-suite-header .suite-arrow { font-size: 0.6rem; transition: transform 0.2s; color: var(--text-muted); }
  .evals-suite-header.open .suite-arrow { transform: rotate(90deg); }
  .evals-suite-body { display: none; padding: 0 4px 6px 16px; }
  .evals-suite-body.open { display: block; }
  .evals-case-row { display: flex; align-items: center; padding: 3px 0; font-size: 0.7rem; color: var(--text-secondary); gap: 8px; }
  .evals-case-row .case-icon { width: 16px; text-align: center; flex-shrink: 0; }
  .evals-case-row .case-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .evals-case-row .case-duration { font-size: 0.65rem; color: var(--text-muted); white-space: nowrap; }
  .evals-case-row .case-error { font-size: 0.62rem; color: var(--danger); margin-top: 1px; padding-left: 24px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .evals-empty { text-align: center; padding: 30px 10px; color: var(--text-muted); font-size: 0.75rem; }
  .tier-bars { display: flex; flex-direction: column; gap: 8px; }
  .tier-bar-row { display: flex; align-items: center; gap: 10px; }
  .tier-bar-label {
    font-size: 0.7rem; color: var(--text-dim); min-width: 65px;
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .tier-bar-track {
    flex: 1; height: 10px; background: rgba(255,255,255,0.04);
    border-radius: 5px; overflow: hidden;
  }
  .tier-bar-fill {
    height: 100%; border-radius: 5px; transition: width 0.6s ease;
  }
  .tier-bar-fill.cheap { background: linear-gradient(90deg, #4ade80, #22c55e); }
  .tier-bar-fill.standard { background: linear-gradient(90deg, #6c8cff, #818cf8); }
  .tier-bar-fill.premium { background: linear-gradient(90deg, #a78bfa, #c084fc); }
  .tier-bar-count { font-size: 0.75rem; color: var(--text); min-width: 40px; text-align: right; }
  .cost-cards { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
  .cost-card {
    flex: 1; min-width: 100px; text-align: center;
    background: rgba(255,255,255,0.02); border-radius: 8px; padding: 10px 8px;
  }
  .cost-card .cost-val {
    font-size: 1.1rem; font-weight: 700; color: var(--accent);
  }
  .cost-card .cost-label {
    font-size: 0.6rem; color: var(--text-dim); text-transform: uppercase;
    letter-spacing: 0.5px; margin-top: 2px;
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

  /* ── Pipeline Monitor DAG ── */
  @keyframes pmon-pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.4); }
    50% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
  }
  .pmon-pulse {
    animation: pmon-pulse 1.5s ease-in-out infinite;
  }

  /* ── Plugin Marketplace ── */
  .plugin-tabs { display: flex; gap: 0; margin-bottom: 16px; border-bottom: 2px solid var(--border-color); }
  .plugin-tab-btn {
    background: none; border: none; color: var(--text-secondary); cursor: pointer;
    padding: 8px 20px; font-family: inherit; font-size: 0.82rem; font-weight: 600;
    border-bottom: 2px solid transparent; margin-bottom: -2px;
    transition: color 0.2s, border-color 0.2s;
  }
  .plugin-tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
  .plugin-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
  .plugin-card {
    background: var(--bg-card); border: 1px solid var(--border-color);
    border-radius: 10px; padding: 16px; transition: border-color 0.2s, box-shadow 0.2s;
    position: relative; display: flex; flex-direction: column; gap: 8px;
  }
  .plugin-card:hover { border-color: var(--accent); box-shadow: 0 0 12px rgba(108,140,255,0.08); }
  .plugin-card.installed { border-color: var(--success); }
  .plugin-card.installed::after {
    content: '✓'; position: absolute; top: 10px; right: 12px;
    color: var(--success); font-weight: bold; font-size: 16px;
  }
  .plugin-card-name { font-size: 1rem; font-weight: 700; color: var(--text-primary); }
  .plugin-card-version { font-size: 0.7rem; color: var(--text-dim); margin-left: 6px; }
  .plugin-card-desc { font-size: 0.78rem; color: var(--text-secondary); line-height: 1.4; flex: 1; }
  .plugin-card-meta { font-size: 0.7rem; color: var(--text-muted); display: flex; gap: 12px; }
  .plugin-card-meta span { display: flex; align-items: center; gap: 3px; }
  .plugin-card-caps { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
  .plugin-cap-tag {
    font-size: 0.63rem; padding: 1px 8px; border-radius: 10px;
    background: rgba(108,140,255,0.1); color: var(--accent);
    border: 1px solid rgba(108,140,255,0.2);
  }
  .plugin-card-actions { display: flex; gap: 8px; margin-top: 4px; }
  .plugin-btn {
    padding: 6px 14px; border-radius: 6px; font-family: inherit; font-size: 0.75rem;
    cursor: pointer; border: none; font-weight: 600; transition: filter 0.2s;
  }
  .plugin-btn.install { background: var(--accent); color: #fff; }
  .plugin-btn.uninstall { background: var(--danger); color: #fff; }
  .plugin-btn.toggle { background: var(--bg-card-hover); color: var(--text-primary); border: 1px solid var(--border-color); }
  .plugin-btn.toggle.enabled { background: var(--success); border-color: var(--success); }
  .plugin-btn.config { background: none; border: 1px solid var(--border-color); color: var(--text-secondary); padding: 6px 10px; font-size: 1rem; }
  .plugin-btn:hover { filter: brightness(1.15); }

  /* Plugin config modal */
  .plugin-config-modal { display: none; }
  .plugin-config-modal.show { display: flex; }
  .plugin-config-content {
    max-width: 500px; max-height: 80vh; overflow-y: auto;
  }
  .plugin-config-content textarea {
    width: 100%; min-height: 140px; background: var(--input-bg); color: var(--text-primary);
    border: 1px solid var(--border-color); border-radius: 6px; padding: 10px;
    font-family: 'Consolas', 'Courier New', monospace; font-size: 0.75rem;
    resize: vertical; box-sizing: border-box;
  }
  .plugin-config-content .config-label { display: block; color: var(--text-secondary); font-size: 0.78rem; margin-bottom: 4px; margin-top: 10px; }
  /* ── Toast notifications ── */
  #toast-container { position: fixed; top: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 8px; max-width: 400px; width: 100%; pointer-events: none; }
  .toast { display: flex; align-items: flex-start; gap: 10px; background: var(--bg-card); border: 1px solid var(--card-border); border-left: 4px solid var(--accent); border-radius: 8px; padding: 12px 16px; backdrop-filter: blur(12px); box-shadow: 0 8px 32px rgba(0,0,0,0.4); animation: toastIn 0.35s cubic-bezier(0.21, 1.02, 0.73, 1); pointer-events: auto; transition: opacity 0.3s, transform 0.3s; }
  .toast.removing { opacity: 0; transform: translateX(120%); }
  @keyframes toastIn { from { opacity: 0; transform: translateX(120%); } to { opacity: 1; transform: translateX(0); } }
  .toast-icon { font-size: 1.2rem; flex-shrink: 0; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; border-radius: 6px; }
  .toast-body { flex: 1; min-width: 0; }
  .toast-body .toast-title { font-size: 0.82rem; font-weight: 700; color: var(--text-primary); margin-bottom: 2px; }
  .toast-body .toast-detail { font-size: 0.72rem; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .toast-close { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 1rem; padding: 2px 6px; border-radius: 4px; flex-shrink: 0; }
  .toast-close:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
  /* event type colors */
  .toast.type-dispatch { border-left-color: #6366f1; } .toast.type-dispatch .toast-icon { background: rgba(99,102,241,0.15); }
  .toast.type-sandbox { border-left-color: #06b6d4; } .toast.type-sandbox .toast-icon { background: rgba(6,182,212,0.15); }
  .toast.type-pipeline { border-left-color: #8b5cf6; } .toast.type-pipeline .toast-icon { background: rgba(139,92,246,0.15); }
  .toast.type-governance { border-left-color: #f59e0b; } .toast.type-governance .toast-icon { background: rgba(245,158,11,0.15); }
  .toast.type-healing { border-left-color: #10b981; } .toast.type-healing .toast-icon { background: rgba(16,185,129,0.15); }
  .toast.type-approval { border-left-color: #f43f5e; } .toast.type-approval .toast-icon { background: rgba(244,63,94,0.15); }
  .toast.type-memory { border-left-color: #a78bfa; } .toast.type-memory .toast-icon { background: rgba(167,139,250,0.15); }
  .toast.type-eval { border-left-color: #22d3ee; } .toast.type-eval .toast-icon { background: rgba(34,211,238,0.15); }
  .toast.type-alert { border-left-color: #ef4444; } .toast.type-alert .toast-icon { background: rgba(239,68,68,0.15); }
  /* Event log panel */
  #event-log-panel { position: fixed; bottom: 20px; right: 20px; z-index: 9998; width: 360px; max-height: 320px; background: var(--bg-card); border: 1px solid var(--card-border); border-radius: 10px; backdrop-filter: blur(12px); box-shadow: 0 8px 32px rgba(0,0,0,0.4); display: flex; flex-direction: column; overflow: hidden; }
  #event-log-panel.collapsed .event-log-body { display: none; }
  .event-log-header { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--border-color); cursor: pointer; user-select: none; }
  .event-log-header:hover { background: rgba(255,255,255,0.02); }
  .event-log-header .log-title { font-size: 0.8rem; font-weight: 700; color: var(--text-primary); }
  .event-log-header .log-count { font-size: 0.7rem; color: var(--text-muted); background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 10px; }
  .event-log-body { overflow-y: auto; max-height: 260px; padding: 4px 0; scrollbar-width: thin; scrollbar-color: var(--border-color) transparent; }
  .event-log-entry { display: flex; align-items: center; gap: 8px; padding: 6px 14px; font-size: 0.72rem; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.02); }
  .event-log-entry .log-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .event-log-entry .log-time { color: var(--text-muted); font-size: 0.65rem; flex-shrink: 0; min-width: 48px; }
  .event-log-entry .log-msg { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .event-log-empty { padding: 20px; text-align: center; color: var(--text-muted); font-size: 0.75rem; }
  /* ── Healing Timeline ── */
  .healing-timeline { position: relative; padding-left: 28px; max-height: 520px; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--border-color) transparent; }
  .healing-timeline::before { content: ''; position: absolute; left: 11px; top: 4px; bottom: 4px; width: 2px; background: var(--border-color); }
  .healing-timeline-empty { text-align: center; padding: 24px 12px; color: var(--text-muted); font-size: 0.8rem; }
  .ht-entry { position: relative; margin-bottom: 14px; padding-left: 0; }
  .ht-dot { position: absolute; left: -21px; top: 8px; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--bg-card); z-index: 2; }
  .ht-dot.success { background: var(--success); }
  .ht-dot.failed { background: var(--danger); }
  .ht-dot.running { background: var(--warning); animation: ht-pulse 1.2s infinite; }
  @keyframes ht-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .ht-card { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px; padding: 10px 14px; }
  .ht-card-header { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .ht-card-header .ht-name { font-weight: 600; font-size: 0.85rem; color: var(--text-primary); flex: 1; }
  .ht-badge { font-size: 0.65rem; padding: 2px 8px; border-radius: 10px; font-weight: 600; text-transform: uppercase; white-space: nowrap; }
  .ht-badge.success { background: rgba(34,197,94,0.15); color: var(--success); }
  .ht-badge.failed { background: rgba(239,68,68,0.15); color: var(--danger); }
  .ht-badge.running { background: rgba(245,158,11,0.15); color: var(--warning); }
  .ht-card-meta { display: flex; align-items: center; gap: 14px; font-size: 0.7rem; color: var(--text-secondary); }
  .ht-card-meta .ht-source { color: var(--text-muted); }
  .ht-card-meta .ht-elapsed { margin-left: auto; font-variant-numeric: tabular-nums; }
  .ht-card-meta .ht-time { color: var(--text-muted); font-variant-numeric: tabular-nums; }

  /* ── Keyboard shortcuts help overlay ── */
  .kbd-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.65);
    z-index: 10000; display: flex; align-items: center; justify-content: center;
    opacity: 0; pointer-events: none; transition: opacity 0.2s;
  }
  .kbd-overlay.show { opacity: 1; pointer-events: auto; }
  .kbd-card {
    background: var(--bg-card); border: 1px solid var(--card-border);
    border-radius: 14px; padding: 28px 32px 24px;
    max-width: 560px; width: 90vw; box-shadow: 0 12px 48px rgba(0,0,0,0.5);
    backdrop-filter: blur(20px); position: relative;
  }
  .kbd-card h2 {
    font-size: 1.15rem; font-weight: 700; color: var(--text-primary);
    margin: 0 0 18px 0; letter-spacing: -0.3px;
  }
  .kbd-card .kbd-close {
    position: absolute; top: 14px; right: 18px;
    background: none; border: none; color: var(--text-dim); font-size: 1.3rem;
    cursor: pointer; padding: 4px 8px; border-radius: 6px; line-height: 1;
    transition: color 0.2s, background 0.2s;
  }
  .kbd-card .kbd-close:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
  .kbd-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  .kbd-table th {
    text-align: left; padding: 7px 12px; color: var(--text-dim); font-size: 0.68rem;
    text-transform: uppercase; letter-spacing: 0.6px; border-bottom: 1px solid var(--card-border);
  }
  .kbd-table td { padding: 8px 12px; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.03); }
  .kbd-table tr:last-child td { border-bottom: none; }
  .kbd-key {
    display: inline-block; padding: 3px 9px; border-radius: 5px;
    background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12);
    font-family: 'Cascadia Code','Fira Code','Consolas',monospace;
    font-size: 0.78rem; font-weight: 600; color: var(--text-primary);
    min-width: 20px; text-align: center;
  }
  .kbd-table .kbd-desc { color: var(--text-dim); font-size: 0.75rem; }

  /* ── Notification Center ── */
  .notif-wrapper { position: relative; display: inline-block; }
  .notif-bell-btn {
    background: none; border: 1px solid var(--border-color); color: var(--text-primary);
    font-size: 20px; cursor: pointer; padding: 6px 10px; border-radius: 6px;
    margin-right: 8px; transition: border-color 0.2s; position: relative;
    line-height: 1;
  }
  .notif-bell-btn:hover { border-color: var(--accent); }
  .notif-badge {
    position: absolute; top: -6px; right: -8px;
    background: var(--danger); color: #fff;
    font-size: 0.6rem; font-weight: 700; min-width: 18px; height: 18px;
    border-radius: 9px; display: flex; align-items: center; justify-content: center;
    padding: 0 4px; line-height: 1; pointer-events: none;
  }
  .notif-badge:empty { display: none; }
  .notif-dropdown {
    display: none; position: absolute; right: 0; top: 100%; margin-top: 8px;
    width: 380px; max-height: 480px; background: var(--bg-card);
    border: 1px solid var(--card-border); border-radius: 10px;
    backdrop-filter: blur(20px); box-shadow: 0 12px 40px rgba(0,0,0,0.45);
    z-index: 10000; overflow: hidden; flex-direction: column;
  }
  .notif-dropdown.open { display: flex; }
  .notif-tabs {
    display: flex; gap: 0; border-bottom: 1px solid var(--border-color);
    flex-shrink: 0; padding: 0 4px;
  }
  .notif-tab {
    background: none; border: none; color: var(--text-secondary); cursor: pointer;
    padding: 10px 14px; font-family: inherit; font-size: 0.72rem; font-weight: 600;
    border-bottom: 2px solid transparent; margin-bottom: -1px;
    transition: color 0.2s, border-color 0.2s; white-space: nowrap;
  }
  .notif-tab:hover { color: var(--text-primary); }
  .notif-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .notif-list { overflow-y: auto; flex: 1; padding: 4px 0; scrollbar-width: thin; scrollbar-color: var(--border-color) transparent; }
  .notif-item {
    display: flex; align-items: flex-start; gap: 10px; padding: 10px 16px;
    border-bottom: 1px solid rgba(255,255,255,0.03); cursor: pointer;
    transition: background 0.15s;
  }
  .notif-item:hover { background: rgba(255,255,255,0.04); }
  .notif-item.unread { background: rgba(108,140,255,0.06); }
  .notif-item.unread:hover { background: rgba(108,140,255,0.09); }
  .notif-icon {
    width: 32px; height: 32px; border-radius: 8px; display: flex;
    align-items: center; justify-content: center; font-size: 0.85rem;
    flex-shrink: 0; margin-top: 1px;
  }
  .notif-icon.alert { background: rgba(239,68,68,0.15); }
  .notif-icon.approval { background: rgba(168,85,247,0.15); }
  .notif-icon.healing { background: rgba(34,197,94,0.15); }
  .notif-icon.pipeline { background: rgba(59,130,246,0.15); }
  .notif-body { flex: 1; min-width: 0; }
  .notif-title { font-size: 0.8rem; font-weight: 600; color: var(--text-primary); margin-bottom: 2px; }
  .notif-desc { font-size: 0.7rem; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .notif-time { font-size: 0.65rem; color: var(--text-muted); margin-top: 3px; }
  .notif-empty { padding: 40px 20px; text-align: center; color: var(--text-muted); font-size: 0.82rem; }
  .notif-see-all {
    display: block; text-align: center; padding: 10px; font-size: 0.72rem;
    color: var(--accent); cursor: pointer; border-top: 1px solid var(--border-color);
    text-decoration: none;
  }
  .notif-see-all:hover { background: rgba(108,140,255,0.06); }

  /* Mobile: full-width notification dropdown */
  @media (max-width: 480px) {
    .notif-dropdown { width: calc(100vw - 32px); right: -60px; }
    .notif-tab { padding: 8px 10px; font-size: 0.68rem; }
  }

  /* ── Governance Panel ── */
  .gov-rule-row {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 12px; border-bottom: 1px solid var(--card-border);
    transition: background 0.15s;
  }
  .gov-rule-row:hover { background: rgba(108,140,255,0.04); }
  .gov-rule-row:last-child { border-bottom: none; }

  .gov-priority-badge {
    display: inline-block; min-width: 32px; text-align: center;
    padding: 2px 8px; border-radius: 4px; font-size: 0.7rem;
    font-weight: 700; letter-spacing: 0.5px; flex-shrink: 0;
  }
  .gov-priority-P0 { background: rgba(239,68,68,0.2); color: #ef4444; }
  .gov-priority-P1 { background: rgba(249,115,22,0.2); color: #f97316; }
  .gov-priority-P2 { background: rgba(234,179,8,0.2); color: #eab308; }
  .gov-priority-P3 { background: rgba(148,163,184,0.2); color: #94a3b8; }

  .gov-rule-desc {
    flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 0.8rem; color: var(--text-primary); cursor: default;
  }

  /* Toggle switch */
  .gov-toggle { position: relative; display: inline-block; width: 40px; height: 22px; flex-shrink: 0; }
  .gov-toggle input { opacity: 0; width: 0; height: 0; }
  .gov-toggle-slider {
    position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
    background: #555; border-radius: 22px; transition: 0.25s;
  }
  .gov-toggle-slider::before {
    content: ""; position: absolute; height: 16px; width: 16px;
    left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: 0.25s;
  }
  .gov-toggle input:checked + .gov-toggle-slider { background: #8b5cf6; }
  .gov-toggle input:checked + .gov-toggle-slider::before { transform: translateX(18px); }

  .gov-delete-btn {
    background: none; border: 1px solid transparent; color: var(--text-muted);
    cursor: pointer; font-size: 0.75rem; padding: 3px 8px; border-radius: 4px;
    flex-shrink: 0; transition: all 0.2s;
  }
  .gov-delete-btn:hover { color: #ef4444; border-color: rgba(239,68,68,0.4); background: rgba(239,68,68,0.08); }

  .gov-empty {
    text-align: center; padding: 32px 16px; color: var(--text-muted); font-size: 0.82rem;
  }

  /* ── Governance New Rule Modal ── */
  .gov-modal-overlay {
    display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.55); z-index: 1000; justify-content: center; align-items: center;
  }
  .gov-modal-overlay.active { display: flex; }
  .gov-modal-card {
    background: var(--panel-bg); border: 1px solid var(--card-border);
    border-radius: 12px; padding: 24px; width: 460px; max-width: 90vw;
    box-shadow: 0 12px 40px rgba(0,0,0,0.4);
  }
  .gov-modal-card h3 { margin: 0 0 18px 0; font-size: 1.1rem; color: var(--text-primary); }
  .gov-modal-card label {
    display: block; margin-bottom: 4px; font-size: 0.78rem; color: var(--text-secondary);
  }
  .gov-modal-card textarea, .gov-modal-card select {
    width: 100%; box-sizing: border-box; padding: 8px 10px; margin-bottom: 14px;
    background: rgba(255,255,255,0.05); border: 1px solid var(--card-border);
    border-radius: 6px; color: var(--text-primary); font-size: 0.82rem;
    font-family: inherit; resize: vertical;
  }
  .gov-modal-card textarea:focus, .gov-modal-card select:focus {
    outline: none; border-color: var(--accent);
  }
  .gov-modal-actions {
    display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px;
  }
  .gov-modal-actions button {
    padding: 8px 20px; border-radius: 6px; font-size: 0.82rem; cursor: pointer;
    border: 1px solid var(--card-border); transition: background 0.2s;
  }
  .gov-btn-submit { background: var(--accent); color: #fff; border-color: var(--accent) !important; }
  .gov-btn-submit:hover { background: #7c3aed; }
  .gov-btn-cancel { background: transparent; color: var(--text-secondary); }
  .gov-btn-cancel:hover { background: rgba(255,255,255,0.05); }

  /* ── Alert Rules Panel ── */
  .alert-rule-row {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 12px; border-bottom: 1px solid var(--card-border);
    transition: background 0.15s;
  }
  .alert-rule-row:hover { background: rgba(108,140,255,0.04); }
  .alert-rule-row:last-child { border-bottom: none; }

  .alert-severity-badge {
    display: inline-block; min-width: 36px; text-align: center;
    padding: 2px 8px; border-radius: 4px; font-size: 0.7rem;
    font-weight: 700; letter-spacing: 0.5px; flex-shrink: 0;
  }
  .alert-severity-critical { background: rgba(239,68,68,0.2); color: #ef4444; }
  .alert-severity-warning { background: rgba(249,115,22,0.2); color: #f97316; }
  .alert-severity-info { background: rgba(59,130,246,0.2); color: #3b82f6; }

  .alert-rule-main { flex: 1; min-width: 0; }
  .alert-rule-name { font-size: 0.82rem; font-weight: 600; color: var(--text-primary); }
  .alert-rule-condition { font-size: 0.72rem; color: var(--text-muted); }
  .alert-rule-threshold {
    font-family: 'Courier New', monospace; font-size: 0.78rem;
    color: var(--accent); flex-shrink: 0; min-width: 48px; text-align: right;
  }

  /* Alert rule toggle — green / gray (distinct from governance purple) */
  .alert-toggle { position: relative; display: inline-block; width: 40px; height: 22px; flex-shrink: 0; }
  .alert-toggle input { opacity: 0; width: 0; height: 0; }
  .alert-toggle-slider {
    position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
    background: #555; border-radius: 22px; transition: 0.25s;
  }
  .alert-toggle-slider::before {
    content: ""; position: absolute; height: 16px; width: 16px;
    left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: 0.25s;
  }
  .alert-toggle input:checked + .alert-toggle-slider { background: #22c55e; }
  .alert-toggle input:checked + .alert-toggle-slider::before { transform: translateX(18px); }

  .alert-delete-btn {
    background: none; border: 1px solid transparent; color: var(--text-muted);
    cursor: pointer; font-size: 0.75rem; padding: 3px 8px; border-radius: 4px;
    flex-shrink: 0; transition: all 0.2s;
  }
  .alert-delete-btn:hover { color: #ef4444; border-color: rgba(239,68,68,0.4); background: rgba(239,68,68,0.08); }

  .alert-empty {
    text-align: center; padding: 32px 16px; color: var(--text-muted); font-size: 0.82rem;
  }

  /* ── Alert Rules New Rule Modal ── */
  .alert-modal-overlay {
    display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.55); z-index: 1000; justify-content: center; align-items: center;
  }
  .alert-modal-overlay.active { display: flex; }
  .alert-modal-card {
    background: var(--panel-bg); border: 1px solid var(--card-border);
    border-radius: 12px; padding: 24px; width: 460px; max-width: 90vw;
    box-shadow: 0 12px 40px rgba(0,0,0,0.4);
  }
  .alert-modal-card h3 { margin: 0 0 18px 0; font-size: 1.1rem; color: var(--text-primary); }
  .alert-modal-card label {
    display: block; margin-bottom: 4px; font-size: 0.78rem; color: var(--text-secondary);
  }
  .alert-modal-card input, .alert-modal-card select {
    width: 100%; box-sizing: border-box; padding: 8px 10px; margin-bottom: 14px;
    background: rgba(255,255,255,0.05); border: 1px solid var(--card-border);
    border-radius: 6px; color: var(--text-primary); font-size: 0.82rem;
    font-family: inherit;
  }
  .alert-modal-card input:focus, .alert-modal-card select:focus {
    outline: none; border-color: var(--accent);
  }
  .alert-modal-actions {
    display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px;
  }
  .alert-modal-actions button {
    padding: 8px 20px; border-radius: 6px; font-size: 0.82rem; cursor: pointer;
    border: 1px solid var(--card-border); transition: background 0.2s;
  }
  .alert-btn-submit { background: var(--accent); color: #fff; border-color: var(--accent) !important; }
  .alert-btn-submit:hover { background: #7c3aed; }
  .alert-btn-cancel { background: transparent; color: var(--text-secondary); }
  .alert-btn-cancel:hover { background: rgba(255,255,255,0.05); }
</style>
</head>
<body>

<div class="header">
  <h1>Emperor Evolution Dashboard</h1>
  <div>
    <div class="notif-wrapper" id="notifWrapper">
      <button id="notif-bell" class="notif-bell-btn" onclick="toggleNotifications(event)" title="通知中心">🔔</button>
      <span class="notif-badge" id="notifBadge"></span>
      <div class="notif-dropdown" id="notifDropdown">
        <div class="notif-tabs">
          <button class="notif-tab active" onclick="filterNotifications('all', event)">全部</button>
          <button class="notif-tab" onclick="filterNotifications('alert', event)">告警</button>
          <button class="notif-tab" onclick="filterNotifications('approval', event)">审批</button>
          <button class="notif-tab" onclick="filterNotifications('healing', event)">自愈</button>
          <button class="notif-tab" onclick="filterNotifications('pipeline', event)">Pipeline</button>
        </div>
        <div class="notif-list" id="notifList"></div>
        <div class="notif-see-all" onclick="seeAllNotifications()" style="display:none;" id="notifSeeAll">查看全部通知</div>
      </div>
    </div>
    <button id="theme-toggle" class="theme-btn" onclick="cycleTheme()" title="切换主题">\u263E</button>
    <span class="badge refresh" id="connectionStatus">Connecting...</span>
    <span class="badge" style="margin-left:8px;" id="lastUpdate"></span>
  </div>
</div>

<div class="dashboard-grid">

<!-- Smart Search Bar -->
<div class="panel-full" style="margin-bottom:0;">
  <div style="position:relative;">
    <input id="dashboard-search-input" type="text" placeholder="全局搜索 — 跨任务 / 评测 / 审计 / 自愈 / 版本快照…" 
      style="width:100%;background:var(--card-bg);border:2px solid var(--border-color);color:var(--text-primary);padding:14px 16px;border-radius:10px;font-size:15px;font-family:inherit;box-sizing:border-box;transition:border-color 0.2s;"
      onfocus="this.style.borderColor='var(--accent)';" 
      onblur="this.style.borderColor='var(--border-color)';"
      oninput="debouncedSearch()">
    <span id="search-badge" style="position:absolute;right:16px;top:50%;transform:translateY(-50%);font-size:11px;color:var(--text-muted);display:none;"></span>
  </div>
  <div id="search-results" style="display:none;margin-top:12px;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:16px;max-height:480px;overflow-y:auto;"></div>
</div>

<!-- Status summary bar -->
<div class="summary-bar" id="summaryBar">
  <div class="summary-card" id="sc-ministers">
    <div class="summary-icon">👥</div>
    <div class="summary-value accent" id="sv-ministers">--</div>
    <div class="summary-label">活跃 Minister</div>
  </div>
  <div class="summary-card" id="sc-success">
    <div class="summary-icon">✅</div>
    <div class="summary-value" id="sv-success">--</div>
    <div class="summary-label">成功率（1h）</div>
  </div>
  <div class="summary-card" id="sc-alerts">
    <div class="summary-icon">🚨</div>
    <div class="summary-value" id="sv-alerts">--</div>
    <div class="summary-label">活动告警</div>
  </div>
  <div class="summary-card" id="sc-healing">
    <div class="summary-icon">💚</div>
    <div class="summary-value" id="sv-healing">--</div>
    <div class="summary-label">今日自愈</div>
  </div>
  <div class="summary-card" id="sc-pipelines">
    <div class="summary-icon">📋</div>
    <div class="summary-value" id="sv-pipelines">--</div>
    <div class="summary-label">今日 Pipeline</div>
  </div>
</div>

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

<!-- Self-Healing Dashboard Panel -->
<div class="panel panel-full" id="panel-healing">
  <div class="panel-header">
    <h2>自愈动作面板 <span id="healing-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:var(--bg-secondary);color:var(--text-muted);">空闲</span></h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-healing')">▼</button>
    <span class="panel-actions">
      <button onclick="healingCheckAll()" style="background:var(--success);color:#fff;border:none;border-radius:4px;padding:3px 12px;font-size:12px;cursor:pointer;">检查</button>
      <button onclick="healingResetAll()" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:4px;padding:3px 12px;font-size:12px;cursor:pointer;">重置</button>
    </span>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:16px;margin-bottom:12px;">
      <div style="font-size:12px;color:var(--text-secondary);">总计: <b id="healing-total">0</b></div>
      <div style="font-size:12px;color:var(--success);">可用: <b id="healing-avail">0</b></div>
      <div style="font-size:12px;color:var(--warning);">冷却中: <b id="healing-cooldown">0</b></div>
      <div style="font-size:12px;color:var(--danger);">耗尽: <b id="healing-exhausted">0</b></div>
      <div style="font-size:12px;color:var(--text-secondary);">历史: <b id="healing-last">--</b></div>
    </div>
    <div id="healing-timeline" class="healing-timeline">
      <div class="healing-timeline-empty">加载中...</div>
    </div>
    <div id="healing-actions-list" style="display:none;"></div>
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

<!-- Sandbox Code Runner 代码沙箱面板 -->
<div class="panel-full" id="panel-sandbox">
  <div class="panel-header">
    <h2>Sandbox Code Runner</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-sandbox')">▼</button>
    <span class="panel-actions" style="display:flex;gap:8px;">
      <select id="sandbox-engine" onchange="updateSandboxEngine()"
        style="padding:4px 8px;border-radius:4px;border:1px solid var(--card-border);background:rgba(255,255,255,0.06);color:var(--text);font-size:0.75rem;">
        <option value="local_subprocess">local_subprocess</option>
        <option value="local_direct">local_direct</option>
      </select>
      <span id="sandbox-status-dot" style="width:8px;height:8px;border-radius:50%;background:var(--success);display:inline-block;margin-left:4px;"></span>
    </span>
  </div>
  <div class="panel-body">
    <textarea id="sandbox-editor" placeholder="# Write Python code here&#10;print('Hello, JARVIS Sandbox!')&#10;&#10;for i in range(5):&#10;    print(f'Iteration {i}')"
      style="width:100%;height:160px;background:rgba(0,0,0,0.3);color:var(--text);border:1px solid var(--card-border);border-radius:6px;padding:12px;font-family:'Cascadia Code','Fira Code','Consolas',monospace;font-size:0.8rem;line-height:1.5;resize:vertical;outline:none;tab-size:4;"></textarea>
    <div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap;">
      <button id="sandbox-run-btn" onclick="runSandboxCode()" class="btn btn-sm"
        style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:6px 16px;cursor:pointer;font-family:inherit;font-size:0.8rem;">
        Run
      </button>
      <label style="font-size:0.75rem;color:var(--text-secondary);display:flex;align-items:center;gap:4px;cursor:pointer;">
        Timeout (s): <input type="number" id="sandbox-timeout" value="30" min="1" max="300"
          style="width:55px;padding:3px 6px;border-radius:4px;border:1px solid var(--card-border);background:rgba(255,255,255,0.06);color:var(--text);font-size:0.75rem;">
      </label>
      <span id="sandbox-exec-time" style="font-size:0.72rem;color:var(--text-dim);margin-left:auto;"></span>
    </div>
    <div style="margin-top:10px;display:flex;gap:10px;">
      <div style="flex:1;">
        <div style="font-size:0.7rem;color:var(--text-secondary);margin-bottom:4px;font-weight:600;">STDOUT</div>
        <pre id="sandbox-stdout" style="min-height:60px;max-height:240px;overflow-y:auto;background:rgba(0,0,0,0.2);color:#b8f5b8;border:1px solid var(--card-border);border-radius:4px;padding:8px;font-size:0.75rem;font-family:monospace;margin:0;white-space:pre-wrap;word-break:break-all;"></pre>
      </div>
      <div style="flex:1;">
        <div style="font-size:0.7rem;color:var(--text-secondary);margin-bottom:4px;font-weight:600;">STDERR / Exit Code</div>
        <pre id="sandbox-stderr" style="min-height:60px;max-height:240px;overflow-y:auto;background:rgba(0,0,0,0.2);color:#ff9494;border:1px solid var(--card-border);border-radius:4px;padding:8px;font-size:0.75rem;font-family:monospace;margin:0;white-space:pre-wrap;word-break:break-all;"></pre>
      </div>
    </div>
    <!-- Execution History -->
    <div style="margin-top:14px;display:flex;justify-content:space-between;align-items:center;">
      <div style="font-size:0.72rem;color:var(--text-secondary);font-weight:600;">Execution History</div>
      <button onclick="refreshSandboxHistory()" class="btn btn-sm"
        style="background:transparent;color:var(--text-secondary);border:1px solid var(--card-border);border-radius:4px;padding:2px 10px;cursor:pointer;font-size:0.7rem;">Refresh</button>
    </div>
    <div id="sandbox-history" style="font-size:0.7rem;max-height:160px;overflow-y:auto;margin-top:6px;"></div>
  </div>
</div>

<!-- Distributed Tracing Panel -->
<div class="panel-full" id="panel-traces">
  <div class="panel-header">
    <h2>Tracing <span id="traces-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:var(--bg-secondary);color:var(--text-muted);">0</span></h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-traces')">▼</button>
    <span class="panel-actions">
      <button onclick="fetchTraces()" style="background:transparent;color:var(--text-secondary);border:1px solid var(--card-border);border-radius:4px;padding:2px 10px;cursor:pointer;font-size:0.7rem;">Refresh</button>
    </span>
  </div>
  <div class="panel-body">
    <div id="traces-list" style="font-size:0.75rem;">
      <div style="color:var(--text-muted);text-align:center;padding:16px;">暂无追踪数据，执行任务后自动生成</div>
    </div>
    <div id="traces-detail" style="display:none;margin-top:12px;padding:10px;background:rgba(255,255,255,0.03);border-radius:6px;border:1px solid var(--card-border);"></div>
  </div>
</div>

<!-- Pipeline Execution Visualization Panel -->
<div class="panel-full" id="panel-pipeline">
  <div class="panel-header">
    <h2>Pipeline <span id="pipeline-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:var(--bg-secondary);color:var(--text-muted);">0</span></h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-pipeline')">▼</button>
    <span class="panel-actions">
      <button onclick="refreshPipelineList()" style="background:transparent;color:var(--text-secondary);border:1px solid var(--card-border);border-radius:4px;padding:2px 10px;cursor:pointer;font-size:0.7rem;">Refresh</button>
    </span>
  </div>
  <div class="panel-body">
    <div id="pipeline-list" style="font-size:0.75rem;">
      <div style="color:var(--text-muted);text-align:center;padding:16px;">尚无 Pipeline 执行记录</div>
    </div>
    <div id="pipeline-detail" style="display:none;margin-top:12px;padding:10px;background:rgba(255,255,255,0.03);border-radius:6px;border:1px solid var(--card-border);"></div>
  </div>
</div>

<!-- Governance Rules Panel -->
<div class="panel-full" id="panel-governance">
  <div class="panel-header">
    <h2>Governance Rules</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-governance')">▼</button>
    <span class="panel-actions" style="display:flex;gap:8px;">
      <span id="gov-count" style="color:var(--text-secondary);font-size:13px;"></span>
      <button onclick="openGovernanceModal()" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:0.75rem;">+ 新建规则</button>
    </span>
  </div>
  <div class="panel-body" id="gov-rules-container">
    <div class="gov-empty">暂无治理规则，点击「新建规则」添加</div>
  </div>
</div>

<!-- Alert Rules Panel -->
<div class="panel-full" id="panel-alert-rules">
  <div class="panel-header">
    <h2>Alert Rules</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-alert-rules')">▼</button>
    <span class="panel-actions" style="display:flex;gap:8px;">
      <span id="alert-rule-count" style="color:var(--text-secondary);font-size:13px;"></span>
      <button onclick="openAlertRuleModal()" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:0.75rem;">+ 新建规则</button>
    </span>
  </div>
  <div class="panel-body" id="alert-rules-container">
    <div class="alert-empty">暂无告警规则，点击「新建规则」添加</div>
  </div>
</div>

<!-- Alert Rules New Rule Modal -->
<div class="alert-modal-overlay" id="alert-rule-modal">
  <div class="alert-modal-card">
    <h3>新建告警规则</h3>
    <label>规则名称</label>
    <input type="text" id="ar-new-name" placeholder="规则名称...">
    <label>触发条件描述</label>
    <input type="text" id="ar-new-condition" placeholder="当 任务失败率 > 10%">
    <label>阈值</label>
    <input type="number" id="ar-new-threshold" placeholder="阈值数值" step="0.01">
    <label>严重级别</label>
    <select id="ar-new-severity">
      <option value="critical">critical — 严重 (红色)</option>
      <option value="warning" selected>warning — 警告 (橙色)</option>
      <option value="info">info — 提示 (蓝色)</option>
    </select>
    <div class="alert-modal-actions">
      <button class="alert-btn-cancel" onclick="closeAlertRuleModal()">取消</button>
      <button class="alert-btn-submit" onclick="createAlertRule()">提交</button>
    </div>
  </div>
</div>

<!-- Governance New Rule Modal -->
<div class="gov-modal-overlay" id="gov-modal">
  <div class="gov-modal-card">
    <h3>新建治理规则</h3>
    <label>规则描述</label>
    <textarea id="gov-new-desc" rows="3" placeholder="输入规则描述..."></textarea>
    <label>优先级</label>
    <select id="gov-new-priority">
      <option value="P0">P0 — 立即阻止 (Critical)</option>
      <option value="P1">P1 — 需要审批 (High)</option>
      <option value="P2" selected>P2 — 标记提醒 (Medium)</option>
      <option value="P3">P3 — 仅记录 (Low)</option>
    </select>
    <label>修复建议 (可选)</label>
    <textarea id="gov-new-remediation" rows="2" placeholder="触发此规则时的修复建议..."></textarea>
    <div class="gov-modal-actions">
      <button class="gov-btn-cancel" onclick="closeGovernanceModal()">取消</button>
      <button class="gov-btn-submit" onclick="createGovernanceRule()">提交</button>
    </div>
  </div>
</div>

<!-- Plugin Marketplace Panel -->
<div class="panel-full" id="panel-plugins">
  <div class="panel-header">
    <h2>Plugin Marketplace</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-plugins')">▼</button>
    <span class="panel-actions" style="display:flex;gap:8px;">
      <span id="plugin-stats" style="color:var(--text-secondary);font-size:13px;"></span>
    </span>
  </div>
  <div class="panel-body">
    <div class="plugin-tabs">
      <button class="plugin-tab-btn active" id="tab-available" onclick="switchPluginTab('available')">Available</button>
      <button class="plugin-tab-btn" id="tab-installed" onclick="switchPluginTab('installed')">Installed</button>
    </div>
    <div class="plugin-grid" id="plugin-grid"></div>
  </div>
</div>

<!-- Plugin Config Modal -->
<div class="modal-overlay plugin-config-modal" id="plugin-config-modal">
  <div class="modal-box plugin-config-content">
    <h3 id="plugin-config-title">Plugin Config</h3>
    <label class="config-label">Configuration (JSON)</label>
    <textarea id="plugin-config-textarea"></textarea>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closePluginConfig()">取消</button>
      <button class="btn-save" onclick="savePluginConfig()">保存</button>
    </div>
    <div class="modal-error" id="plugin-config-error"></div>
  </div>
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
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
      <button onclick="runEvals()" id="evals-run-btn" class="btn btn-sm" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;">Run All Evals</button>
      <span id="evals-status" style="font-size:11px;color:var(--text-muted);"></span>
    </div>
    <div id="evals-donut-area">
      <div class="evals-donut">
        <svg width="140" height="140" viewBox="0 0 140 140">
          <circle cx="70" cy="70" r="52" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="12"/>
          <circle id="evals-arc-pass" cx="70" cy="70" r="52" fill="none" stroke="var(--success)" stroke-width="12" stroke-linecap="butt" stroke-dasharray="0 326.7"/>
          <circle id="evals-arc-fail" cx="70" cy="70" r="52" fill="none" stroke="var(--danger)" stroke-width="12" stroke-linecap="butt" stroke-dasharray="0 326.7"/>
          <circle id="evals-arc-error" cx="70" cy="70" r="52" fill="none" stroke="var(--warning)" stroke-width="12" stroke-linecap="butt" stroke-dasharray="0 326.7"/>
        </svg>
        <div class="evals-donut-center">
          <div class="evals-donut-pct" id="evals-donut-pct">--</div>
          <div class="evals-donut-label">Pass Rate</div>
        </div>
      </div>
      <div class="evals-donut-legend">
        <span><span class="leg-dot pass"></span> Passed <strong id="evals-passed" style="color:var(--success);margin-left:2px;">--</strong></span>
        <span><span class="leg-dot fail"></span> Failed <strong id="evals-failed" style="color:var(--danger);margin-left:2px;">--</strong></span>
        <span><span class="leg-dot warn"></span> Errored <strong id="evals-errored" style="color:var(--warning);margin-left:2px;">--</strong></span>
      </div>
    </div>
    <div class="evals-toggle-bar">
      <button onclick="toggleAllSuites(true)">Expand All</button>
      <button onclick="toggleAllSuites(false)" style="margin-left:4px;">Collapse All</button>
    </div>
    <div id="evals-suites" style="max-height:360px;overflow-y:auto;font-size:12px;"></div>
  </div><!-- .panel-body -->
</div>

<!-- Prompt Templates 面板 -->
<div class="panel" id="panel-templates" style="min-width:0;">
  <div class="panel-header">
    <h2>Prompt Templates</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-templates')">-</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
      <button onclick="refreshTemplates()" class="btn btn-sm" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;">Refresh</button>
      <span id="templates-status" style="font-size:11px;color:var(--text-muted);">Auto-refresh: 30s</span>
    </div>
    <table class="ministers-table" style="width:100%;">
      <thead>
        <tr>
          <th>Capability</th><th>Version</th><th>Score</th><th>Frozen</th><th>Actions</th>
        </tr>
      </thead>
      <tbody id="templates-tbody"></tbody>
    </table>
    <!-- Template detail expansion -->
    <div id="template-detail" style="display:none;margin-top:12px;padding:12px;background:var(--bg-secondary);border-radius:6px;font-size:12px;">
      <div id="template-detail-content"></div>
      <button onclick="closeTemplateDetail()" style="margin-top:8px;background:var(--border-color);color:var(--text-secondary);border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:11px;">Close</button>
    </div>
  </div><!-- .panel-body -->
</div>

<!-- Model Cost 模型成本面板 -->
<div class="panel" id="panel-model-cost">
  <div class="panel-header">
    <h2>Model Routing & Cost</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-model-cost')">-</button>
  </div>
  <div class="panel-body">
    <div class="ring-progress">
      <svg width="120" height="120" viewBox="0 0 120 120">
        <circle class="ring-bg" cx="60" cy="60" r="52"/>
        <circle class="ring-fill" id="cost-ring" cx="60" cy="60" r="52"
          stroke-dasharray="326.7" stroke-dashoffset="326.7"/>
      </svg>
      <div class="ring-center">
        <div class="ring-pct" id="cost-savings-pct">0%</div>
        <div class="ring-label">Savings</div>
      </div>
    </div>
    <div class="tier-bars" id="tier-distribution">
      <div class="tier-bar-row">
        <span class="tier-bar-label">Cheap</span>
        <div class="tier-bar-track"><div class="tier-bar-fill cheap" id="bar-cheap" style="width:0%"></div></div>
        <span class="tier-bar-count" id="cnt-cheap">0</span>
      </div>
      <div class="tier-bar-row">
        <span class="tier-bar-label">Standard</span>
        <div class="tier-bar-track"><div class="tier-bar-fill standard" id="bar-standard" style="width:0%"></div></div>
        <span class="tier-bar-count" id="cnt-standard">0</span>
      </div>
      <div class="tier-bar-row">
        <span class="tier-bar-label">Premium</span>
        <div class="tier-bar-track"><div class="tier-bar-fill premium" id="bar-premium" style="width:0%"></div></div>
        <span class="tier-bar-count" id="cnt-premium">0</span>
      </div>
    </div>
    <div class="cost-cards">
      <div class="cost-card">
        <div class="cost-val" id="cost-total-reqs">0</div>
        <div class="cost-label">Total Requests</div>
      </div>
      <div class="cost-card">
        <div class="cost-val" style="color:var(--success);" id="cost-saved-val">$0</div>
        <div class="cost-label">Est. Cost Saved</div>
      </div>
    </div>
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

<!-- Approval 审批面板 -->
<div class="panel panel-full" id="panel-approval">
  <div class="panel-header">
    <h2>Approval 审批队列</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-approval')">▼</button>
    <span id="approval-badge" style="display:none;background:var(--danger);color:#fff;border-radius:10px;padding:1px 8px;font-size:11px;margin-left:8px;"></span>
  </div>
  <div class="panel-body">
    <div class="tab-bar" style="display:flex;gap:2px;margin-bottom:12px;border-bottom:1px solid var(--card-border);">
      <button class="approval-tab active" onclick="switchApprovalTab('pending')" id="approval-tab-pending" style="background:none;border:none;color:var(--accent);padding:6px 14px;cursor:pointer;font-family:inherit;font-size:0.78rem;border-bottom:2px solid var(--accent);margin-bottom:-1px;">待审批</button>
      <button class="approval-tab" onclick="switchApprovalTab('history')" id="approval-tab-history" style="background:none;border:none;color:var(--text-secondary);padding:6px 14px;cursor:pointer;font-family:inherit;font-size:0.78rem;border-bottom:2px solid transparent;margin-bottom:-1px;">审批历史</button>
      <button class="approval-tab" onclick="switchApprovalTab('policies')" id="approval-tab-policies" style="background:none;border:none;color:var(--text-secondary);padding:6px 14px;cursor:pointer;font-family:inherit;font-size:0.78rem;border-bottom:2px solid transparent;margin-bottom:-1px;">策略配置</button>
    </div>
    <div id="approval-tab-content" style="font-size:12px;max-height:400px;overflow-y:auto;">
      <div class="empty">Loading...</div>
    </div>
  </div><!-- .panel-body -->
</div>

<!-- Version History 版本历史面板 -->
<div class="panel panel-full" id="panel-versions">
  <div class="panel-header">
    <h2>Version History & Rollback</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-versions')">▼</button>
    <span class="panel-actions" style="display:flex;gap:8px;">
      <input id="ver-snap-desc" placeholder="版本描述 (可选)" style="width:180px;background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-primary);border-radius:4px;padding:3px 8px;font-size:12px;font-family:inherit;">
      <button onclick="createSnapshot()" class="btn btn-sm" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;font-size:12px;">+ Snapshot</button>
      <button onclick="refreshVersions()" class="btn btn-sm" style="background:var(--bg-secondary);color:var(--text-secondary);border:1px solid var(--border-color);border-radius:4px;padding:6px 14px;cursor:pointer;font-size:12px;">Refresh</button>
    </span>
  </div>
  <div class="panel-body">
    <div id="version-list" style="font-size:12px;max-height:400px;overflow-y:auto;">
      <div class="empty">Loading...</div>
    </div>
    <!-- Rollback preview area -->
    <div id="version-diff-preview" style="display:none;margin-top:12px;padding:12px;background:var(--bg-secondary);border-radius:6px;font-size:12px;"></div>
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

<!-- Pipeline Monitor DAG Panel -->
<div class="panel" id="panel-pipeline-monitor" style="min-width:0;">
  <div class="panel-header">
    <h2>Pipeline DAG 监控</h2>
    <span style="margin-left:auto;display:flex;align-items:center;gap:8px;">
      <span id="pmon-live-badge" style="font-size:10px;padding:2px 8px;border-radius:10px;background:var(--bg-secondary);color:var(--text-muted);">实时</span>
      <button class="panel-collapse-btn" onclick="togglePanel('panel-pipeline-monitor')">&#9660;</button>
    </span>
  </div>
  <div class="panel-body">
    <!-- Stats row -->
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:80px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:20px;font-weight:700;color:var(--accent);" id="pmon-total">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">总计</div>
      </div>
      <div style="flex:1;min-width:80px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:20px;font-weight:700;color:var(--warning);" id="pmon-active">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">活跃</div>
      </div>
      <div style="flex:1;min-width:80px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:20px;font-weight:700;color:var(--success);" id="pmon-done">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">完成</div>
      </div>
      <div style="flex:1;min-width:80px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:20px;font-weight:700;color:var(--danger);" id="pmon-failed">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">失败</div>
      </div>
    </div>

    <!-- DAG Canvas -->
    <div style="margin-bottom:12px;">
      <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">执行拓扑图</div>
      <div id="pmon-dag-canvas" style="background:var(--bg-secondary);border-radius:8px;min-height:200px;max-height:400px;overflow-y:auto;padding:16px;display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start;justify-content:center;">
        <div style="color:var(--text-muted);font-size:12px;text-align:center;width:100%;">暂无流水线数据，执行任务后自动生成</div>
      </div>
    </div>

    <!-- Pipeline list with expandable DAG -->
    <div id="pmon-list" style="font-size:12px;max-height:400px;overflow-y:auto;"></div>
  </div><!-- .panel-body -->
</div>

<!-- Governance & Autonomy Panel -->
<div class="panel" id="panel-governance">
  <div class="panel-header">
    <h2>治理与自主权 <span class="count" id="govRuleCount">0</span></h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-governance')">&#9660;</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:70px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--success);" id="gov-green">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">GREEN</div>
      </div>
      <div style="flex:1;min-width:70px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--warning);" id="gov-yellow">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">YELLOW</div>
      </div>
      <div style="flex:1;min-width:70px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--danger);" id="gov-red">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">RED</div>
      </div>
      <div style="flex:1;min-width:70px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--accent);" id="gov-rules-total">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">规则</div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">治理规则</div>
    <div id="gov-rules-list" style="max-height:180px;overflow-y:auto;font-size:12px;">
      <div class="empty">No governance rules</div>
    </div>
  </div>
</div>

<!-- Failure Recovery Panel -->
<div class="panel" id="panel-recovery">
  <div class="panel-header">
    <h2>故障恢复</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-recovery')">&#9660;</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--success);" id="rec-success">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">成功</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--warning);" id="rec-retry">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">重试成功</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--accent);" id="rec-degraded">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">降级</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--danger);" id="rec-failed">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">失败</div>
      </div>
    </div>
    <div style="margin-bottom:8px;">
      <span style="font-size:11px;color:var(--text-secondary);">熔断器: </span>
      <span id="cb-state" style="font-size:12px;font-weight:600;padding:2px 8px;border-radius:4px;">--</span>
      <button onclick="resetCircuitBreaker()" id="cb-reset-btn" class="btn btn-sm" style="margin-left:8px;background:var(--accent);color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:11px;">重置</button>
    </div>
    <div id="cb-stats" style="font-size:11px;color:var(--text-secondary);"></div>
  </div>
</div>

<!-- Tool Guard Panel -->
<div class="panel" id="panel-toolguard">
  <div class="panel-header">
    <h2>Tool Guard</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-toolguard')">&#9660;</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--success);" id="tg-passed">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">Passed</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--danger);" id="tg-blocked">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">Blocked</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--warning);" id="tg-pii">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">PII Hits</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:18px;font-weight:700;color:var(--accent);" id="tg-rate-limited">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">Rate Limited</div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">Recent Intercepts</div>
    <div id="tg-intercepts" style="max-height:140px;overflow-y:auto;font-size:11px;">
      <div class="empty">No intercepts</div>
    </div>
  </div>
</div>

<!-- Hallucination Watch Panel -->
<div class="panel" id="panel-hallucination">
  <div class="panel-header">
    <h2>Hallucination Watch</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-hallucination')">&#9660;</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:14px;font-weight:700;color:var(--accent);" id="hall-threshold-critical">0.90</div>
        <div style="font-size:10px;color:var(--text-secondary);">CRITICAL</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:14px;font-weight:700;color:var(--danger);" id="hall-threshold-high">0.75</div>
        <div style="font-size:10px;color:var(--text-secondary);">HIGH</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:14px;font-weight:700;color:var(--warning);" id="hall-threshold-med">0.50</div>
        <div style="font-size:10px;color:var(--text-secondary);">MEDIUM</div>
      </div>
      <div style="flex:1;min-width:60px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;">
        <div style="font-size:14px;font-weight:700;color:var(--text-secondary);" id="hall-status">--</div>
        <div style="font-size:10px;color:var(--text-secondary);">Status</div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">High-Risk Detections</div>
    <div id="hall-recent" style="max-height:140px;overflow-y:auto;font-size:11px;">
      <div class="empty">No detections yet</div>
    </div>
  </div>
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

<!-- Hierarchical Memory Panel -->
<div class="panel" id="panel-memory">
  <div class="panel-header">
    <h2>Memory Hierarchy</h2>
    <button class="panel-collapse-btn" onclick="togglePanel('panel-memory')">&#9660;</button>
  </div>
  <div class="panel-body">
    <div style="display:flex;gap:12px;margin-bottom:10px;flex-wrap:wrap;">
      <div style="flex:1;min-width:55px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #a78bfa;">
        <div style="font-size:13px;font-weight:700;color:#a78bfa;" id="mem-working">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">Working</div>
      </div>
      <div style="flex:1;min-width:55px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #818cf8;">
        <div style="font-size:13px;font-weight:700;color:#818cf8;" id="mem-episodic">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">Episodic</div>
      </div>
      <div style="flex:1;min-width:55px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #f59e0b;">
        <div style="font-size:13px;font-weight:700;color:#f59e0b;" id="mem-semantic">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">Semantic</div>
      </div>
      <div style="flex:1;min-width:55px;text-align:center;padding:8px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #10b981;">
        <div style="font-size:13px;font-weight:700;color:#10b981;" id="mem-procedural">0</div>
        <div style="font-size:10px;color:var(--text-secondary);">Procedural</div>
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:8px;align-items:center;">
      <span style="font-size:11px;color:var(--text-secondary);">Consolidation:</span>
      <span style="font-size:11px;font-weight:600;" id="mem-consolidation-status">idle</span>
      <span style="font-size:10px;color:var(--text-muted);margin-left:auto;" id="mem-last-consolidation">--</span>
      <button style="font-size:10px;padding:3px 8px;background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-primary);border-radius:4px;cursor:pointer;" onclick="triggerConsolidation()">Consolidate</button>
    </div>
    <div style="font-size:10px;color:var(--text-muted);margin-bottom:6px;">
      Retention: Epi <span id="mem-ret-epi">--</span> | Sem <span id="mem-ret-sem">--</span> &nbsp;
      Threshold: <span id="mem-threshold">0.55</span>
    </div>
    <div style="font-size:10px;color:var(--text-secondary);margin-bottom:4px;">Last Consolidation</div>
    <div id="mem-history" style="max-height:100px;overflow-y:auto;font-size:10px;">
      <div class="empty">No consolidation yet</div>
    </div>

    <!-- GraphRAG sub-panel -->
    <div style="border-top:1px solid var(--border-color);margin-top:8px;padding-top:8px;">
      <div style="font-size:11px;font-weight:700;color:var(--text-primary);margin-bottom:6px;">&#129504; GraphRAG (L4 Knowledge Graph)</div>
      <div style="display:flex;gap:12px;margin-bottom:8px;flex-wrap:wrap;">
        <div style="flex:1;min-width:55px;text-align:center;padding:6px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #ec4899;">
          <div style="font-size:13px;font-weight:700;color:#ec4899;" id="gr-entities">0</div>
          <div style="font-size:10px;color:var(--text-secondary);">Entities</div>
        </div>
        <div style="flex:1;min-width:55px;text-align:center;padding:6px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #8b5cf6;">
          <div style="font-size:13px;font-weight:700;color:#8b5cf6;" id="gr-relations">0</div>
          <div style="font-size:10px;color:var(--text-secondary);">Relations</div>
        </div>
        <div style="flex:1;min-width:55px;text-align:center;padding:6px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid #3b82f6;">
          <div style="font-size:13px;font-weight:700;color:#3b82f6;" id="gr-docs">0</div>
          <div style="font-size:10px;color:var(--text-secondary);">Documents</div>
        </div>
      </div>
      <div style="font-size:10px;color:var(--text-muted);margin-bottom:4px;">Avg Degree: <span id="gr-avg-degree">--</span> | Max: <span id="gr-max-degree">--</span></div>
      <div style="font-size:10px;font-weight:600;color:var(--text-primary);margin-bottom:4px;">Top Entities</div>
      <div id="gr-top-entities" style="font-size:10px;max-height:80px;overflow-y:auto;"></div>
      <div style="margin-top:8px;">
        <input type="text" id="gr-search-input" placeholder="Search entity..." style="font-size:10px;padding:3px 6px;background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-primary);border-radius:4px;width:120px;"
          onkeydown="if(event.key==='Enter') searchGraphEntity()" />
        <button onclick="searchGraphEntity()" style="font-size:10px;padding:3px 8px;background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-primary);border-radius:4px;cursor:pointer;">Search</button>
      </div>
      <div id="gr-entity-detail" style="font-size:10px;margin-top:6px;max-height:120px;overflow-y:auto;display:none;"></div>
    </div>
  </div>
</div>

<script>
  var API = "{{API_BASE}}";

  function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
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

  // ── Summary bar ──
  function renderSummaryBar(data) {
    setSummaryValue('sv-ministers', data.active_ministers, 'accent');
    var rate = data.success_rate;
    var rateClass = rate >= 95 ? 'success' : rate >= 80 ? 'warning' : 'danger';
    setSummaryValue('sv-success', rate != null ? rate + '%' : '--', rateClass);
    setSummaryValue('sv-alerts', data.active_alerts);
    setSummaryValue('sv-healing', data.healings_today);
    setSummaryValue('sv-pipelines', data.pipelines_today);

    // Alert pulse animation
    var alertCard = document.getElementById('sc-alerts');
    if (data.active_alerts > 0) {
      alertCard.classList.add('alert-pulse');
    } else {
      alertCard.classList.remove('alert-pulse');
    }

    // Update header uptime
    var hu = document.getElementById('health-uptime');
    if (hu && data.uptime_seconds != null) {
      var h = Math.floor(data.uptime_seconds / 3600);
      var m = Math.floor((data.uptime_seconds % 3600) / 60);
      hu.textContent = '运行 ' + h + 'h ' + m + 'm';
    }
  }

  function setSummaryValue(id, val, cls) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = val != null ? val : '--';
    if (cls) {
      el.className = 'summary-value ' + cls;
    } else {
      el.className = 'summary-value';
    }
  }

  function refreshSummary() {
    fetch(API + '/api/dashboard/summary')
      .then(function(r) { return r.json(); })
      .then(function(d) { renderSummaryBar(d); })
      .catch(function() {});
  }

  function _bumpSummaryCounter(elId) {
    var el = document.getElementById(elId);
    if (!el) return;
    var v = parseInt(el.textContent, 10);
    if (!isNaN(v)) {
      el.textContent = v + 1;
    }
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

  // ═══ Self-Healing Dashboard functions ═════════════════════

  async function refreshHealing() {
    try {
      var resp = await fetch(API + '/api/healing/actions');
      var data = await resp.json();
      renderHealingActions(data);
    } catch (e) {
      console.error('Healing refresh failed:', e);
    }
  }

  function renderHealingActions(data) {
    var actions = data.actions || [];
    document.getElementById('healing-total').textContent = actions.length;

    var avail = 0, cooldown = 0, exhausted = 0;
    actions.forEach(function(a) {
      if (a.exhausted) exhausted++;
      else if (a.on_cooldown) cooldown++;
      else if (a.enabled) avail++;
    });

    document.getElementById('healing-avail').textContent = avail;
    document.getElementById('healing-cooldown').textContent = cooldown;
    document.getElementById('healing-exhausted').textContent = exhausted;

    var badge = document.getElementById('healing-badge');
    if (exhausted > 0) {
      badge.textContent = '需关注';
      badge.style.background = 'var(--danger)';
      badge.style.color = '#fff';
    } else if (cooldown > 0) {
      badge.textContent = '冷却中';
      badge.style.background = 'var(--warning)';
      badge.style.color = '#fff';
    } else if (avail > 0) {
      badge.textContent = '就绪';
      badge.style.background = 'var(--success)';
      badge.style.color = '#fff';
    } else {
      badge.textContent = '空闲';
      badge.style.background = 'var(--bg-secondary)';
      badge.style.color = 'var(--text-muted)';
    }

    var listEl = document.getElementById('healing-actions-list');
    if (actions.length === 0) {
      listEl.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:12px;">无注册自愈动作</div>';
      return;
    }

    listEl.innerHTML = actions.map(function(a) {
      var statusColor, statusText;
      if (a.exhausted)      { statusColor = 'var(--danger)';  statusText = '耗尽'; }
      else if (a.on_cooldown) { statusColor = 'var(--warning)'; statusText = '冷却 ' + a.cooldown_remaining + 's'; }
      else if (!a.enabled)  { statusColor = 'var(--text-muted)'; statusText = '已禁用'; }
      else                  { statusColor = 'var(--success)'; statusText = '就绪'; }

      var progressWidth = a.max_attempts
        ? Math.min(100, a.attempts / a.max_attempts * 100)
        : 0;
      var progressColor = progressWidth >= 100 ? 'var(--danger)' :
                          progressWidth >= 50 ? 'var(--warning)' : 'var(--success)';
      var attemptLabel = a.max_attempts ? a.attempts + '/' + a.max_attempts : a.attempts + '/∞';

      return '<div style="padding:8px 0;border-bottom:1px solid var(--border-color);display:flex;align-items:center;gap:12px;">'
        + '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + statusColor + ';flex-shrink:0;" title="' + statusText + '"></span>'
        + '<div style="flex:1;min-width:0;">'
        + '<div style="font-weight:500;font-size:13px;">' + a.name
        + ' <span style="color:var(--text-muted);font-size:10px;">← ' + a.alert_rule + '</span></div>'
        + '<div style="display:flex;align-items:center;gap:12px;margin-top:4px;">'
        + '<span style="font-size:10px;color:var(--text-secondary);">' + attemptLabel + ' attempts</span>'
        + (a.tags && a.tags.length > 0
          ? '<span style="font-size:10px;color:var(--text-muted);">' + a.tags.join(', ') + '</span>'
          : '')
        + '</div>'
        + (a.max_attempts
          ? '<div style="width:80px;height:4px;background:var(--bg-secondary);border-radius:2px;margin-top:4px;">'
          + '<div style="width:' + progressWidth + '%;height:100%;background:' + progressColor + ';border-radius:2px;"></div></div>'
          : '')
        + '</div>'
        + '<div style="display:flex;gap:4px;flex-shrink:0;">'
        + (!a.exhausted && a.enabled
          ? '<button onclick="healingTrigger(\'' + a.name + '\')" style="background:var(--success);color:#fff;border:none;border-radius:3px;padding:2px 8px;font-size:11px;cursor:pointer;">触发</button>'
          : '<button disabled style="background:transparent;color:var(--text-muted);border:1px solid var(--border-color);border-radius:3px;padding:2px 8px;font-size:11px;">触发</button>')
        + '<button onclick="healingReset(\'' + a.name + '\')" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:3px;padding:2px 8px;font-size:11px;cursor:pointer;">重置</button>'
        + '<button onclick="healingToggle(\'' + a.name + '\', ' + !a.enabled + ')" style="background:none;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:3px;padding:2px 8px;font-size:11px;cursor:pointer;">' + (a.enabled ? '停用' : '启用') + '</button>'
        + '</div>'
        + '</div>';
    }).join('');

    // Also refresh history
    healRefreshHistory();
  }

  async function healRefreshHistory() {
    try {
      var resp = await fetch(API + '/api/healing/history?limit=5');
      var data = await resp.json();
      var last = data.history[0];
      if (last) {
        var dt = new Date(last.timestamp * 1000);
        document.getElementById('healing-last').textContent =
          dt.toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'})
          + ' ' + last.action_name
          + ' ' + (last.success ? '✓' : '✗');
      }
    } catch (e) {}
  }

  async function refreshHealingTimeline() {
    var timeline = document.getElementById('healing-timeline');
    if (!timeline) return;
    try {
      var resp = await fetch(API + '/api/healing/timeline?limit=20');
      var data = await resp.json();
      var items = data.timeline || [];

      if (items.length === 0) {
        timeline.innerHTML = '<div class="healing-timeline-empty">暂无自愈操作记录，系统健康运行中</div>';
        return;
      }

      timeline.innerHTML = items.map(function(r) {
        var resultLabel = r.result === 'success' ? 'SUCCESS' : (r.result === 'running' ? 'RUNNING' : 'FAILED');
        var dt = new Date(r.timestamp * 1000);
        var timeStr = dt.toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
        var elapsedStr = r.elapsed_ms > 0
          ? (r.elapsed_ms >= 1000 ? (r.elapsed_ms / 1000).toFixed(1) + 's' : r.elapsed_ms.toFixed(0) + 'ms')
          : '--';

        return '<div class="ht-entry">'
          + '<div class="ht-dot ' + r.result + '"></div>'
          + '<div class="ht-card">'
          + '<div class="ht-card-header">'
          + '<span class="ht-name">' + _esc(r.action_name) + '</span>'
          + '<span class="ht-badge ' + r.result + '">' + resultLabel + '</span>'
          + '</div>'
          + '<div class="ht-card-meta">'
          + '<span class="ht-source">' + _esc(r.triggered_by || 'manual') + '</span>'
          + '<span class="ht-time">' + timeStr + '</span>'
          + '<span class="ht-elapsed">' + elapsedStr + '</span>'
          + '</div>'
          + '</div>'
          + '</div>';
      }).join('');
    } catch (e) {
      console.error('Healing timeline refresh failed:', e);
    }
  }

  async function healingTrigger(name) {
    try {
      var resp = await fetch(API + '/api/healing/trigger/' + name, {method: 'POST'});
      var data = await resp.json();
      if (data.success) {
        refreshHealing();
      } else {
        alert('触发失败: ' + (data.error || '未知错误'));
      }
    } catch (e) {
      alert('请求失败: ' + e.message);
    }
  }

  async function healingReset(name) {
    try {
      await fetch(API + '/api/healing/reset/' + name, {method: 'POST'});
      refreshHealing();
    } catch (e) {}
  }

  async function healingResetAll() {
    try {
      await fetch(API + '/api/healing/reset/_all', {method: 'POST'});
      refreshHealing();
    } catch (e) {}
  }

  async function healingCheckAll() {
    try {
      var resp = await fetch(API + '/api/healing/check', {method: 'POST'});
      var data = await resp.json();
      alert('自愈检查完成: ' + data.actions_executed + ' 个动作已执行');
      refreshHealing();
    } catch (e) {
      alert('检查失败: ' + e.message);
    }
  }

  async function healingToggle(name, enabled) {
    try {
      await fetch(API + '/api/healing/toggle/' + name, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: enabled})
      });
      refreshHealing();
    } catch (e) {}
  }

  // ═══ Old heal button (kept for backward compat) ══════════════

  function triggerHeal() {
    healingCheckAll();
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

  // ── Toast notification helpers ──
  var _toastId = 0;
  var _eventLog = [];
  var _MAX_LOG = 100;

  function showToast(type, title, detail) {
    var id = ++_toastId;
    var icons = {
      dispatch: '⚡', sandbox: '🔧', pipeline: '📋', governance: '⚖️',
      healing: '💚', approval: '🛡️', memory: '🧠', eval: '🧪', alert: '🚨'
    };
    var container = document.getElementById('toast-container');
    var el = document.createElement('div');
    el.className = 'toast type-' + type;
    el.id = 'toast-' + id;
    el.innerHTML = '<div class="toast-icon">' + (icons[type] || '📌') + '</div>'
      + '<div class="toast-body"><div class="toast-title">' + _esc(title) + '</div>'
      + '<div class="toast-detail">' + _esc(detail) + '</div></div>'
      + '<button class="toast-close" onclick="dismissToast(' + id + ')">×</button>';
    container.appendChild(el);
    setTimeout(function() { dismissToast(id); }, 6000);
  }

  function dismissToast(id) {
    var el = document.getElementById('toast-' + id);
    if (!el) return;
    el.classList.add('removing');
    setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
  }

  function addEventLog(type, msg) {
    var now = new Date();
    var time = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0') + ':' + now.getSeconds().toString().padStart(2,'0');
    _eventLog.unshift({type: type, msg: msg, time: time});
    if (_eventLog.length > _MAX_LOG) _eventLog.length = _MAX_LOG;
    renderEventLog();
  }

  function renderEventLog() {
    var body = document.getElementById('event-log-body');
    var countEl = document.getElementById('event-log-count');
    countEl.textContent = _eventLog.length;
    if (_eventLog.length === 0) {
      body.innerHTML = '<div class="event-log-empty">暂无事件</div>';
      return;
    }
    var colors = { dispatch:'#6366f1', sandbox:'#06b6d4', pipeline:'#8b5cf6', governance:'#f59e0b', healing:'#10b981', approval:'#f43f5e', memory:'#a78bfa', eval:'#22d3ee', alert:'#ef4444' };
    var html = '';
    for (var i = 0; i < _eventLog.length; i++) {
      var e = _eventLog[i];
      html += '<div class="event-log-entry"><span class="log-dot" style="background:' + (colors[e.type]||'#888') + ';"></span><span class="log-time">' + e.time + '</span><span class="log-msg">' + _esc(e.msg) + '</span></div>';
    }
    body.innerHTML = html;
  }

  function toggleEventLog() {
    var panel = document.getElementById('event-log-panel');
    panel.classList.toggle('collapsed');
  }

  function _esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  function handleSSEEvent(msg) {
    var type = msg.type || 'unknown';
    var data = msg.data || msg;

    // Show toast for all substantive event types
    switch(type) {
      case 'dispatch':
        var minister = data.minister || 'unknown';
        var ok = data.success ? '✓' : '✗';
        showToast('dispatch', 'Dispatch ' + ok, minister + ' — ' + (data.intent || '') + ' (' + (data.elapsed_ms||0) + 'ms)');
        addEventLog('dispatch', 'Dispatch → ' + minister + ' ' + ok);
        fetchTaskHistory(); loadMinisters();
        refreshSummary();
        break;
      case 'sandbox':
        showToast('sandbox', 'Sandbox Exec', (data.engine||'?') + ' · exit ' + data.exit_code + ' · ' + (data.elapsed_ms||0) + 'ms');
        addEventLog('sandbox', 'Sandbox ' + (data.engine||'?') + ' exit=' + data.exit_code);
        break;
      case 'pipeline':
        showToast('pipeline', 'Pipeline ' + (data.status||'?'), (data.template||'') + (data.steps!=null ? ' · ' + data.steps + ' steps' : '') + ' · ' + (data.elapsed_ms||0) + 'ms');
        addEventLog('pipeline', 'Pipeline ' + (data.template||'') + ' → ' + (data.status||''));
        pushNotification('pipeline', (data.template||'Pipeline'), 'Status: ' + (data.status||'?') + ' · ' + (data.elapsed_ms||0) + 'ms', 'pipeline');
        refreshPipelineList();
        _bumpSummaryCounter('sv-pipelines');
        break;
      case 'governance':
        showToast('governance', 'Governance ' + (data.action||'?'), (data.description||data.rule_id||''));
        addEventLog('governance', 'Governance ' + (data.action||'') + ' rule=' + (data.rule_id||''));
        refreshGovernanceRules();
        break;
      case 'healing':
        showToast('healing', 'Healing: ' + (data.action_name||'?'), (data.result||'') + (data.triggered_by ? ' by ' + data.triggered_by : ''));
        addEventLog('healing', 'Healing ' + (data.action_name||'') + ' → ' + (data.result||''));
        pushNotification('healing', 'Healing: ' + (data.action_name||'?'), (data.result||'') + (data.triggered_by ? ' by ' + data.triggered_by : ''), 'healing');
        loadMinisters();
        refreshHealingTimeline();
        _bumpSummaryCounter('sv-healing');
        break;
      case 'approval':
        var approved = data.approved != null ? (data.approved ? 'Approved' : 'Denied') : 'Requested';
        showToast('approval', 'Approval ' + approved, (data.action||'') + ' · risk=' + (data.risk_level||'?'));
        addEventLog('approval', 'Approval ' + approved + ' ' + (data.action||''));
        pushNotification('approval', 'Approval ' + approved, (data.action||'') + ' · risk=' + (data.risk_level||'?'), 'approval');
        break;
      case 'memory':
        showToast('memory', 'Memory ' + (data.operation||'update'), (data.layer||'') + ' · ' + (data.detail||''));
        addEventLog('memory', 'Memory ' + (data.operation||'') + ' @ ' + (data.layer||''));
        loadMinisters();
        break;
      case 'eval':
        showToast('eval', 'Eval ' + (data.status||'completed'), (data.name||'') + ' \u00b7 ' + (data.score!=null ? 'score=' + data.score : ''));
        addEventLog('eval', 'Eval ' + (data.name||'') + ' \u2192 ' + (data.status||'completed'));
        if (typeof refreshEvals === 'function') refreshEvals();
        break;
      case 'alert':
        showToast('alert', '⚠ Alert: ' + (data.title||data.message||''), (data.message||data.detail||''));
        addEventLog('alert', 'Alert: ' + (data.title||data.message||''));
        pushNotification('alert', (data.title||data.message||'Alert'), (data.message||data.detail||''), 'alert');
        fetchAlertHistory();
        refreshSummary();
        break;
      case 'task_completed':
        fetchTaskHistory(); loadMinisters();
        break;
      case 'evolution':
        loadMeritBoard(); updateCharts(); loadMinisters();
        break;
      case 'heartbeat':
      case 'connected':
        break;
      default:
        // unknown event type — log only
        addEventLog(type, JSON.stringify(data).substring(0, 100));
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

  // ══════════════════════════════════════════════════════════════
  // Global Smart Search
  // ══════════════════════════════════════════════════════════════

  var searchTimer = null;
  function debouncedSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(doSearch, 300);
  }

  async function doSearch() {
    var q = document.getElementById('dashboard-search-input').value.trim();
    var badge = document.getElementById('search-badge');
    var results = document.getElementById('search-results');

    if (q.length < 2) {
      results.style.display = 'none';
      badge.style.display = 'none';
      return;
    }

    try {
      var resp = await fetch(API + '/api/dashboard/search?q=' + encodeURIComponent(q) + '&limit=5');
      var data = await resp.json();
      var total = (data.tasks || []).length + (data.evals || []).length + (data.audits || []).length + (data.healing || []).length + (data.context_versions || []).length + (data.memories || []).length;
      badge.textContent = total + ' 条结果';
      badge.style.display = 'inline';
      _lastQuery = q;
      _searchTab = 'all';
      renderSearchResults(data);
    } catch (e) {
      badge.textContent = '搜索失败';
      badge.style.display = 'inline';
    }
  }

  var _searchTab = 'all';
  var _lastSearchData = null;
  var _lastQuery = '';

  function switchSearchTab(tab) {
    _searchTab = tab;
    document.querySelectorAll('.search-tab').forEach(function(btn) {
      btn.style.color = 'var(--text-secondary)';
      btn.style.borderBottomColor = 'transparent';
      btn.classList.remove('active');
    });
    var activeBtn = document.getElementById('search-tab-' + tab);
    if (activeBtn) {
      activeBtn.style.color = 'var(--accent)';
      activeBtn.style.borderBottomColor = 'var(--accent)';
      activeBtn.classList.add('active');
    }
    if (_lastSearchData) renderSearchResults(_lastSearchData);
  }

  function renderSearchResults(data) {
    _lastSearchData = data;
    var el = document.getElementById('search-results');
    el.style.display = 'block';

    var q = _lastQuery;

    // ── Tab bar ──
    var tabBar = '<div style="display:flex;gap:0;border-bottom:1px solid var(--border-color);margin-bottom:14px;">';
    var tabs = [
      {id: 'all', label: '全部'},
      {id: 'tasks', label: '任务'},
      {id: 'memories', label: '记忆'},
    ];
    tabs.forEach(function(tab) {
      var isActive = _searchTab === tab.id;
      tabBar += '<button id="search-tab-' + tab.id + '" class="search-tab' + (isActive ? ' active' : '') + '" '
        + 'onclick="switchSearchTab(\'' + tab.id + '\')" '
        + 'style="background:none;border:none;border-bottom:2px solid ' + (isActive ? 'var(--accent)' : 'transparent') + ';'
        + 'color:' + (isActive ? 'var(--accent)' : 'var(--text-secondary)') + ';'
        + 'padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.2s;font-family:inherit;">'
        + tab.label + '</button>';
    });
    tabBar += '</div>';

    // ── Content by tab ──
    var sections = [];
    var sectionIcon = {tasks: '📋', evals: '🧪', audits: '📜', healing: '🩺', context_versions: '🏷️'};
    var sectionName = {tasks: '任务', evals: '评测', audits: '审计', healing: '自愈', context_versions: '版本快照'};

    var allKeys = ['tasks', 'evals', 'audits', 'healing', 'context_versions'];

    if (_searchTab === 'all' || _searchTab === 'tasks') {
      allKeys.forEach(function(key) {
        var items = data[key] || [];
        if (items.length === 0) return;

        var rows = items.map(function(item) {
          if (key === 'tasks') {
            return '<tr><td style="font-weight:500;">' + esc(item.description) + '</td>'
              + '<td style="color:var(--text-secondary);">' + (item.minister || '--') + '</td>'
              + '<td style="color:var(--text-muted);">' + item.status + '</td></tr>';
          } else if (key === 'evals') {
            return '<tr><td style="font-weight:500;">' + esc(item.suite) + '</td>'
              + '<td style="color:var(--success);">' + item.passed + ' pass</td>'
              + '<td style="color:' + (item.failed > 0 ? 'var(--danger)' : 'var(--text-secondary)') + ';">' + item.failed + ' fail</td></tr>';
          } else if (key === 'audits') {
            return '<tr><td style="font-weight:500;">' + esc(item.task) + '</td>'
              + '<td style="color:var(--text-secondary);">' + esc(item.result).substring(0, 80) + '</td>'
              + '<td style="color:var(--text-muted);font-size:10px;">' + new Date(item.timestamp * 1000).toLocaleTimeString('zh-CN') + '</td></tr>';
          } else if (key === 'healing') {
            return '<tr><td style="font-weight:500;">' + esc(item.action_name) + '</td>'
              + '<td style="color:var(--text-secondary);">← ' + item.alert_rule + '</td>'
              + '<td style="color:' + (item.success ? 'var(--success)' : 'var(--danger)') + ';">' + (item.success ? '✓' : '✗') + '</td></tr>';
          } else {
            return '<tr><td style="font-weight:500;">' + esc(item.tag) + '</td>'
              + '<td style="color:var(--text-secondary);">' + (item.component || '') + '</td>'
              + '<td style="color:var(--text-muted);font-size:10px;">' + (item.notes || '').substring(0, 60) + '</td></tr>';
          }
        }).join('');

        sections.push(
          '<div style="margin-bottom:12px;">'
          + '<div style="font-size:12px;font-weight:700;margin-bottom:6px;color:var(--accent);">' + sectionName[key] + ' (' + items.length + ')</div>'
          + '<table style="width:100%;font-size:12px;border-collapse:collapse;">'
          + rows
          + '</table></div>'
        );
      });
    }

    // ── Memories section (All tab or Memories tab) ──
    if (_searchTab === 'all' || _searchTab === 'memories') {
      var memories = data.memories || [];
      if (memories.length > 0) {
        // Group by layer
        var layerOrder = ['L0', 'L1', 'L2', 'L3'];
        var layerColors = {L0: '#818cf8', L1: '#34d399', L2: '#f59e0b', L3: '#f472b6'};
        var layerNames = {L0: 'Working', L1: 'Episodic', L2: 'Semantic', L3: 'Procedural'};
        var grouped = {};
        memories.forEach(function(m) {
          var layer = m.layer || m.tier || '?';
          if (!grouped[layer]) grouped[layer] = [];
          grouped[layer].push(m);
        });

        var memHtml = '';
        layerOrder.forEach(function(layer) {
          var items = grouped[layer];
          if (!items || items.length === 0) return;
          var color = layerColors[layer] || '#888';
          memHtml += '<div style="margin-bottom:14px;">'
            + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
            + '<span style="background:' + color + ';color:#fff;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:700;">' + layer + '</span>'
            + '<span style="font-size:11px;color:var(--text-secondary);">' + (layerNames[layer] || layer) + ' (' + items.length + ')</span>'
            + '</div>';

          items.forEach(function(item) {
            var snippet = highlightKeyword(item.content, q, layer);
            var ts = item.timestamp ? new Date(item.timestamp * 1000).toLocaleString('zh-CN') : '--';
            memHtml += '<div style="padding:6px 0;border-bottom:1px solid var(--border-color);display:flex;gap:12px;align-items:flex-start;">'
              + '<div style="flex:1;min-width:0;">'
              + '<div style="font-size:12px;line-height:1.6;word-break:break-all;">' + snippet + '</div>'
              + '<div style="margin-top:4px;display:flex;gap:12px;font-size:10px;color:var(--text-muted);">'
              + '<span>imp:' + item.importance + '</span><span>ret:' + item.retention + '</span><span>' + ts + '</span>'
              + '</div></div></div>';
          });
          memHtml += '</div>';
        });

        if (_searchTab === 'all') {
          sections.push(
            '<div style="margin-bottom:12px;">'
            + '<div style="font-size:12px;font-weight:700;margin-bottom:6px;color:var(--accent);">记忆 (' + memories.length + ')</div>'
            + memHtml + '</div>'
          );
        } else {
          sections.push(memHtml);
        }
      } else if (_searchTab === 'memories') {
        sections.push(
          '<div style="text-align:center;padding:20px;color:var(--text-muted);">'
          + '<div style="font-size:14px;margin-bottom:8px;">未找到记忆结果</div>'
          + '<div style="font-size:12px;">请尝试扩大搜索关键词，或确认记忆引擎已启用</div>'
          + '</div>'
        );
      }
    }

    // ── Final render ──
    if (sections.length === 0 && _searchTab === 'memories') {
      el.innerHTML = tabBar + '<div style="text-align:center;padding:20px;color:var(--text-muted);">'
        + '<div style="font-size:14px;margin-bottom:8px;">未找到记忆结果</div>'
        + '<div style="font-size:12px;">尝试开启 memories 搜索</div></div>';
    } else if (sections.length === 0) {
      el.innerHTML = tabBar + '<div style="color:var(--text-muted);text-align:center;padding:16px;">未找到匹配结果</div>';
    } else {
      el.innerHTML = tabBar + sections.join('');
    }
  }

  function highlightKeyword(text, query, layer) {
    if (!query || !text) return esc(text).substring(0, 200);
    var escaped = esc(text);
    var qLower = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var regex;
    try { regex = new RegExp(qLower, 'gi'); } catch(e) { return escaped.substring(0, 200); }

    var match = regex.exec(escaped);
    if (!match) return escaped.substring(0, 200);

    var idx = match.index;
    var ctxLen = 40;
    var start = Math.max(0, idx - ctxLen);
    var end = Math.min(escaped.length, idx + match[0].length + ctxLen);

    var prefix = start > 0 ? '...' : '';
    var suffix = end < escaped.length ? '...' : '';
    var context = prefix + escaped.substring(start, end) + suffix;

    // Highlight: wrap all query matches in context with <mark>
    var hlRegex;
    try { hlRegex = new RegExp('(' + qLower + ')', 'gi'); } catch(e) { return context; }
    context = context.replace(hlRegex, '<mark style="background:#6366f1;color:#fff;padding:0 2px;border-radius:2px;">$1</mark>');
    return context;
  }

  function esc(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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
  refreshSummary();
  setInterval(refreshSummary, 5000);
  refreshLive();
  setInterval(refreshLive, 300000);
  refreshCapabilityStats();
  setInterval(refreshCapabilityStats, 60000);
  refreshPipelineHistory();
  setInterval(refreshPipelineHistory, 30000);
  refreshPipelineSchedules();
  setInterval(refreshPipelineSchedules, 60000);
  refreshPipelineMonitor();
  setInterval(refreshPipelineMonitor, 10000);
  refreshHealing();
  setInterval(refreshHealing, 15000);
  refreshHealingTimeline();
  setInterval(refreshHealingTimeline, 15000);
  refreshGovernanceRules();
  setInterval(refreshGovernanceRules, 30000);

  // ═══ Governance Rules ═════════════════════════════════════

  async function refreshGovernanceRules() {
    var container = document.getElementById('gov-rules-container');
    var countEl = document.getElementById('gov-count');
    if (!container || !countEl) return;

    try {
      var res = await fetch(API + '/api/governance/rules');
      if (!res.ok) { container.innerHTML = '<div class="gov-empty">Failed to load rules</div>'; return; }
      var data = await res.json();
      var rules = (data.rules || []).slice(0, 20);

      countEl.textContent = data.total + ' rules';

      if (rules.length === 0) {
        container.innerHTML = '<div class="gov-empty">暂无治理规则，点击「新建规则」添加</div>';
        return;
      }

      var html = '';
      for (var i = 0; i < rules.length; i++) {
        var r = rules[i];
        var desc = _esc(r.description || '');
        var checked = r.enabled ? ' checked' : '';
        var pClass = 'gov-priority-' + (r.priority || 'P3');
        html +=
          '<div class="gov-rule-row">' +
            '<span class="gov-priority-badge ' + pClass + '">' + _esc(r.priority) + '</span>' +
            '<span class="gov-rule-desc" title="' + desc + '">' + desc + '</span>' +
            '<label class="gov-toggle">' +
              '<input type="checkbox" ' + checked + ' onchange="toggleGovernanceRule(\'' + _esc(r.rule_id) + '\', this)" />' +
              '<span class="gov-toggle-slider"></span>' +
            '</label>' +
            '<button class="gov-delete-btn" onclick="deleteGovernanceRule(\'' + _esc(r.rule_id) + '\')" title="删除规则">✕</button>' +
          '</div>';
        if ((r.remediation || '') !== '') {
          html +=
            '<div style="margin-left:52px;padding:4px 8px 10px 8px;font-size:0.72rem;color:var(--text-muted);">' +
              '修复: ' + _esc(r.remediation) +
            '</div>';
        }
      }
      container.innerHTML = html;
    } catch(e) {
      container.innerHTML = '<div class="gov-empty">无法加载治理规则</div>';
    }
  }

  async function toggleGovernanceRule(ruleId, checkbox) {
    try {
      var res = await fetch(API + '/api/governance/rules/' + encodeURIComponent(ruleId) + '/toggle', { method: 'PUT' });
      if (!res.ok) { checkbox.checked = !checkbox.checked; return; }
    } catch(e) {
      checkbox.checked = !checkbox.checked;
    }
  }

  async function deleteGovernanceRule(ruleId) {
    if (!confirm('确认删除此治理规则？此操作不可撤销。')) return;
    try {
      var res = await fetch(API + '/api/governance/rules/' + encodeURIComponent(ruleId), { method: 'DELETE' });
      if (!res.ok) { var err = await res.json(); alert('删除失败: ' + (err.detail || '未知错误')); return; }
      refreshGovernanceRules();
    } catch(e) {
      alert('删除失败: ' + e.message);
    }
  }

  function openGovernanceModal() {
    document.getElementById('gov-modal').classList.add('active');
    document.getElementById('gov-new-desc').value = '';
    document.getElementById('gov-new-priority').value = 'P2';
    document.getElementById('gov-new-remediation').value = '';
  }

  function closeGovernanceModal() {
    document.getElementById('gov-modal').classList.remove('active');
  }

  async function createGovernanceRule() {
    var desc = document.getElementById('gov-new-desc').value.trim();
    var priority = document.getElementById('gov-new-priority').value;
    var remediation = document.getElementById('gov-new-remediation').value.trim();

    if (!desc) { alert('请输入规则描述'); return; }

    try {
      var res = await fetch(API + '/api/governance/rules', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({description: desc, priority: priority, remediation: remediation})
      });
      if (!res.ok) { var err = await res.json(); alert('创建失败: ' + (err.detail || '未知错误')); return; }
      closeGovernanceModal();
      refreshGovernanceRules();
    } catch(e) {
      alert('创建失败: ' + e.message);
    }
  }

  // ═══ Alert Rules ═════════════════════════════════════

  async function refreshAlertRules() {
    var container = document.getElementById('alert-rules-container');
    var countEl = document.getElementById('alert-rule-count');
    if (!container || !countEl) return;

    try {
      var res = await fetch(API + '/api/alerts/rules');
      if (!res.ok) { container.innerHTML = '<div class="alert-empty">Failed to load rules</div>'; return; }
      var data = await res.json();
      var rules = (data.rules || []).slice(0, 20);

      countEl.textContent = data.total + ' rules';

      if (rules.length === 0) {
        container.innerHTML = '<div class="alert-empty">暂无告警规则，点击「新建规则」添加</div>';
        return;
      }

      var html = '';
      for (var i = 0; i < rules.length; i++) {
        var r = rules[i];
        var checked = r.enabled ? ' checked' : '';
        var sevClass = 'alert-severity-' + (r.severity || 'info');
        var sevLabel = (r.severity || 'info');
        html +=
          '<div class="alert-rule-row">' +
            '<span class="alert-severity-badge ' + sevClass + '">' + _esc(sevLabel) + '</span>' +
            '<div class="alert-rule-main">' +
              '<div class="alert-rule-name">' + _esc(r.name || '') + '</div>' +
              '<div class="alert-rule-condition">' + _esc(r.condition || '') + '</div>' +
            '</div>' +
            '<span class="alert-rule-threshold">' + _esc(String(r.threshold)) + '</span>' +
            '<label class="alert-toggle">' +
              '<input type="checkbox" ' + checked + ' onchange="toggleAlertRule(\'' + _esc(r.rule_id) + '\', this)" />' +
              '<span class="alert-toggle-slider"></span>' +
            '</label>' +
            '<button class="alert-delete-btn" onclick="deleteAlertRule(\'' + _esc(r.rule_id) + '\')" title="删除规则">✕</button>' +
          '</div>';
      }
      container.innerHTML = html;
    } catch(e) {
      container.innerHTML = '<div class="alert-empty">无法加载告警规则</div>';
    }
  }

  async function toggleAlertRule(ruleId, checkbox) {
    try {
      var res = await fetch(API + '/api/alerts/rules/' + encodeURIComponent(ruleId) + '/toggle', { method: 'PUT' });
      if (!res.ok) { checkbox.checked = !checkbox.checked; return; }
    } catch(e) {
      checkbox.checked = !checkbox.checked;
    }
  }

  async function deleteAlertRule(ruleId) {
    if (!confirm('确认删除此告警规则？此操作不可撤销。')) return;
    try {
      var res = await fetch(API + '/api/alerts/rules/' + encodeURIComponent(ruleId), { method: 'DELETE' });
      if (!res.ok) { var err = await res.json(); alert('删除失败: ' + (err.detail || '未知错误')); return; }
      refreshAlertRules();
    } catch(e) {
      alert('删除失败: ' + e.message);
    }
  }

  function openAlertRuleModal() {
    document.getElementById('alert-rule-modal').classList.add('active');
    document.getElementById('ar-new-name').value = '';
    document.getElementById('ar-new-condition').value = '';
    document.getElementById('ar-new-threshold').value = '';
    document.getElementById('ar-new-severity').value = 'warning';
  }

  function closeAlertRuleModal() {
    document.getElementById('alert-rule-modal').classList.remove('active');
  }

  async function createAlertRule() {
    var name = document.getElementById('ar-new-name').value.trim();
    var condition = document.getElementById('ar-new-condition').value.trim();
    var severity = document.getElementById('ar-new-severity').value;
    var thresholdRaw = document.getElementById('ar-new-threshold').value.trim();
    var threshold = parseFloat(thresholdRaw);

    if (!name) { alert('请输入规则名称'); return; }
    if (!condition) { alert('请输入触发条件描述'); return; }
    if (isNaN(threshold)) { alert('请输入有效的阈值数值'); return; }

    try {
      var res = await fetch(API + '/api/alerts/rules', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name, condition: condition, threshold: threshold, severity: severity})
      });
      if (!res.ok) { var err = await res.json(); alert('创建失败: ' + (err.detail || '未知错误')); return; }
      closeAlertRuleModal();
      refreshAlertRules();
    } catch(e) {
      alert('创建失败: ' + e.message);
    }
  }

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

  // ═══ Pipeline Monitor DAG functions ════════════════════════

  async function refreshPipelineMonitor() {
    try {
      var resp = await fetch(API + '/api/pipelines/monitor/summary');
      var data = await resp.json();

      // Update stats
      document.getElementById('pmon-total').textContent = data.total_pipelines || 0;
      document.getElementById('pmon-active').textContent = data.active_pipelines || 0;
      document.getElementById('pmon-done').textContent = data.completed_pipelines || 0;
      document.getElementById('pmon-failed').textContent = data.failed_pipelines || 0;

      // Update live badge
      var badge = document.getElementById('pmon-live-badge');
      if (data.active_pipelines > 0) {
        badge.style.background = 'var(--success)';
        badge.style.color = '#fff';
        badge.textContent = '活跃';
      } else {
        badge.style.background = 'var(--bg-secondary)';
        badge.style.color = 'var(--text-muted)';
        badge.textContent = '空闲';
      }

      // Render DAG canvas
      renderPipelineDAG(data.pipelines || []);

      // Render pipeline list
      renderPipelineList(data.pipelines || []);
    } catch (e) {
      console.error('Pipeline monitor refresh failed:', e);
    }
  }

  function renderPipelineDAG(pipelines) {
    var canvas = document.getElementById('pmon-dag-canvas');
    if (!canvas) return;

    if (pipelines.length === 0) {
      canvas.innerHTML = '<div style="color:var(--text-muted);font-size:12px;text-align:center;width:100%;">暂无流水线数据，执行任务后自动生成</div>';
      return;
    }

    // Show most recent pipeline's DAG
    var latest = pipelines[pipelines.length - 1];
    var stages = latest.nodes || [];

    var dotsHtml = stages.map(function(n, i) {
      var color;
      switch(n.status) {
        case 'running': color = 'var(--warning)'; break;
        case 'completed': color = 'var(--success)'; break;
        case 'failed': color = 'var(--danger)'; break;
        case 'skipped': color = 'var(--text-muted)'; break;
        default: color = 'var(--text-secondary)';
      }
      var pulse = n.status === 'running' ? 'pmon-pulse' : '';
      var durStr = n.duration_ms ? ' (' + (n.duration_ms / 1000).toFixed(1) + 's)' : '';
      return '<div style="display:flex;align-items:center;">'
        + (i > 0 ? '<span style="width:24px;height:2px;background:var(--border-color);margin:0 4px;"></span>' : '')
        + '<div class="' + pulse + '" style="background:' + color + ';border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#fff;flex-shrink:0;" title="' + n.stage_name + durStr + '">' + (i + 1) + '</div>'
        + '</div>';
    }).join('');

    var statusColor = latest.status === 'completed' ? 'var(--success)' :
                     latest.status === 'running' ? 'var(--warning)' : 'var(--danger)';

    canvas.innerHTML = '<div style="width:100%;text-align:center;">'
      + '<div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:8px;">'
      + latest.pipeline_name
      + ' <span style="color:' + statusColor + ';font-size:10px;">[' + latest.status + ']</span>'
      + ' <span style="color:var(--text-muted);font-size:10px;">' + (latest.total_duration_ms / 1000).toFixed(1) + 's</span>'
      + '</div>'
      + '<div style="display:flex;align-items:center;justify-content:center;flex-wrap:wrap;">' + dotsHtml + '</div>'
      + (latest.success_rate !== undefined ? '<div style="font-size:10px;color:var(--text-secondary);margin-top:6px;">成功率: ' + (latest.success_rate * 100).toFixed(0) + '%</div>' : '')
      + '</div>';
  }

  function renderPipelineList(pipelines) {
    var el = document.getElementById('pmon-list');
    if (!el) return;

    if (pipelines.length === 0) {
      el.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:8px;">暂无流水线记录</div>';
      return;
    }

    el.innerHTML = pipelines.slice().reverse().map(function(p) {
      var statusColor = p.status === 'completed' ? 'var(--success)' :
                       p.status === 'running' ? 'var(--warning)' : 'var(--danger)';
      var stageDots = (p.nodes || []).map(function(n) {
        var c = n.status === 'completed' ? 'var(--success)' :
                n.status === 'running' ? 'var(--warning)' :
                n.status === 'failed' ? 'var(--danger)' : 'var(--text-secondary)';
        return '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + c + ';margin-right:2px;"></span>';
      }).join('');

      return '<div style="padding:6px 0;border-bottom:1px solid var(--border-color);cursor:pointer;" onclick="expandPipelineDAG(\'' + p.pipeline_id + '\')">'
        + '<div style="display:flex;justify-content:space-between;align-items:center;">'
        + '<span style="font-weight:500;">' + p.pipeline_name + '</span>'
        + '<span style="color:' + statusColor + ';font-size:10px;">[' + p.status + ']</span>'
        + '</div>'
        + '<div style="display:flex;justify-content:space-between;margin-top:4px;">'
        + '<span>' + stageDots + '</span>'
        + '<span style="color:var(--text-muted);font-size:10px;">' + (p.total_duration_ms / 1000).toFixed(1) + 's | ' + (p.success_rate * 100).toFixed(0) + '%</span>'
        + '</div>'
        + '<div id="pmon-expand-' + p.pipeline_id + '" style="display:none;margin-top:8px;padding:8px;background:var(--bg-secondary);border-radius:4px;font-size:11px;"></div>'
        + '</div>';
    }).join('');
  }

  async function expandPipelineDAG(pid) {
    var expandEl = document.getElementById('pmon-expand-' + pid);
    if (!expandEl) return;

    if (expandEl.style.display === 'block') {
      expandEl.style.display = 'none';
      return;
    }

    try {
      var resp = await fetch(API + '/api/pipelines/monitor/dag/' + pid);
      var dag = await resp.json();

      var nodesHtml = (dag.nodes || []).map(function(n, i) {
        var color = n.status === 'completed' ? 'var(--success)' :
                    n.status === 'running' ? 'var(--warning)' :
                    n.status === 'failed' ? 'var(--danger)' : 'var(--text-secondary)';
        return '<div style="display:flex;align-items:center;gap:6px;padding:4px 0;">'
          + '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + color + ';"></span>'
          + '<span style="font-weight:500;">' + n.stage_name + '</span>'
          + '<span style="color:var(--text-muted);font-size:10px;">[' + n.status + ']</span>'
          + (n.duration_ms ? '<span style="color:var(--text-muted);font-size:10px;">' + (n.duration_ms / 1000).toFixed(2) + 's</span>' : '')
          + (n.error ? '<span style="color:var(--danger);font-size:10px;">' + n.error + '</span>' : '')
          + '</div>';
      }).join('');

      // Show timeline
      var tlHtml = '';
      if (dag.timeline && dag.timeline.length > 0) {
        tlHtml = '<div style="margin-top:6px;border-top:1px solid var(--border-color);padding-top:6px;">'
          + '<div style="font-size:10px;color:var(--text-secondary);margin-bottom:4px;">时间线</div>'
          + dag.timeline.slice(-8).map(function(e) {
            var tColor = e.status === 'completed' ? 'var(--success)' :
                        e.status === 'failed' ? 'var(--danger)' : 'var(--text-secondary)';
            return '<div style="font-size:10px;padding:1px 0;color:' + tColor + ';">'
              + new Date(e.timestamp * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit',second:'2-digit'})
              + '  ' + e.stage_name
              + (e.duration_ms ? ' (' + (e.duration_ms / 1000).toFixed(2) + 's)' : '')
              + '</div>';
          }).join('')
          + '</div>';
      }

      expandEl.innerHTML = '<div style="display:flex;gap:16px;flex-wrap:wrap;">'
        + '<div style="flex:1;min-width:200px;">' + nodesHtml + '</div>'
        + '<div style="flex:1;min-width:180px;">' + tlHtml + '</div>'
        + '</div>';
      expandEl.style.display = 'block';
    } catch (e) {
      expandEl.innerHTML = '<div style="color:var(--danger);">加载失败: ' + e.message + '</div>';
      expandEl.style.display = 'block';
    }
  }

  // ═══ Model Cost panel functions ═════════════════════════════

  var RING_CIRCUMFERENCE = 326.7; // 2 * PI * 52

  async function refreshModelCosts() {
    try {
      var res = await fetch(API + '/api/dashboard/model-costs');
      var data = await res.json();
      renderModelCosts(data);
    } catch (e) {
      console.error('Model costs fetch failed:', e);
    }
  }

  function renderModelCosts(report) {
    var total = report.total_requests || 0;
    var byTier = report.requests_by_tier || {};
    var cheap = byTier.cheap || 0;
    var standard = byTier.standard || 0;
    var premium = byTier.premium || 0;

    // Savings ring
    var savingsPct = report.savings_percent || 0;
    var offset = RING_CIRCUMFERENCE - (RING_CIRCUMFERENCE * Math.min(savingsPct, 100) / 100);
    var ring = document.getElementById('cost-ring');
    if (ring) {
      ring.setAttribute('stroke-dashoffset', offset);
      // Color based on savings level
      if (savingsPct > 50) ring.style.stroke = 'var(--success)';
      else if (savingsPct > 25) ring.style.stroke = 'var(--warning)';
      else ring.style.stroke = 'var(--danger)';
    }
    var pctEl = document.getElementById('cost-savings-pct');
    if (pctEl) pctEl.textContent = savingsPct.toFixed(0) + '%';

    // Tier bars
    var maxTier = Math.max(cheap, standard, premium, 1);
    function setBar(id, value) {
      var bar = document.getElementById(id);
      if (bar) bar.style.width = (value / maxTier * 100) + '%';
    }
    setBar('bar-cheap', cheap);
    setBar('bar-standard', standard);
    setBar('bar-premium', premium);

    var cntCheap = document.getElementById('cnt-cheap');
    if (cntCheap) cntCheap.textContent = cheap;
    var cntStandard = document.getElementById('cnt-standard');
    if (cntStandard) cntStandard.textContent = standard;
    var cntPremium = document.getElementById('cnt-premium');
    if (cntPremium) cntPremium.textContent = premium;

    // Cost cards
    var totalReqs = document.getElementById('cost-total-reqs');
    if (totalReqs) totalReqs.textContent = total;
    var savedVal = document.getElementById('cost-saved-val');
    if (savedVal) savedVal.textContent = '$' + (report.estimated_cost_saved || 0).toFixed(3);
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
    var total = report.total_cases || 0;
    var passed = report.passed || 0;
    var failed = report.failed || 0;
    var errored = report.errored || 0;

    // Update legend counts
    var passEl = document.getElementById('evals-passed');
    var failEl = document.getElementById('evals-failed');
    var errEl = document.getElementById('evals-errored');
    if (passEl) passEl.textContent = passed;
    if (failEl) failEl.textContent = failed;
    if (errEl) errEl.textContent = errored;

    // Draw donut chart
    var circumference = 2 * Math.PI * 52; // ~326.73
    var arcPass = document.getElementById('evals-arc-pass');
    var arcFail = document.getElementById('evals-arc-fail');
    var arcError = document.getElementById('evals-arc-error');
    var pctEl = document.getElementById('evals-donut-pct');

    if (total === 0) {
      if (arcPass) arcPass.setAttribute('stroke-dasharray', '0 ' + circumference);
      if (arcFail) arcFail.setAttribute('stroke-dasharray', '0 ' + circumference);
      if (arcError) arcError.setAttribute('stroke-dasharray', '0 ' + circumference);
      if (pctEl) pctEl.textContent = '--';
      if (pctEl) pctEl.style.color = 'var(--text-muted)';
    } else {
      var passFrac = passed / total;
      var failFrac = failed / total;
      var errFrac = errored / total;
      var passLen = passFrac * circumference;
      var failLen = failFrac * circumference;
      var errLen = errFrac * circumference;

      // Pass arc starts at top
      if (arcPass) arcPass.setAttribute('stroke-dasharray', passLen + ' ' + circumference);
      if (arcPass) arcPass.setAttribute('stroke-dashoffset', '0');
      // Fail arc starts after pass arc (negative offset shifts clockwise due to -90deg rotation)
      if (arcFail) arcFail.setAttribute('stroke-dasharray', failLen + ' ' + circumference);
      if (arcFail) arcFail.setAttribute('stroke-dashoffset', String(-passLen));
      // Error arc starts after pass+fail
      if (arcError) arcError.setAttribute('stroke-dasharray', errLen + ' ' + circumference);
      if (arcError) arcError.setAttribute('stroke-dashoffset', String(-(passLen + failLen)));

      var rate = report.pass_rate != null ? (report.pass_rate * 100).toFixed(1) : 0;
      if (pctEl) pctEl.textContent = rate + '%';
      if (pctEl) pctEl.style.color = rate >= 90 ? 'var(--success)' : rate >= 70 ? 'var(--warning)' : 'var(--danger)';
    }

    // Render suites accordion
    var suitesHtml = '';
    var suites = report.suites || [];
    if (suites.length === 0) {
      suitesHtml = '<div class="evals-empty">No eval results yet. Click "Run All Evals" to start.</div>';
    } else {
      suites.forEach(function(s, idx) {
        var suitePassRate = s.pass_rate != null ? (s.pass_rate * 100).toFixed(0) + '%' : '--';
        suitesHtml += '<div class="evals-suite-item">';
        suitesHtml += '<div class="evals-suite-header" onclick="toggleSuite(this)">';
        suitesHtml += '<span class="suite-title">' + s.suite_name + '</span>';
        suitesHtml += '<span class="suite-meta">' + suitePassRate + ' <span class="suite-arrow">&#9654;</span></span>';
        suitesHtml += '</div>';
        suitesHtml += '<div class="evals-suite-body">';
        (s.results || []).forEach(function(r) {
          var icon = r.status === 'pass' ? '<span style="color:var(--success);">&#10003;</span>' :
                    r.status === 'fail' ? '<span style="color:var(--danger);">&#10007;</span>' :
                    r.status === 'error' ? '<span style="color:var(--warning);">&#9888;</span>' :
                    '<span style="color:var(--text-muted);">-</span>';
          var dur = r.duration_ms != null ? (r.duration_ms < 1000 ? r.duration_ms + 'ms' : (r.duration_ms / 1000).toFixed(1) + 's') : '';
          suitesHtml += '<div class="evals-case-row">';
          suitesHtml += '<span class="case-icon">' + icon + '</span>';
          suitesHtml += '<span class="case-name">' + r.case + '</span>';
          suitesHtml += '<span class="case-duration">' + dur + '</span>';
          suitesHtml += '</div>';
          if (r.status !== 'pass' && r.details) {
            var errSummary = r.details.length > 80 ? r.details.substring(0, 80) + '...' : r.details;
            suitesHtml += '<div class="case-error">' + errSummary + '</div>';
          }
        });
        suitesHtml += '</div></div>';
      });
    }
    document.getElementById('evals-suites').innerHTML = suitesHtml;
  }

  function toggleSuite(headerEl) {
    var body = headerEl.nextElementSibling;
    if (body) {
      var isOpen = body.classList.contains('open');
      if (isOpen) {
        body.classList.remove('open');
        headerEl.classList.remove('open');
      } else {
        body.classList.add('open');
        headerEl.classList.add('open');
      }
    }
  }

  function toggleAllSuites(expand) {
    var headers = document.querySelectorAll('#evals-suites .evals-suite-header');
    headers.forEach(function(h) {
      var body = h.nextElementSibling;
      if (!body) return;
      if (expand) {
        body.classList.add('open');
        h.classList.add('open');
      } else {
        body.classList.remove('open');
        h.classList.remove('open');
      }
    });
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

  // Prompt Templates panel
  var templatesData = {};
  var templatesTimer = null;

  function refreshTemplates() {
    apiGet('/api/dashboard/templates').then(function(data) {
      templatesData = data || {};
      renderTemplates();
    }).catch(function() {
      document.getElementById('templates-tbody').innerHTML =
        '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;">Failed to load</td></tr>';
    });
  }

  function renderTemplates() {
    var tbody = document.getElementById('templates-tbody');
    if (!templatesData || Object.keys(templatesData).length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted);text-align:center;">No templates found</td></tr>';
      return;
    }
    var html = '';
    Object.keys(templatesData).sort().forEach(function(cap) {
      var t = templatesData[cap] || {};
      var score = t.performance_score != null ? t.performance_score : 0;
      var scoreColor = score > 0.8 ? 'var(--success)' : (score > 0.6 ? 'var(--warning)' : 'var(--danger)');
      var frozen = t.frozen ? 'Yes' : 'No';
      var frozenColor = t.frozen ? 'var(--warning)' : 'var(--text-dim)';
      html += '<tr style="cursor:pointer;" onclick="toggleTemplateDetail(\'' + cap + '\')">';
      html += '<td><strong>' + cap + '</strong></td>';
      html += '<td>v' + (t.version || 1) + '</td>';
      html += '<td style="color:' + scoreColor + ';font-weight:bold;">' + score.toFixed(2) + '</td>';
      html += '<td style="color:' + frozenColor + ';">' + frozen + '</td>';
      html += '<td>';
      html += '<button onclick="event.stopPropagation();optimizeTemplate(\'' + cap + '\')" style="background:var(--accent);color:#fff;border:none;border-radius:3px;padding:2px 8px;font-size:10px;cursor:pointer;margin-right:2px;">Optimize</button>';
      html += '<button onclick="event.stopPropagation();rollbackTemplate(\'' + cap + '\')" style="background:var(--border-color);color:var(--text-secondary);border:none;border-radius:3px;padding:2px 8px;font-size:10px;cursor:pointer;">Rollback</button>';
      html += '</td></tr>';
    });
    tbody.innerHTML = html;
    document.getElementById('templates-status').textContent = 'Auto-refresh: 30s';
  }

  function toggleTemplateDetail(cap) {
    var t = templatesData[cap];
    if (!t) return;
    var detail = document.getElementById('template-detail');
    var content = document.getElementById('template-detail-content');
    var html = '<strong>' + cap + '</strong> v' + (t.version || 1) +
               ' | Score: ' + ((t.performance_score||0).toFixed(2)) +
               ' | Frozen: ' + (t.frozen ? 'Yes' : 'No') + '<br><br>';
    html += '<div style="color:var(--text-dim);text-transform:uppercase;font-size:10px;margin-bottom:4px;">System Prompt:</div>';
    html += '<div style="white-space:pre-wrap;background:var(--bg-primary);padding:8px;border-radius:4px;max-height:120px;overflow-y:auto;">' +
            (t.system_prompt || '(none)') + '</div>';
    var examples = t.examples || [];
    if (examples.length > 0) {
      html += '<div style="color:var(--text-dim);text-transform:uppercase;font-size:10px;margin:8px 0 4px;">Examples (' + examples.length + '):</div>';
      examples.forEach(function(ex, i) {
        html += '<div style="background:var(--bg-primary);padding:6px;border-radius:4px;margin-bottom:4px;font-size:11px;">';
        html += '<span style="color:var(--accent);">#' + (i+1) + '</span> ';
        html += '<span style="color:var(--text-dim);">Q:</span> ' + (ex.input||'') + ' ';
        html += '<span style="color:var(--text-dim);">A:</span> ' + (ex.output||'') + '</div>';
      });
    }
    content.innerHTML = html;
    detail.style.display = 'block';
  }

  function closeTemplateDetail() {
    document.getElementById('template-detail').style.display = 'none';
  }

  function optimizeTemplate(cap) {
    var statusEl = document.getElementById('templates-status');
    statusEl.textContent = 'Optimizing ' + cap + '...';
    apiPost('/api/dashboard/templates/optimize', {capability: cap}).then(function(data) {
      templatesData[cap] = data;
      renderTemplates();
      statusEl.textContent = 'Optimized ' + cap + ' OK';
    }).catch(function(err) {
      statusEl.textContent = 'Optimize failed: ' + (err.message || err);
    });
  }

  function rollbackTemplate(cap) {
    var ver = prompt('Rollback ' + cap + ' to version (current: v' +
      ((templatesData[cap]||{}).version||1) + '):', '1');
    if (!ver) return;
    var v = parseInt(ver, 10);
    if (isNaN(v) || v < 1) { alert('Invalid version'); return; }
    var statusEl = document.getElementById('templates-status');
    statusEl.textContent = 'Rolling back ' + cap + ' to v' + v + '...';
    apiPost('/api/dashboard/templates/rollback', {capability: cap, version: v}).then(function(data) {
      templatesData[cap] = data;
      renderTemplates();
      statusEl.textContent = 'Rollback ' + cap + ' to v' + v + ' OK';
    }).catch(function(err) {
      statusEl.textContent = 'Rollback failed: ' + (err.message || err);
    });
  }

  if (templatesTimer) clearInterval(templatesTimer);
  refreshTemplates();
  templatesTimer = setInterval(refreshTemplates, 30000);

  // ═══ Version History & Rollback ═════════════════════════════════

  var _versionData = [];

  function refreshVersions() {
    apiGet('/api/dashboard/versions').then(function(data) {
      _versionData = data || [];
      renderVersions();
    }).catch(function(err) {
      document.getElementById('version-list').innerHTML =
        '<div class="empty">Failed: ' + (err.message || err) + '</div>';
    });
  }

  function renderVersions() {
    var listEl = document.getElementById('version-list');
    if (!_versionData || _versionData.length === 0) {
      listEl.innerHTML = '<div class="empty">No snapshots yet. Click "+ Snapshot" to capture current state.</div>';
      return;
    }
    var html = '<table class="ministers-table" style="width:100%;"><thead><tr>' +
      '<th style="width:80px;">ID</th><th>Description</th><th>Time</th><th>Components</th><th style="width:120px;">Actions</th>' +
      '</tr></thead><tbody>';
    _versionData.forEach(function(v) {
      var dt = new Date(v.timestamp * 1000);
      var timeStr = dt.toLocaleString();
      var compStr = (v.components || []).join(', ');
      html += '<tr>';
      html += '<td style="font-family:monospace;font-size:10px;">' + v.id.substr(0, 8) + '</td>';
      html += '<td>' + (v.description || '') + '</td>';
      html += '<td style="font-size:11px;">' + timeStr + '</td>';
      html += '<td style="font-size:11px;">' + (compStr || v.component_count + ' components') + '</td>';
      html += '<td>';
      html += '<button onclick="previewRollback(\'' + v.id + '\')" style="background:var(--accent);color:#fff;border:none;border-radius:3px;padding:2px 8px;font-size:10px;cursor:pointer;margin-right:2px;">Preview</button>';
      html += '<button onclick="doRollback(\'' + v.id + '\')" style="background:var(--warning);color:#000;border:none;border-radius:3px;padding:2px 8px;font-size:10px;cursor:pointer;">Rollback</button>';
      html += '</td></tr>';
    });
    html += '</tbody></table>';
    listEl.innerHTML = html;
  }

  function createSnapshot() {
    var desc = document.getElementById('ver-snap-desc').value.trim();
    document.getElementById('ver-snap-desc').value = '';
    apiPost('/api/dashboard/versions/snapshot', {description: desc}).then(function(data) {
      refreshVersions();
    }).catch(function(err) {
      alert('Snapshot failed: ' + (err.message || err));
    });
  }

  function previewRollback(snapId) {
    var previewDiv = document.getElementById('version-diff-preview');
    previewDiv.style.display = 'block';
    previewDiv.innerHTML = '<span style="color:var(--text-muted);">Computing diff...</span>';
    apiGet('/api/dashboard/versions/' + snapId + '/diff').then(function(data) {
      var html = '<div style="font-weight:600;margin-bottom:8px;">Rollback Preview → <code>' + snapId.substr(0,8) + '</code></div>';
      html += '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:8px;">' + (data.summary || '') + '</div>';
      var comps = data.components || {};
      Object.keys(comps).forEach(function(comp) {
        var d = comps[comp];
        html += '<div style="margin-bottom:8px;padding:8px;background:var(--bg-primary);border-radius:4px;">';
        html += '<strong style="color:var(--accent);">' + comp + '</strong>';
        html += ' <span style="font-size:10px;color:var(--text-muted);">+' + d.added_keys.length + '/-' + d.removed_keys.length + '/~' + d.changed_keys.length + '</span>';
        if (d.changes && d.changes.length > 0) {
          html += '<div style="font-size:11px;margin-top:4px;max-height:200px;overflow-y:auto;">';
          d.changes.forEach(function(c) {
            html += '<div style="font-family:monospace;font-size:10px;padding:2px 0;">' + c + '</div>';
          });
          html += '</div>';
        } else {
          html += '<div style="color:var(--text-dim);font-size:10px;">No changes</div>';
        }
        html += '</div>';
      });
      previewDiv.innerHTML = html;
    }).catch(function(err) {
      previewDiv.innerHTML = '<span style="color:var(--danger);">Diff failed: ' + (err.message || err) + '</span>';
    });
  }

  function doRollback(snapId) {
    if (!confirm('确认回滚到版本 ' + snapId.substr(0,8) + '?\n\n系统将先自动创建当前状态的快照作为保护。')) return;
    apiPost('/api/dashboard/versions/rollback', {snapshot_id: snapId}).then(function(data) {
      refreshVersions();
      document.getElementById('version-diff-preview').style.display = 'none';
      alert('Rollback completed: ' + JSON.stringify(data.results));
    }).catch(function(err) {
      alert('Rollback failed: ' + (err.message || err));
    });
  }

  refreshVersions();
  setInterval(refreshVersions, 60000);

  // Auto-refresh Evals, Audit, and Model Costs
  refreshEvals();
  refreshAudit();
  refreshModelCosts();
  setInterval(refreshEvals, 15000);
  setInterval(refreshAudit, 60000);
  setInterval(refreshModelCosts, 60000);

  // ═══ Governance & Autonomy Panel ═══════════════════════════════

  async function refreshGovernance() {
    try {
      var [govRes, autoRes] = await Promise.all([
        fetch(API + '/governance/stats'),
        fetch(API + '/autonomy/stats'),
      ]);
      var govData = await govRes.json();
      var autoData = await autoRes.json();

      document.getElementById('gov-green').textContent = autoData.green_spaces || 0;
      document.getElementById('gov-yellow').textContent = autoData.yellow_spaces || 0;
      document.getElementById('gov-red').textContent = autoData.red_spaces || 0;
      document.getElementById('gov-rules-total').textContent = govData.total_rules || 0;
      document.getElementById('govRuleCount').textContent = (govData.enabled_rules || 0) + '/' + (govData.total_rules || 0);

      // Render rules list
      var rulesRes = await fetch(API + '/governance/rules');
      var rulesData = await rulesRes.json();
      var rules = rulesData.rules || [];
      var list = document.getElementById('gov-rules-list');
      if (!rules.length) {
        list.innerHTML = '<div class="empty">No governance rules</div>';
        return;
      }
      list.innerHTML = rules.map(function(r) {
        var color = '#8892b0';
        if (r.priority === 'CRITICAL') color = 'var(--danger)';
        else if (r.priority === 'HIGH') color = 'var(--warning)';
        else if (r.priority === 'MEDIUM') color = 'var(--accent)';
        return '<div style="display:flex;align-items:center;padding:4px 0;border-bottom:1px solid var(--card-border);gap:8px;">' +
          '<span style="font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">' + r.name + '</span>' +
          '<span style="font-size:10px;color:' + color + ';">' + r.priority + '</span>' +
          '<span style="font-size:10px;color:var(--text-muted);">' + r.rule_type + '</span>' +
          '<span style="font-size:10px;' + (r.enabled ? 'color:var(--success)' : 'color:var(--text-muted)') + ';">' + (r.enabled ? 'ON' : 'OFF') + '</span>' +
          '</div>';
      }).join('');
    } catch(e) {
      console.error('Governance refresh failed:', e);
    }
  }

  // ═══ Failure Recovery Panel ════════════════════════════════════

  async function refreshRecovery() {
    try {
      var [cbRes, statsRes] = await Promise.all([
        fetch(API + '/recovery/circuit-breakers'),
        fetch(API + '/recovery/stats'),
      ]);
      var cbData = await cbRes.json();
      var stats = await statsRes.json();

      document.getElementById('rec-success').textContent = stats.success || 0;
      document.getElementById('rec-retry').textContent = stats.retry_success || 0;
      document.getElementById('rec-degraded').textContent = stats.degraded || 0;
      document.getElementById('rec-failed').textContent = stats.failed || 0;

      var cbs = cbData.circuit_breakers || [];
      if (cbs.length > 0) {
        var cb = cbs[0];
        var stateEl = document.getElementById('cb-state');
        stateEl.textContent = cb.state || 'UNKNOWN';
        if (cb.state === 'CLOSED') stateEl.style.background = 'rgba(0,200,83,0.15)';
        else if (cb.state === 'OPEN') stateEl.style.background = 'rgba(255,23,68,0.15)';
        else stateEl.style.background = 'rgba(255,171,0,0.15)';
        document.getElementById('cb-stats').textContent =
          '失败数: ' + (cb.failure_count || 0) + ' | 成功数: ' + (cb.success_count || 0);
        document.getElementById('cb-reset-btn').style.display = (cb.state === 'OPEN') ? '' : 'none';
      }
    } catch(e) {
      console.error('Recovery refresh failed:', e);
    }
  }

  async function resetCircuitBreaker() {
    try {
      await fetch(API + '/recovery/circuit-breakers/default/reset', {method: 'POST'});
      refreshRecovery();
    } catch(e) {
      alert('Reset circuit breaker failed: ' + e.message);
    }
  }

  refreshGovernance();
  refreshAlertRules();
  refreshRecovery();
  refreshToolGuard();
  refreshHallucination();
  refreshMemory();
  setInterval(refreshGovernance, 15000);
  setInterval(refreshAlertRules, 30000);
  setInterval(refreshRecovery, 15000);
  setInterval(refreshToolGuard, 20000);
  setInterval(refreshHallucination, 30000);
  setInterval(refreshMemory, 20000);

  // ═══ Tool Guard Panel ═════════════════════════════════════════

  async function refreshToolGuard() {
    try {
      var res = await fetch(API + '/tools/guard/stats');
      var data = await res.json();
      document.getElementById('tg-passed').textContent = data.passed || 0;
      document.getElementById('tg-blocked').textContent = data.blocked || 0;
      document.getElementById('tg-pii').textContent = data.pii_events || 0;
      document.getElementById('tg-rate-limited').textContent = data.rate_limited || 0;

      var intercepts = data.recent_intercepts || [];
      var container = document.getElementById('tg-intercepts');
      if (intercepts.length === 0) {
        container.innerHTML = '<div class="empty">No intercepts</div>';
        return;
      }
      container.innerHTML = intercepts.map(function(evt) {
        var sevColor = evt.severity === 'CRITICAL' ? 'var(--danger)' :
                       evt.severity === 'HIGH' ? '#ff9800' :
                       evt.severity === 'MEDIUM' ? 'var(--warning)' : 'var(--text-muted)';
        var timeStr = evt.timestamp ? new Date(evt.timestamp * 1000).toLocaleTimeString() : '--';
        return '<div style="padding:3px 0;border-bottom:1px solid var(--border-color);display:flex;gap:8px;align-items:center;">'
          + '<span style="color:' + sevColor + ';font-weight:600;min-width:40px;">' + (evt.severity || '--') + '</span>'
          + '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (evt.tool_name || '--') + '</span>'
          + '<span style="color:var(--text-muted);">' + timeStr + '</span>'
          + '</div>';
      }).join('');
    } catch(e) {
      console.error('ToolGuard refresh failed:', e);
    }
  }

  // ═══ Hallucination Watch Panel ═════════════════════════════════

  async function refreshHallucination() {
    try {
      var res = await fetch(API + '/hallucination/stats');
      var data = await res.json();
      var thresholds = data.risk_thresholds || {};
      document.getElementById('hall-threshold-critical').textContent =
        thresholds.CRITICAL != null ? thresholds.CRITICAL.toFixed(2) : '0.90';
      document.getElementById('hall-threshold-high').textContent =
        thresholds.HIGH != null ? thresholds.HIGH.toFixed(2) : '0.75';
      document.getElementById('hall-threshold-med').textContent =
        thresholds.MEDIUM != null ? thresholds.MEDIUM.toFixed(2) : '0.50';
      document.getElementById('hall-status').textContent = data.enabled ? 'Active' : 'Disabled';
      document.getElementById('hall-status').style.color = data.enabled ? 'var(--success)' : 'var(--text-muted)';

      var detections = data.recent_detections || [];
      var container = document.getElementById('hall-recent');
      if (detections.length === 0) {
        container.innerHTML = '<div class="empty">No detections yet</div>';
        return;
      }
      container.innerHTML = detections.map(function(d) {
        var levelColor = d.risk_level === 'CRITICAL' ? 'var(--danger)' :
                         d.risk_level === 'HIGH' ? '#ff9800' :
                         d.risk_level === 'MEDIUM' ? 'var(--warning)' : 'var(--success)';
        return '<div style="padding:3px 0;border-bottom:1px solid var(--border-color);display:flex;gap:8px;align-items:center;">'
          + '<span style="color:' + levelColor + ';font-weight:600;min-width:50px;">' + (d.risk_level || '--') + '</span>'
          + '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">score: ' + (d.risk_score != null ? d.risk_score.toFixed(2) : '--') + '</span>'
          + '<span style="color:var(--text-muted);font-size:10px;">' + (d.suggested_action || '') + '</span>'
          + '</div>';
      }).join('');
    } catch(e) {
      console.error('Hallucination refresh failed:', e);
    }
  }

  // ═══ Hierarchical Memory Panel ════════════════════════════════

  async function refreshMemory() {
    try {
      var res = await fetch(API + '/memory/stats');
      var data = await res.json();
      document.getElementById('mem-working').textContent = data.working_count || 0;
      document.getElementById('mem-episodic').textContent = data.episodic_count || 0;
      document.getElementById('mem-semantic').textContent = data.semantic_count || 0;
      document.getElementById('mem-procedural').textContent = data.procedural_count || 0;
      document.getElementById('mem-consolidation-status').textContent = data.consolidation_status || 'idle';
      document.getElementById('mem-threshold').textContent = (data.importance_threshold || 0.55).toFixed(2);
      document.getElementById('mem-ret-epi').textContent = ((data.avg_retention_episodic || 0) * 100).toFixed(0) + '%';
      document.getElementById('mem-ret-sem').textContent = ((data.avg_retention_semantic || 0) * 100).toFixed(0) + '%';
      if (data.last_consolidation > 0) {
        var d = new Date(data.last_consolidation * 1000);
        document.getElementById('mem-last-consolidation').textContent = d.toLocaleTimeString();
      }
    } catch(e) {
      console.error('Memory refresh failed:', e);
    }

    // Refresh consolidation history
    try {
      var histRes = await fetch(API + '/memory/consolidation-history?limit=5');
      var histData = await histRes.json();
      var container = document.getElementById('mem-history');
      if (!histData || histData.length === 0) {
        container.innerHTML = '<div class="empty">No consolidation yet</div>';
        return;
      }
      container.innerHTML = histData.map(function(c) {
        var statusColor = c.status === 'completed' ? 'var(--success)' : c.status === 'failed' ? 'var(--danger)' : 'var(--text-muted)';
        return '<div style="padding:2px 0;border-bottom:1px solid var(--border-color);">' +
          '<span style="color:' + statusColor + ';">' + c.status + '</span> ' +
          'W→E:' + c.promoted_to_episodic + ' E→S:' + c.episodic_to_semantic + ' Sum:' + c.facts_summarized + '</div>';
      }).join('');
    } catch(e) {
      console.error('Memory history refresh failed:', e);
    }

    // Refresh GraphRAG stats
    try {
      var grRes = await fetch(API + '/api/memory/graph/stats');
      var grData = await grRes.json();
      document.getElementById('gr-entities').textContent = grData.entity_count || 0;
      document.getElementById('gr-relations').textContent = grData.relation_count || 0;
      document.getElementById('gr-docs').textContent = grData.document_count || 0;
      document.getElementById('gr-avg-degree').textContent = (grData.avg_degree || 0).toFixed(1);
      document.getElementById('gr-max-degree').textContent = grData.max_degree || 0;
      var topContainer = document.getElementById('gr-top-entities');
      var topEnts = grData.top_entities || [];
      if (topEnts.length === 0) {
        topContainer.innerHTML = '<span class="empty">No entities yet</span>';
      } else {
        topContainer.innerHTML = topEnts.slice(0, 8).map(function(e) {
          return '<div style="padding:1px 0;border-bottom:1px solid var(--border-color);">' +
            '<span style="font-weight:600;">' + escHtml(e.name) + '</span> ' +
            '<span style="color:var(--text-muted);">(' + e.type + ')</span> ' +
            '<span style="color:var(--text-secondary);">deg:' + e.degree + '</span></div>';
        }).join('');
      }
    } catch(e) {
      console.error('GraphRAG refresh failed:', e);
    }
  }

  async function searchGraphEntity() {
    var query = document.getElementById('gr-search-input').value.trim();
    if (!query) return;
    try {
      var res = await fetch(API + '/api/memory/graph/entity/' + encodeURIComponent(query));
      var data = await res.json();
      var detailDiv = document.getElementById('gr-entity-detail');
      detailDiv.style.display = 'block';
      detailDiv.innerHTML = '<div style="font-weight:600;margin-bottom:4px;">Entity: ' + escHtml(data.name) + '</div>' +
        '<pre style="font-size:9px;white-space:pre-wrap;margin:0;">' + escHtml(data.summary) + '</pre>';
    } catch(e) {
      console.error('Graph entity search failed:', e);
    }
  }

  async function triggerConsolidation() {
    try {
      var res = await fetch(API + '/memory/consolidate', {method: 'POST'});
      var data = await res.json();
      alert('Consolidation ' + data.status + ' (W→E:' + data.promoted_to_episodic + ' E→S:' + data.episodic_to_semantic + ')');
      refreshMemory();
    } catch(e) {
      alert('Consolidation failed: ' + e.message);
    }
  }

  // ═══ Sandbox Code Runner ══════════════════════════════════════

  var _sandboxHistory = [];

  async function updateSandboxEngine() {
    var engine = document.getElementById('sandbox-engine').value;
    try {
      var resp = await fetch('/api/dashboard/sandbox/status');
      if (resp.ok) {
        var dot = document.getElementById('sandbox-status-dot');
        if (dot) dot.style.background = 'var(--success)';
      }
    } catch(e) {
      var dot = document.getElementById('sandbox-status-dot');
      if (dot) dot.style.background = 'var(--danger)';
    }
  }

  async function runSandboxCode() {
    var code = document.getElementById('sandbox-editor').value.trim();
    if (!code) { document.getElementById('sandbox-stderr').textContent = 'No code provided'; return; }

    var btn = document.getElementById('sandbox-run-btn');
    var origText = btn.textContent;
    btn.textContent = 'Running...';
    btn.disabled = true;

    var engine = document.getElementById('sandbox-engine').value;
    var timeout = parseInt(document.getElementById('sandbox-timeout').value) || 30;

    var startTime = performance.now();
    try {
      var resp = await fetch('/api/dashboard/sandbox/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code: code, engine: engine, timeout: timeout})
      });
      var data = await resp.json();
      var elapsed = (performance.now() - startTime).toFixed(0);

      document.getElementById('sandbox-stdout').textContent = data.stdout || '(empty)';
      var stderrText = (data.stderr || '') + (data.exit_code !== 0 ? '\nExit code: ' + data.exit_code : '');
      document.getElementById('sandbox-stderr').textContent = stderrText || '(none)';
      document.getElementById('sandbox-exec-time').textContent =
        'Done in ' + data.execution_time_ms + 'ms (API: ' + elapsed + 'ms)';

      refreshSandboxHistory();
    } catch(e) {
      document.getElementById('sandbox-stderr').textContent = 'Error: ' + e.message;
      document.getElementById('sandbox-exec-time').textContent = '';
    } finally {
      btn.textContent = origText;
      btn.disabled = false;
    }
  }

  async function refreshSandboxHistory() {
    try {
      var resp = await fetch('/api/dashboard/sandbox/history?limit=15');
      var data = await resp.json();
      _sandboxHistory = data.history || [];

      var container = document.getElementById('sandbox-history');
      if (_sandboxHistory.length === 0) {
        container.innerHTML = '<div class="empty" style="font-size:0.7rem;">No executions yet</div>';
        return;
      }
      var html = '<table style="width:100%;font-size:0.7rem;border-collapse:collapse;">';
      html += '<thead><tr style="color:var(--text-secondary);border-bottom:1px solid var(--card-border);">';
      html += '<th style="padding:4px 6px;text-align:left;">Time</th>';
      html += '<th style="padding:4px 6px;text-align:left;">Command</th>';
      html += '<th style="padding:4px 6px;text-align:center;">Exit</th>';
      html += '<th style="padding:4px 6px;text-align:right;">Duration</th>';
      html += '</tr></thead><tbody>';
      for (var i = 0; i < _sandboxHistory.length; i++) {
        var h = _sandboxHistory[i];
        var ts = new Date(h.timestamp * 1000).toLocaleTimeString();
        var cmd = (h.command || '').substring(0, 60) + (h.command && h.command.length > 60 ? '...' : '');
        var exitColor = h.exit_code === 0 ? 'var(--success)' : 'var(--danger)';
        html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
        html += '<td style="padding:3px 6px;color:var(--text-dim);">' + ts + '</td>';
        html += '<td style="padding:3px 6px;font-family:monospace;">' + cmd + '</td>';
        html += '<td style="padding:3px 6px;text-align:center;color:' + exitColor + ';">' + h.exit_code + '</td>';
        html += '<td style="padding:3px 6px;text-align:right;color:var(--text-dim);">' + h.execution_time_ms.toFixed(0) + 'ms</td>';
        html += '</tr>';
      }
      html += '</tbody></table>';
      container.innerHTML = html;
    } catch(e) {
      document.getElementById('sandbox-history').innerHTML =
        '<div style="color:var(--danger);font-size:0.7rem;">Failed: ' + e.message + '</div>';
    }
  }

  // Keyboard shortcut: Ctrl+Enter to run sandbox
  document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && e.key === 'Enter') {
      var editor = document.getElementById('sandbox-editor');
      if (editor && document.activeElement === editor) {
        e.preventDefault();
        runSandboxCode();
      }
    }
  });

  // ═══ Pipeline Visualization ═══════════════════════════════════

  var _pipelineSelectedId = null;

  function statusBadge(status) {
    var color = status === 'completed' ? 'var(--success)' :
                status === 'running' ? 'var(--accent)' :
                status === 'failed' ? 'var(--danger)' : 'var(--text-muted)';
    return '<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:0.65rem;background:' + color + ';color:#fff;">' + status + '</span>';
  }

  function statusIcon(status) {
    if (status === 'success' || status === 'completed') return '<span style="color:var(--success);">&#10003;</span>';
    if (status === 'failed') return '<span style="color:var(--danger);">&#10007;</span>';
    if (status === 'running') return '<span style="color:var(--accent);">&#8987;</span>';
    return '<span style="color:var(--text-muted);">&#9679;</span>';
  }

  function formatMs(ms) {
    if (ms == null || ms === 0) return '--';
    if (ms < 1000) return ms.toFixed(0) + 'ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
    var m = Math.floor(ms / 60000);
    var s = Math.round((ms % 60000) / 1000);
    return m + 'm ' + s + 's';
  }

  async function refreshPipelineList() {
    try {
      var resp = await fetch(API + '/api/pipelines?limit=10');
      if (!resp.ok) return;
      var data = await resp.json();
      var records = data.records || [];
      var container = document.getElementById('pipeline-list');
      var badge = document.getElementById('pipeline-badge');
      badge.textContent = data.total || 0;

      if (records.length === 0) {
        container.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:16px;">尚无 Pipeline 执行记录</div>';
        return;
      }

      var html = '<table style="width:100%;font-size:0.75rem;border-collapse:collapse;">';
      html += '<thead><tr style="color:var(--text-secondary);border-bottom:1px solid var(--card-border);">';
      html += '<th style="padding:4px 6px;text-align:left;">Template</th>';
      html += '<th style="padding:4px 6px;text-align:center;">Status</th>';
      html += '<th style="padding:4px 6px;text-align:center;">Progress</th>';
      html += '<th style="padding:4px 6px;text-align:right;">Duration</th>';
      html += '</tr></thead><tbody>';

      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        var stepsDone = r.steps != null ? r.steps : 0;
        var stepsTotal = r.total_steps != null ? r.total_steps : '?';
        var selected = _pipelineSelectedId === r.pipeline_id ? 'background:rgba(108,140,255,0.1);' : '';
        html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);cursor:pointer;' + selected + '" onclick="showPipelineDetail(\'' + r.pipeline_id + '\')">';
        html += '<td style="padding:4px 6px;">' + _esc(r.template || 'N/A') + '</td>';
        html += '<td style="padding:4px 6px;text-align:center;">' + statusBadge(r.status) + '</td>';
        html += '<td style="padding:4px 6px;text-align:center;color:var(--text-dim);">' + stepsDone + '/' + stepsTotal + ' steps</td>';
        html += '<td style="padding:4px 6px;text-align:right;color:var(--text-dim);">' + formatMs(r.elapsed_ms) + '</td>';
        html += '</tr>';
      }
      html += '</tbody></table>';
      container.innerHTML = html;
    } catch(e) {
      // Silently fail on refresh
    }
  }

  async function showPipelineDetail(pipelineId) {
    var detailDiv = document.getElementById('pipeline-detail');
    if (_pipelineSelectedId === pipelineId) {
      // Toggle off
      _pipelineSelectedId = null;
      detailDiv.style.display = 'none';
      refreshPipelineList();
      return;
    }
    _pipelineSelectedId = pipelineId;

    try {
      var resp = await fetch(API + '/api/pipelines/' + pipelineId);
      if (!resp.ok) {
        detailDiv.style.display = 'none';
        _pipelineSelectedId = null;
        return;
      }
      var detail = await resp.json();
      var steps = detail.step_details || [];

      var html = '<div style="font-size:0.7rem;color:var(--text-secondary);margin-bottom:8px;">';
      html += '<b>' + _esc(detail.template || 'N/A') + '</b>';
      html += ' &middot; ' + statusBadge(detail.status);
      html += ' &middot; ' + (detail.steps || 0) + '/' + (detail.total_steps || '?') + ' steps';
      html += ' &middot; ' + formatMs(detail.elapsed_ms);
      html += ' <span style="float:right;cursor:pointer;color:var(--text-dim);" onclick="showPipelineDetail(\'' + pipelineId + '\')">&#10005;</span>';
      html += '</div>';

      if (steps.length === 0) {
        html += '<div style="color:var(--text-muted);font-size:0.7rem;">No step details available</div>';
      } else {
        html += '<table style="width:100%;font-size:0.7rem;border-collapse:collapse;">';
        html += '<thead><tr style="color:var(--text-secondary);border-bottom:1px solid var(--card-border);">';
        html += '<th style="padding:3px 6px;text-align:left;">Step</th>';
        html += '<th style="padding:3px 6px;text-align:center;">Status</th>';
        html += '<th style="padding:3px 6px;text-align:right;">Duration</th>';
        html += '</tr></thead><tbody>';
        for (var i = 0; i < steps.length; i++) {
          var s = steps[i];
          var stepStatus = s.status || 'pending';
          html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">';
          html += '<td style="padding:3px 6px;">' + statusIcon(stepStatus) + ' ' + _esc(s.step_name || 'Step ' + (i+1)) + '</td>';
          html += '<td style="padding:3px 6px;text-align:center;font-size:0.65rem;">' + stepStatus + '</td>';
          html += '<td style="padding:3px 6px;text-align:right;color:var(--text-dim);">' + (s.elapsed_ms != null ? formatMs(s.elapsed_ms) : '--') + '</td>';
          html += '</tr>';
          if (s.error) {
            html += '<tr><td colspan="3" style="padding:2px 6px;color:var(--danger);font-size:0.65rem;">&nbsp;&nbsp;&nbsp;Error: ' + _esc(String(s.error)) + '</td></tr>';
          }
        }
        html += '</tbody></table>';
      }
      detailDiv.innerHTML = html;
      detailDiv.style.display = 'block';
      refreshPipelineList();
    } catch(e) {
      detailDiv.style.display = 'none';
      _pipelineSelectedId = null;
    }
  }

  // Auto-refresh pipeline list (polling + SSE triggers instant refresh)
  refreshPipelineList();
  setInterval(refreshPipelineList, 5000);

  // ═══ Plugin Marketplace ═══════════════════════════════════════

  var _pluginTab = 'available';
  var _pluginData = null;
  var _pluginConfigTarget = null;

  function switchPluginTab(tab) {
    _pluginTab = tab;
    document.querySelectorAll('.plugin-tab-btn').forEach(function(btn) {
      btn.classList.remove('active');
    });
    document.getElementById('tab-' + tab).classList.add('active');
    renderPlugins();
  }

  async function fetchPlugins() {
    try {
      var res = await fetch(API + '/api/dashboard/plugins');
      _pluginData = await res.json();
      renderPlugins();
    } catch(e) {
      console.error('Plugin fetch failed:', e);
    }
  }

  function renderPlugins() {
    if (!_pluginData) return;
    var grid = document.getElementById('plugin-grid');
    var stats = document.getElementById('plugin-stats');
    if (stats) {
      stats.textContent = _pluginData.installed + ' / ' + _pluginData.total + ' installed (' + _pluginData.enabled + ' enabled)';
    }

    var list = _pluginTab === 'available' ? (_pluginData.available || []) : (_pluginData.installed_list || []);
    if (!list.length) {
      grid.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:24px;grid-column:1/-1;">' +
        (_pluginTab === 'installed' ? 'No plugins installed yet.' : 'No plugins available.') + '</div>';
      return;
    }

    grid.innerHTML = list.map(function(p) {
      var capsHtml = (p.capabilities_used || []).map(function(c) {
        return '<span class="plugin-cap-tag">' + c + '</span>';
      }).join('');

      var installed = p.installed || (_pluginData.installed_list || []).some(function(i) { return i.id === p.id; });
      var enabled = p.enabled === true;
      var cardClass = installed ? ' installed' : '';

      var actionsHtml = '';
      if (!installed) {
        actionsHtml = '<button class="plugin-btn install" onclick="installPlugin(\'' + p.id + '\')">Install</button>';
      } else {
        actionsHtml =
          '<button class="plugin-btn toggle' + (enabled ? ' enabled' : '') + '" onclick="togglePlugin(\'' + p.id + '\', ' + !enabled + ')">' +
            (enabled ? 'Enabled' : 'Disabled') +
          '</button>' +
          '<button class="plugin-btn config" onclick="openPluginConfig(\'' + p.id + '\')" title="配置">&#9881;</button>' +
          '<button class="plugin-btn uninstall" onclick="uninstallPlugin(\'' + p.id + '\')">Uninstall</button>';
      }

      return '<div class="plugin-card' + cardClass + '">' +
        '<div><span class="plugin-card-name">' + p.name + '</span><span class="plugin-card-version">v' + (p.version || '1.0.0') + '</span></div>' +
        '<div class="plugin-card-desc">' + (p.description || '') + '</div>' +
        '<div class="plugin-card-caps">' + capsHtml + '</div>' +
        '<div class="plugin-card-meta">' +
          '<span>by ' + (p.author || 'Unknown') + '</span>' +
        '</div>' +
        '<div class="plugin-card-actions">' + actionsHtml + '</div>' +
      '</div>';
    }).join('');
  }

  async function installPlugin(pluginId) {
    try {
      await fetch(API + '/api/dashboard/plugins/install', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({plugin_id: pluginId})
      });
      await fetchPlugins();
    } catch(e) {
      console.error('Install failed:', e);
    }
  }

  async function uninstallPlugin(pluginId) {
    try {
      await fetch(API + '/api/dashboard/plugins/uninstall', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({plugin_id: pluginId})
      });
      await fetchPlugins();
    } catch(e) {
      console.error('Uninstall failed:', e);
    }
  }

  async function togglePlugin(pluginId, enabled) {
    try {
      await fetch(API + '/api/dashboard/plugins/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({plugin_id: pluginId, enabled: enabled})
      });
      await fetchPlugins();
    } catch(e) {
      console.error('Toggle failed:', e);
    }
  }

  function openPluginConfig(pluginId) {
    _pluginConfigTarget = pluginId;
    var plugin = (_pluginData.available || []).concat(_pluginData.installed_list || [])
      .find(function(p) { return p.id === pluginId; });
    if (!plugin) return;

    document.getElementById('plugin-config-title').textContent = '配置 - ' + plugin.name;
    document.getElementById('plugin-config-textarea').value = JSON.stringify(plugin.config || {}, null, 2);
    document.getElementById('plugin-config-error').style.display = 'none';
    document.getElementById('plugin-config-modal').classList.add('show');
  }

  function closePluginConfig() {
    document.getElementById('plugin-config-modal').classList.remove('show');
    _pluginConfigTarget = null;
  }

  async function savePluginConfig() {
    var errEl = document.getElementById('plugin-config-error');
    var textarea = document.getElementById('plugin-config-textarea');
    var config;
    try {
      config = JSON.parse(textarea.value);
    } catch(e) {
      errEl.textContent = 'Invalid JSON: ' + e.message;
      errEl.style.display = 'block';
      return;
    }
    try {
      await fetch(API + '/api/dashboard/plugins/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({plugin_id: _pluginConfigTarget, config: config})
      });
      closePluginConfig();
      await fetchPlugins();
    } catch(e) {
      errEl.textContent = 'Save failed: ' + e.message;
      errEl.style.display = 'block';
    }
  }

  // Load plugins on page load
  fetchPlugins();
  setInterval(fetchPlugins, 30000);

  // ── Approval Queue ──
  var _approvalTab = 'pending';

  function switchApprovalTab(tab) {
    _approvalTab = tab;
    document.querySelectorAll('.approval-tab').forEach(function(btn) {
      btn.style.color = 'var(--text-secondary)';
      btn.style.borderBottomColor = 'transparent';
      btn.classList.remove('active');
    });
    var activeBtn = document.getElementById('approval-tab-' + tab);
    if (activeBtn) {
      activeBtn.style.color = 'var(--accent)';
      activeBtn.style.borderBottomColor = 'var(--accent)';
      activeBtn.classList.add('active');
    }
    refreshApproval();
  }

  async function refreshApproval() {
    var container = document.getElementById('approval-tab-content');
    try {
      if (_approvalTab === 'pending') {
        var res = await fetch(API + '/api/approvals/pending');
        var data = await res.json();
        renderPendingApprovals(data, container);
      } else if (_approvalTab === 'history') {
        var res = await fetch(API + '/api/approvals/history?limit=50');
        var data = await res.json();
        renderApprovalHistory(data, container);
      } else if (_approvalTab === 'policies') {
        var res = await fetch(API + '/api/approvals/policies');
        var data = await res.json();
        renderApprovalPolicies(data, container);
      }
    } catch(e) {
      container.innerHTML = '<div class="empty">Fetch error: ' + e.message + '</div>';
    }
  }

  function renderPendingApprovals(data, container) {
    var badge = document.getElementById('approval-badge');
    var count = data.count || 0;
    if (count > 0) {
      badge.style.display = 'inline';
      badge.textContent = count;
    } else {
      badge.style.display = 'none';
    }
    var list = data.requests || [];
    if (list.length === 0) {
      container.innerHTML = '<div class="empty" style="color:var(--success);">No pending approvals — all clear.</div>';
      return;
    }
    var html = '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:11px;">';
    html += '<thead><tr style="border-bottom:1px solid var(--card-border);color:var(--text-secondary);text-align:left;">';
    html += '<th style="padding:4px 8px;">Task</th>';
    html += '<th style="padding:4px 8px;">Domain</th>';
    html += '<th style="padding:4px 8px;">Risk</th>';
    html += '<th style="padding:4px 8px;">Requested</th>';
    html += '<th style="padding:4px 8px;">Actions</th>';
    html += '</tr></thead><tbody>';
    list.forEach(function(r) {
      var riskColor = r.risk_level === 'critical' ? 'var(--danger)' :
                      r.risk_level === 'high' ? '#ff9800' :
                      r.risk_level === 'medium' ? 'var(--warning)' : 'var(--text-muted)';
      html += '<tr style="border-bottom:1px solid var(--card-border);">';
      html += '<td style="padding:3px 8px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (r.prompt || '') + '">' + (r.prompt || '') + '</td>';
      html += '<td style="padding:3px 8px;">' + (r.domain || '') + '</td>';
      html += '<td style="padding:3px 8px;color:' + riskColor + ';font-weight:600;">' + (r.risk_level || '') + '</td>';
      html += '<td style="padding:3px 8px;color:var(--text-muted);white-space:nowrap;">' + (r.requested_at || '').slice(0, 19) + '</td>';
      html += '<td style="padding:3px 8px;white-space:nowrap;">';
      html += '<button onclick="approveRequest(\'' + r.id + '\')" style="background:var(--success);color:#fff;border:none;border-radius:3px;padding:2px 10px;cursor:pointer;font-size:11px;margin-right:4px;">Approve</button>';
      html += '<button onclick="denyRequest(\'' + r.id + '\')" style="background:var(--danger);color:#fff;border:none;border-radius:3px;padding:2px 10px;cursor:pointer;font-size:11px;">Deny</button>';
      html += '</td></tr>';
    });
    html += '</tbody></table></div>';
    container.innerHTML = html;
  }

  function renderApprovalHistory(data, container) {
    var list = data.requests || [];
    if (list.length === 0) {
      container.innerHTML = '<div class="empty">No approval history.</div>';
      return;
    }
    var html = '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:11px;">';
    html += '<thead><tr style="border-bottom:1px solid var(--card-border);color:var(--text-secondary);text-align:left;">';
    html += '<th style="padding:4px 8px;">Task</th>';
    html += '<th style="padding:4px 8px;">Status</th>';
    html += '<th style="padding:4px 8px;">Risk</th>';
    html += '<th style="padding:4px 8px;">Resolved</th>';
    html += '<th style="padding:4px 8px;">Note</th>';
    html += '</tr></thead><tbody>';
    list.forEach(function(r) {
      var statusColor = r.status === 'approved' ? 'var(--success)' :
                        r.status === 'denied' ? 'var(--danger)' :
                        r.status === 'timed_out' ? '#ff9800' : 'var(--text-muted)';
      html += '<tr style="border-bottom:1px solid var(--card-border);">';
      html += '<td style="padding:3px 8px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (r.prompt || '') + '">' + (r.prompt || '') + '</td>';
      html += '<td style="padding:3px 8px;color:' + statusColor + ';font-weight:600;">' + (r.status || '') + '</td>';
      html += '<td style="padding:3px 8px;">' + (r.risk_level || '') + '</td>';
      html += '<td style="padding:3px 8px;color:var(--text-muted);white-space:nowrap;">' + ((r.resolved_at || '').slice(0, 19)) + '</td>';
      html += '<td style="padding:3px 8px;color:var(--text-muted);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (r.approver_note || '') + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    container.innerHTML = html;
  }

  function renderApprovalPolicies(data, container) {
    var policies = data.policies || [];
    var html = '<div style="margin-bottom:12px;">';
    html += '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">';
    html += '<select id="policy-type" style="background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-primary);border-radius:4px;padding:4px 8px;font-size:11px;font-family:inherit;">';
    html += '<option value="domain">Domain</option><option value="risk_level">Risk Level</option><option value="capability">Capability</option><option value="keyword">Keyword</option></select>';
    html += '<input id="policy-value" placeholder="rule value" style="background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-primary);border-radius:4px;padding:4px 8px;font-size:11px;font-family:inherit;width:160px;">';
    html += '<button onclick="addPolicy()" style="background:var(--accent);color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:11px;">Save</button>';
    html += '</div></div>';

    if (policies.length === 0) {
      html += '<div class="empty" style="color:var(--text-muted);">No policies configured. Default: critical & high risk require approval.</div>';
    } else {
      html += '<table style="width:100%;border-collapse:collapse;font-size:11px;">';
      html += '<thead><tr style="border-bottom:1px solid var(--card-border);color:var(--text-secondary);text-align:left;">';
      html += '<th style="padding:4px 8px;">Type</th><th style="padding:4px 8px;">Value</th><th style="padding:4px 8px;">Enabled</th><th style="padding:4px 8px;">Actions</th>';
      html += '</tr></thead><tbody>';
      policies.forEach(function(p) {
        html += '<tr style="border-bottom:1px solid var(--card-border);">';
        html += '<td style="padding:3px 8px;">' + p.rule_type + '</td>';
        html += '<td style="padding:3px 8px;">' + p.rule_value + '</td>';
        html += '<td style="padding:3px 8px;color:' + (p.enabled ? 'var(--success)' : 'var(--text-muted)') + ';">' + (p.enabled ? 'Yes' : 'No') + '</td>';
        html += '<td style="padding:3px 8px;"><button onclick="deletePolicy(' + p.id + ')" style="background:var(--danger);color:#fff;border:none;border-radius:3px;padding:1px 8px;cursor:pointer;font-size:10px;">Delete</button></td>';
        html += '</tr>';
      });
      html += '</tbody></table>';
    }
    container.innerHTML = html;
  }

  async function approveRequest(id) {
    try {
      await fetch(API + '/api/approvals/' + id + '/approve', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({note: ''})
      });
      refreshApproval();
    } catch(e) { alert('Approve failed: ' + e.message); }
  }

  async function denyRequest(id) {
    var note = prompt('Reason for denial (optional):');
    if (note === null) return; // cancelled
    try {
      await fetch(API + '/api/approvals/' + id + '/deny', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({note: note || ''})
      });
      refreshApproval();
    } catch(e) { alert('Deny failed: ' + e.message); }
  }

  async function addPolicy() {
    var type = document.getElementById('policy-type').value;
    var value = document.getElementById('policy-value').value.trim();
    if (!value) { alert('Rule value required'); return; }
    try {
      await fetch(API + '/api/approvals/policies', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rule_type: type, rule_value: value, enabled: true})
      });
      _approvalTab = 'policies';
      switchApprovalTab('policies');
    } catch(e) { alert('Add policy failed: ' + e.message); }
  }

  async function deletePolicy(id) {
    if (!confirm('Delete this policy?')) return;
    try {
      await fetch(API + '/api/approvals/policies/' + id, {method: 'DELETE'});
      refreshApproval();
    } catch(e) { alert('Delete policy failed: ' + e.message); }
  }

  // Auto-refresh approval queue
  refreshApproval();
  setInterval(refreshApproval, 15000);

  // ═══ Keyboard shortcuts ════════════════════════════════════
  (function() {
    var kbdOverlay = null;

    function createKbdOverlay() {
      if (kbdOverlay) return;
      kbdOverlay = document.createElement('div');
      kbdOverlay.className = 'kbd-overlay';
      kbdOverlay.innerHTML =
        '<div class="kbd-card">' +
          '<button class="kbd-close" title="关闭">&times;</button>' +
          '<h2>⌨ 键盘快捷键</h2>' +
          '<table class="kbd-table">' +
            '<thead><tr><th>按键</th><th>功能</th><th>说明</th></tr></thead>' +
            '<tbody>' +
              '<tr><td><kbd class="kbd-key">?</kbd></td><td>快捷键帮助</td><td class="kbd-desc">显示/隐藏此帮助面板</td></tr>' +
              '<tr><td><kbd class="kbd-key">/</kbd></td><td>聚焦搜索</td><td class="kbd-desc">跳转到全局搜索输入框</td></tr>' +
              '<tr><td><kbd class="kbd-key">Esc</kbd></td><td>关闭面板</td><td class="kbd-desc">关闭帮助 / 搜索面板 / 事件日志</td></tr>' +
              '<tr><td><kbd class="kbd-key">r</kbd></td><td>刷新面板</td><td class="kbd-desc">强制刷新所有数据面板</td></tr>' +
              '<tr><td><kbd class="kbd-key">t</kbd></td><td>切换主题</td><td class="kbd-desc">在暗色 / 亮色 / 自动之间切换</td></tr>' +
            '</tbody>' +
          '</table>' +
        '</div>';
      document.body.appendChild(kbdOverlay);

      // Close handlers
      kbdOverlay.addEventListener('click', function(e) {
        if (e.target === kbdOverlay) hideKbdOverlay();
      });
      kbdOverlay.querySelector('.kbd-close').addEventListener('click', hideKbdOverlay);
    }

    function showKbdOverlay() {
      createKbdOverlay();
      kbdOverlay.classList.add('show');
    }

    function hideKbdOverlay() {
      if (kbdOverlay) kbdOverlay.classList.remove('show');
    }

    function isKbdOverlayVisible() {
      return kbdOverlay && kbdOverlay.classList.contains('show');
    }

    function isSearchPanelOpen() {
      var results = document.getElementById('search-results');
      return results && results.style.display !== 'none';
    }

    function closeSearchPanel() {
      var results = document.getElementById('search-results');
      var badge = document.getElementById('search-badge');
      if (results) results.style.display = 'none';
      if (badge) badge.style.display = 'none';
    }

    function isEventLogOpen() {
      var panel = document.getElementById('event-log-panel');
      return panel && !panel.classList.contains('collapsed');
    }

    function closeEventLog() {
      var panel = document.getElementById('event-log-panel');
      if (panel && !panel.classList.contains('collapsed')) {
        panel.classList.add('collapsed');
      }
    }

    function refreshAllPanels() {
      refreshSummary();
      fetchStatus();
      fetchMetrics();
      fetchAlerts();
      fetchTaskHistory();
      fetchAlertHistory();
      refreshHealth();
      refreshLive();
      refreshCapabilityStats();
      refreshPipelineHistory();
      refreshPipelineSchedules();
      refreshPipelineMonitor();
      refreshHealing();
      refreshHealingTimeline();
      refreshModelCosts();
      refreshGovernance();
      refreshRecovery();
      refreshToolGuard();
      refreshHallucination();
      refreshMemory();
      if (typeof refreshEvals === 'function') refreshEvals();
      if (typeof refreshAudit === 'function') refreshAudit();
      if (typeof refreshVersions === 'function') refreshVersions();
      if (typeof refreshTemplates === 'function') refreshTemplates();
      loadMinisters();
    }

    document.addEventListener('keydown', function(e) {
      // Skip if focus is on input/textarea/select
      var tag = document.activeElement ? document.activeElement.tagName : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      var key = e.key;

      // ? — toggle keyboard shortcuts help
      if (key === '?') {
        e.preventDefault();
        if (isKbdOverlayVisible()) {
          hideKbdOverlay();
        } else {
          showKbdOverlay();
        }
        return;
      }

      // Escape — close overlays / panels
      if (key === 'Escape') {
        if (isKbdOverlayVisible()) {
          e.preventDefault();
          hideKbdOverlay();
          return;
        }
        if (isSearchPanelOpen()) {
          e.preventDefault();
          closeSearchPanel();
          return;
        }
        if (isEventLogOpen()) {
          e.preventDefault();
          closeEventLog();
          return;
        }
        if (isNotifOpen()) {
          e.preventDefault();
          closeNotifications();
          return;
        }
        return;
      }

      // / — focus search
      if (key === '/') {
        e.preventDefault();
        var searchInput = document.getElementById('dashboard-search-input');
        if (searchInput) {
          searchInput.focus();
          searchInput.select();
        }
        return;
      }

      // r — refresh all panels
      if (key === 'r' || key === 'R') {
        e.preventDefault();
        refreshAllPanels();
        return;
      }

      // t — toggle theme
      if (key === 't' || key === 'T') {
        e.preventDefault();
        cycleTheme();
        return;
      }
    });

    // ═══ Notification Center ═══
    var _notifications = [];
    var _notifFilter = 'all';
    var NOTIF_MAX = 100;

    function pushNotification(type, title, desc, category) {
      var now = Date.now();
      _notifications.unshift({
        id: now + '_' + Math.random().toString(36).substr(2, 5),
        type: type,
        title: title || '',
        desc: desc || '',
        category: category,
        timestamp: now,
        read: false
      });
      if (_notifications.length > NOTIF_MAX) { _notifications.length = NOTIF_MAX; }
      updateNotifBadge();
      if (_notifFilter === 'all' || _notifFilter === category) {
        renderNotifications();
      }
    }

    function updateNotifBadge() {
      var badge = document.getElementById('notifBadge');
      if (!badge) return;
      var unread = 0;
      for (var i = 0; i < _notifications.length; i++) {
        if (!_notifications[i].read) unread++;
      }
      badge.textContent = unread > 0 ? (unread > 99 ? '99+' : unread) : '';
    }

    function timeAgo(ts) {
      var diff = Date.now() - ts;
      var sec = Math.floor(diff / 1000);
      if (sec < 60) return '刚刚';
      var min = Math.floor(sec / 60);
      if (min < 60) return min + '分钟前';
      var hr = Math.floor(min / 60);
      if (hr < 24) return hr + '小时前';
      return Math.floor(hr / 24) + '天前';
    }

    function notifIcon(category) {
      switch(category) {
        case 'alert': return '⚠';
        case 'approval': return '✅';
        case 'healing': return '🩺';
        case 'pipeline': return '⚡';
        default: return '📌';
      }
    }

    function notifPanelId(category) {
      switch(category) {
        case 'alert': return 'alertPanel';
        case 'approval': return 'approvalPanel';
        case 'healing': return 'healingPanel';
        case 'pipeline': return 'pipelinePanel';
        default: return '';
      }
    }

    function markAllVisibleRead() {
      var visible = getFilteredNotifications();
      for (var i = 0; i < visible.length; i++) {
        visible[i].read = true;
      }
      // Also mark originals
      for (var j = 0; j < _notifications.length; j++) {
        if ((_notifFilter === 'all' || _notifications[j].category === _notifFilter) && !_notifications[j].read) {
          _notifications[j].read = true;
        }
      }
      updateNotifBadge();
    }

    function getFilteredNotifications() {
      var result = [];
      for (var i = 0; i < _notifications.length; i++) {
        if (_notifFilter === 'all' || _notifications[i].category === _notifFilter) {
          result.push(_notifications[i]);
        }
        if (result.length >= 30) break;
      }
      return result;
    }

    function renderNotifications() {
      var list = document.getElementById('notifList');
      var seeAll = document.getElementById('notifSeeAll');
      if (!list) return;
      var filtered = getFilteredNotifications();
      if (filtered.length === 0) {
        list.innerHTML = '<div class="notif-empty">暂无新通知</div>';
        if (seeAll) seeAll.style.display = 'none';
        return;
      }
      var html = '';
      for (var i = 0; i < filtered.length; i++) {
        var n = filtered[i];
        var cls = n.read ? '' : ' unread';
        html += '<div class="notif-item' + cls + '" onclick="onNotifClick(\'' + n.id + '\', \'' + n.category + '\')">';
        html += '<div class="notif-icon ' + n.category + '">' + notifIcon(n.category) + '</div>';
        html += '<div class="notif-body">';
        html += '<div class="notif-title">' + _esc(n.title) + '</div>';
        html += '<div class="notif-desc">' + _esc(n.desc) + '</div>';
        html += '<div class="notif-time">' + timeAgo(n.timestamp) + '</div>';
        html += '</div></div>';
      }
      list.innerHTML = html;
      if (seeAll) {
        var total = 0;
        for (var j = 0; j < _notifications.length; j++) {
          if (_notifFilter === 'all' || _notifications[j].category === _notifFilter) total++;
        }
        seeAll.style.display = total > 30 ? '' : 'none';
      }
    }

    function onNotifClick(id, category) {
      // Mark as read
      for (var i = 0; i < _notifications.length; i++) {
        if (_notifications[i].id === id) {
          _notifications[i].read = true;
          break;
        }
      }
      updateNotifBadge();
      closeNotifications();
      // Navigate to corresponding panel
      var panelId = notifPanelId(category);
      if (panelId) {
        var el = document.getElementById(panelId);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          // Flash highlight
          el.style.transition = 'box-shadow 0.3s';
          el.style.boxShadow = '0 0 0 3px ' + (category === 'alert' ? 'rgba(239,68,68,0.5)' : category === 'approval' ? 'rgba(168,85,247,0.5)' : category === 'healing' ? 'rgba(34,197,94,0.5)' : 'rgba(59,130,246,0.5)');
          setTimeout(function() { el.style.boxShadow = ''; }, 2000);
        }
      }
    }

    function toggleNotifications(e) {
      if (e) e.stopPropagation();
      var dd = document.getElementById('notifDropdown');
      if (!dd) return;
      if (dd.classList.contains('open')) {
        closeNotifications();
      } else {
        dd.classList.add('open');
        markAllVisibleRead();
        renderNotifications();
        // Close event log if open
        closeEventLog();
      }
    }

    function closeNotifications() {
      var dd = document.getElementById('notifDropdown');
      if (dd) dd.classList.remove('open');
    }

    function isNotifOpen() {
      var dd = document.getElementById('notifDropdown');
      return dd && dd.classList.contains('open');
    }

    function filterNotifications(cat, e) {
      if (e) e.stopPropagation();
      _notifFilter = cat;
      // Update tab active states
      var tabs = document.querySelectorAll('.notif-tab');
      for (var i = 0; i < tabs.length; i++) {
        tabs[i].classList.remove('active');
      }
      if (e && e.target) e.target.classList.add('active');
      markAllVisibleRead();
      renderNotifications();
    }

    function seeAllNotifications() {
      closeNotifications();
      // Open event log as a general overview
      var panel = document.getElementById('event-log-panel');
      if (panel && panel.classList.contains('collapsed')) {
        panel.classList.remove('collapsed');
        var body = document.getElementById('event-log-body');
        if (body) body.scrollTop = 0;
      }
    }

    // Click outside to close
    document.addEventListener('click', function(e) {
      var wrapper = document.getElementById('notifWrapper');
      if (wrapper && !wrapper.contains(e.target) && isNotifOpen()) {
        closeNotifications();
      }
    });

    // Initial notification aggregation from API endpoints
    async function aggregateInitialNotifications() {
      var endpoints = [
        { url: '/api/alerts', key: 'alerts', cat: 'alert', titleFn: function(d) { return d.title || d.message || 'Alert'; }, descFn: function(d) { return d.message || d.detail || ''; } },
        { url: '/api/pipelines', key: 'pipelines', cat: 'pipeline', titleFn: function(d) { return d.template || 'Pipeline'; }, descFn: function(d) { return 'Status: ' + (d.status || '?'); } },
        { url: '/api/healing/timeline', key: 'entries', cat: 'healing', titleFn: function(d) { return 'Healing: ' + (d.action_name || '?'); }, descFn: function(d) { return d.result || ''; } },
        { url: '/api/approvals/queue', key: 'items', cat: 'approval', titleFn: function(d) { return (d.approved != null ? (d.approved ? 'Approved' : 'Denied') : 'Pending'); }, descFn: function(d) { return (d.action || '') + ' · risk=' + (d.risk_level || '?'); } }
      ];
      for (var i = 0; i < endpoints.length; i++) {
        try {
          var ep = endpoints[i];
          var res = await fetch(API + ep.url);
          if (!res.ok) continue;
          var json = await res.json();
          var items = json[ep.key] || json || [];
          if (!Array.isArray(items)) items = [];
          for (var j = 0; j < Math.min(items.length, 10); j++) {
            var d = items[j];
            var ts = (d.timestamp || d.created_at || d.time) ? new Date(d.timestamp || d.created_at || d.time).getTime() : Date.now() - j * 60000;
            if (isNaN(ts)) ts = Date.now() - j * 60000;
            var exists = false;
            for (var k = 0; k < _notifications.length; k++) {
              if (_notifications[k].category === ep.cat && _notifications[k].title === ep.titleFn(d)) { exists = true; break; }
            }
            if (!exists) {
              _notifications.push({
                id: 'init_' + ep.cat + '_' + j + '_' + Date.now(),
                type: ep.cat,
                title: ep.titleFn(d),
                desc: ep.descFn(d),
                category: ep.cat,
                timestamp: ts,
                read: true
              });
            }
          }
        } catch(ex) { /* skip failed endpoints */ }
      }
      // Sort by timestamp desc
      _notifications.sort(function(a, b) { return b.timestamp - a.timestamp; });
      if (_notifications.length > NOTIF_MAX) { _notifications.length = NOTIF_MAX; }
      updateNotifBadge();
      if (_notifFilter === 'all' || isNotifOpen()) {
        renderNotifications();
      }
    }

    // Init notification aggregation
    aggregateInitialNotifications();

    // ── Distributed Tracing ─────────────────────────────────────────
    var _expandedTrace = null;

    async function fetchTraces() {
      try {
        var res = await fetch(API + '/api/traces?limit=20');
        if (!res.ok) return;
        var data = await res.json();
        var traces = data.traces || [];
        var badge = document.getElementById('traces-badge');
        if (badge) badge.textContent = traces.length;
        renderTraces(traces);
      } catch(e) {}
    }

    function renderTraces(traces) {
      var list = document.getElementById('traces-list');
      if (!traces.length) {
        list.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:16px;">暂无追踪数据，执行任务后自动生成</div>';
        return;
      }
      var html = '';
      for (var i = 0; i < traces.length; i++) {
        var t = traces[i];
        var statusIcon = t.status === 'error' ? '⚠️' : '✅';
        var statusColor = t.status === 'error' ? 'var(--danger)' : 'var(--success)';
        html += '<div style="padding:8px 0;border-bottom:1px solid var(--card-border);cursor:pointer;" onclick="toggleTraceDetail(\'' + t.trace_id + '\')">' +
          '<span style="color:' + statusColor + ';margin-right:6px;">' + statusIcon + '</span>' +
          '<strong>' + escHtml(t.root_span_name || 'trace') + '</strong>' +
          ' <span style="color:var(--text-secondary);font-size:0.7rem;">' +
          ' spans:' + (t.span_count || 0) +
          ' | ' + (t.total_latency_ms != null ? t.total_latency_ms.toFixed(1) + 'ms' : '—') +
          '</span>' +
          '</div>';
      }
      list.innerHTML = html;
    }

    async function toggleTraceDetail(traceId) {
      var detail = document.getElementById('traces-detail');
      if (_expandedTrace === traceId) {
        detail.style.display = 'none';
        _expandedTrace = null;
        return;
      }
      _expandedTrace = traceId;
      try {
        var res = await fetch(API + '/api/traces/' + traceId);
        if (!res.ok) { detail.style.display = 'none'; return; }
        var data = await res.json();
        var spans = data.spans || [];
        // Build waterfall bars
        var maxMs = 0;
        for (var i = 0; i < spans.length; i++) {
          var end = spans[i].start_offset_ms + spans[i].latency_ms;
          if (end > maxMs) maxMs = end;
        }
        maxMs = Math.max(maxMs, 1);
        var html = '<div style="font-size:0.7rem;margin-bottom:8px;color:var(--text-secondary);">Trace ' + traceId.substring(0,12) + '... (' + spans.length + ' spans)</div>';
        for (var j = 0; j < spans.length; j++) {
          var s = spans[j];
          var leftPct = (s.start_offset_ms / maxMs * 100).toFixed(1);
          var widthPct = Math.max((s.latency_ms / maxMs * 100).toFixed(1), 0.5);
          var barColor = s.status === 'error' ? '#ff6b6b' : 'var(--accent)';
          var indent = s.parent_id ? '&nbsp;&nbsp;&nbsp;' : '';
          html += '<div style="display:flex;align-items:center;margin:3px 0;">' +
            '<span style="width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.72rem;">' + indent + escHtml(s.name) + '</span>' +
            '<span style="flex:1;height:16px;position:relative;background:rgba(255,255,255,0.04);border-radius:3px;margin:0 8px;">' +
            '<span style="position:absolute;left:' + leftPct + '%;width:' + widthPct + '%;height:100%;background:' + barColor + ';border-radius:3px;min-width:2px;"></span>' +
            '</span>' +
            '<span style="font-size:0.68rem;color:var(--text-secondary);width:55px;text-align:right;">' + s.latency_ms.toFixed(1) + 'ms</span>' +
            '</div>';
        }
        detail.innerHTML = html;
        detail.style.display = 'block';
      } catch(e) {
        detail.style.display = 'none';
      }
    }

    // Poll traces every 15 seconds
    setInterval(fetchTraces, 15000);
    fetchTraces();

  })();
</script>
<div id="toast-container"></div>
<div id="event-log-panel" class="collapsed">
  <div class="event-log-header" onclick="toggleEventLog()">
    <span class="log-title">📡 实时事件</span>
    <span class="log-count" id="event-log-count">0</span>
  </div>
  <div class="event-log-body" id="event-log-body">
    <div class="event-log-empty">暂无事件</div>
  </div>
</div>
</body>
</html>"""
