"""ChatGPT / Claude 风格的对话控制台（清爽风 + 多用户）。

左栏：当前用户卡片（用户名 / 累计 token 消耗 / 最后活跃 / 登出）+ 会话列表
（新建 / 切换 / 重命名 / 删除）。中间：居中气泡对话，支持多轮上下文（历史从
数据库加载，刷新不丢）。右栏：滑动「进化看板」（学习曲线 / 大臣榜 / 实时事件）。

所有请求经 ``Authorization: Bearer <token>``（SSE 用 ``?token=``）透传登录态；token 存
localStorage，刷新不丢。首次访问未登录会弹出登录 / 注册模态。
"""
from __future__ import annotations


def generate_chat_html(api_base: str = "http://127.0.0.1:8000") -> str:
    html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link id="hljs-theme" rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github.min.css" />
  <script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.9/dist/purify.min.js"></script>
  <title>Emperor Core · 自进化对话</title>
<style>
  :root{
    --bg:#ffffff; --bg-side:#f7f7f8; --bg-elev:#ffffff; --bg-input:#f4f4f5;
    --text:#1f2023; --text-dim:#6b7280; --border:#ececef; --accent:#10a37f;
    --accent-soft:#eafaf4; --user-bubble:#eef2ff; --radius:16px;
    --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
    --danger:#e0564b; --warn:#e0a93b; --info:#4a90d9;
  }
  html[data-theme="dark"]{
    --bg:#1a1b1e; --bg-side:#202123; --bg-elev:#2a2b2e; --bg-input:#2a2b2e;
    --text:#ececf1; --text-dim:#9a9ba1; --border:#33353a; --accent:#10a37f;
    --accent-soft:#1e2f2a; --user-bubble:#2a2f3a;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;}
  button{font-family:inherit;cursor:pointer;border:none;background:none}
  .app{display:flex;height:100vh;overflow:hidden}

  /* ── Sidebar ── */
  .sidebar{width:290px;flex:0 0 290px;background:var(--bg-side);border-right:1px solid var(--border);
    display:flex;flex-direction:column;padding:14px;gap:12px}
  .brand{display:flex;align-items:center;gap:11px;padding:2px 4px}
  .brand .logo{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,var(--accent),#0d8e6e);
    display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:17px}
  .brand .name{font-weight:700;font-size:16px}
  .brand .sub{font-size:11px;color:var(--text-dim)}

  /* 用户卡片 */
  .ucard{background:var(--bg-elev);border:1px solid var(--border);border-radius:14px;padding:12px;box-shadow:var(--shadow);
    display:flex;flex-direction:column;gap:9px}
  .ucard .top{display:flex;align-items:center;gap:10px}
  .ucard .av{width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,#4a90d9,#10a37f);
    color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px;flex:0 0 38px}
  .ucard .uname{font-weight:600;font-size:14px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ucard .badge{font-size:10px;background:var(--accent-soft);color:var(--accent);padding:2px 7px;border-radius:999px;font-weight:600}
  .ucard .meta{display:flex;gap:8px}
  .ucard .mini{flex:1;background:var(--bg-input);border-radius:10px;padding:7px 9px}
  .ucard .mini .v{font-size:15px;font-weight:700}
  .ucard .mini .k{font-size:10px;color:var(--text-dim)}
  .ucard .logout{font-size:12px;color:var(--text-dim);border:1px solid var(--border);border-radius:8px;padding:5px 8px}
  .ucard .logout:hover{color:var(--danger);border-color:var(--danger)}

  .newchat{display:flex;align-items:center;justify-content:center;gap:8px;padding:11px;border:1px dashed var(--border);
    border-radius:12px;color:var(--text);font-weight:600;transition:.15s}
  .newchat:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}

  .sect-title{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--text-dim);margin:2px 6px}
  .convlist{overflow:auto;flex:1;display:flex;flex-direction:column;gap:3px}
  .conv{display:flex;align-items:center;gap:8px;padding:9px 10px;border-radius:10px;font-size:13.5px;cursor:pointer;position:relative}
  .conv:hover{background:var(--bg-elev)}
  .conv.active{background:var(--accent-soft)}
  .conv .ctitle{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .conv .cact{display:none;gap:4px}
  .conv:hover .cact{display:flex}
  .conv .cact button{font-size:13px;color:var(--text-dim);padding:1px 4px;border-radius:6px}
  .conv .cact button:hover{color:var(--text)}
  .conv .cact .del:hover{color:var(--danger)}

  .side-foot{font-size:11px;color:var(--text-dim);padding:6px 6px;border-top:1px solid var(--border)}

  /* ── Main ── */
  .main{flex:1;display:flex;flex-direction:column;min-width:0}
  .topbar{display:flex;align-items:center;gap:10px;padding:11px 18px;border-bottom:1px solid var(--border);background:var(--bg)}
  .model-badge{display:flex;align-items:center;gap:7px;background:var(--bg-input);border-radius:999px;padding:6px 13px;font-size:13px}
  .model-badge .live{width:8px;height:8px;border-radius:50%;background:var(--accent)}
  .model-badge .mock{width:8px;height:8px;border-radius:50%;background:var(--warn)}
  .spacer{flex:1}
  .tbtn{background:var(--bg-elev);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:7px 13px;font-size:13px}
  .tbtn:hover{border-color:var(--accent)}
  .tbtn.primary{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}

  .chat{flex:1;overflow:auto;display:flex;flex-direction:column}
  .messages{max-width:780px;margin:0 auto;width:100%;padding:24px 20px 8px;display:flex;flex-direction:column;gap:18px}
  .welcome{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;padding:24px;text-align:center;min-height:60vh}
  .welcome h1{font-size:27px;margin:0;font-weight:700}
  .welcome p{color:var(--text-dim);margin:0;max-width:520px}
  .chips{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;max-width:680px}
  .chip{background:var(--bg-elev);border:1px solid var(--border);border-radius:999px;padding:9px 16px;font-size:13px;color:var(--text)}
  .chip:hover{border-color:var(--accent);color:var(--accent)}

  .msg{display:flex;gap:14px;align-items:flex-start}
  .msg .av{width:30px;height:30px;border-radius:50%;flex:0 0 30px;display:flex;align-items:center;justify-content:center;
    font-weight:700;font-size:13px;color:#fff;margin-top:2px}
  .msg.user .av{background:#5b6b8c}
  .msg.bot .av{background:var(--accent)}
  .msg .body{flex:1;min-width:0;white-space:pre-wrap;word-break:break-word;line-height:1.65}
  .msg.user .body{background:var(--user-bubble);padding:10px 14px;border-radius:14px;width:fit-content;max-width:100%}
  .msg pre{background:#1e1e1e;color:#e8e8e8;padding:12px 14px;border-radius:10px;overflow:auto;font-size:13px}
  .msg code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .msg :not(pre) > code{background:var(--bg-input);padding:1px 5px;border-radius:5px;font-size:.92em}
  .typing{display:inline-flex;gap:4px;align-items:center}
  .typing span{width:7px;height:7px;border-radius:50%;background:var(--text-dim);animation:blink 1.2s infinite}
  .typing span:nth-child(2){animation-delay:.2s}.typing span:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}

  .composer{padding:14px 20px 18px}
  .composer .box{display:flex;align-items:flex-end;gap:10px;max-width:780px;margin:0 auto;background:var(--bg-elev);
    border:1px solid var(--border);border-radius:18px;padding:10px 12px;box-shadow:var(--shadow)}
  .composer textarea{flex:1;background:transparent;border:none;outline:none;color:var(--text);resize:none;
    font:inherit;max-height:160px;line-height:1.5}
  .composer .send{background:var(--accent);border:none;color:#fff;border-radius:11px;width:38px;height:38px;font-size:18px;flex:0 0 38px}
  .composer .send:disabled{opacity:.4;cursor:not-allowed}

  /* ── 进化看板 drawer ── */
  .insight{position:fixed;top:0;right:0;height:100vh;width:420px;background:var(--bg-side);border-left:1px solid var(--border);
    transform:translateX(100%);transition:transform .25s ease;z-index:40;display:flex;flex-direction:column;padding:16px;gap:14px;overflow:auto}
  .insight.open{transform:translateX(0)}
  .insight h3{margin:0;font-size:15px;display:flex;align-items:center;justify-content:space-between}
  .insight .close{background:none;border:none;color:var(--text-dim);font-size:20px}
  .card{background:var(--bg-elev);border:1px solid var(--border);border-radius:12px;padding:12px}
  .card .ct{font-size:12px;color:var(--text-dim);margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}
  .legend{display:flex;gap:14px;font-size:11px;color:var(--text-dim);margin-top:6px}
  .legend i{display:inline-block;width:10px;height:3px;margin-right:4px;vertical-align:middle}
  .leader{display:flex;flex-direction:column;gap:7px}
  .lb{display:flex;align-items:center;gap:8px;font-size:13px}
  .lb .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lb .bar{height:7px;border-radius:4px;background:var(--accent);flex:0 0 auto}
  .lb .val{font-size:11px;color:var(--text-dim);width:38px;text-align:right}
  .feed{display:flex;flex-direction:column;gap:6px;font-size:12px;max-height:220px;overflow:auto}
  .ev{padding:6px 8px;background:var(--bg-elev);border-radius:8px;border-left:3px solid var(--info)}
  .ev.evo{border-left-color:var(--accent)}.ev.dispatch{border-left-color:var(--warn)}.ev.alert{border-left-color:var(--danger)}
  .empty{color:var(--text-dim);font-size:12px;font-style:italic}
  .scrim{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:35;display:none}
  .scrim.open{display:block}
  ::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-thumb{background:var(--border);border-radius:6px}

  /* ── 登录模态 ── */
  .overlay{position:fixed;inset:0;background:rgba(15,15,20,.5);z-index:60;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(3px)}
  .authcard{width:360px;max-width:92vw;background:var(--bg);border:1px solid var(--border);border-radius:18px;padding:26px 24px;box-shadow:0 12px 40px rgba(0,0,0,.2)}
  .authcard h2{margin:0 0 4px;font-size:20px}
  .authcard .sub{color:var(--text-dim);font-size:13px;margin-bottom:18px}
  .tabs{display:flex;gap:8px;margin-bottom:16px}
  .tabs button{flex:1;padding:8px;border-radius:10px;background:var(--bg-input);color:var(--text-dim);font-weight:600;font-size:13px}
  .tabs button.on{background:var(--accent-soft);color:var(--accent)}
  .field{display:flex;flex-direction:column;gap:6px;margin-bottom:13px}
  .field label{font-size:12px;color:var(--text-dim)}
  .field input{background:var(--bg-input);border:1px solid var(--border);border-radius:10px;padding:10px 12px;color:var(--text);font:inherit;outline:none}
  .field input:focus{border-color:var(--accent)}
  .auth-submit{width:100%;background:var(--accent);color:#fff;border-radius:11px;padding:11px;font-weight:600;font-size:14px;margin-top:4px}
  .auth-submit:hover{filter:brightness(1.05)}
  .auth-err{color:var(--danger);font-size:12px;min-height:16px;margin-top:8px;text-align:center}

  /* ── Composer 工具条 / 上传 / 联网开关 ── */
  .composer-tools{display:flex;align-items:center;gap:8px;max-width:780px;margin:0 auto 8px;padding:0 4px}
  .chip-btn{background:var(--bg-elev);border:1px solid var(--border);color:var(--text-dim);border-radius:999px;padding:5px 12px;font-size:12px}
  .chip-btn.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:600}
  .file-chip{display:inline-flex;align-items:center;gap:4px;background:var(--bg-elev);border:1px solid var(--border);
    border-radius:999px;padding:5px 12px;font-size:12px;color:var(--text);max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .composer .attach{flex:0 0 38px;width:38px;height:38px;border-radius:11px;border:1px solid var(--border);
    background:var(--bg-input);color:var(--text);font-size:17px;display:flex;align-items:center;justify-content:center}
  .composer .attach:hover{border-color:var(--accent);color:var(--accent)}

  /* ── 文件卡片 / 图片缩略图 / 来源链接 ── */
  .msg.user .body.has-attach{background:transparent;padding:0}
  .msg.user .bubble{background:var(--user-bubble);padding:10px 14px;border-radius:14px;width:fit-content;max-width:100%;margin-top:8px}
  .file-card{display:flex;align-items:center;gap:10px;background:var(--bg-elev);border:1px solid var(--border);
    border-radius:12px;padding:8px 10px;max-width:340px;box-shadow:var(--shadow)}
  .file-card .thumb{width:56px;height:56px;border-radius:8px;object-fit:cover;border:1px solid var(--border);flex:0 0 56px}
  .file-card .file-ico{width:40px;height:40px;font-size:26px;display:flex;align-items:center;justify-content:center;flex:0 0 40px}
  .file-meta{min-width:0}
  .file-meta .fname{font-size:13px;font-weight:600;word-break:break-all}
  .file-meta .fsize{font-size:11px;color:var(--text-dim)}
  .sources{margin-top:10px;display:flex;flex-direction:column;gap:4px}
  .sources .st{font-size:11px;color:var(--text-dim);margin-bottom:2px}
  .sources .src{font-size:12px}
  .sources .src a{color:var(--info);text-decoration:none;word-break:break-all}
  .sources .src a:hover{text-decoration:underline}
  .search-degraded{margin-top:8px;padding:8px 10px;background:rgba(245,158,11,.08);border-left:3px solid #f59e0b;border-radius:4px;font-size:12px;color:var(--text-dim)}
  .search-degraded b{color:#f59e0b;display:block;margin-bottom:2px}
  .search-degraded .reason{font-family:monospace;font-size:11px;color:var(--text-dim);opacity:.85;margin:2px 0 4px}
  .search-degraded .hint{font-size:11px;opacity:.7}

  /* ── 管理员面板 ── */
  .admincard{width:560px;max-width:94vw;max-height:80vh;display:flex;flex-direction:column;background:var(--bg);
    border:1px solid var(--border);border-radius:18px;padding:22px;box-shadow:0 12px 40px rgba(0,0,0,.2)}
  .admincard h3{margin:0 0 14px;font-size:17px;display:flex;align-items:center;justify-content:space-between}
  .admincard .close{background:none;border:none;color:var(--text-dim);font-size:20px}
  .admin-list{overflow:auto;display:flex;flex-direction:column;gap:8px}
  .arow{display:flex;align-items:center;gap:10px;background:var(--bg-input);border:1px solid var(--border);border-radius:12px;padding:10px 12px}
  .ainfo{flex:1;min-width:0}
  .ainfo b{font-size:14px}
  .ainfo .asub{font-size:11px;color:var(--text-dim)}
  .aacts{display:flex;gap:6px}
  .aacts button{font-size:12px;border:1px solid var(--border);border-radius:8px;padding:5px 9px;color:var(--text);background:var(--bg-elev)}
  .aacts button:hover{border-color:var(--accent);color:var(--accent)}
  .aacts button.danger:hover{border-color:var(--danger);color:var(--danger)}

  /* ── 视觉打磨：动效 / 代码块 / 代码模式 / 响应式 ── */
  .msg{ animation: msgIn .26s ease both; }
  @keyframes msgIn{ from{opacity:0; transform:translateY(6px);} to{opacity:1; transform:none;} }
  .messages{ animation: fadeIn .3s ease both; }
  @keyframes fadeIn{ from{opacity:0;} to{opacity:1;} }
  .welcome h1{ background:linear-gradient(135deg,var(--accent),#4a90d9); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
  .composer .box:focus-within{ border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }
  .msg .body{ min-height:1em; }

  /* 代码块（Codex 风格） */
  .ec-pre{ position:relative; margin:10px 0; }
  .code-bar{ display:flex; align-items:center; justify-content:space-between; background:#161b22; color:#8b949e;
    font-size:11px; padding:5px 12px; border:1px solid #2d333b; border-bottom:none; border-radius:9px 9px 0 0; }
  .code-lang{ text-transform:uppercase; letter-spacing:.6px; font-weight:600; }
  .code-copy{ background:#21262d; border:1px solid #30363d; color:#c9d1d9; border-radius:6px; padding:3px 11px; font-size:11px; cursor:pointer; }
  .code-copy:hover{ border-color:var(--accent); color:var(--accent); }
  .msg pre{ margin:0; border-radius:0 0 9px 9px; border:1px solid #2d333b; border-top:none; }
  html[data-theme="light"] .code-bar{ background:#f0f0f3; color:#57606a; border-color:#d8dde3; }
  html[data-theme="light"] .code-copy{ background:#fff; border-color:#d8dde3; color:#333; }
  html[data-theme="light"] .msg pre{ border-color:#d8dde3; }

  /* 代码模式（Codex 式编码视图） */
  body.code-mode .messages{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; max-width:940px; }
  body.code-mode .msg .body{ font-size:13.5px; line-height:1.7; }
  body.code-mode .ec-pre{ margin:14px 0; }
  body.code-mode .code-bar{ font-size:12px; padding:7px 14px; }
  body.code-mode .msg.user .body{ background:transparent; border:1px dashed var(--border); }

  @media (prefers-reduced-motion: reduce){ .msg,.messages{ animation:none; } }

  /* 移动端：侧栏抽屉化 */
  .menu-btn{ display:none; }
  .scrim-side{ position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:45; display:none; }
  .scrim-side.open{ display:block; }
  @media (max-width: 760px){
    body{ padding:0; }
    .sidebar{ position:fixed; left:0; top:0; bottom:0; z-index:50; transform:translateX(-100%); transition:transform .25s ease; box-shadow:0 0 40px rgba(0,0,0,.3); }
    .sidebar.open{ transform:translateX(0); }
    .menu-btn{ display:inline-flex; }
    .insight{ width:100%; }
    .messages{ padding:18px 14px 8px; }
    .composer{ padding:12px 14px 16px; }
  }
</style>
</head>
<body>
<div class="app">
  <div class="scrim-side" id="scrimSide" onclick="document.querySelector('.sidebar').classList.remove('open'); this.classList.remove('open')"></div>
  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="brand">
      <div class="logo">E</div>
      <div><div class="name">Emperor Core</div><div class="sub">自进化 AI 朝堂</div></div>
    </div>

    <div class="ucard" id="ucard" style="display:none">
      <div class="top">
        <div class="av" id="uav">U</div>
        <div class="uname" id="uname">—</div>
        <span class="badge" id="ubadge" style="display:none">ADMIN</span>
      </div>
      <div class="meta">
        <div class="mini"><div class="v" id="utok">0</div><div class="k">累计 token</div></div>
        <div class="mini"><div class="v" id="uconv">0</div><div class="k">会话数</div></div>
      </div>
      <div style="font-size:11px;color:var(--text-dim)">最后活跃：<span id="ulast">—</span></div>
      <button class="logout" onclick="logout()">退出登录</button>
    </div>

    <button class="newchat" onclick="newChat(); closeSidebarMobile()">＋ 新对话</button>
    <div class="sect-title">会话</div>
    <div class="convlist" id="convlist"><div class="empty">加载中…</div></div>
    <div class="side-foot">学习曲线已记录 <b id="st-rounds">0</b> 轮 · 数据持久化于数据卷</div>
  </aside>

  <!-- Main -->
  <section class="main">
    <div class="topbar">
      <button class="tbtn menu-btn" onclick="document.querySelector('.sidebar').classList.toggle('open'); document.getElementById('scrimSide').classList.toggle('open')" title="菜单">☰</button>
      <div class="model-badge" id="modelBadge"><span class="mock"></span><span id="modelName">连接中…</span></div>
      <div class="spacer"></div>
      <button class="tbtn primary" onclick="runRounds()">⚡ 运行进化轮次</button>
      <button class="tbtn" onclick="toggleInsight()">📈 进化看板</button>
      <button class="tbtn" id="adminBtn" style="display:none" onclick="toggleAdmin()">🛡️ 用户管理</button>
      <button class="tbtn" id="codeBtn" onclick="toggleCodeMode()" title="Codex 式代码模式">💻 代码模式</button>
      <button class="tbtn" id="themeBtn" onclick="toggleTheme()">🌙</button>
    </div>
    <div class="chat" id="chat">
      <div class="messages" id="messages">
        <div class="welcome" id="welcome">
          <h1>Emperor Core</h1>
          <p>一个会自我进化的 AI 系统。右侧「进化看板」实时展示它如何在多轮进化中提升功绩与成功率。登录后即可创建多个会话、长期记忆对话、并查看你的 token 消耗。</p>
          <div class="chips">
            <div class="chip" onclick="quick('用一句话介绍 Emperor Core 的自进化机制')">介绍自进化机制</div>
            <div class="chip" onclick="quick('帮我写一段 Python 快速排序')">写个快排</div>
            <div class="chip" onclick="quick('当前系统有哪些大臣？各自负责什么领域？')">有哪些大臣</div>
            <div class="chip" onclick="quick('运行几次进化后，成功率有什么变化？')">进化效果如何</div>
          </div>
        </div>
      </div>
    </div>
    <div class="composer">
      <div class="composer-tools">
        <button class="chip-btn" id="webToggle" onclick="toggleWeb()" title="开启后基于实时互联网信息回答">🌐 联网搜索</button>
        <span class="file-chip" id="fileChip" style="display:none"></span>
      </div>
      <div class="box">
        <input type="file" id="fileInput" accept=".jpg,.jpeg,.png,.webp,.txt,.md,.pdf" style="display:none" onchange="onFilePicked(event)" />
        <button class="attach" id="attachBtn" onclick="document.getElementById('fileInput').click()" title="上传文件（图片/文档）">📎</button>
        <textarea id="input" rows="1" placeholder="给 Emperor Core 发消息…（Enter 发送，Shift+Enter 换行）" oninput="autoGrow()" onkeydown="onKey(event)"></textarea>
        <button class="send" id="sendBtn" onclick="send()">↑</button>
      </div>
    </div>
  </section>
</div>

<!-- Insight panel -->
<div class="scrim" id="scrim" onclick="toggleInsight()"></div>
<aside class="insight" id="insight">
  <h3>📈 进化看板 <button class="close" onclick="toggleInsight()">×</button></h3>
  <div class="card">
    <div class="ct">学习曲线（功绩 / 成功率）</div>
    <div id="curve"></div>
    <div class="legend"><span><i style="background:var(--accent)"></i>平均功绩</span><span><i style="background:var(--warn)"></i>成功率×100</span></div>
  </div>
  <div class="card">
    <div class="ct">大臣排行榜（按功绩）</div>
    <div class="leader" id="leader"><div class="empty">加载中…</div></div>
  </div>
  <div class="card">
    <div class="ct">实时事件</div>
    <div class="feed" id="feed"><div class="empty">等待事件…</div></div>
  </div>
</aside>

<!-- Login / Register modal -->
<div class="overlay" id="overlay" style="display:none">
  <div class="authcard">
    <div class="tabs">
      <button id="tabLogin" class="on" onclick="switchTab('login')">登录</button>
      <button id="tabReg" onclick="switchTab('register')">注册</button>
    </div>
    <h2 id="authTitle">登录</h2>
    <div class="sub" id="authSub">使用你的账号登录 Emperor Core</div>
    <div class="field"><label>用户名</label><input id="authUser" autocomplete="username" placeholder="用户名" /></div>
    <div class="field"><label>密码</label><input id="authPass" type="password" autocomplete="current-password" placeholder="密码" /></div>
    <div class="field" id="authConfirmField" style="display:none"><label>确认密码</label><input id="authConfirm" type="password" placeholder="再次输入密码" /></div>
    <button class="auth-submit" id="authSubmitBtn" onclick="submitAuth()">进入</button>
    <div class="auth-err" id="authErr"></div>
  </div>
</div>

<!-- Admin panel modal -->
<div class="overlay" id="adminOverlay" style="display:none">
  <div class="admincard">
    <h3>🛡️ 用户管理 <button class="close" onclick="toggleAdmin()">×</button></h3>
    <div class="admin-list" id="adminList"><div class="empty">加载中…</div></div>
  </div>
</div>

<script>
// ── API 基地址 & 鉴权 ──
const API = (window.location && window.location.origin) ? window.location.origin : "{API_BASE}";
const SYS_PROMPT = "你是 Emperor Core —— 一个会自我进化的 AI 助手，回答简洁、准确、有帮助。";
const URL_TOKEN = new URLSearchParams(location.search).get('token');
let TOKEN = localStorage.getItem('ec_token') || URL_TOKEN || '';
let Q = TOKEN ? ('?token='+encodeURIComponent(TOKEN)) : '';
const state = { user:null, usage:{}, currentConv:null, busy:false, evoRunning:false, webSearch:false, pendingFile:null };

function setToken(t){ TOKEN=t; Q = t ? ('?token='+encodeURIComponent(t)) : ''; localStorage.setItem('ec_token', t); }
function authHeaders(){ return TOKEN ? {'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'} : {'Content-Type':'application/json'}; }

async function apiGet(path){
  const res = await fetch(API+path+Q, {headers: authHeaders()});
  if(res.status===401){ showLogin(); throw new Error('unauthorized'); }
  return res;
}
async function apiPost(path, body){
  const res = await fetch(API+path+Q, {method:'POST', headers: authHeaders(), body: JSON.stringify(body||{})});
  if(res.status===401){ showLogin(); throw new Error('unauthorized'); }
  return res;
}

/* ── Theme ── */
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeBtn').textContent = t==='light' ? '☀️' : '🌙';
  try{ localStorage.setItem('ec-theme', t); }catch(e){}
  applyHljsTheme(t);
}
function applyHljsTheme(t){
  const el=document.getElementById('hljs-theme');
  if(el) el.href = t==='light'
    ? 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github.min.css'
    : 'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css';
}
(function(){ let t='light'; try{ t=localStorage.getItem('ec-theme')||'light'; }catch(e){} applyTheme(t); })();
function toggleTheme(){ const cur=document.documentElement.getAttribute('data-theme'); applyTheme(cur==='light'?'dark':'light'); }
function toggleCodeMode(){
  const on=document.body.classList.toggle('code-mode');
  const b=document.getElementById('codeBtn');
  if(b){ b.classList.toggle('primary', on); b.textContent = on ? '💻 代码模式·开' : '💻 代码模式'; }
  try{ localStorage.setItem('ec-code-mode', on?'1':'0'); }catch(e){}
}
(function(){ try{ if(localStorage.getItem('ec-code-mode')==='1'){ document.body.classList.add('code-mode'); const b=document.getElementById('codeBtn'); if(b){ b.classList.add('primary'); b.textContent='💻 代码模式·开'; } } }catch(e){} })();

/* ── Utilities ── */
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function autoGrow(){ const t=document.getElementById('input'); t.style.height='auto'; t.style.height=Math.min(t.scrollHeight,160)+'px'; }
function onKey(e){ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send(); } }
function scrollChat(){ const c=document.getElementById('chat'); c.scrollTop=c.scrollHeight; }
function fmtTime(ts){ if(!ts) return '—'; const d=new Date(ts*1000); return d.toLocaleString(); }
function fmtNum(n){ n=Number(n||0); return n>=1000 ? (n/1000).toFixed(1)+'k' : ''+n; }
function fmtSize(n){ n=Number(n||0); if(n<1024) return n+' B'; if(n<1048576) return (n/1024).toFixed(1)+' KB'; return (n/1048576).toFixed(1)+' MB'; }
function isImageExt(ext){ return ['.jpg','.jpeg','.png','.webp'].indexOf(ext)>=0; }

// 轻量 Markdown 回退（CDN 不可用时）
function mdLite(text){
  let out=''; const re=/```(?:[a-zA-Z0-9]*)\n([\s\S]*?)```/g; let last=0; let m;
  while((m=re.exec(text))!==null){
    out += esc(text.slice(last,m.index));
    out += '<pre><code>'+esc(m[1].replace(/\n$/,''))+'</code></pre>';
    last = re.lastIndex;
  }
  out += esc(text.slice(last));
  out = out.replace(/`([^`]+)`/g,'<code>$1</code>');
  return out;
}
// 真实 Markdown 渲染：marked + highlight.js，带 DOMPurify 防 XSS
function renderMarkdown(text){
  let html;
  if(window.marked){ try{ html = marked.parse(text, {breaks:true, gfm:true}); }catch(e){ html = mdLite(text); } }
  else { html = mdLite(text); }
  if(window.DOMPurify){ try{ html = DOMPurify.sanitize(html, {ADD_ATTR:['target']}); }catch(e){} }
  return html;
}
// 为代码块加语言标签 + 复制按钮，并启用语法高亮
function enhanceCodeBlocks(root){
  if(!root) return;
  root.querySelectorAll('pre code').forEach(function(code){
    if(code.dataset.ecEnhanced) return; code.dataset.ecEnhanced='1';
    const pre = code.parentElement;
    let lang=''; const m=(code.className||'').match(/language-([\w-]+)/); if(m) lang=m[1];
    if(window.hljs){ try{ code.classList.add('hljs'); window.hljs.highlightElement(code); }catch(e){} }
    const bar=document.createElement('div'); bar.className='code-bar';
    bar.innerHTML='<span class="code-lang">'+(lang||'code')+'</span>';
    const btn=document.createElement('button'); btn.className='code-copy'; btn.type='button'; btn.textContent='复制';
    btn.onclick=function(){ navigator.clipboard.writeText(code.innerText).then(function(){ btn.textContent='已复制 ✓'; setTimeout(function(){btn.textContent='复制';},1500); }).catch(function(){ btn.textContent='失败'; }); };
    bar.appendChild(btn);
    pre.classList.add('ec-pre'); pre.insertBefore(bar, pre.firstChild);
  });
}

/* ── Auth modal ── */
let authMode = 'login';
function showLogin(){ document.getElementById('overlay').style.display='flex'; }
function closeLogin(){ document.getElementById('overlay').style.display='none'; }
function switchTab(mode){
  authMode = mode;
  const isReg = mode==='register';
  document.getElementById('tabLogin').classList.toggle('on', !isReg);
  document.getElementById('tabReg').classList.toggle('on', isReg);
  document.getElementById('authTitle').textContent = isReg ? '注册' : '登录';
  document.getElementById('authSub').textContent = isReg ? '创建新账号，注册成功后自动登录' : '使用你的账号登录 Emperor Core';
  document.getElementById('authConfirmField').style.display = isReg ? 'flex' : 'none';
  document.getElementById('authPass').placeholder = isReg ? '设置密码（至少 6 位）' : '密码';
  document.getElementById('authSubmitBtn').textContent = isReg ? '注册并进入' : '进入';
  document.getElementById('authErr').textContent = '';
}
async function submitAuth(){
  const u=document.getElementById('authUser').value.trim();
  const p=document.getElementById('authPass').value;
  const err=document.getElementById('authErr');
  if(!u||!p){ err.textContent='请输入用户名和密码'; return; }
  if(authMode==='register'){
    if(p.length<6){ err.textContent='密码至少 6 位'; return; }
    const c=document.getElementById('authConfirm').value;
    if(p!==c){ err.textContent='两次输入的密码不一致'; return; }
  }
  const path = authMode==='register' ? '/api/auth/register' : '/api/auth/login';
  try{
    const res = await fetch(API+path+Q, {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username:u, password:p})});
    const j = await res.json().catch(()=>({}));
    if(!res.ok){ err.textContent = j.detail || ((authMode==='register'?'注册':'登录')+'失败：'+res.status); return; }
    if(j.token){ setToken(j.token); }
    closeLogin(); await boot();
  }catch(e){ err.textContent='网络错误：'+e; }
}
async function logout(){
  try{ await apiPost('/api/auth/logout'); }catch(e){}
  setToken(''); localStorage.removeItem('ec_token'); state.user=null; state.usage={}; state.currentConv=null;
  document.getElementById('ucard').style.display='none';
  showLogin();
}

/* ── User card ── */
function renderUser(){
  const u=state.user; if(!u) return;
  document.getElementById('ucard').style.display='flex';
  document.getElementById('uav').textContent=(u.username||'U').slice(0,1).toUpperCase();
  document.getElementById('uname').textContent=u.username||'—';
  document.getElementById('ubadge').style.display = u.is_admin ? 'inline-block' : 'none';
  const ab=document.getElementById('adminBtn'); if(ab) ab.style.display = u.is_admin ? 'inline-block' : 'none';
  const us=state.usage||{};
  document.getElementById('utok').textContent = fmtNum(us.total_tokens||0);
  document.getElementById('uconv').textContent = us.conversations||0;
  document.getElementById('ulast').textContent = fmtTime(us.last_active||u.last_active);
}

/* ── Conversations ── */
async function loadConversations(){
  try{
    const j = await (await apiGet('/api/conversations')).json();
    const list = j.conversations||[];
    const el = document.getElementById('convlist');
    if(!list.length){ el.innerHTML='<div class="empty">暂无会话，点「新对话」开始</div>'; return; }
    el.innerHTML = list.map(c=>
      '<div class="conv'+(c.id===state.currentConv?' active':'')+'" onclick="switchConv('+c.id+'); closeSidebarMobile()">'+
      '<span class="ctitle">'+esc(c.title||'新对话')+'</span>'+
      '<span class="cact"><button title="重命名" onclick="event.stopPropagation();renameConv('+c.id+')">✎</button>'+
      '<button class="del" title="删除" onclick="event.stopPropagation();deleteConv('+c.id+')">🗑</button></span></div>'
    ).join('');
  }catch(e){}
}
async function newChat(){
  if(!state.user){ showLogin(); return; }
  try{
    const j = await (await apiPost('/api/conversations',{title:'新对话'})).json();
    state.currentConv = j.id;
    clearChat();
    await loadConversations();
    document.getElementById('input').focus();
  }catch(e){}
}
async function switchConv(id){
  state.currentConv=id; await loadConversations();
  try{
    const j = await (await apiGet('/api/conversations/'+id+'/messages')).json();
    const msgs = j.messages||[];
    const box=document.getElementById('messages'); box.innerHTML='';
    if(!msgs.length){ showWelcome(); }
    else msgs.forEach(m=> addMsg(m.role, m.content, false));
    scrollChat();
  }catch(e){}
}
async function renameConv(id){
  const t=prompt('重命名会话：'); if(!t) return;
  try{ await apiPut('/api/conversations/'+id, {title:t}); await loadConversations(); }catch(e){}
}
async function deleteConv(id){
  if(!confirm('删除该会话及其全部消息？')) return;
  try{
    await apiDelete('/api/conversations/'+id);
    if(state.currentConv===id){ state.currentConv=null; clearChat(); showWelcome(); }
    await loadConversations();
  }catch(e){}
}
async function apiPut(path, body){
  const res = await fetch(API+path+Q, {method:'PUT', headers:authHeaders(), body:JSON.stringify(body)});
  if(res.status===401){ showLogin(); throw new Error('unauthorized'); } return res;
}
async function apiDelete(path){
  const res = await fetch(API+path+Q, {method:'DELETE', headers:authHeaders()});
  if(res.status===401){ showLogin(); throw new Error('unauthorized'); } return res;
}

/* ── Chat ── */
function clearChat(){ document.getElementById('messages').innerHTML=''; }
function showWelcome(){
  document.getElementById('messages').innerHTML =
   '<div class="welcome" id="welcome"><h1>Emperor Core</h1>'+
   '<p>一个会自我进化的 AI 系统。右侧「进化看板」实时展示它如何在多轮进化中提升功绩与成功率。现在就和它对话吧。</p>'+
   '<div class="chips">'+
   '<div class="chip" onclick="quick(\'用一句话介绍 Emperor Core 的自进化机制\')">介绍自进化机制</div>'+
   '<div class="chip" onclick="quick(\'帮我写一段 Python 快速排序\')">写个快排</div>'+
   '<div class="chip" onclick="quick(\'当前系统有哪些大臣？各自负责什么领域？\')">有哪些大臣</div>'+
   '<div class="chip" onclick="quick(\'运行几次进化后，成功率有什么变化？\')">进化效果如何</div>'+
   '</div></div>';
}
function quick(q){ document.getElementById('input').value=q; send(); }
function addMsg(role, content, streaming=true){
  const box=document.getElementById('messages');
  const w=document.getElementById('welcome'); if(w) w.remove();
  const d=document.createElement('div'); d.className='msg '+(role==='user'?'user':'bot');
  d.innerHTML='<div class="av">'+(role==='user'?'你':'E')+'</div><div class="body"></div>';
  box.appendChild(d); scrollChat();
  const body=d.querySelector('.body');
  if(role==='assistant' && streaming){ body.innerHTML='<div class="typing"><span></span><span></span><span></span></div>'; }
  else { body.innerHTML=renderMarkdown(content); enhanceCodeBlocks(body); }
  return body;
}

/* 用户消息（可带文件卡片 / 图片缩略图）*/
function addUserMsg(text, file){
  const box=document.getElementById('messages');
  const w=document.getElementById('welcome'); if(w) w.remove();
  const d=document.createElement('div'); d.className='msg user';
  let bodyHtml='';
  if(file){
    const isImg=isImageExt(file.ext);
    bodyHtml += '<div class="file-card">' +
      (isImg ? '<img class="thumb" src="'+API+'/api/files/'+file.id+Q+'" alt="'+esc(file.name)+'" />'
             : '<div class="file-ico">📄</div>') +
      '<div class="file-meta"><div class="fname">'+esc(file.name)+'</div>'+
      '<div class="fsize">'+fmtSize(file.size)+' · '+String(file.ext||'').replace('.','').toUpperCase()+'</div></div></div>';
  }
  if(text){ bodyHtml += '<div class="bubble">'+esc(text)+'</div>'; }
  d.innerHTML='<div class="av">你</div><div class="body'+(file?' has-attach':'')+'">'+bodyHtml+'</div>';
  box.appendChild(d); scrollChat();
  return d;
}

/* 来源链接（解析 SSE sources 事件）*/
function renderSources(body, sources){
  if(!sources || !sources.length) return;
  const el=document.createElement('div'); el.className='sources';
  el.innerHTML='<div class="st">🔗 来源</div>' +
    sources.map(s=>'<div class="src"><a href="'+esc(s.url)+'" target="_blank" rel="noopener">'+esc(s.title||s.url)+'</a></div>').join('');
  body.appendChild(el);
}

/* 联网搜索降级提示（SSE search_degraded 事件）*/
function renderSearchDegraded(reason){
  const node=document.createElement('div'); node.className='search-degraded';
  const r = (reason||'').replace(/[<>&]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
  node.innerHTML='<b>⚠️ 联网搜索不可用</b><div class="reason">'+esc(r)+'</div>'+
    '<div class="hint">模型将仅依据已有知识回答，不会伪造来源。</div>';
  searchDegraded={reason:reason,_node:node};
  return node.outerHTML;
}

/* 联网搜索开关 */
function toggleWeb(){
  state.webSearch = !state.webSearch;
  const b=document.getElementById('webToggle');
  b.classList.toggle('on', state.webSearch);
  b.textContent = state.webSearch ? '🌐 已开启联网' : '🌐 联网搜索';
}

/* 文件上传 */
async function onFilePicked(e){
  const f = e.target.files && e.target.files[0];
  e.target.value='';
  if(!f) return;
  if(!state.user){ showLogin(); return; }
  const chip=document.getElementById('fileChip');
  chip.style.display='inline-flex'; chip.textContent='📎 上传中…';
  try{
    const fd=new FormData(); fd.append('file', f);
    const res=await fetch(API+'/api/upload'+Q, {method:'POST',
      headers: TOKEN ? {'Authorization':'Bearer '+TOKEN} : {}, body:fd});
    const j=await res.json().catch(()=>({}));
    if(res.status===401){ showLogin(); chip.style.display='none'; return; }
    if(!res.ok){ chip.textContent='❌ '+(j.detail||('上传失败：'+res.status)); setTimeout(()=>chip.style.display='none',4000); return; }
    state.pendingFile = j.file;
    chip.textContent='📎 '+j.file.name;
  }catch(e){ chip.textContent='❌ 上传失败：'+e; }
}

async function send(){
  const ta=document.getElementById('input'); const text=ta.value.trim();
  const file = state.pendingFile;
  if((!text && !file) || state.busy) return;
  if(!state.user){ showLogin(); return; }
  if(!state.currentConv){ // 自动建会话
    const title = text ? text.slice(0,30) : (file ? file.name.slice(0,30) : '新对话');
    try{ const j=(await (await apiPost('/api/conversations',{title:title})).json()); state.currentConv=j.id; await loadConversations(); }
    catch(e){ return; }
  }
  addUserMsg(text, file);
  ta.value=''; autoGrow();
  const isImg = file && isImageExt(file.ext);
  const body=addMsg('assistant', '', true);
  if(isImg){ body.innerHTML='<div class="typing"><span></span><span></span><span></span></div><div style="font-size:12px;color:var(--text-dim);margin-top:6px">🖼️ 正在识别图片…</div>'; }
  state.busy=true; document.getElementById('sendBtn').disabled=true;
  let acc=''; let lastSources=null; let searchDegraded=null;
  const payload = {
    message: text,
    conversation_id: state.currentConv,
    system: SYS_PROMPT,
    web_search: state.webSearch,
    file_id: file ? file.id : null,
    image_url: null,
  };
  // 清空待发送附件
  state.pendingFile=null; document.getElementById('fileChip').style.display='none';
  try{
    const res = await fetch(API+'/api/chat'+Q, {method:'POST', headers:authHeaders(),
      body: JSON.stringify(payload)});
    if(res.status===401){ showLogin(); throw new Error('unauthorized'); }
    if(!res.ok){ const j=await res.json().catch(()=>({})); throw new Error(j.detail||('HTTP '+res.status)); }
    const reader=res.body.getReader(); const dec=new TextDecoder(); let buf='';
    while(true){
      const {done,value}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let idx;
      while((idx=buf.indexOf('\n'))>=0){
        const line=buf.slice(0,idx); buf=buf.slice(idx+1);
        if(line.startsWith('data: ')){
          const p=line.slice(6).trim();
          if(p==='[DONE]') continue;
          try{ const j=JSON.parse(p);
            if(j.sources){ lastSources=j.sources; }
            if(j.search_degraded){
              body.innerHTML=renderMarkdown(acc) + renderSearchDegraded(j.reason||'');
              scrollChat(); continue;
            }
            if(j.delta){ acc+=j.delta; body.innerHTML=renderMarkdown(acc); if(lastSources) renderSources(body,lastSources); if(searchDegraded) body.appendChild(searchDegraded._node); scrollChat(); }
          }catch(e){}
        }
      }
    }
    if(!acc){ body.innerHTML='（无回复）'; if(lastSources) renderSources(body,lastSources); }
    else { enhanceCodeBlocks(body); }
    // 刷新用量 + 会话列表（updated_at 变化）
    await refreshMe(); await loadConversations();
  }catch(e){ body.textContent='请求失败：'+e; }
  state.busy=false; document.getElementById('sendBtn').disabled=false;
}

async function refreshMe(){
  try{ const j=(await (await apiGet('/api/me')).json()); state.user=j.user; state.usage=j.usage||{}; renderUser(); }catch(e){}
}

/* ── Admin panel ── */
async function toggleAdmin(){
  const ov=document.getElementById('adminOverlay');
  const show = ov.style.display==='none' || !ov.style.display;
  ov.style.display = show ? 'flex' : 'none';
  if(show) await loadAdminUsers();
}
async function loadAdminUsers(){
  const el=document.getElementById('adminList');
  try{
    const j=await (await apiGet('/api/admin/users')).json();
    const users=j.users||[];
    el.innerHTML = users.length ? users.map(u=>
      '<div class="arow"><div class="ainfo"><b>'+esc(u.username)+'</b>'+
      (u.is_admin?' <span class="badge">ADMIN</span>':'')+
      (u.banned?' <span style="color:var(--danger);font-size:11px">已封禁</span>':'')+
      '<div class="asub">ID '+u.id+' · 配额 '+(u.quota?esc(JSON.stringify(u.quota)):'不限额')+'</div></div>'+
      '<div class="aacts">'+
      (u.banned ? '<button onclick="adminUnban('+u.id+')">解封</button>'
                : '<button class="danger" onclick="adminBan('+u.id+')">封禁</button>')+
      '<button onclick="adminResetPw('+u.id+')">重置密码</button>'+
      '<button onclick="adminSetQuota('+u.id+')">配额</button>'+
      '</div></div>'
    ).join('') : '<div class="empty">暂无用户</div>';
  }catch(e){ el.innerHTML='<div class="empty">加载失败：'+e+'</div>'; }
}
async function adminBan(id){ try{ await apiPost('/api/admin/users/'+id+'/ban',{banned:true}); await loadAdminUsers(); }catch(e){ alert('封禁失败：'+e); } }
async function adminUnban(id){ try{ await apiPost('/api/admin/users/'+id+'/unban'); await loadAdminUsers(); }catch(e){ alert('解封失败：'+e); } }
async function adminResetPw(id){
  const p=prompt('为该用户设置新密码（至少 6 位）：'); if(p===null) return;
  if(p.length<6){ alert('密码至少 6 位'); return; }
  try{ await apiPost('/api/admin/users/'+id+'/password',{password:p}); alert('密码已重置'); }catch(e){ alert('重置失败：'+e); }
}
async function adminSetQuota(id){
  const q=prompt('输入配额 JSON（留空=不限额），例如 {"max_conversations":100}'); if(q===null) return;
  let quota=null;
  if(q.trim()){ try{ quota=JSON.parse(q); }catch(e){ alert('JSON 格式错误'); return; } }
  try{ await apiPut('/api/admin/users/'+id+'/quota',{quota:quota}); await loadAdminUsers(); }catch(e){ alert('设置失败：'+e); }
}

/* ── Run evolution rounds (async + progress polling) ── */
async function runRounds(){
  if(state.evoRunning) return;
  const n=prompt('运行多少轮进化？（推荐 3–10）','5');
  if(n===null) return; const cycles=Math.max(1,Math.min(200,parseInt(n)||3));
  const btn=document.querySelector('.tbtn.primary'); const old=btn.textContent;
  state.evoRunning=true; btn.disabled=true; btn.textContent='⏳ 启动中…';
  try{
    const r=await apiPost('/api/evolution/run',{cycles});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const j=await r.json();
    if(j.already_running) addEvent('evo','进化已在后台进行中，接入进度轮询');
    await pollEvolution(btn);
  }catch(e){ alert('进化启动失败：'+e); }
  state.evoRunning=false; btn.disabled=false; btn.textContent=old;
}
async function pollEvolution(btn){
  const deadline=Date.now()+1000*60*30;
  while(Date.now()<deadline){
    await sleep(1500);
    let s; try{ s=await (await fetch(API+'/api/evolution/status'+Q)).json(); }catch(e){ continue; }
    const done=Number(s.rounds_done||0), total=Number(s.rounds_total||0);
    btn.textContent='⏳ 进化中 '+done+'/'+total;
    await refreshInsight();
    if(!s.running){
      if(s.last_error){ addEvent('evo','进化出错：'+s.last_error); }
      else { addEvent('evo','完成进化 · 已记录 '+done+' 轮 · 曲线点 '+s.last_recorded_round); }
      await refreshStats(); return;
    }
  }
  addEvent('evo','进化轮询超时（>30min），可手动点「进化看板」刷新');
}

/* ── Insight panel ── */
function closeSidebarMobile(){ const s=document.querySelector('.sidebar'); const sc=document.getElementById('scrimSide'); if(s) s.classList.remove('open'); if(sc) sc.classList.remove('open'); }
function toggleInsight(){ document.getElementById('insight').classList.toggle('open'); document.getElementById('scrim').classList.toggle('open'); refreshInsight(); }
function drawCurve(points){
  const el=document.getElementById('curve');
  if(!points.length){ el.innerHTML='<div class="empty">暂无数据，点「运行进化轮次」生成学习曲线</div>'; return; }
  const W=380,H=150,pad=24;
  const merit=points.map(p=>p.avg_merit||0);
  const succ=points.map(p=>(p.success_rate||0)*100);
  const maxV=Math.max(1, ...merit, ...succ);
  const n=points.length;
  const X=i=> pad + (n===1?0:(i/(n-1))*(W-2*pad));
  const Y=v=> H-pad - (v/maxV)*(H-2*pad);
  const line=(arr,color)=>{ let d=''; arr.forEach((v,i)=>{ d+=(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' '; }); return '<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="2"/>'; };
  const dots=(arr,color)=>arr.map((v,i)=>'<circle cx="'+X(i).toFixed(1)+'" cy="'+Y(v).toFixed(1)+'" r="2.4" fill="'+color+'"/>').join('');
  const last=points[n-1];
  el.innerHTML='<svg width="100%" viewBox="0 0 '+W+' '+H+'" style="display:block">'+
    '<line x1="'+pad+'" y1="'+(H-pad)+'" x2="'+(W-pad)+'" y2="'+(H-pad)+'" stroke="var(--border)"/>'+
    line(merit,'var(--accent)')+dots(merit,'var(--accent)')+
    line(succ,'var(--warn)')+dots(succ,'var(--warn)')+'</svg>'+
    '<div style="font-size:11px;color:var(--text-dim);margin-top:4px">当前 功绩 '+ (last.avg_merit||0) +' · 成功率 '+ Math.round((last.success_rate||0)*100) +'% · 共 '+n+' 点</div>';
}
async function refreshInsight(){
  try{
    const lc=await (await apiGet('/api/evolution/learning-curve')).json();
    drawCurve(lc.points||[]);
    document.getElementById('st-rounds').textContent=lc.rounds||0;
  }catch(e){}
  try{
    const m=await (await apiGet('/api/ministers')).json();
    const arr=(m.ministers||[]).slice().sort((a,b)=>(b.merit||0)-(a.merit||0));
    const max=Math.max(1,...arr.map(x=>x.merit||0));
    document.getElementById('leader').innerHTML = arr.length? arr.map(x=>
      '<div class="lb"><span class="nm">'+esc(x.name)+'</span>'+
      '<span class="bar" style="width:'+Math.max(6,((x.merit||0)/max)*120)+'px"></span>'+
      '<span class="val">'+(x.merit||0)+'</span></div>').join('') : '<div class="empty">无大臣</div>';
  }catch(e){}
}
async function refreshStats(){
  try{ const s=await (await apiGet('/api/dashboard/summary')).json();
    document.getElementById('st-rate')&&(document.getElementById('st-rate').textContent=(s.success_rate||0)+'%');
  }catch(e){}
}
async function refreshModel(){
  try{
    const s=await (await apiGet('/api/llm/status')).json();
    document.getElementById('modelName').textContent=(s.mock_mode?'mock · ':'LIVE · ')+(s.model||'?');
    document.querySelector('#modelBadge span').className=s.mock_mode?'mock':'live';
  }catch(e){ document.getElementById('modelName').textContent='unknown'; }
}

/* ── Live events (SSE) ── */
function addEvent(kind, msg){
  const feed=document.getElementById('feed');
  if(feed.querySelector('.empty')) feed.innerHTML='';
  const d=document.createElement('div'); d.className='ev '+kind;
  d.textContent='['+new Date().toLocaleTimeString()+'] '+msg; feed.prepend(d);
  while(feed.children.length>40) feed.removeChild(feed.lastChild);
}
function connectSSE(){
  try{
    const es=new EventSource(API+'/api/events'+Q);
    es.onmessage=(e)=>{ try{ const j=JSON.parse(e.data); const kind=(j.type||'info').toLowerCase();
      let label=j.type||'event'; if(j.data&&j.data.message) label+=' · '+j.data.message;
      addEvent(['evo','evolution'].some(k=>kind.includes(k))?'evo':(['dispatch'].some(k=>kind.includes(k))?'dispatch':'ev'), label);
    }catch(err){} };
    es.onerror=()=>{};
  }catch(e){}
}

/* ── Boot ── */
async function boot(){
  try{
    const me=await (await apiGet('/api/me')).json();
    state.user=me.user; state.usage=me.usage||{};
    renderUser();
  }catch(e){ showLogin(); return; }
  await loadConversations();
  await refreshStats(); await refreshModel(); await refreshInsight(); connectSSE();
  setInterval(refreshMe, 20000); setInterval(refreshStats, 15000); setInterval(refreshInsight, 30000);
  document.getElementById('input').focus();
  // 若后台进化仍在进行（如刷新页面），恢复进度轮询
  try{
    const s=await (await fetch(API+'/api/evolution/status'+Q)).json();
    if(s.running){ state.evoRunning=true; const b=document.querySelector('.tbtn.primary'); if(b) b.disabled=true; pollEvolution(b); }
  }catch(e){}
}
boot();
</script>
</body>
</html>
'''
    return html.replace("{API_BASE}", api_base)
