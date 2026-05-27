"""把当天的 daily_report.md 渲染成可点击的 UI 预览(单文件 HTML)。

v2 (Anna 反馈): 不要每条一个大卡片框,改成「一个迁移档下面一连串列表」、暖色调、每条更轻。

跑法: python3 build_preview.py  → 生成 preview.html,浏览器打开即可。
"""
from __future__ import annotations
import hashlib
import html
import json
import os
import re

REPORT = "/Users/annayang/Projects/xhs-ai-ip/system1-scraper/data/daily_report.md"
OUT = os.path.join(os.path.dirname(__file__), "preview.html")

TIER_HDR = re.compile(r"^###\s*(🔥|🟡|⚪)\s*(\S+?迁移)\s*[(（](.+?)[)）]")
ITEM = re.compile(r"^\*\*(\d+)\.\s*(.+?)\*\*$")
META = re.compile(
    r"^原帖\((.+?)\):\*(.+?)\*"
    r"(?:\s*·\s*👍(\d+)\s*💬(\d+))?"
    r"\s*·\s*\[打开原帖\]\((.+?)\)")
COMMENT = re.compile(r"^💡\s*(.+)")
DATE = re.compile(r"#\s*每日选题清单\s*—\s*(\S+)")


def parse(path):
    items, date = [], ""
    tier_emoji = tier_name = tier_desc = ""
    cur = None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = DATE.match(line)
        if m:
            date = m.group(1)
        m = TIER_HDR.match(line)
        if m:
            tier_emoji, tier_name, tier_desc = m.group(1), m.group(2), m.group(3)
            continue
        m = ITEM.match(line)
        if m:
            cur = {"rank": int(m.group(1)), "id": "", "title": m.group(2),
                   "tier_emoji": tier_emoji, "tier_name": tier_name, "tier_desc": tier_desc,
                   "source": "", "english": "", "likes": "", "comments": "",
                   "url": "#", "comment": ""}
            items.append(cur)
            continue
        if cur is not None:
            m = META.match(line)
            if m:
                url = m.group(5)
                cur.update(source=m.group(1), english=m.group(2),
                           likes=m.group(3) or "", comments=m.group(4) or "",
                           url=url,
                           # 稳定身份(收藏/React key 用):由原帖 URL 派生,不随排序/日期变。
                           # 真接 DB 后换成 posts_archive.post_id。
                           id="p" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:11])
                continue
            m = COMMENT.match(line)
            if m:
                cur["comment"] = m.group(1)
    return date, items


TIER_CLASS = {"🔥": "strong", "🟡": "mid", "⚪": "weak"}


def rows_by_tier(items):
    """按档分组,每档一个 header + 下面一连串行。"""
    out, seen = [], []
    for it in items:
        key = (it["tier_emoji"], it["tier_name"], it["tier_desc"])
        if key not in seen:
            seen.append(key)
    e = html.escape
    for emoji, name, desc in seen:
        cls = TIER_CLASS.get(emoji, "weak")
        out.append(f'<div class="tierhdr {cls}"><span class="te">{e(emoji)}</span>'
                   f'<b>{e(name)}</b><span class="td">{e(desc)}</span></div>')
        out.append('<div class="tierlist">')
        for it in [x for x in items if (x["tier_emoji"], x["tier_name"], x["tier_desc"]) == (emoji, name, desc)]:
            metrics = f'👍 {e(it["likes"])} · 💬 {e(it["comments"])}' if it["likes"] else "Product Hunt"
            out.append(f'''<div class="row" data-rank="{it['rank']}">
  <button class="star" onclick="toggleStar(this)" title="收藏">☆</button>
  <div class="rowmain">
    <a class="rtitle" href="{e(it['url'])}" target="_blank" rel="noopener">{it['rank']}. {e(it['title'])}</a>
    <div class="rmeta">{e(it['source'])} · {metrics}</div>
    <div class="rcomment">{e(it['comment'])}</div>
  </div>
</div>''')
        out.append('</div>')
    return "\n".join(out)


