#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OKX Orbit 帖子查重工具 v6
Usage: python plagiarism_checker.py
Open:  http://localhost:5099

密码配置（任选一种）：
  方式1 - 环境变量：export APP_PASSWORD=yourpassword
  方式2 - 直接改下面 DEFAULT_PASSWORD 的值
"""
import sys, subprocess, os

_deps = {'flask':'flask','requests':'requests','bs4':'beautifulsoup4','sklearn':'scikit-learn'}
for m, p in _deps.items():
    try: __import__(m)
    except ImportError:
        print(f"Installing {p}…")
        subprocess.check_call([sys.executable,'-m','pip','install',p,'--break-system-packages','-q'])

from flask import Flask, request, jsonify, render_template_string, Response
import requests as rq, re, json, difflib
from urllib.parse import quote, urlparse, urlunparse
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sk_cos

app  = Flask(__name__)
CST  = timezone(timedelta(hours=8))
UA   = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
HDRS = {'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9',
        'Accept':'text/html,application/xhtml+xml,*/*;q=0.8',
        'Referer':'https://www.okx.com/'}


# ── 图片代理 ─────────────────────────────────────────────────────────────────
@app.route('/imgproxy')
def imgproxy():
    url = request.args.get('url','')
    if not url or not url.startswith('http'): return '',400
    try:
        r = rq.get(url, headers={**HDRS,'Accept':'image/*'}, timeout=10, stream=True)
        return Response(r.content, content_type=r.headers.get('Content-Type','image/jpeg'),
                        headers={'Cache-Control':'public,max-age=3600'})
    except: return '',404

def px(url): return f'/imgproxy?url={quote(url,safe="")}'

# ── UID 查询（从用户主页抓取）────────────────────────────────────────────────
def fetch_uid(channel_id):
    if not channel_id: return ''
    try:
        url = f'https://www.okx.com/zh-hans/orbit/user/{channel_id}'
        r = rq.get(url, headers=HDRS, timeout=15)
        scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                             r.text, re.DOTALL)
        for s in scripts:
            if 'profileUserInfo' in s:
                data = json.loads(s)
                info = data['appContext']['initialProps']['orbit']['profileUserInfo']
                return str(info.get('uid',''))
    except: pass
    return ''

# ── 规范化输入 ────────────────────────────────────────────────────────────────
def normalize(raw):
    raw = raw.strip()
    if re.fullmatch(r'\d{10,}', raw): return 'id', raw
    m = re.search(r'/(?:orbit|feed)/post/(\d+)', raw)
    if m: return 'id', m.group(1)
    if raw.startswith('http'): return 'url', raw
    return 'unknown', raw

def id_to_url(pid): return f'https://www.okx.com/zh-hans/feed/post/{pid}'

# ── 解析 OKX 帖子页面 ─────────────────────────────────────────────────────────
def parse_orbit(html, page_url):
    out = {}

    # 作者昵称
    m = re.search(r'"nickName"\s*:\s*"([^"]+)"', html)
    out['author'] = m.group(1) if m else ''

    # 渠道ID (authorId)
    m = re.search(r'"authorId"\s*:\s*"(\d+)"', html)
    out['channel_id'] = m.group(1) if m else ''

    # 帖子 ID（优先 URL）
    m = re.search(r'/(?:orbit|feed)/post/(\d+)', page_url)
    out['post_id'] = m.group(1) if m else ''
    if not out['post_id']:
        m = re.search(r'"contentId"\s*:\s*"(\d+)"', html)
        out['post_id'] = m.group(1) if m else ''

    # 精确发帖时间（毫秒戳 → 北京时间）
    out['post_time'] = ''
    m = re.search(r'"publishTime"\s*:\s*"?(\d{13})"?', html)
    if not m: m = re.search(r'"createTime"\s*:\s*"?(\d{13})"?', html)
    if m:
        dt = datetime.fromtimestamp(int(m.group(1))/1000, tz=CST)
        out['post_time'] = dt.strftime('%Y-%m-%d %H:%M:%S')

    # 完整正文：从 "content":"..." 字段取（在 contentId 之前）
    text = ''
    pid = out['post_id']
    if pid:
        ci = html.find(f'"contentId":"{pid}"')
        if ci > 0:
            region = html[max(0, ci-8000):ci]
            # content 字段在 contentId 前面
            m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)+)"\s*,\s*"contentId"', html[max(0,ci-8000):ci+50])
            if m:
                text = m.group(1).replace('\\n','\n').replace('\\"','"').replace('\\\\','\\')
    # 备用：og:description
    if not text:
        soup = BeautifulSoup(html,'html.parser')
        og = soup.find('meta', property='og:description')
        if og and og.get('content'):
            text = re.sub(r'^欧易\s*[-－]\s*','',og['content']).strip()
    out['text'] = text

    # 图片列表（imageList 里的 url）
    imgs, seen = [], set()
    for u in re.findall(r'"url"\s*:\s*"(https://[^"]*cdn/trade/content[^"]+)"', html):
        if u not in seen:
            seen.add(u); imgs.append({'url':px(u),'orig':u,'alt':''})
    out['images'] = imgs

    out['sentences'] = sentences(text)
    return out

def sentences(text):
    parts = re.split(r'[。！？\.\!\?\n]+', text)
    return [p.strip() for p in parts if len(p.strip()) >= 5]

# ── HTTP 抓取 ─────────────────────────────────────────────────────────────────
def clean_url(raw_url):
    """去掉 URL 中 ? 和 # 后的参数，只保留路径部分"""
    p = urlparse(raw_url)
    return urlunparse(p._replace(query='', fragment=''))

