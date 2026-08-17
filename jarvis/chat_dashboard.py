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
</style>
</head>
<body>
<div class="app">
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

    <button class="newchat" onclick="newChat()">＋ 新对话</button>
    <div class="sect-title">会话</div>
    <div class="convlist" id="convlist"><div class="empty">加载中…</div></div>
    <div class="side-foot">学习曲线已记录 <b id="st-rounds">0</b> 轮 · 数据持久化于数据卷</div>
  </aside>

  <!-- Main -->
  <section class="main">
    <div class="topbar">
      <div class="model-badge" id="modelBadge"><span class="mock"></span><span id="modelName">连接中…</span></div>
      <div class="spacer"></div>
      <button class="tbtn primary" onclick="runRounds()">⚡ 运行进化轮次</button>
      <button class="tbtn" onclick="toggleInsight()">📈 进化看板</button>
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
      <div class="box">
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

<!-- Login modal -->
<div class="overlay" id="overlay" style="display:none">
  <div class="authcard">
    <h2 id="authTitle">登录</h2>
    <div class="sub">单用户部署 · 使用你的管理员账号登录</div>
    <div class="field"><label>用户名</label><input id="authUser" autocomplete="username" placeholder="用户名" /></div>
    <div class="field"><label>密码</label><input id="authPass" type="password" autocomplete="current-password" placeholder="管理员密码" /></div>
    <button class="auth-submit" onclick="submitAuth()">进入</button>
    <div class="auth-err" id="authErr"></div>
  </div>
</div>

<script>
// ── API 基地址 & 鉴权 ──
const API = (window.location && window.location.origin) ? window.location.origin : "{API_BASE}";
const SYS_PROMPT = "你是 Emperor Core —— 一个会自我进化的 AI 助手，回答简洁、准确、有帮助。";
const URL_TOKEN = new URLSearchParams(location.search).get('token');
let TOKEN = localStorage.getItem('ec_token') || URL_TOKEN || '';
let Q = TOKEN ? ('?token='+encodeURIComponent(TOKEN)) : '';
const state = { user:null, usage:{}, currentConv:null, busy:false, evoRunning:false };

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
}
(function(){ let t='light'; try{ t=localStorage.getItem('ec-theme')||'light'; }catch(e){} applyTheme(t); })();
function toggleTheme(){ const cur=document.documentElement.getAttribute('data-theme'); applyTheme(cur==='light'?'dark':'light'); }

/* ── Utilities ── */
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function autoGrow(){ const t=document.getElementById('input'); t.style.height='auto'; t.style.height=Math.min(t.scrollHeight,160)+'px'; }
function onKey(e){ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send(); } }
function scrollChat(){ const c=document.getElementById('chat'); c.scrollTop=c.scrollHeight; }
function fmtTime(ts){ if(!ts) return '—'; const d=new Date(ts*1000); return d.toLocaleString(); }
function fmtNum(n){ n=Number(n||0); return n>=1000 ? (n/1000).toFixed(1)+'k' : ''+n; }

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

/* ── Auth modal ── */
function showLogin(){ document.getElementById('overlay').style.display='flex'; }
function closeLogin(){ document.getElementById('overlay').style.display='none'; }
async function submitAuth(){
  const u=document.getElementById('authUser').value.trim();
  const p=document.getElementById('authPass').value;
  const err=document.getElementById('authErr');
  if(!u||!p){ err.textContent='请输入用户名和密码'; return; }
  try{
    const res = await fetch(API+'/api/auth/login'+Q, {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username:u, password:p})});
    const j = await res.json().catch(()=>({}));
    if(!res.ok){ err.textContent = j.detail || ('登录失败：'+res.status); return; }
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
      '<div class="conv'+(c.id===state.currentConv?' active':'')+'" onclick="switchConv('+c.id+')">'+
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
  else { body.innerHTML=mdLite(content); }
  return body;
}

async function send(){
  const ta=document.getElementById('input'); const text=ta.value.trim(); if(!text||state.busy) return;
  if(!state.user){ showLogin(); return; }
  if(!state.currentConv){ // 自动建会话
    try{ const j=(await (await apiPost('/api/conversations',{title:text.slice(0,30)||'新对话'})).json()); state.currentConv=j.id; await loadConversations(); }
    catch(e){ return; }
  }
  addMsg('user', esc(text));
  ta.value=''; autoGrow();
  const body=addMsg('assistant', '', true);
  state.busy=true; document.getElementById('sendBtn').disabled=true;
  let acc='';
  try{
    const res = await fetch(API+'/api/chat'+Q, {method:'POST', headers:authHeaders(),
      body: JSON.stringify({message:text, conversation_id: state.currentConv, system: SYS_PROMPT})});
    if(res.status===401){ showLogin(); throw new Error('unauthorized'); }
    const reader=res.body.getReader(); const dec=new TextDecoder(); let buf=''; let lastUsage=null;
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
            if(j.delta){ acc+=j.delta; body.innerHTML=mdLite(acc); scrollChat(); }
            if(j.usage){ lastUsage=j.usage; }
          }catch(e){}
        }
      }
    }
    if(!acc) body.textContent='（无回复）';
    // 刷新用量 + 会话列表（updated_at 变化）
    await refreshMe(); await loadConversations();
  }catch(e){ body.textContent='请求失败：'+e; }
  state.busy=false; document.getElementById('sendBtn').disabled=false;
}

async function refreshMe(){
  try{ const j=(await (await apiGet('/api/me')).json()); state.user=j.user; state.usage=j.usage||{}; renderUser(); }catch(e){}
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