def build():
    date, items = parse(REPORT)
    body = rows_by_tier(items)
    data_json = json.dumps(items, ensure_ascii=False)
    return TEMPLATE.replace("{{DATE}}", html.escape(date)) \
                   .replace("{{COUNT}}", str(len(items))) \
                   .replace("{{BODY}}", body) \
                   .replace("{{DATA}}", data_json)


TEMPLATE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>系统① 预览 — 每日选题</title>
<style>
:root{
 --bg:#fbf3e8;        /* 暖奶油底 */
 --panel:#fffaf2;     /* 暖白面板 */
 --line:#ecdcc6;      /* 暖分隔线 */
 --ink:#3d2f24;       /* 暖深棕字 */
 --mut:#a08a72;       /* 暖灰棕 次要字 */
 --accent:#c2562a;    /* 暖陶土橙 主色 */
 --accent-soft:#fbeee0;
 --strong:#c0392b; --mid:#cf8a2e; --weak:#8a7a68;
}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"PingFang SC",sans-serif;background:var(--bg);color:var(--ink)}
.app{display:flex;min-height:100vh}
.side{width:208px;background:var(--panel);border-right:1px solid var(--line);padding:18px 12px;position:sticky;top:0;height:100vh}
.brand{font-weight:700;font-size:15px;padding:6px 10px 16px;color:var(--accent)}
.brand small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px}
.nav{display:flex;flex-direction:column;gap:4px}
.nav button{all:unset;cursor:pointer;padding:9px 12px;border-radius:9px;font-size:14px;color:var(--ink)}
.nav button:hover{background:var(--accent-soft)}
.nav button.on{background:var(--accent);color:#fff;font-weight:600}
.main{flex:1;padding:26px 32px;max-width:760px}
.h1{font-size:20px;font-weight:700;margin:0 0 2px}
.sub{color:var(--mut);font-size:13px;margin-bottom:18px}
.runbar{display:flex;gap:10px;margin-bottom:8px}
.runbar input{flex:1;padding:11px 14px;border:1px solid var(--line);border-radius:10px;font-size:14px;background:var(--panel);color:var(--ink)}
.runbar button{all:unset;cursor:pointer;background:var(--accent);color:#fff;padding:0 22px;border-radius:10px;font-weight:600;display:flex;align-items:center}
.hint{color:var(--mut);font-size:12px;margin:6px 2px 14px}
.note{background:#fdeede;border:1px solid #f3cfa3;color:#8a4b1d;font-size:12px;padding:8px 12px;border-radius:9px;margin-bottom:18px}

/* 一个档 = 一个 header + 下面一连串(无每条框) */
.tierhdr{display:flex;align-items:baseline;gap:8px;margin:24px 0 4px;padding-bottom:6px;border-bottom:2px solid var(--line);font-size:16px}
.tierhdr .te{font-size:17px}
.tierhdr.strong b{color:var(--strong)} .tierhdr.mid b{color:var(--mid)} .tierhdr.weak b{color:var(--weak)}
.tierhdr .td{color:var(--mut);font-size:12px;font-weight:400}
.tierlist{}
.row{display:flex;gap:10px;padding:12px 4px;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:none}
.star{all:unset;cursor:pointer;font-size:18px;color:#d8c4a8;line-height:1.5;flex:0 0 auto}
.star.on{color:#e0962a}
.rowmain{flex:1;min-width:0}
.rtitle{display:block;font-size:15px;font-weight:600;color:var(--ink);text-decoration:none;line-height:1.45}
.rtitle:hover{color:var(--accent)}
.rmeta{font-size:12px;color:var(--mut);margin:3px 0 4px}
.rcomment{font-size:13px;color:#6b5644}
.view{display:none}.view.on{display:block}
.empty{color:var(--mut);font-size:14px;padding:36px 0;text-align:center}
.badge{background:var(--accent-soft);color:var(--accent);font-size:11px;padding:2px 8px;border-radius:20px;margin-left:8px}
.pcard{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 16px;margin-bottom:12px}
</style></head><body>
<div class="app">
  <aside class="side">
    <div class="brand">🔥 热点选题<small>系统① · 设计预览 v2</small></div>
    <nav class="nav">
      <button class="on" onclick="show('run',this)">🚀 跑一次</button>
      <button onclick="show('star',this)">⭐ 精选库 <span id="starcount"></span></button>
      <button onclick="show('topic',this)">🎯 主题管理</button>
      <button onclick="show('set',this)">⚙️ 设置</button>
    </nav>
  </aside>
  <main class="main">
    <div class="note">⚠️ 界面<b>设计预览 v2</b>(暖色调 + 一连串列表,按你的反馈改的)。用 {{DATE}} 真实数据,"开始跑"是演示,真功能 Step 5-7。</div>

    <section id="run" class="view on">
      <div class="h1">🚀 跑一次</div>
      <div class="sub">{{DATE}} · 共 {{COUNT}} 条 · 按选题潜力分档</div>
      <div class="runbar"><input value="AI 创业" placeholder="输入主题词:AI 创业 / 具身智能 / AI 编程"><button>开始跑</button></div>
      <div class="hint">点卡片左侧 ☆ 收藏到精选库 · 点标题打开原帖</div>
      {{BODY}}
    </section>

    <section id="star" class="view">
      <div class="h1">⭐ 精选库</div>
      <div class="sub">你 star 过的选题,跨天累积(演示:本地记,刷新清空)</div>
      <div id="starlist"><div class="empty">还没有收藏 —— 去"跑一次"点 ☆</div></div>
    </section>

    <section id="topic" class="view">
      <div class="h1">🎯 主题管理</div>
      <div class="sub">配置抓哪些主题 + 看版块映射(预览占位)</div>
      <div class="pcard"><b>AI 创业</b> <span class="badge">当前主题</span>
        <div class="rmeta" style="margin-top:6px">内容来自这些版块:r/Entrepreneur · r/startups · r/SaaS · r/OpenAI · r/artificial · r/indiehackers</div>
        <div class="rcomment" style="margin-top:8px"><a href="#" style="color:var(--accent);text-decoration:none;font-weight:600">重新挑选版块</a> <span style="color:var(--mut)">— 如果觉得抓回来的内容不对口,点这个让系统按主题重挑一遍</span></div></div>
      <div class="empty">真功能(主题增删 / 白黑名单 / 缓存刷新)在 Step 7</div>
    </section>

    <section id="set" class="view">
      <div class="h1">⚙️ 设置</div>
      <div class="sub">账号密钥 / 关注词 / 排序口味(预览占位)</div>
      <div class="pcard"><div class="rcomment">热度怎么排:看点赞 + 评论 + 转发,越新的越靠前(当前是默认口味,以后可调)</div></div>
      <div class="empty">真功能在 Step 7</div>
    </section>
  </main>
</div>
<script>
const DATA = {{DATA}};
const starred = new Set();
function show(id, btn){
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('on'));
  document.getElementById(id).classList.add('on');
  document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
}
function toggleStar(btn){
  const rank = +btn.closest('.row').dataset.rank;
  btn.classList.toggle('on');
  if(btn.classList.contains('on')){btn.textContent='★';starred.add(rank);}
  else{btn.textContent='☆';starred.delete(rank);}
  renderStars();
}
const TC={'🔥':'strong','🟡':'mid','⚪':'weak'};
function renderStars(){
  document.getElementById('starcount').textContent = starred.size?('('+starred.size+')'):'';
  const box = document.getElementById('starlist');
  if(!starred.size){box.innerHTML='<div class="empty">还没有收藏 —— 去"跑一次"点 ☆</div>';return;}
  box.innerHTML = '<div class="tierlist">'+DATA.filter(it=>starred.has(it.rank)).map(it=>`
    <div class="row"><div class="rowmain">
      <a class="rtitle" href="${it.url}" target="_blank">${it.tier_emoji} ${it.title}</a>
      <div class="rmeta">${it.source}${it.likes?(' · 👍 '+it.likes+' · 💬 '+it.comments):''}</div>
      <div class="rcomment">${it.comment}</div></div></div>`).join('')+'</div>';
}
</script></body></html>"""


if __name__ == "__main__":
    open(OUT, "w", encoding="utf-8").write(build())
    print("wrote", OUT)