def fetch(url):
    base = dict(url=url,author='',uid='',channel_id='',post_id='',post_time='',
                text='',sentences=[],images=[],success=False,error='')
    try:
        r = rq.Session().get(url, headers=HDRS, timeout=20, allow_redirects=True)
        r.raise_for_status()
        base.update(parse_orbit(r.text, r.url))
        # 清理 URL，去掉 ?xxx=yyy 部分
        pid = base.get('post_id','')
        base['url'] = f'https://www.okx.com/zh-hans/feed/post/{pid}' if pid else clean_url(r.url)
        # 查询真实 UID
        base['uid'] = fetch_uid(base.get('channel_id',''))
        base['success'] = True
    except Exception as e:
        base['error'] = str(e)
    return base

# ── 相似度 ────────────────────────────────────────────────────────────────────
def cos_sim(t1,t2):
    try:
        v = TfidfVectorizer(analyzer='char_wb',ngram_range=(2,4),max_features=20000,sublinear_tf=True)
        m = v.fit_transform([t1,t2])
        return float(sk_cos(m[0:1],m[1:2])[0][0])
    except: return 0.0

def jaccard(t1,t2):
    ex = lambda t: set(re.findall(r'[一-鿿]|[a-zA-Z]{2,}',t))
    a,b = ex(t1),ex(t2)
    return len(a&b)/len(a|b) if (a|b) else 0.0

def sim(t1,t2):
    if not t1 or not t2: return 0.0
    return round(0.45*cos_sim(t1,t2)+0.25*jaccard(t1,t2)
                 +0.30*difflib.SequenceMatcher(None,t1[:8000],t2[:8000]).ratio(),4)

def sent_match(s1,s2,thr=0.50):
    sc1=[0.0]*len(s1); sc2=[0.0]*len(s2)
    for i,a in enumerate(s1):
        for j,b in enumerate(s2):
            r=difflib.SequenceMatcher(None,a,b).ratio()
            if r>sc1[i]: sc1[i]=r
            if r>sc2[j]: sc2[j]=r
    return sc1,sc2

def dup_imgs(a,b):
    u1={i['orig'] for i in a if 'orig' in i}
    u2={i['orig'] for i in b if 'orig' in i}
    return [{'url':px(u)} for u in u1&u2]

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index(): return render_template_string(HTML)

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json(force=True)
    items = data.get('items',[])
    if len(items)<2: return jsonify({'error':'至少需要 2 篇'}),400
    if len(items)>6: return jsonify({'error':'最多支持 6 篇'}),400
    pages=[]
    for it in items:
        if it.get('type')=='text':
            txt=it.get('value','')
            pages.append(dict(url='',author=it.get('label','手动输入'),channel_id='',post_id='',
                              post_time='',text=txt,sentences=sentences(txt),images=[],success=bool(txt)))
        else:
            tp,val=normalize(it.get('value',''))
            pages.append(fetch(id_to_url(val) if tp=='id' else val))
    # 按发帖时间排序（无时间的排最后）
    pages.sort(key=lambda p: p.get('post_time','') or '9999')

    # 只对比：其他帖子 vs 最早的帖子（index 0）
    cmps=[]
    n=len(pages)
    p0=pages[0]
    for j in range(1,n):
        p2=pages[j]
        sc=sim(p0['text'],p2['text'])
        s1,s2=sent_match(p0['sentences'],p2['sentences'])
        cmps.append({'i':0,'j':j,'pct':round(sc*100,1),
                     'ss1':s1,'ss2':s2,
                     'dup_imgs':dup_imgs(p0['images'],p2['images'])})
    return jsonify({'pages':pages,'cmps':cmps})

# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orbit 查重</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f1f5f9;--card:#fff;--border:#e2e8f0;
  --blue:#2563eb;--blue-lt:#eff6ff;--blue-bd:#bfdbfe;
  --green:#16a34a;--green-lt:#f0fdf4;--green-bd:#bbf7d0;
  --amber:#d97706;--amber-lt:#fffbeb;--amber-bd:#fde68a;
  --red:#dc2626;--red-lt:#fef2f2;--red-bd:#fecaca;
  --text:#0f172a;--muted:#64748b;--muted2:#94a3b8;
}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Segoe UI',sans-serif;
     background:var(--bg);color:var(--text);font-size:14px;min-height:100vh}

