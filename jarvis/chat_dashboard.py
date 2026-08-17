"""ChatGPT-style dashboard for Emperor Core.

A clean, modern conversational UI (left sidebar + centered chat + slide-over
"evolution insight" panel). The chat talks to the real multi-backend LLM
(``POST /api/chat`` → NVIDIA NIM when configured), while the insight panel
exposes the self-evolution **learning curve**, minister leaderboard and a live
event feed — so the user can both *talk* to the system and *watch it learn*.

The HTML is returned as a plain string with a single ``{API_BASE}`` placeholder
that is replaced at call time, so CSS/JS braces never collide with Python f-strings.
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
    --bg:#212121; --bg-side:#171717; --bg-elev:#2a2a2a; --bg-input:#2f2f2f;
    --text:#ececec; --text-dim:#9b9b9b; --border:#3a3a3a; --accent:#10a37f;
    --accent-soft:rgba(16,163,127,.15); --user:#2f2f2f; --radius:14px;
    --danger:#e06c5e; --warn:#e0b35e; --info:#5ea0e0;
  }
  html[data-theme="light"]{
    --bg:#ffffff; --bg-side:#f7f7f8; --bg-elev:#f0f0f0; --bg-input:#ffffff;
    --text:#1f1f1f; --text-dim:#6b6b6b; --border:#e3e3e3; --accent:#10a37f;
    --accent-soft:rgba(16,163,127,.12); --user:#f0f0f0;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;}
  a{color:var(--accent);text-decoration:none}
  button{font-family:inherit;cursor:pointer}
  .app{display:flex;height:100vh;overflow:hidden}

  /* ── Sidebar ── */
  .sidebar{width:268px;flex:0 0 268px;background:var(--bg-side);border-right:1px solid var(--border);
    display:flex;flex-direction:column;padding:12px;gap:10px}
  .brand{display:flex;align-items:center;gap:10px;padding:6px 4px}
  .brand .logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#0d8e6e);
    display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff}
  .brand .name{font-weight:700;font-size:16px;letter-spacing:.3px}
  .brand .sub{font-size:11px;color:var(--text-dim)}
  .newchat{display:flex;align-items:center;justify-content:center;gap:8px;padding:11px;border:1px solid var(--border);
    border-radius:10px;background:var(--bg-elev);color:var(--text);font-weight:600;transition:.15s}
  .newchat:hover{border-color:var(--accent)}
  .stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:2px}
  .stat{background:var(--bg-elev);border:1px solid var(--border);border-radius:10px;padding:8px 10px}
  .stat .v{font-size:18px;font-weight:700}
  .stat .k{font-size:11px;color:var(--text-dim)}
  .sect-title{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--text-dim);margin:6px 4px 2px}
  .minlist{overflow:auto;flex:1;display:flex;flex-direction:column;gap:4px}
  .min{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:9px;font-size:13px}
  .min:hover{background:var(--bg-elev)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:0 0 8px}
  .min .mname{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .min .mmerit{font-size:11px;color:var(--text-dim)}
  .side-foot{font-size:11px;color:var(--text-dim);padding:6px 4px;border-top:1px solid var(--border)}

  /* ── Main ── */
  .main{flex:1;display:flex;flex-direction:column;min-width:0}
  .topbar{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--border)}
  .model-badge{display:flex;align-items:center;gap:7px;background:var(--bg-elev);border:1px solid var(--border);
    border-radius:999px;padding:6px 12px;font-size:13px}
  .model-badge .live{width:8px;height:8px;border-radius:50%;background:var(--accent)}
  .model-badge .mock{width:8px;height:8px;border-radius:50%;background:var(--warn)}
  .spacer{flex:1}
  .tbtn{background:var(--bg-elev);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:7px 12px;font-size:13px}
  .tbtn:hover{border-color:var(--accent)}
  .tbtn.primary{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}

  .chat{flex:1;overflow:auto;display:flex;flex-direction:column}
  .welcome{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;padding:24px;text-align:center}
  .welcome h1{font-size:28px;margin:0;font-weight:700}
  .welcome p{color:var(--text-dim);margin:0;max-width:520px}
  .chips{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;max-width:680px}
  .chip{background:var(--bg-elev);border:1px solid var(--border);border-radius:999px;padding:9px 16px;font-size:13px;color:var(--text)}
  .chip:hover{border-color:var(--accent)}

  .msg{display:flex;gap:14px;padding:18px 20px;max-width:860px;margin:0 auto;width:100%}
  .msg .av{width:30px;height:30px;border-radius:7px;flex:0 0 30px;display:flex;align-items:center;justify-content:center;
    font-weight:700;font-size:13px;color:#fff}
  .msg.user .av{background:#5b6b8c}
  .msg.bot .av{background:var(--accent)}
  .msg .body{flex:1;min-width:0;white-space:pre-wrap;word-break:break-word}
  .msg pre{background:#0f0f0f;color:#e8e8e8;padding:12px 14px;border-radius:10px;overflow:auto;font-size:13px}
  html[data-theme="light"] .msg pre{background:#1e1e1e;color:#e8e8e8}
  .msg code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .msg :not(pre) > code{background:var(--bg-elev);padding:1px 5px;border-radius:5px;font-size:.92em}
  .typing{display:inline-flex;gap:4px;align-items:center}
  .typing span{width:7px;height:7px;border-radius:50%;background:var(--text-dim);animation:blink 1.2s infinite}
  .typing span:nth-child(2){animation-delay:.2s}.typing span:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}

  .composer{padding:14px 20px 18px;border-top:1px solid var(--border)}
  .composer .box{display:flex;align-items:flex-end;gap:10px;max-width:860px;margin:0 auto;background:var(--bg-input);
    border:1px solid var(--border);border-radius:16px;padding:10px 12px}
  .composer textarea{flex:1;background:transparent;border:none;outline:none;color:var(--text);resize:none;
    font:inherit;max-height:160px;line-height:1.5}
  .composer .send{background:var(--accent);border:none;color:#fff;border-radius:10px;width:38px;height:38px;font-size:18px;flex:0 0 38px}
  .composer .send:disabled{opacity:.4;cursor:not-allowed}

  /* ── Insight panel ── */
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
    <button class="newchat" onclick="newChat()">＋ 新对话</button>
    <div class="stats">
      <div class="stat"><div class="v" id="st-min">–</div><div class="k">活跃大臣</div></div>
      <div class="stat"><div class="v" id="st-rate">–</div><div class="k">成功率</div></div>
    </div>
    <div class="sect-title">大臣</div>
    <div class="minlist" id="minlist"><div class="empty">加载中…</div></div>
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
      <div class="welcome" id="welcome">
        <h1>Emperor Core</h1>
        <p>一个会自我进化的 AI 系统。右侧「进化看板」实时展示它如何在多轮进化中提升功绩与成功率。现在就和它对话吧。</p>
        <div class="chips">
          <div class="chip" onclick="quick('用一句话介绍 Emperor Core 的自进化机制')">介绍自进化机制</div>
          <div class="chip" onclick="quick('帮我写一段 Python 快速排序')">写个快排</div>
          <div class="chip" onclick="quick('当前系统有哪些大臣？各自负责什么领域？')">有哪些大臣</div>
          <div class="chip" onclick="quick('运行几次进化后，成功率有什么变化？')">进化效果如何</div>
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

<script>
// 用当前页面的真实来源作为 API 基地址，避免服务器填的 bind host（127.0.0.1/0.0.0.0）
// 在远程浏览器里指向错误地址导致 "Failed to fetch"；有 origin 时优先用它，否则回退到服务端注入的基址。
const API = (window.location && window.location.origin) ? window.location.origin : "{API_BASE}";
// 从地址栏 ?token= 取令牌并透传给所有 API 调用（与 /dashboard?token= 一致）
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const Q = TOKEN ? ('?token='+encodeURIComponent(TOKEN)) : '';
let history = [];
let busy = false;
let evoRunning = false;

/* ── Theme ── */
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeBtn').textContent = t === 'light' ? '☀️' : '🌙';
  try{ localStorage.setItem('ec-theme', t); }catch(e){}
}
(function(){ let t='dark'; try{ t=localStorage.getItem('ec-theme')||'dark'; }catch(e){} applyTheme(t); })();
function toggleTheme(){ const cur=document.documentElement.getAttribute('data-theme'); applyTheme(cur==='light'?'dark':'light'); }

/* ── Utilities ── */
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function autoGrow(){ const t=document.getElementById('input'); t.style.height='auto'; t.style.height=Math.min(t.scrollHeight,160)+'px'; }
function onKey(e){ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send(); } }
function scrollChat(){ const c=document.getElementById('chat'); c.scrollTop=c.scrollHeight; }

function mdLite(text){
  // fenced code blocks first
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

/* ── Chat ── */
function newChat(){ history=[]; document.getElementById('chat').innerHTML=''; showWelcome(); document.getElementById('input').focus(); }
function showWelcome(){
  document.getElementById('chat').innerHTML =
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

function addMsg(role, content){
  const chat=document.getElementById('chat');
  const w=document.getElementById('welcome'); if(w) w.remove();
  const d=document.createElement('div'); d.className='msg '+(role==='user'?'user':'bot');
  d.innerHTML='<div class="av">'+(role==='user'?'你':'E')+'</div><div class="body"></div>';
  chat.appendChild(d); scrollChat();
  return d.querySelector('.body');
}

async function send(){
  const ta=document.getElementById('input'); const text=ta.value.trim(); if(!text||busy) return;
  addMsg('user', esc(text)); history.push({role:'user',content:text});
  ta.value=''; autoGrow();
  const body=addMsg('bot','<div class="typing"><span></span><span></span><span></span></div>');
  busy=true; document.getElementById('sendBtn').disabled=true;
  let acc='';
  try{
    const res=await fetch(API+'/api/chat'+Q,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text,history:history.slice(0,-1)})});
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
          try{ const j=JSON.parse(p); if(j.delta){ acc+=j.delta; body.innerHTML=mdLite(acc); scrollChat(); } }catch(e){}
        }
      }
    }
    if(!acc) body.textContent='（无回复）';
    history.push({role:'assistant',content:acc});
  }catch(e){ body.textContent='请求失败：'+e; }
  busy=false; document.getElementById('sendBtn').disabled=false;
}

/* ── Run evolution rounds (async + progress polling) ── */
async function runRounds(){
  if(evoRunning) return;
  const n=prompt('运行多少轮进化？（推荐 3–10）','5');
  if(n===null) return; const cycles=Math.max(1,Math.min(200,parseInt(n)||3));
  const btn=document.querySelector('.tbtn.primary'); const old=btn.textContent;
  evoRunning=true; btn.disabled=true; btn.textContent='⏳ 启动中…';
  try{
    const r=await fetch(API+'/api/evolution/run'+Q,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cycles})});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const j=await r.json();
    if(j.already_running) addEvent('evo','进化已在后台进行中，接入进度轮询');
    await pollEvolution(btn);
  }catch(e){ alert('进化启动失败：'+e); }
  evoRunning=false; btn.disabled=false; btn.textContent=old;
}

async function pollEvolution(btn){
  const deadline=Date.now()+1000*60*30; // 30 分钟硬上限，超时不再等待
  while(Date.now()<deadline){
    await sleep(1500);
    let s;
    try{ s=await (await fetch(API+'/api/evolution/status'+Q)).json(); }
    catch(e){ continue; } // 偶发网络抖动，继续轮询
    const done=Number(s.rounds_done||0), total=Number(s.rounds_total||0);
    btn.textContent='⏳ 进化中 '+done+'/'+total;
    await refreshInsight(); // 实时刷新学习曲线，逐点生长
    if(!s.running){
      if(s.last_error){ addEvent('evo','进化出错：'+s.last_error); }
      else { addEvent('evo','完成进化 · 已记录 '+done+' 轮 · 曲线点 '+s.last_recorded_round); }
      await refreshStats();
      return;
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
  const line=(arr,color)=>{ let d=''; arr.forEach((v,i)=>{ d+=(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' '; });
    return '<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="2"/>'; };
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
    const lc=await (await fetch(API+'/api/evolution/learning-curve'+Q)).json();
    drawCurve(lc.points||[]);
    document.getElementById('st-rounds').textContent=lc.rounds||0;
  }catch(e){}
  try{
    const m=await (await fetch(API+'/api/ministers'+Q)).json();
    const arr=(m.ministers||[]).slice().sort((a,b)=>(b.merit||0)-(a.merit||0));
    const max=Math.max(1,...arr.map(x=>x.merit||0));
    document.getElementById('leader').innerHTML = arr.length? arr.map(x=>
      '<div class="lb"><span class="nm">'+esc(x.name)+'</span>'+
      '<span class="bar" style="width:'+Math.max(6,((x.merit||0)/max)*120)+'px"></span>'+
      '<span class="val">'+(x.merit||0)+'</span></div>').join('') : '<div class="empty">无大臣</div>';
  }catch(e){}
}

/* ── Stats + ministers sidebar ── */
async function refreshStats(){
  try{
    const s=await (await fetch(API+'/api/dashboard/summary'+Q)).json();
    document.getElementById('st-rate').textContent=(s.success_rate||0)+'%';
  }catch(e){}
  try{
    const m=await (await fetch(API+'/api/ministers'+Q)).json();
    const arr=m.ministers||[];
    document.getElementById('st-min').textContent=arr.length;
    document.getElementById('minlist').innerHTML = arr.length? arr.map(x=>
      '<div class="min"><span class="dot"></span><span class="mname">'+esc(x.name)+'</span>'+
      '<span class="mmerit">'+(x.merit||0)+'</span></div>').join('') : '<div class="empty">无大臣</div>';
  }catch(e){}
}

/* ── Live events (SSE) ── */
function addEvent(kind, msg){
  const feed=document.getElementById('feed');
  if(feed.querySelector('.empty')) feed.innerHTML='';
  const d=document.createElement('div'); d.className='ev '+kind;
  const t=new Date().toLocaleTimeString();
  d.textContent='['+t+'] '+msg; feed.prepend(d);
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

/* ── Model badge ── */
async function refreshModel(){
  try{
    const s=await (await fetch(API+'/api/llm/status'+Q)).json();
    document.getElementById('modelName').textContent = (s.mock_mode?'mock · ':'LIVE · ')+(s.model||'?');
    document.querySelector('#modelBadge span').className = s.mock_mode?'mock':'live';
  }catch(e){ document.getElementById('modelName').textContent='unknown'; }
}

/* ── Boot ── */
(async function(){ await refreshStats(); await refreshModel(); await refreshInsight(); connectSSE();
  setInterval(refreshStats, 15000); setInterval(refreshInsight, 30000);
  document.getElementById('input').focus();
  // 若后台进化仍在进行（如刷新页面），恢复进度轮询
  try{
    const s=await (await fetch(API+'/api/evolution/status'+Q)).json();
    if(s.running){ evoRunning=true; const b=document.querySelector('.tbtn.primary'); if(b) b.disabled=true; pollEvolution(b); }
  }catch(e){}
})();
</script>
</body>
</html>
'''
    return html.replace("{API_BASE}", api_base)