/* topbar */
.topbar{background:#fff;border-bottom:1px solid var(--border);padding:0 24px;height:52px;
        display:flex;align-items:center;gap:10px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.logo{width:28px;height:28px;background:var(--blue);border-radius:7px;display:flex;
      align-items:center;justify-content:center;color:#fff;font-size:14px}
.topbar h1{font-size:.95rem;font-weight:700}
.badge{background:var(--blue-lt);color:var(--blue);border:1px solid var(--blue-bd);
       border-radius:4px;font-size:.62rem;font-weight:700;padding:2px 7px}
.topbar-r{margin-left:auto;font-size:.72rem;color:var(--muted)}

.wrap{max-width:1320px;margin:0 auto;padding:22px 18px}

/* card */
.card{background:var(--card);border-radius:12px;border:1px solid var(--border);
      box-shadow:0 1px 3px rgba(0,0,0,.04);margin-bottom:16px}
.card-pad{padding:20px 22px}
.ctitle{font-size:.67rem;font-weight:700;color:var(--muted);text-transform:uppercase;
        letter-spacing:.9px;margin-bottom:14px}

/* input */
.post-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.idx{width:22px;height:22px;border-radius:6px;color:#fff;font-size:.68rem;font-weight:800;
     display:flex;align-items:center;justify-content:center;flex-shrink:0}
.pi{flex:1;background:#fff;border:1.5px solid var(--border);border-radius:8px;
    padding:8px 12px;color:var(--text);font-size:.86rem;outline:none;transition:.15s}
.pi:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(37,99,235,.1)}
.pi::placeholder{color:var(--muted2)}
.btn-del{background:#fff;border:1px solid var(--border);color:var(--muted2);
         width:28px;height:28px;border-radius:7px;cursor:pointer;
         display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:12px}
.btn-del:hover{border-color:var(--red);color:var(--red);background:var(--red-lt)}
.actions{display:flex;gap:8px;margin-top:12px;align-items:center}
.btn-add{background:#fff;border:1.5px dashed var(--border);color:var(--muted);
         border-radius:8px;padding:7px 14px;cursor:pointer;font-size:.8rem;transition:.15s}
.btn-add:hover{border-color:var(--blue);color:var(--blue);background:var(--blue-lt)}
.btn-go{background:var(--blue);color:#fff;border:none;border-radius:8px;
        padding:8px 26px;font-size:.88rem;font-weight:700;cursor:pointer;transition:.15s;
        box-shadow:0 1px 4px rgba(37,99,235,.25)}
.btn-go:hover:not(:disabled){background:#1d4ed8;transform:translateY(-1px)}
.btn-go:disabled{opacity:.45;cursor:not-allowed}
.btn-batch{background:#fff;border:1.5px solid var(--border);color:var(--muted);
           border-radius:8px;padding:7px 14px;cursor:pointer;font-size:.82rem;transition:.15s}
.btn-batch:hover,.btn-batch.on{border-color:var(--blue);color:var(--blue);background:var(--blue-lt)}
.batch-ta{width:100%;border:1.5px solid var(--border);border-radius:9px;padding:11px 14px;
          font-size:.84rem;font-family:inherit;color:var(--text);resize:vertical;outline:none;
          line-height:1.65;transition:.15s;background:#fff}
.batch-ta:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(37,99,235,.1)}
.batch-ta::placeholder{color:var(--muted2)}
.batch-link-row{display:flex;align-items:center;gap:8px;background:#f8fafc;
                border:1px solid var(--border);border-radius:7px;padding:7px 12px;font-size:.8rem}
.batch-link-num{width:20px;height:20px;border-radius:5px;color:#fff;font-size:.68rem;font-weight:800;
                display:flex;align-items:center;justify-content:center;flex-shrink:0}
.batch-link-val{flex:1;color:var(--blue);font-family:monospace;font-size:.77rem;
                word-break:break-all;min-width:0}
.hint{font-size:.7rem;color:var(--muted2);margin-top:8px}
.hint code{background:#f1f5f9;border:1px solid var(--border);border-radius:3px;
           padding:1px 4px;color:var(--blue);font-family:monospace}

/* loading */
.loading{display:none;text-align:center;padding:50px}
.spin{width:38px;height:38px;border:3px solid #e2e8f0;border-top-color:var(--blue);
      border-radius:50%;animation:spin .7s linear infinite;margin:0 auto 12px}
@keyframes spin{to{transform:rotate(360deg)}}
#results{display:none}

/* post grid */
.post-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-bottom:16px}
.post-card{background:var(--card);border-radius:12px;border:1px solid var(--border);
           overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.pc-top{padding:16px 18px 14px;border-bottom:1px solid var(--border)}
.pc-num-row{display:flex;align-items:center;gap:8px;margin-bottom:14px}
.pc-num{width:22px;height:22px;border-radius:6px;color:#fff;font-size:.7rem;font-weight:800;
        display:flex;align-items:center;justify-content:center;flex-shrink:0}
.pc-name{font-size:1.05rem;font-weight:800;color:var(--text)}

/* info rows – stacked: label above value */
.info-rows{display:flex;flex-direction:column;gap:10px}
.info-row{}
.info-lbl{font-size:.68rem;font-weight:600;color:var(--muted);margin-bottom:3px;text-transform:uppercase;letter-spacing:.4px}
.info-val{display:flex;align-items:center;gap:7px;flex-wrap:nowrap}
.info-val .v{font-size:.85rem;color:var(--text);font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.info-val .v.mono{font-family:monospace;font-size:.82rem}
.info-val .sub{font-size:.68rem;color:var(--muted);flex-shrink:0;white-space:nowrap}

/* copy button */
.cpbtn{background:#f1f5f9;border:1px solid var(--border);color:var(--muted);
       border-radius:4px;padding:2px 9px;cursor:pointer;font-size:.68rem;
       white-space:nowrap;transition:.15s;flex-shrink:0}
.cpbtn:hover{background:var(--blue-lt);color:var(--blue);border-color:var(--blue-bd)}
.cpbtn.ok{background:var(--green-lt);color:var(--green);border-color:var(--green-bd)}

.pc-err{color:var(--red);font-size:.76rem;padding:4px 0}

/* images in card */
.pc-imgs{display:flex;gap:6px;flex-wrap:wrap;padding:8px 12px;
         background:#fafbfc;border-bottom:1px solid var(--border)}
.pc-imgs img{width:88px;height:66px;object-fit:cover;border-radius:6px;
             border:1px solid var(--border);cursor:zoom-in;transition:.15s}
.pc-imgs img:hover{border-color:var(--blue);transform:scale(1.03)}

/* post body */
.pc-body{padding:10px 14px;font-size:.82rem;line-height:1.85;color:#334155;
         max-height:190px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;background:#fff;
         transition:max-height .3s ease}
.pc-body.exp{max-height:none}
.pc-toggle{display:block;text-align:center;padding:6px;font-size:.71rem;color:var(--blue);
           cursor:pointer;background:#f8fafc;border-top:1px solid var(--border)}
.pc-toggle:hover{background:var(--blue-lt)}

/* comparison */
.cmp-card{background:var(--card);border-radius:12px;border:1px solid var(--border);
          padding:20px 22px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.cmp-head{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.cmp-names{font-size:.92rem;font-weight:700}
.rpill{border-radius:20px;padding:3px 12px;font-size:.73rem;font-weight:700}
.rp-ok{background:var(--green-lt);color:var(--green);border:1px solid var(--green-bd)}
.rp-med{background:var(--amber-lt);color:var(--amber);border:1px solid var(--amber-bd)}
.rp-hi{background:var(--red-lt);color:var(--red);border:1px solid var(--red-bd)}
.big-pct{font-size:2rem;font-weight:800;margin-left:auto}

/* side by side */
.sb{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.col{border-radius:9px;overflow:hidden;border:1px solid var(--border)}
.col-h{padding:9px 13px;font-size:.8rem;font-weight:700;background:#f8fafc;
       border-bottom:1px solid var(--border);display:flex;align-items:baseline;gap:6px}
.col-time{font-size:.68rem;color:var(--muted);font-weight:400;margin-left:auto}
.col-b{padding:12px 14px;font-size:.82rem;line-height:2.05;color:#334155;
       max-height:580px;overflow-y:auto;background:#fff;word-break:break-all}
.col-imgs{display:flex;gap:6px;flex-wrap:wrap;padding:8px 12px;
          background:#fafbfc;border-top:1px solid var(--border)}
.col-imgs img{width:78px;height:58px;object-fit:cover;border-radius:6px;
              border:1px solid var(--border);cursor:zoom-in;transition:.15s}
.col-imgs img:hover{border-color:var(--blue);transform:scale(1.04)}

/* highlights */
.s{border-radius:3px;padding:1px 2px;display:inline}
.h0{color:#334155}
.h1{background:#fef9c3;color:#854d0e;border-bottom:1px solid #fde047}
.h2{background:#fef08a;color:#713f12;border-bottom:1.5px solid #facc15}
.h3{background:#fed7aa;color:#9a3412;border-bottom:2px solid #f97316}
.h4{background:#fecaca;color:#991b1b;border-bottom:2px solid #ef4444;font-weight:700}

/* dup images */
.dup-section{margin-top:12px;padding-top:12px;border-top:1px solid var(--border)}
.dup-imgs{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.dw{border-radius:7px;overflow:hidden;border:2px solid var(--red);padding:4px;background:var(--red-lt)}
.dw img{width:90px;height:68px;object-fit:cover;display:block;border-radius:4px;cursor:zoom-in}
.dl{font-size:.6rem;color:var(--red);text-align:center;padding:2px 0;font-weight:700}

/* highlight legend */
.hl-legend{display:flex;align-items:center;gap:6px;margin-bottom:10px;flex-wrap:wrap;font-size:.72rem;color:var(--muted)}
.hl-legend span{padding:1px 8px;border-radius:3px}

/* copy-all button */
.btn-copy-all{background:#fff;border:1.5px solid var(--border);color:var(--text);
              border-radius:8px;padding:7px 18px;cursor:pointer;font-size:.82rem;
              font-weight:600;transition:.15s;display:flex;align-items:center;gap:6px;
              margin-bottom:14px}
.btn-copy-all:hover{border-color:var(--blue);color:var(--blue);background:var(--blue-lt)}
.btn-copy-all.ok{border-color:var(--green);color:var(--green);background:var(--green-lt)}

/* earliest badge */
.earliest-badge{background:#fef9c3;color:#854d0e;border:1px solid #fde047;
                border-radius:4px;font-size:.6rem;font-weight:700;padding:1px 6px;
                margin-left:6px;vertical-align:middle}
.origin-tag{background:var(--green-lt);color:var(--green);border:1px solid var(--green-bd);
            border-radius:4px;font-size:.62rem;font-weight:700;padding:1px 6px;white-space:nowrap}

/* ── Lightbox ── */
#lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);
    z-index:9999;align-items:center;justify-content:center}
#lb.on{display:flex}
#lb img{max-width:90vw;max-height:88vh;border-radius:8px;object-fit:contain;
        box-shadow:0 8px 40px rgba(0,0,0,.5)}
#lb-close{position:fixed;top:18px;right:22px;color:#fff;font-size:28px;
          cursor:pointer;line-height:1;background:rgba(0,0,0,.4);
          width:40px;height:40px;border-radius:50%;display:flex;
          align-items:center;justify-content:center;transition:.15s}
#lb-close:hover{background:rgba(255,255,255,.2)}

/* scrollbar */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:#f1f5f9}
::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:#94a3b8}

@media(max-width:700px){
  .sb{grid-template-columns:1fr}
  .topbar{padding:0 14px}
  .wrap{padding:14px 10px}
  .post-grid{grid-template-columns:1fr}
}
</style>
</head>
<body>

<!-- Lightbox -->
<div id="lb" onclick="lbClose()">
  <div id="lb-close" onclick="lbClose()">✕</div>
  <img id="lb-img" src="" onclick="event.stopPropagation()">
</div>

<div class="topbar">
  <div class="logo">🔍</div>
  <h1>Orbit 查重</h1>
  <span class="badge">OKX v6</span>
  <span class="topbar-r">最多 6 篇</span>
</div>

<div class="wrap">
  <div class="card card-pad">
    <div class="ctitle">输入帖子</div>

    <!-- 批量粘贴区 -->
    <div id="batchBox" style="display:none;margin-bottom:14px">
      <textarea id="batchTA" class="batch-ta"
        placeholder="把包含链接的文字整段粘贴进来，例如：&#10;原贴：https://oyidl.me/ul/xPxRN1i&#10;抄袭贴：https://oyidl.net/ul/PNsp0iy&#10;&#10;支持 oyidl.me、oyidl.net 短链，OKX 帖子链接，纯数字 ID，每行一条或混在文字里都行"
        rows="5" oninput="batchPreview()"></textarea>
      <div id="batchPreviewArea" style="display:none;margin-top:8px">
        <div style="font-size:.72rem;color:var(--muted);margin-bottom:6px">识别到以下链接，点击确认填入：</div>
        <div id="batchLinks" style="display:flex;flex-direction:column;gap:5px"></div>
        <button class="btn-go" style="margin-top:10px;padding:7px 20px;font-size:.84rem"
                onclick="batchConfirm()">✓ 填入并查重</button>
      </div>
    </div>

    <div id="postList">
      <div class="post-row">
        <div class="idx" style="background:#2563eb">1</div>
        <input class="pi" placeholder="帖子 ID（如 76458135775648）或完整 OKX 链接" autocomplete="off">
      </div>
      <div class="post-row">
        <div class="idx" style="background:#16a34a">2</div>
        <input class="pi" placeholder="帖子 ID（如 76458135775648）或完整 OKX 链接" autocomplete="off">
      </div>
    </div>
    <div class="actions">
      <button class="btn-add" id="btnAdd" onclick="addRow()">＋ 添加</button>
      <button class="btn-batch" id="btnBatch" onclick="toggleBatch()">📋 批量粘贴</button>
      <button class="btn-go" id="btnGo" onclick="go()">🔍 开始查重</button>
    </div>
    <div class="hint">
      支持：OKX 链接、短链（oyidl.me / oyidl.net）、纯数字 ID · Ctrl+Enter 快速开始
    </div>
  </div>

  <div class="loading" id="loading">
    <div class="spin"></div>
    <div style="color:var(--muted);font-size:.88rem">抓取并分析中，请稍候…</div>
  </div>

  <div id="results">
    <div id="postGrid" class="post-grid"></div>
    <div id="cmpList"></div>
  </div>
</div>

<script>
const C=['#2563eb','#16a34a','#d97706','#7c3aed','#db2777','#0891b2'];
let rc=2;

function addRow(){
  if(rc>=6)return;
  const i=rc++,c=C[i%C.length];
  const r=document.createElement('div'); r.className='post-row';
  r.innerHTML=`<div class="idx" style="background:${c}">${i+1}</div>
    <input class="pi" placeholder="帖子 ID 或链接" autocomplete="off">
    <button class="btn-del" onclick="delRow(this)">✕</button>`;
  document.getElementById('postList').appendChild(r);
  if(rc>=6)document.getElementById('btnAdd').style.display='none';
}
function delRow(btn){
  btn.closest('.post-row').remove(); rc--;
  document.getElementById('btnAdd').style.display='';
  document.querySelectorAll('.post-row').forEach((r,i)=>{
    r.querySelector('.idx').textContent=i+1;
    r.querySelector('.idx').style.background=C[i%C.length];
  });
}
function items(){
  return [...document.querySelectorAll('.post-row')]
    .map(r=>({type:'url',value:r.querySelector('.pi').value.trim()}))
    .filter(x=>x.value);
}
async function go(){
  const its=items();
  if(its.length<2){alert('至少填 2 篇');return;}
  document.getElementById('btnGo').disabled=true;
  document.getElementById('loading').style.display='block';
  document.getElementById('results').style.display='none';
  try{
    const res=await fetch('/api/analyze',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({items:its})});
    const d=await res.json();
    if(d.error){alert(d.error);return;}
    render(d);
  }catch(e){alert('失败：'+e.message);}
  finally{
    document.getElementById('loading').style.display='none';
    document.getElementById('btnGo').disabled=false;
  }
}

function x(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

/* 复制按钮 */
function cp(val,btn){
  navigator.clipboard.writeText(val).catch(()=>{
    const t=document.createElement('textarea');
    t.value=val;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();
  });
  btn.textContent='✓'; btn.classList.add('ok');
  setTimeout(()=>{btn.textContent='复制';btn.classList.remove('ok');},1600);
}
function cprow(val){
  if(!val)return `<span style="color:var(--muted2)">—</span>`;
  return `<span class="cr"><span class="cv">${x(val)}</span>
    <button class="cpbtn" onclick="cp('${x(val)}',this)">复制</button></span>`;
}

/* Lightbox */
function lbOpen(src){
  document.getElementById('lb-img').src=src;
  document.getElementById('lb').classList.add('on');
  document.body.style.overflow='hidden';
}
function lbClose(){
  document.getElementById('lb').classList.remove('on');
  document.getElementById('lb-img').src='';
  document.body.style.overflow='';
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')lbClose();});

function imgTag(src,w=88,h=66){
  return `<img src="${x(src)}" style="width:${w}px;height:${h}px;object-fit:cover;border-radius:6px;
    border:1px solid var(--border);cursor:zoom-in;transition:.15s"
    onclick="lbOpen('${x(src)}')"
    onmouseover="this.style.borderColor='var(--blue)';this.style.transform='scale(1.03)'"
    onmouseout="this.style.borderColor='var(--border)';this.style.transform=''">`;
}

/* highlight class */
function hcls(s){
  if(s<.50)return 'h0';
  if(s<.65)return 'h1';
  if(s<.80)return 'h2';
  if(s<.92)return 'h3';
  return 'h4';
}
function renderSents(sents,scores){
  if(!sents||!sents.length)return '<span style="color:var(--muted2)">（空）</span>';
  return sents.map((s,i)=>{
    const sc=scores[i]||0,cls=hcls(sc);
    const tip=sc>.5?`相似度 ${Math.round(sc*100)}%`:'';
    return `<span class="s ${cls}" title="${tip}">${x(s)}。</span> `;
  }).join('');
}

function riskCls(v){return v<30?'rp-ok':v<60?'rp-med':'rp-hi';}
function riskLabel(v){return v<30?'✅ 正常':v<60?'⚠️ 中等风险':'🚨 高风险抄袭';}
function pctColor(v){return v<30?'#16a34a':v<60?'#d97706':'#dc2626';}

function infoRow(label, val, opts={}){
  const {mono=false, sub='', copyVal=''} = opts;
  const cls = mono ? 'v mono' : 'v';
  const cpBtn = copyVal ? `<button class="cpbtn" onclick="cp('${x(copyVal)}',this)">复制</button>` : '';
  const subHtml = sub ? `<span class="sub">${x(sub)}</span>` : '';
  return `<div class="info-row">
    <div class="info-lbl">${label}</div>
    <div class="info-val">
      <span class="${cls}" title="${x(val)}">${x(val)||'<span style=\'color:var(--muted2)\'>—</span>'}</span>
      ${subHtml}${cpBtn}
    </div>
  </div>`;
}

function renderPostGrid(pages){
  document.getElementById('postGrid').innerHTML=pages.map((p,i)=>{
    const c=C[i%C.length];
    const name=p.author||'未知';
    const earliestBadge = i===0 ? '<span class="earliest-badge">最早发帖</span>' : '';

    let rowsHtml = '';
    rowsHtml += infoRow('昵称',    p.author,      {copyVal: p.author});
    rowsHtml += infoRow('帖子 ID', p.post_id,     {mono:true, copyVal: p.post_id});
    rowsHtml += infoRow('UID',     p.uid||'',     {mono:true, copyVal: p.uid||''});
    rowsHtml += infoRow('渠道 ID', p.channel_id,  {mono:true, copyVal: p.channel_id});
    rowsHtml += infoRow('发帖时间', p.post_time||'未识别', {
      sub: p.post_time ? '北京时间' : '',
      copyVal: p.post_time
    });
    if(!p.success && p.error)
      rowsHtml += `<div class="info-row"><div class="info-lbl">错误</div>
        <div class="info-val"><span style="color:var(--red);font-size:.8rem">${x(p.error)}</span></div></div>`;

    const imgs=(p.images||[]).length
      ? `<div class="pc-imgs">${p.images.slice(0,8).map(im=>imgTag(im.url)).join('')}</div>` : '';

    const txt=(p.text||'').trim();
    const body=txt
      ? `<div class="pc-body" id="pb${i}">${x(txt)}</div>
         <span class="pc-toggle" id="pt${i}" onclick="tog(${i})">▼ 展开全文</span>`
      : `<div class="pc-body" style="color:var(--muted2);font-style:italic;font-size:.8rem">（未获取到正文）</div>`;

    return `<div class="post-card">
      <div class="pc-top">
        <div class="pc-num-row">
          <div class="pc-num" style="background:${c}">${i+1}</div>
          <div class="pc-name">${x(name)}${earliestBadge}</div>
        </div>
        <div class="info-rows">${rowsHtml}</div>
        ${p.url?`<div style="margin-top:10px;font-size:.7rem">
          <a href="${x(p.url)}" target="_blank"
             style="color:var(--blue);text-decoration:none;word-break:break-all;
                    display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
             title="${x(p.url)}">${x(p.url)}</a>
        </div>`:''}
      </div>
      ${imgs}${body}
    </div>`;
  }).join('');
}

function tog(i){
  const el=document.getElementById('pb'+i);
  const t=document.getElementById('pt'+i);
  const exp=el.classList.toggle('exp');
  t.textContent=exp?'▲ 收起':'▼ 展开全文';
}

function copyAllInfo(pages){
  const btn=document.getElementById('btnCopyAll');
  const lines=pages.map((p,i)=>{
    const label = i===0 ? `${i+1}. 【最早发帖 / 原帖】` : `${i+1}.`;
    return [
      label,
      `   昵称：${p.author||'—'}`,
      `   帖子ID：${p.post_id||'—'}`,
      `   UID：${p.uid||'—'}`,
      `   渠道ID：${p.channel_id||'—'}`,
      `   发帖时间：${p.post_time||'—'}`
    ].join('\n');
  }).join('\n\n');
  navigator.clipboard.writeText(lines).catch(()=>{
    const t=document.createElement('textarea');
    t.value=lines;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();
  });
  btn.textContent='✓ 已复制';btn.classList.add('ok');
  setTimeout(()=>{btn.textContent='📋 一键复制全部信息';btn.classList.remove('ok');},2000);
}

function render({pages,cmps}){
  renderPostGrid(pages);

  // 一键复制按钮
  document.getElementById('postGrid').insertAdjacentHTML('afterend',
    `<button class="btn-copy-all" id="btnCopyAll" onclick="copyAllInfo(window.__pages)">
      📋 一键复制全部信息
    </button>`);
  window.__pages = pages;

  document.getElementById('cmpList').innerHTML=cmps.map(c=>{
    const v=c.pct,p1=pages[c.i],p2=pages[c.j];
    const c1=C[c.i%C.length],c2=C[c.j%C.length];

    // 统一显示格式：序号. 昵称（没有昵称就显示"未知用户"）
    const name1 = p1.author || '未知用户';
    const name2 = p2.author || '未知用户';
    const label1 = `${c.i+1}. ${name1}`;
    const label2 = `${c.j+1}. ${name2}`;

    const imgRow=pg=>{
      const imgs=(pg.images||[]).slice(0,8);
      if(!imgs.length)return '';
      return `<div class="col-imgs">${imgs.map(im=>imgTag(im.url,78,58)).join('')}</div>`;
    };

    const dupHtml=(c.dup_imgs||[]).length?`
      <div class="dup-section">
        <div style="font-size:.72rem;font-weight:700;color:var(--red);margin-bottom:6px">🖼 重复图片</div>
        <div class="dup-imgs">${c.dup_imgs.map(im=>`<div class="dw">
          ${imgTag(im.url,90,68)}
          <div class="dl">重复</div></div>`).join('')}</div>
      </div>`:'' ;

    return `<div class="cmp-card">
      <div class="cmp-head">
        <div class="cmp-names">
          <span style="color:${c1}">${x(label1)}</span>
          <span style="color:var(--muted);margin:0 7px">vs</span>
          <span style="color:${c2}">${x(label2)}</span>
        </div>
        <div class="rpill ${riskCls(v)}">${riskLabel(v)}</div>
        <div class="big-pct" style="color:${pctColor(v)}">${v}%</div>
      </div>

      <div class="hl-legend">
        高亮说明：
        <span style="background:#fef9c3;color:#854d0e">淡黄 = 轻度相似</span>
        <span style="background:#fef08a;color:#713f12">黄 = 中度</span>
        <span style="background:#fed7aa;color:#9a3412">橙 = 高度</span>
        <span style="background:#fecaca;color:#991b1b;font-weight:700">红 = 几乎一字不差</span>
      </div>

      <div class="sb">
        <div class="col">
          <div class="col-h" style="background:${c1}0f;border-bottom:1px solid ${c1}28">
            <span style="color:${c1}">${x(label1)}</span>
            <span class="origin-tag" style="margin-left:6px">原帖·最早</span>
            <span class="col-time">${x(p1.post_time||'')}</span>
          </div>
          <div class="col-b">${renderSents(p1.sentences,c.ss1)}</div>
          ${imgRow(p1)}
        </div>
        <div class="col">
          <div class="col-h" style="background:${c2}0f;border-bottom:1px solid ${c2}28">
            <span style="color:${c2}">${x(label2)}</span>
            <span class="col-time">${x(p2.post_time||'')}</span>
          </div>
          <div class="col-b">${renderSents(p2.sentences,c.ss2)}</div>
          ${imgRow(p2)}
        </div>
      </div>
      ${dupHtml}
    </div>`;
  }).join('');

  document.getElementById('results').style.display='block';
  document.getElementById('results').scrollIntoView({behavior:'smooth',block:'start'});
}

document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.ctrlKey)go();});

/* ── 批量粘贴 ── */
function toggleBatch(){
  const box=document.getElementById('batchBox');
  const btn=document.getElementById('btnBatch');
  const on=box.style.display==='none';
  box.style.display=on?'block':'none';
  btn.classList.toggle('on',on);
  if(on) document.getElementById('batchTA').focus();
}

function extractLinks(text){
  // 提取 URL：http/https，或纯数字 ID（10位以上）
  const urls=[];
  const seen=new Set();
  // 先提取所有 http/https 链接
  const re=/https?:\/\/[^\s\n\r\t，。、；;""''「」【】()（）<>]+/g;
  let m;
  while((m=re.exec(text))!==null){
    let u=m[0].replace(/[.,;:!?)）]+$/,''); // 去掉末尾标点
    if(!seen.has(u)){seen.add(u);urls.push(u);}
  }
  // 再找纯数字 ID（至少10位，不在 URL 里）
  const textClean=text.replace(/https?:\/\/[^\s]+/g,'');
  const re2=/\b(\d{10,})\b/g;
  while((m=re2.exec(textClean))!==null){
    if(!seen.has(m[1])){seen.add(m[1]);urls.push(m[1]);}
  }
  return urls.slice(0,6);
}

function batchPreview(){
  const txt=document.getElementById('batchTA').value;
  const links=extractLinks(txt);
  const pa=document.getElementById('batchPreviewArea');
  const bl=document.getElementById('batchLinks');
  if(!links.length){pa.style.display='none';return;}
  pa.style.display='block';
  bl.innerHTML=links.map((u,i)=>`
    <div class="batch-link-row">
      <div class="batch-link-num" style="background:${C[i%C.length]}">${i+1}</div>
      <span class="batch-link-val">${x(u)}</span>
    </div>`).join('');
}

function batchConfirm(){
  const txt=document.getElementById('batchTA').value;
  const links=extractLinks(txt);
  if(!links.length){alert('未识别到链接');return;}

  // 重建输入行
  const list=document.getElementById('postList');
  list.innerHTML='';
  rc=0;
  links.forEach((u,i)=>{
    const c=C[i%C.length];
    const row=document.createElement('div'); row.className='post-row';
    const del=i>=2?`<button class="btn-del" onclick="delRow(this)">✕</button>`:'';
    row.innerHTML=`<div class="idx" style="background:${c}">${i+1}</div>
      <input class="pi" autocomplete="off" value="${x(u)}">${del}`;
    list.appendChild(row);
    rc++;
  });

  // 隐藏批量面板
  document.getElementById('batchBox').style.display='none';
  document.getElementById('batchTA').value='';
  document.getElementById('batchPreviewArea').style.display='none';
  document.getElementById('btnBatch').classList.remove('on');
  document.getElementById('btnAdd').style.display=rc>=6?'none':'';

  // 直接开始查重
  go();
}
</script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', sys.argv[1] if len(sys.argv) > 1 else 5099))
    print(f"\n{'='*52}")
    print(f"  Orbit 查重 v6  →  http://localhost:{port}")
    print(f"  密码: {APP_PASSWORD}")
    print(f"  Ctrl+C 退出")
    print(f"{'='*52}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
