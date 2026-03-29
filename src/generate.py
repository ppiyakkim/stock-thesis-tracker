"""
generate.py
Reads stocks.yaml → fetches OHLCV from Yahoo Finance → writes:
  data/<TICKER>.json   full price history (always MAX_PRE + MAX_POST days)
  index.html           main dashboard (Notion embed target)
  manage.html          stock / thesis management UI

Key design: JSON always stores the MAXIMUM window (365 days each side).
The frontend sliders filter client-side — no re-fetch needed when slider changes.
"""

import json
import math
from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

MAX_PRE  = 365   # max days before report stored in JSON
MAX_POST = 365   # max days after  report stored in JSON


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch(ticker: str, report_date: datetime) -> pd.DataFrame:
    """Fetch MAX_PRE days before and MAX_POST days after report_date."""
    # Extra 60-day MA warmup buffer so MA50 is accurate at left edge
    start = report_date - timedelta(days=MAX_PRE + 60)
    end   = max(
        report_date + timedelta(days=MAX_POST + 5),
        datetime.today() + timedelta(days=2),
    )

    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError(f"No data returned for '{ticker}'")

    df.index = pd.to_datetime(df.index)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Compute MAs on FULL history before slicing (accurate edges)
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()

    # Trim to display window only (MA warmup rows discarded here)
    left  = report_date - timedelta(days=MAX_PRE)
    right = max(
        report_date + timedelta(days=MAX_POST),
        datetime.today() + timedelta(days=1),
    )
    return df[(df.index >= left) & (df.index <= right)]


def df_to_payload(df: pd.DataFrame, report_date: datetime) -> dict:
    def safe(v):
        try:
            f = float(v)
            return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
        except (TypeError, ValueError):
            return None

    rows = [
        {
            "date": ts.strftime("%Y-%m-%d"),
            "o":    safe(row.get("Open")),
            "h":    safe(row.get("High")),
            "l":    safe(row.get("Low")),
            "c":    safe(row.get("Close")),
            "v":    int(row.get("Volume") or 0),
            "ma20": safe(row.get("MA20")),
            "ma50": safe(row.get("MA50")),
        }
        for ts, row in df.iterrows()
    ]
    return {
        "report_date": report_date.strftime("%Y-%m-%d"),
        "generated":   date.today().isoformat(),
        "max_pre":     MAX_PRE,
        "max_post":    MAX_POST,
        "rows":        rows,
    }


# ── Shared assets ─────────────────────────────────────────────────────────────

GOOGLE_FONTS = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600"
    "&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
)
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.32.0.min.js"

COMMON_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;
  transition:background .2s,color .2s}
.site-header{
  padding:13px 22px;border-bottom:1px solid var(--border);background:var(--panel);
  display:flex;align-items:center;justify-content:space-between;
}
.site-title{font-family:var(--mono);font-size:11px;font-weight:600;
  letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.nav{display:flex;gap:4px}
.nav a{font-family:var(--mono);font-size:10px;padding:4px 11px;border-radius:4px;
  color:var(--sub);border:1px solid transparent;text-decoration:none;transition:all .15s}
.nav a:hover{color:var(--text);border-color:var(--border)}
.nav a.active{color:var(--accent);border-color:var(--accent)}
.tab.tag-hidden{display:none}
.tab.month-hidden{display:none}
"""


# ── index.html ────────────────────────────────────────────────────────────────

def build_index(stocks: list) -> str:
    from collections import defaultdict
    from datetime import datetime as _dt

    # Separate active vs archived
    active   = [s for s in stocks if not s.get("archived")]
    archived = [s for s in stocks if s.get("archived")]

    stocks_json = json.dumps(active + archived, ensure_ascii=False)

    # Group active stocks by YYYY-MM, sorted newest first
    months_map = defaultdict(list)
    for i, s in enumerate(active):
        months_map[s["report_date"][:7]].append((i, s))
    sorted_months = sorted(months_map.keys(), reverse=True)

    # Build tab bar: month dropdown + per-stock ticker tabs
    options_html = "".join(
        f'<option value="{m}">{_dt.strptime(m, "%Y-%m").strftime("%B %Y")}</option>'
        for m in sorted_months
    )
    month_dropdown = f"""<select class="month-select" id="month-select" onchange="switchMonth(this.value)">{options_html}</select>"""

    tab_bar_html = month_dropdown
    first_stock = True
    for m in sorted_months:
        for i, s in months_map[m]:
            tags_str  = " ".join(s.get("tags", []))
            active_cls = " active" if first_stock else ""
            tab_bar_html += (
                f'<button class="tab{active_cls}" onclick="switchTab({i})" '
                f'id="tab-{i}" data-month="{m}" data-tags="{tags_str}">'
                f'{s["ticker"]}</button>'
            )
            first_stock = False

    # Build panels
    panels = ""
    prev_month = None
    for i, s in enumerate(active):
        month       = s["report_date"][:7]
        pre         = s.get("default_pre",  60)
        post        = s.get("default_post", 60)
        tags        = s.get("tags", [])
        tags_str    = " ".join(tags)
        tags_html   = "".join(f'<span class="tag-pill">{t}</span>' for t in tags)
        target_price = s.get("target_price", "")

        if month != prev_month:
            label = _dt.strptime(month, "%Y-%m").strftime("%B %Y")
            panels += f'<div class="month-header" data-month="{month}">{label}</div>'
            prev_month = month

        # target_price data attribute (empty string if not set)
        tp_attr = f'data-target="{target_price}"' if target_price else ''

        panels += f"""
<div class="panel {'visible' if i == 0 else 'hidden'}" id="panel-{i}" data-month="{month}" data-tags="{tags_str}" {tp_attr}>
  <div class="card-header">
    <div class="ch-left">
      <span class="ticker-badge">{s["ticker"]}</span>
      <span class="label-text">{s.get("label","")}</span>
    </div>
    <div class="meta-row">
      <span class="meta-item">Report&nbsp;<b>{s["report_date"]}</b></span>
      <span class="meta-item updated" id="updated-{i}"></span>
      <button class="embed-toggle-btn" onclick="toggleEmbed({i})" id="embedtoggle-{i}">Notion embed URL</button>
    </div>
  </div>
  {'<div class="tag-row">' + tags_html + '</div>' if tags_html else ""}
  {'<p class="thesis-text">' + s["thesis"] + '</p>' if s.get("thesis") else ""}
  <div class="price-stat-row" id="pstat-{i}"></div>
  <div class="embed-bar" id="embedbar-{i}" style="display:none">
    <span class="embed-label">Notion embed URL</span>
    <span class="embed-url" id="embedurl-{i}"></span>
    <button class="copy-btn" id="copybtn-{i}" onclick="copyEmbed({i})">Copy</button>
  </div>
  <div class="ctrl-row">
    <div class="cg">
      <label>Past days</label>
      <input type="range" id="sl-pre-{i}" min="10" max="365" step="5" value="{pre}">
      <span class="cv" id="cv-pre-{i}">{pre}d</span>
    </div>
    <div class="cg">
      <label>Future days</label>
      <input type="range" id="sl-post-{i}" min="10" max="365" step="5" value="{post}">
      <span class="cv" id="cv-post-{i}">{post}d</span>
    </div>
  </div>
  <div class="leg-row" id="leg-{i}"></div>
  <div class="chart-wrap" id="chart-{i}"></div>
  <div class="vol-wrap"   id="vol-{i}"></div>
</div>"""

    # Collect all unique tags across active stocks
    all_tags = sorted({t for s in active for t in s.get("tags", [])})
    tag_filter_html = ""
    if all_tags:
        btns = "".join(
            f'<button class="tag-filter-btn" onclick="filterTag(\'{t}\')" id="tagbtn-{t}">{t}</button>'
            for t in all_tags
        )
        tag_filter_html = f"""
<div class="tag-filter-bar">
  <span class="tag-filter-label">Filter</span>
  <button class="tag-filter-btn on" onclick="filterTag(null)" id="tagbtn-all">All</button>
  {btns}
</div>"""
    archived_html = ""
    if archived:
        archived_html = '<div class="archive-section"><div class="archive-header">Archived</div>'
        for s in archived:
            archived_html += f"""
<div class="archive-card">
  <span class="archive-ticker">{s["ticker"]}</span>
  <span class="archive-label">{s.get("label","")}</span>
  <span class="archive-date">{s["report_date"]}</span>
</div>"""
        archived_html += "</div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Stock Thesis Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{GOOGLE_FONTS}" rel="stylesheet">
<script src="{PLOTLY_CDN}"></script>
<style>
{COMMON_CSS}
.tab-bar{{display:flex;gap:2px;padding:10px 16px 0;background:var(--panel);
  border-bottom:1px solid var(--border);overflow-x:auto;flex-wrap:wrap}}
.tab{{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.08em;
  padding:6px 16px 8px;border:none;border-radius:4px 4px 0 0;background:transparent;
  color:var(--sub);cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;
  transition:color .15s,background .15s}}
.tab:hover{{color:var(--text);background:var(--panel2)}}
.tab.active{{color:var(--accent);border-bottom-color:var(--accent)}}
.tab.tag-hidden{{display:none}}
.content{{padding:16px}}
.panel.hidden{{display:none}}.panel.visible{{display:block}}
.card-header{{display:flex;justify-content:space-between;align-items:flex-start;
  flex-wrap:wrap;gap:8px;margin-bottom:10px}}
.ch-left{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}}
.ticker-badge{{font-family:var(--mono);font-size:15px;font-weight:600;color:var(--accent)}}
.label-text{{font-size:13px;color:var(--text)}}
.meta-row{{display:flex;gap:14px;flex-wrap:wrap}}
.meta-item{{font-family:var(--mono);font-size:10px;color:var(--sub);white-space:nowrap}}
.meta-item b{{color:var(--text)}}
.updated{{color:var(--blue)}}
.embed-toggle-btn{{font-family:var(--mono);font-size:10px;padding:3px 10px;
  border-radius:4px;background:transparent;color:var(--sub);
  border:1px solid var(--border);cursor:pointer;transition:all .15s}}
.embed-toggle-btn:hover{{color:var(--accent);border-color:var(--accent)}}
.embed-toggle-btn.active{{color:var(--accent);border-color:var(--accent)}}
.thesis-text{{font-size:12px;color:var(--sub);font-style:italic;margin-bottom:12px;
  padding:8px 12px;border-left:2px solid var(--border);line-height:1.7}}
.ctrl-row{{display:flex;align-items:center;gap:18px;flex-wrap:wrap;
  margin-bottom:12px;padding:9px 13px;background:var(--panel);
  border:1px solid var(--border);border-radius:6px}}
.cg{{display:flex;align-items:center;gap:8px}}
.cg label{{font-family:var(--mono);font-size:10px;color:var(--sub);white-space:nowrap}}
.cg input[type=range]{{width:120px;accent-color:var(--accent)}}
.cv{{font-family:var(--mono);font-size:11px;color:var(--accent);min-width:36px}}
.leg-row{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px}}
.leg{{display:flex;align-items:center;gap:5px;font-family:var(--mono);font-size:10px;color:var(--sub)}}
.ld{{width:14px;height:3px;border-radius:2px;flex-shrink:0}}
.chart-wrap{{border:1px solid var(--border);border-radius:6px 6px 0 0;
  overflow:hidden;background:var(--panel);height:420px}}
.vol-wrap{{border:1px solid var(--border);border-top:none;border-radius:0 0 6px 6px;
  overflow:hidden;background:var(--panel);height:90px;margin-bottom:4px}}
.loading{{display:flex;align-items:center;justify-content:center;height:100%;
  font-family:var(--mono);font-size:11px;color:var(--sub)}}
.embed-bar{{display:flex;align-items:center;gap:8px;margin-bottom:12px;
  padding:8px 12px;background:var(--panel);border:1px solid var(--border);border-radius:6px}}
.embed-label{{font-family:var(--mono);font-size:10px;color:var(--sub);white-space:nowrap;flex-shrink:0}}
.embed-url{{font-family:var(--mono);font-size:10px;color:var(--blue);
  flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:text;
  user-select:all}}
.copy-btn{{font-family:var(--mono);font-size:10px;padding:3px 10px;border-radius:4px;
  background:transparent;color:var(--sub);border:1px solid var(--border);
  cursor:pointer;white-space:nowrap;flex-shrink:0;transition:all .15s}}
.copy-btn:hover{{color:var(--text);border-color:var(--text)}}
.copy-btn.copied{{color:var(--green);border-color:var(--green)}}
.month-header{{font-family:var(--mono);font-size:11px;font-weight:600;
  letter-spacing:.1em;text-transform:uppercase;color:var(--sub);
  padding:10px 0 6px;border-bottom:1px solid var(--border);
  margin-bottom:4px;margin-top:16px}}
.month-header:first-child{{margin-top:0}}
.archive-section{{margin-top:28px;border-top:1px solid var(--border);padding-top:16px}}
.archive-header{{font-family:var(--mono);font-size:11px;font-weight:600;
  letter-spacing:.1em;text-transform:uppercase;color:var(--sub);margin-bottom:10px}}
.archive-card{{display:flex;align-items:center;gap:12px;padding:8px 12px;
  background:var(--panel);border:1px solid var(--border);border-radius:6px;
  margin-bottom:6px;opacity:0.55}}
.archive-ticker{{font-family:var(--mono);font-size:12px;font-weight:600;
  color:var(--sub);min-width:80px}}
.archive-label{{font-size:12px;color:var(--sub);flex:1}}
.archive-date{{font-family:var(--mono);font-size:10px;color:var(--sub)}}
.tag-row{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}}
.tag-pill{{font-family:var(--mono);font-size:10px;padding:2px 8px;border-radius:20px;
  background:var(--panel2);color:var(--sub);border:1px solid var(--border)}}
.tag-filter-bar{{padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.tag-filter-label{{font-family:var(--mono);font-size:10px;color:var(--sub);white-space:nowrap}}
.tag-filter-btn{{font-family:var(--mono);font-size:10px;padding:3px 10px;border-radius:20px;
  border:1px solid var(--border);background:transparent;color:var(--sub);
  cursor:pointer;transition:all .15s;white-space:nowrap}}
.tag-filter-btn:hover{{color:var(--text);border-color:var(--text)}}
.tag-filter-btn.on{{background:var(--accent);color:var(--bg);border-color:var(--accent)}}

/* ── Light / Dark mode ── */
:root{{
  --bg:#f5f4f0;--panel:#ffffff;--panel2:#f0efe9;--border:#e0ddd5;
  --text:#1a1a1a;--sub:#888880;--accent:#b8860b;--blue:#2563eb;
  --green:#16a34a;--red:#dc2626;--orange:#c2410c;
  --mono:'IBM Plex Mono',monospace;--sans:'IBM Plex Sans',sans-serif;
}}
html.dark{{
  --bg:#0d0f14;--panel:#13161e;--panel2:#1a1d27;--border:#252836;
  --text:#c8cdd8;--sub:#5a6070;--accent:#f5c842;--blue:#4fa3f7;
  --green:#00e5a0;--red:#ff4d6a;--orange:#f77f4f;
}}

/* ── Month dropdown ── */
.month-select{{font-family:var(--mono);font-size:11px;font-weight:600;
  letter-spacing:.06em;padding:5px 10px;border-radius:4px;
  border:1px solid var(--border);background:var(--panel2);color:var(--text);
  cursor:pointer;outline:none;margin-right:8px;transition:border .15s}}
.month-select:hover{{border-color:var(--accent)}}
.month-select:focus{{border-color:var(--accent)}}

/* ── Price stat row ── */
.price-stat-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px;
  font-family:var(--mono);font-size:11px}}
.pstat{{padding:6px 12px;border-radius:6px;background:var(--panel);
  border:1px solid var(--border);display:flex;flex-direction:column;gap:2px}}
.pstat-label{{font-size:9px;color:var(--sub);letter-spacing:.06em;text-transform:uppercase}}
.pstat-value{{font-size:14px;font-weight:600;color:var(--text)}}
.pstat-value.up{{color:var(--green)}}
.pstat-value.dn{{color:var(--red)}}
.pstat-value.neutral{{color:var(--sub)}}

/* ── Theme toggle ── */
.theme-toggle{{font-family:var(--mono);font-size:10px;padding:3px 10px;
  border-radius:20px;border:1px solid var(--border);background:transparent;
  color:var(--sub);cursor:pointer;transition:all .15s}}
.theme-toggle:hover{{color:var(--text);border-color:var(--text)}}

/* ── Responsive ── */
@media(max-width:640px){{
  .site-header{{padding:10px 12px}}
  .site-title{{font-size:10px}}
  .content{{padding:10px 10px}}
  .ctrl-row{{gap:10px;padding:8px 10px}}
  .cg input[type=range]{{width:80px}}
  .card-header{{gap:6px}}
  .meta-row{{gap:8px}}
  .embed-toggle-btn{{display:none}}
  .chart-wrap{{height:280px}}
  .vol-wrap{{height:70px}}
  .tab-bar{{padding:6px 10px 0;gap:1px}}
  .tab{{font-size:10px;padding:5px 10px 6px}}
  .month-tab{{font-size:9px;padding:5px 8px 6px;margin-right:2px}}
  .tag-filter-bar{{padding:8px 10px;gap:6px}}
  .fgrid{{grid-template-columns:1fr 1fr!important}}
}}
@media(max-width:400px){{
  .fgrid{{grid-template-columns:1fr!important}}
  .cg{{flex-wrap:wrap}}
}}
</style>
</head>
<body>
<header class="site-header">
  <span class="site-title">Stock Thesis Tracker</span>
  <nav class="nav" style="display:flex;align-items:center;gap:8px">
    <a href="index.html" class="active">Dashboard</a>
    <a href="manage.html">Manage</a>
    <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">☾ Dark</button>
  </nav>
</header>
<div class="tab-bar" id="tab-bar">{tab_bar_html}</div>
{tag_filter_html}
<div class="content">
{panels}
{archived_html}
</div>

<script>
const STOCKS    = {stocks_json};
const TODAY     = new Date(); TODAY.setHours(0,0,0,0);
const TODAY_STR = TODAY.toISOString().slice(0,10);

const jsonCache = {{}};
const curPre    = {{}};
const curPost   = {{}};
STOCKS.forEach((s,i) => {{
  curPre[i]  = s.default_pre  || 60;
  curPost[i] = s.default_post || 60;
}});

// ── Theme ──────────────────────────────────────────────────────────────────
function applyTheme(dark) {{
  document.documentElement.classList.toggle('dark', dark);
  document.getElementById('theme-btn').textContent = dark ? '☀ Light' : '☾ Dark';
  localStorage.setItem('thesis_theme', dark ? 'dark' : 'light');
}}
function toggleTheme() {{
  applyTheme(!document.documentElement.classList.contains('dark'));
}}
// Default: light; restore from localStorage if set
(function() {{
  const saved = localStorage.getItem('thesis_theme');
  applyTheme(saved === 'dark');
}})();

// ── Month tab switching ────────────────────────────────────────────────────
let activeMonth = null;

function switchMonth(month) {{
  activeMonth = month;
  // Sync dropdown value
  const sel = document.getElementById('month-select');
  if (sel) sel.value = month;

  // Show only tabs belonging to this month
  const allTabs = document.querySelectorAll('.tab');
  let firstVisible = -1;
  allTabs.forEach((t, idx) => {{
    const inMonth = t.dataset.month === month;
    const inTag   = !t.classList.contains('tag-hidden');
    t.classList.toggle('month-hidden', !inMonth);
    if (inMonth && inTag && firstVisible === -1) firstVisible = idx;
  }});

  // Show/hide month headers
  document.querySelectorAll('.month-header').forEach(h => {{
    h.style.display = h.dataset.month === month ? '' : 'none';
  }});

  if (firstVisible >= 0) switchTab(firstVisible);
}}

document.addEventListener('DOMContentLoaded', () => {{
  const debounceTimers = {{}};
  STOCKS.forEach((s,i) => {{
    function onSliderChange() {{
      curPre[i]  = parseInt(document.getElementById('sl-pre-' +i).value);
      curPost[i] = parseInt(document.getElementById('sl-post-'+i).value);
      document.getElementById('cv-pre-' +i).textContent = curPre[i]  + 'd';
      document.getElementById('cv-post-'+i).textContent = curPost[i] + 'd';
      clearTimeout(debounceTimers[i]);
      debounceTimers[i] = setTimeout(() => redraw(i), 120);
    }}
    const preEl  = document.getElementById('sl-pre-' +i);
    const postEl = document.getElementById('sl-post-'+i);
    if (preEl)  preEl.addEventListener('input', onSliderChange);
    if (postEl) postEl.addEventListener('input', onSliderChange);
  }});

  // ── URL param handling ─────────────────────────────────────────────────
  const params      = new URLSearchParams(location.search);
  const tickerParam = (params.get('ticker') || '').toUpperCase();
  const embedMode   = params.get('embed') === '1';

  if (embedMode) {{
    document.querySelector('.site-header').style.display = 'none';
    document.querySelector('.tab-bar').style.display     = 'none';
    const tfb = document.querySelector('.tag-filter-bar');
    if (tfb) tfb.style.display = 'none';
    document.querySelectorAll('.embed-bar').forEach(el => el.style.display = 'none');
  }}

  // Populate embed URLs
  const baseUrl = location.origin + location.pathname;
  STOCKS.forEach((s,i) => {{
    const el = document.getElementById('embedurl-'+i);
    if (el) el.textContent = `${{baseUrl}}?ticker=${{s.ticker}}&embed=1`;
  }});

  // Activate first month via dropdown
  const sel = document.getElementById('month-select');
  if (sel && sel.options.length > 0) {{
    activeMonth = sel.options[0].value;
    sel.value   = activeMonth;
    document.querySelectorAll('.tab').forEach(t => {{
      if (t.dataset.month !== activeMonth) t.classList.add('month-hidden');
    }});
    document.querySelectorAll('.month-header').forEach(h => {{
      if (h.dataset.month !== activeMonth) h.style.display = 'none';
    }});
  }}

  // Pick initial tab from URL or default
  let startTab = 0;
  if (tickerParam) {{
    const idx = STOCKS.findIndex(s => s.ticker.toUpperCase() === tickerParam);
    if (idx >= 0) startTab = idx;
  }}
  switchTab(startTab);
}});

function copyEmbed(i) {{
  const url = document.getElementById('embedurl-'+i).textContent;
  navigator.clipboard.writeText(url).then(() => {{
    const btn = document.getElementById('copybtn-'+i);
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent = 'Copy'; btn.classList.remove('copied'); }}, 1800);
  }});
}}

function toggleEmbed(i) {{
  const bar = document.getElementById('embedbar-'+i);
  const btn = document.getElementById('embedtoggle-'+i);
  const visible = bar.style.display !== 'none';
  bar.style.display = visible ? 'none' : 'flex';
  btn.classList.toggle('active', !visible);
}}

let activeTag = null;

function filterTag(tag) {{
  activeTag = tag;
  // Update filter button states
  document.querySelectorAll('.tag-filter-btn').forEach(b => b.classList.remove('on'));
  const btnId = tag ? 'tagbtn-'+tag : 'tagbtn-all';
  const btn = document.getElementById(btnId);
  if (btn) btn.classList.add('on');

  // Show/hide tabs and month headers based on tag
  const tabs    = document.querySelectorAll('.tab');
  const panels  = document.querySelectorAll('.panel');
  const headers = document.querySelectorAll('.month-header');

  // Determine which panel indices are visible
  const visibleMonths = new Set();
  panels.forEach((p, i) => {{
    const tags = (p.dataset.tags || '').split(' ').filter(Boolean);
    const show = !tag || tags.includes(tag);
    tabs[i] && tabs[i].classList.toggle('tag-hidden', !show);
    if (!show && p.classList.contains('visible')) {{
      // Current tab is hidden — find first visible tab
      const firstVisible = [...tabs].findIndex(t => !t.classList.contains('tag-hidden'));
      if (firstVisible >= 0) switchTab(firstVisible);
    }}
    if (show) visibleMonths.add(p.dataset.month);
  }});

  // Show/hide month headers
  headers.forEach(h => {{
    h.style.display = (!tag || visibleMonths.has(h.dataset.month)) ? '' : 'none';
  }});
}}

function switchTab(i) {{
  document.querySelectorAll('.tab').forEach((t,j) => t.classList.toggle('active', i===j));
  document.querySelectorAll('.panel').forEach((p,j) => {{
    p.classList.toggle('visible', i===j);
    p.classList.toggle('hidden',  i!==j);
  }});
  // Update URL param without page reload
  const params = new URLSearchParams(location.search);
  params.set('ticker', STOCKS[i].ticker);
  const newUrl = location.pathname + '?' + params.toString();
  history.replaceState(null, '', newUrl);

  if (!jsonCache[STOCKS[i].ticker]) renderChart(i);
  else redraw(i);
}}

async function renderChart(i) {{
  const s = STOCKS[i];
  document.getElementById('chart-'+i).innerHTML = '<div class="loading">Loading…</div>';
  document.getElementById('vol-'  +i).innerHTML = '';

  if (!jsonCache[s.ticker]) {{
    try {{
      const r = await fetch('data/' + s.ticker + '.json');
      if (!r.ok) throw new Error(r.status);
      jsonCache[s.ticker] = await r.json();
    }} catch(e) {{
      document.getElementById('chart-'+i).innerHTML =
        `<div class="loading" style="color:var(--red)">
          Failed to load ${{s.ticker}}.json — run generate.py first</div>`;
      return;
    }}
  }}
  redraw(i);
}}

function redraw(i) {{
  const s    = STOCKS[i];
  const json = jsonCache[s.ticker];
  if (!json) return;

  const pre  = curPre[i];
  const post = curPost[i];

  const reportDate  = new Date(json.report_date); reportDate.setHours(0,0,0,0);
  const windowStart = addDays(reportDate, -pre);
  const windowEnd   = addDays(reportDate,  post);
  const showToday   = TODAY <= windowEnd;

  // ── TRADING DAYS ONLY ───────────────────────────────────────────────────
  // Build date list from actual JSON rows (only real trading days).
  // For future blank space, add synthetic labels up to windowEnd.
  const tradingDays = new Set(json.rows.map(r => r.date));

  // Past + present: actual trading days within window
  const pastDates = json.rows
    .map(r => r.date)
    .filter(dt => dt >= fmtD(windowStart) && dt <= TODAY_STR && dt <= fmtD(windowEnd));

  // Ensure report_date's nearest trading day is always in the list
  // (it may be just outside the window edge due to rounding)
  const rdStr = json.report_date;
  const rdInList = pastDates.includes(rdStr) ||
    json.rows.some(r => pastDates.includes(r.date) && Math.abs(
      (new Date(r.date) - new Date(rdStr)) / 86400000) <= 5);
  if (!rdInList) {{
    // Find the closest trading day to report_date in the JSON and prepend it
    const rd = new Date(rdStr); rd.setHours(0,0,0,0);
    for (let offset = 0; offset <= 5; offset++) {{
      const next = fmtD(addDays(rd,  offset));
      const prev = fmtD(addDays(rd, -offset));
      const rowNext = json.rows.find(r => r.date === next);
      const rowPrev = json.rows.find(r => r.date === prev);
      if (rowNext && !pastDates.includes(next)) {{ pastDates.unshift(next); break; }}
      if (rowPrev && !pastDates.includes(prev)) {{ pastDates.unshift(prev); break; }}
    }}
  }}

  // Future: synthetic weekday labels from tomorrow to windowEnd (for blank space)
  const futureDates = [];
  if (showToday) {{
    const cursor = addDays(TODAY, 1);
    while (cursor <= windowEnd) {{
      const d = cursor.getDay();
      if (d !== 0 && d !== 6) futureDates.push(fmtD(cursor)); // skip Sat/Sun
      cursor.setDate(cursor.getDate() + 1);
    }}
  }}

  const allDates   = [...pastDates, ...futureDates];
  if (!allDates.length) return;

  const rowMap = {{}};
  json.rows.forEach(r => rowMap[r.date] = r);

  const candleRows = allDates.map(dt => {{
    const r = rowMap[dt];
    return (r && dt <= TODAY_STR) ? r : null;
  }});
  const bull = candleRows.map(r => r ? r.c >= r.o : true);

  const ma20 = allDates.map(dt => {{
    const r = rowMap[dt];
    return (r && r.ma20 != null) ? r.ma20 : null;
  }});
  const ma50 = allDates.map(dt => {{
    const r = rowMap[dt];
    return (r && r.ma50 != null) ? r.ma50 : null;
  }});

  // Dynamic Y range
  const realRows  = candleRows.filter(Boolean);
  if (!realRows.length) return;
  const maVals    = [...ma20, ...ma50].filter(v => v != null);
  const allPrices = [...realRows.flatMap(r => [r.h, r.l]), ...maVals];
  const priceMin  = Math.min(...allPrices);
  const priceMax  = Math.max(...allPrices);
  const pad       = (priceMax - priceMin) * 0.06;
  const yMin      = +(priceMin - pad).toFixed(2);
  const yMax      = +(priceMax + pad).toFixed(2);

  // Vertical line positions — use category index (required for category xaxis)
  // report_date may fall on a weekend/holiday → find nearest trading day in allDates
  let riIdx = allDates.indexOf(json.report_date);
  if (riIdx === -1) {{
    // Try next trading day first, then previous
    const rd = new Date(json.report_date); rd.setHours(0,0,0,0);
    for (let offset = 1; offset <= 5; offset++) {{
      const next = fmtD(addDays(rd,  offset));
      const prev = fmtD(addDays(rd, -offset));
      if (allDates.indexOf(next) >= 0) {{ riIdx = allDates.indexOf(next); break; }}
      if (allDates.indexOf(prev) >= 0) {{ riIdx = allDates.indexOf(prev); break; }}
    }}
  }}
  const tiIdx = showToday
    ? allDates.reduce((best,dt,j) => dt <= TODAY_STR ? j : best, -1)
    : -1;

  // Legend
  document.getElementById('leg-'+i).innerHTML = `
    <span class="leg"><span class="ld" style="background:#00e5a0"></span>Bull</span>
    <span class="leg"><span class="ld" style="background:#ff4d6a"></span>Bear</span>
    <span class="leg"><span class="ld" style="background:#4fa3f7"></span>MA 20</span>
    <span class="leg"><span class="ld" style="background:#f77f4f"></span>MA 50</span>
    <span class="leg"><span class="ld" style="background:#f5c842"></span>Report date</span>
    ${{showToday
      ? '<span class="leg"><span class="ld" style="background:#4fa3f7;opacity:.5"></span>Today</span>'
      : ''}}`;

  // category xaxis = no weekend/holiday gaps
  const isDark   = document.documentElement.classList.contains('dark');
  const gridC    = isDark ? '#1e2230'   : '#e8e6e0';
  const tickC    = isDark ? '#5a6070'   : '#888880';
  const panelBg  = isDark ? '#13161e'   : '#ffffff';
  const panelBg2 = isDark ? '#1a1d27'   : '#f5f4f0';
  const textC    = isDark ? '#c8cdd8'   : '#1a1a1a';
  const reportC  = isDark ? '#f5c842'   : '#b8860b';
  const todayC   = isDark ? '#4fa3f7'   : '#2563eb';
  const bullC    = isDark ? '#00e5a0'   : '#16a34a';
  const bearC    = isDark ? '#ff4d6a'   : '#dc2626';
  const bullF    = isDark ? 'rgba(0,229,160,0.22)'  : 'rgba(22,163,74,0.22)';
  const bearF    = isDark ? 'rgba(255,77,106,0.22)' : 'rgba(220,38,38,0.22)';
  const ma20C    = isDark ? '#4fa3f7'   : '#2563eb';
  const ma50C    = isDark ? '#f77f4f'   : '#c2410c';

  const axisStyle = {{
    type:'category',
    gridcolor:gridC, zerolinecolor:gridC, linecolor:gridC,
    tickfont:{{ size:10, color:tickC, family:"'IBM Plex Mono',monospace" }},
    tickangle: 0,
    nticks: 10,
  }};

  // Shapes/annotations use category index numbers for category xaxis
  const shapes=[], annotations=[];

  if (riIdx >= 0) {{
    shapes.push({{ type:'line',
      x0:riIdx, x1:riIdx, y0:0, y1:1, yref:'paper', xref:'x',
      line:{{ color:reportC, width:1.8, dash:'dash' }} }});
    annotations.push({{ x:riIdx, y:1, yref:'paper', xref:'x',
      text:'Report', showarrow:false, xanchor:'left', yanchor:'top',
      font:{{ color:reportC, size:10, family:"'IBM Plex Mono',monospace" }},
      bgcolor: isDark?'rgba(13,15,20,.85)':'rgba(245,244,240,.9)',
      bordercolor:reportC, borderwidth:1 }});
  }}

  if (showToday && tiIdx >= 0) {{
    shapes.push({{ type:'line',
      x0:tiIdx, x1:tiIdx, y0:0, y1:1, yref:'paper', xref:'x',
      line:{{ color:todayC, width:1.2, dash:'dot' }} }});
    annotations.push({{ x:tiIdx, y:1, yref:'paper', xref:'x',
      text:'Today', showarrow:false, xanchor:'right', yanchor:'top',
      font:{{ color:todayC, size:10, family:"'IBM Plex Mono',monospace" }},
      bgcolor: isDark?'rgba(13,15,20,.85)':'rgba(245,244,240,.9)',
      bordercolor:todayC, borderwidth:1 }});
  }}

  const layout = {{
    paper_bgcolor:panelBg, plot_bgcolor:panelBg,
    font:{{ color:textC, family:"'IBM Plex Mono',monospace" }},
    xaxis:{{ ...axisStyle, rangeslider:{{ visible:false }} }},
    yaxis:{{ gridcolor:gridC, zerolinecolor:gridC, linecolor:gridC,
      tickfont:{{ size:10, color:tickC, family:"'IBM Plex Mono',monospace" }},
      side:'right', range:[yMin,yMax],
      title:{{ text:'Price', font:{{ size:10, color:tickC }} }} }},
    shapes, annotations, showlegend:false,
    margin:{{ l:10, r:65, t:16, b:40 }},
    hovermode:'x unified',
    hoverlabel:{{ bgcolor:panelBg2, bordercolor:gridC,
      font:{{ color:textC, size:11, family:"'IBM Plex Mono',monospace" }} }},
  }};

  const volAxisStyle = {{
    type:'category',
    gridcolor:gridC, zerolinecolor:gridC, linecolor:gridC,
    tickfont:{{ size:10, color:tickC, family:"'IBM Plex Mono',monospace" }},
  }};

  const volLayout = {{
    paper_bgcolor:panelBg, plot_bgcolor:panelBg,
    font:{{ color:textC, family:"'IBM Plex Mono',monospace" }},
    xaxis:{{ ...volAxisStyle, rangeslider:{{ visible:false }} }},
    yaxis:{{ gridcolor:gridC, zerolinecolor:gridC, linecolor:gridC,
      tickfont:{{ size:10, color:tickC, family:"'IBM Plex Mono',monospace" }},
      side:'right', tickformat:'.2s',
      title:{{ text:'Vol', font:{{ size:10, color:tickC }} }} }},
    shapes: shapes.map(sh => ({{...sh}})),
    showlegend:false, margin:{{ l:10, r:65, t:4, b:40 }},
    hovermode:'x unified',
    hoverlabel:{{ bgcolor:panelBg2, bordercolor:gridC,
      font:{{ color:textC, size:11 }} }},
  }};

  const cfg = {{ responsive:true, displayModeBar:false }};

  document.getElementById('chart-'+i).innerHTML = '';
  document.getElementById('vol-'  +i).innerHTML = '';

  Plotly.newPlot('chart-'+i,
    [{{ type:'candlestick', x:allDates,
       open:  candleRows.map(r => r?.o ?? null),
       high:  candleRows.map(r => r?.h ?? null),
       low:   candleRows.map(r => r?.l ?? null),
       close: candleRows.map(r => r?.c ?? null),
       increasing:{{ line:{{ color:bullC, width:1 }}, fillcolor:bullC }},
       decreasing:{{ line:{{ color:bearC, width:1 }}, fillcolor:bearC }},
       whiskerwidth:0.5, name:s.ticker }},
     {{ type:'scatter', x:allDates, y:ma20, mode:'lines', name:'MA 20',
       line:{{ color:ma20C, width:1.3, dash:'dot' }}, opacity:0.85, connectgaps:false }},
     {{ type:'scatter', x:allDates, y:ma50, mode:'lines', name:'MA 50',
       line:{{ color:ma50C, width:1.3, dash:'dot' }}, opacity:0.85, connectgaps:false }}],
    layout, cfg);

  Plotly.newPlot('vol-'+i,
    [{{ type:'bar', x:allDates, y:candleRows.map(r => r?.v ?? null),
       marker:{{ color:bull.map(b => b ? bullF : bearF),
                 line:{{ width:0 }} }},
       showlegend:false }}],
    volLayout, cfg);

  const upEl = document.getElementById('updated-'+i);
  if (upEl) upEl.textContent = 'Updated ' + json.generated;

  // ── Price stats ────────────────────────────────────────────────────────
  const statEl = document.getElementById('pstat-'+i);
  if (!statEl) return;

  const panelEl = document.getElementById('panel-'+i);
  const target  = panelEl ? parseFloat(panelEl.dataset.target) : NaN;

  // Report date price: closing price of the report date (or nearest trading day)
  const reportRow = json.rows.find(r => r.date === json.report_date)
    || json.rows.reduce((best, r) => {{
      const diff = Math.abs(new Date(r.date) - new Date(json.report_date));
      return diff < Math.abs(new Date(best.date) - new Date(json.report_date)) ? r : best;
    }}, json.rows[0]);
  const reportPrice = reportRow?.c ?? null;

  // Today price: last available row
  const todayRow   = [...json.rows].reverse().find(r => r.date <= TODAY_STR);
  const todayPrice = todayRow?.c ?? null;

  function pct(from, to) {{
    if (!from || !to) return null;
    return ((to - from) / from * 100).toFixed(1);
  }}
  function cls(v) {{ return v === null ? 'neutral' : parseFloat(v) >= 0 ? 'up' : 'dn'; }}
  function fmt(v) {{ return v === null ? '—' : (parseFloat(v) >= 0 ? '+' : '') + v + '%'; }}
  function fmtPx(v) {{ return v === null ? '—' : v.toFixed(2); }}

  let html = '';

  // Always show: report price + today price + today vs report
  const todayVsReport = pct(reportPrice, todayPrice);
  html += `
    <div class="pstat">
      <span class="pstat-label">Report date close</span>
      <span class="pstat-value neutral">${{fmtPx(reportPrice)}}</span>
    </div>
    <div class="pstat">
      <span class="pstat-label">Current price</span>
      <span class="pstat-value neutral">${{fmtPx(todayPrice)}}</span>
    </div>
    <div class="pstat">
      <span class="pstat-label">Since report</span>
      <span class="pstat-value ${{cls(todayVsReport)}}">${{fmt(todayVsReport)}}</span>
    </div>`;

  // If target price set: show upside from report + from today
  if (!isNaN(target) && target > 0) {{
    const uptReport = pct(reportPrice, target);
    const uptToday  = pct(todayPrice,  target);
    html += `
    <div class="pstat" style="border-color:var(--accent)">
      <span class="pstat-label">Target</span>
      <span class="pstat-value neutral">${{target.toFixed(2)}}</span>
    </div>
    <div class="pstat">
      <span class="pstat-label">Upside from report</span>
      <span class="pstat-value ${{cls(uptReport)}}">${{fmt(uptReport)}}</span>
    </div>
    <div class="pstat">
      <span class="pstat-label">Upside from today</span>
      <span class="pstat-value ${{cls(uptToday)}}">${{fmt(uptToday)}}</span>
    </div>`;
  }}

  statEl.innerHTML = html;
}}

function addDays(d,n){{ const r=new Date(d); r.setDate(r.getDate()+n); return r; }}
function fmtD(d){{ return d.toISOString().slice(0,10); }}
</script>
</body>
</html>"""


# ── manage.html ───────────────────────────────────────────────────────────────

def build_manage(stocks: list) -> str:
    stocks_json = json.dumps(stocks, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Manage — Stock Thesis Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{GOOGLE_FONTS}" rel="stylesheet">
<style>
{COMMON_CSS}
:root{{
  --bg:#f5f4f0;--panel:#ffffff;--panel2:#f0efe9;--border:#e0ddd5;
  --text:#1a1a1a;--sub:#888880;--accent:#b8860b;--blue:#2563eb;
  --green:#16a34a;--red:#dc2626;--orange:#c2410c;
  --mono:'IBM Plex Mono',monospace;--sans:'IBM Plex Sans',sans-serif;
}}
html.dark{{
  --bg:#0d0f14;--panel:#13161e;--panel2:#1a1d27;--border:#252836;
  --text:#c8cdd8;--sub:#5a6070;--accent:#f5c842;--blue:#4fa3f7;
  --green:#00e5a0;--red:#ff4d6a;--orange:#f77f4f;
}}
.theme-toggle{{font-family:var(--mono);font-size:10px;padding:3px 10px;
  border-radius:20px;border:1px solid var(--border);background:transparent;
  color:var(--sub);cursor:pointer;transition:all .15s}}
.theme-toggle:hover{{color:var(--text);border-color:var(--text)}}
.page-body{{max-width:820px;margin:0 auto;padding:24px 16px}}
h2{{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.1em;
  text-transform:uppercase;color:var(--accent);margin-bottom:14px}}
.info-box{{background:var(--panel);border:1px solid var(--border);border-radius:6px;
  padding:13px 15px;margin-bottom:18px;font-size:12px;color:var(--sub);line-height:1.8}}
.info-box b{{color:var(--text)}}
.stock-card{{background:var(--panel);border:1px solid var(--border);border-radius:8px;
  padding:15px;margin-bottom:12px}}
.sch{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.sticker{{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--accent)}}
.btn-g{{display:flex;gap:6px;align-items:center}}
button{{font-family:var(--mono);font-size:10px;padding:4px 11px;border-radius:4px;
  cursor:pointer;background:transparent;border:1px solid var(--border);
  color:var(--text);transition:all .15s}}
.btn-sv{{color:var(--green);border-color:var(--green)}}
.btn-sv:hover{{background:var(--green);color:var(--bg)}}
.btn-dl{{color:var(--red);border-color:var(--red)}}
.btn-dl:hover{{background:var(--red);color:#fff}}
.btn-arch{{color:var(--sub);border-color:var(--sub)}}
.btn-arch:hover{{color:var(--text);border-color:var(--text)}}
.btn-add{{color:var(--accent);border-color:var(--accent);margin-bottom:14px}}
.btn-add:hover{{background:var(--accent);color:var(--bg)}}
.btn-pub{{font-size:12px;padding:7px 18px;background:var(--blue);
  color:var(--bg);border-color:var(--blue)}}
.btn-pub:hover{{opacity:.85}}
.req{{color:var(--red);margin-left:2px}}
.archived-card{{opacity:0.55}}
.fgrid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:10px}}
.field label{{display:block;font-family:var(--mono);font-size:9px;color:var(--sub);
  margin-bottom:3px;letter-spacing:.05em;text-transform:uppercase}}
.field input,.field textarea{{width:100%;background:var(--panel2);border:1px solid var(--border);
  border-radius:4px;padding:6px 9px;color:var(--text);font-family:var(--sans);
  font-size:13px;outline:none;transition:border .15s}}
.field input:focus,.field textarea:focus{{border-color:var(--accent)}}
.field textarea{{resize:vertical;min-height:72px;line-height:1.6}}
.dispatch-box{{background:var(--panel);border:1px solid var(--border);
  border-radius:8px;padding:16px;margin-top:8px}}
.dispatch-box p{{font-size:12px;color:var(--sub);margin-bottom:12px;line-height:1.7}}
.fg2{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}}
.divider{{height:1px;background:var(--border);margin:20px 0}}
.status-msg{{font-family:var(--mono);font-size:11px;margin-top:10px;min-height:18px}}
.saved-ok{{font-family:var(--mono);font-size:10px;color:var(--green);
  margin-left:6px;opacity:0;transition:opacity .3s}}
code{{font-family:var(--mono);font-size:11px;color:var(--accent)}}
.search-wrap{{position:relative}}
.search-results{{position:absolute;top:100%;left:0;right:0;z-index:100;
  background:var(--panel2);border:1px solid var(--accent);border-top:none;
  border-radius:0 0 4px 4px;max-height:200px;overflow-y:auto}}
.search-item{{padding:8px 10px;cursor:pointer;border-bottom:1px solid var(--border);
  transition:background .1s}}
.search-item:last-child{{border-bottom:none}}
.search-item:hover{{background:var(--panel)}}
.search-item .si-ticker{{font-family:var(--mono);font-size:12px;font-weight:600;color:var(--accent)}}
.search-item .si-name{{font-size:11px;color:var(--sub);margin-left:8px}}
.search-item .si-exch{{font-size:10px;color:var(--sub);float:right;margin-top:2px}}
.search-loading{{padding:8px 10px;font-size:11px;color:var(--sub);font-family:var(--mono)}}
@media(max-width:640px){{
  .fgrid{{grid-template-columns:1fr 1fr!important}}
  .page-body{{padding:12px}}
  .site-header{{padding:10px 12px}}
}}
@media(max-width:400px){{
  .fgrid{{grid-template-columns:1fr!important}}
}}
</style>
</head>
<body>
<header class="site-header">
  <span class="site-title">Stock Thesis Tracker</span>
  <nav class="nav" style="display:flex;align-items:center;gap:8px">
    <a href="index.html">Dashboard</a>
    <a href="manage.html" class="active">Manage</a>
    <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">☾ Dark</button>
  </nav>
</header>
<script>
function applyTheme(dark){{
  document.documentElement.classList.toggle('dark',dark);
  const btn=document.getElementById('theme-btn');
  if(btn) btn.textContent=dark?'☀ Light':'☾ Dark';
  localStorage.setItem('thesis_theme',dark?'dark':'light');
}}
function toggleTheme(){{applyTheme(!document.documentElement.classList.contains('dark'));}}
(function(){{applyTheme(localStorage.getItem('thesis_theme')==='dark');}})();
</script>
<div class="page-body">

  <div class="info-box">
    <b>No login required.</b> Edit stocks and thesis below, click <b>Save</b> on each card.
    Changes stay in your browser until you publish.<br>
    Click <b>Trigger GitHub Actions rebuild</b> to commit <code>stocks.yaml</code>
    and regenerate all charts (~1–2 min).<br>
    Enter your <b>GitHub PAT</b> (Contents + Actions write) when publishing.
    Owner / repo are remembered in localStorage; the PAT is never stored.
  </div>

  <h2>Stocks &amp; Thesis</h2>
  <div id="stock-list"></div>
  <button class="btn-add" onclick="addStock()">+ Add stock</button>

  <div class="divider"></div>

  <div class="dispatch-box">
    <h2 style="margin-bottom:10px">Publish changes</h2>
    <p>
      Commits <code>stocks.yaml</code> via GitHub API and dispatches the
      <code>update-charts.yml</code> workflow.
    </p>
    <div class="fg2">
      <div class="field"><label>GitHub owner</label>
        <input id="gh-owner" placeholder="your-username"></div>
      <div class="field"><label>Repository name</label>
        <input id="gh-repo" placeholder="stock-thesis-tracker"></div>
    </div>
    <div class="field" style="margin-bottom:12px">
      <label>Personal Access Token (not stored)</label>
      <input id="gh-pat" type="password" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx">
    </div>
    <button class="btn-pub" onclick="triggerActions()">Trigger GitHub Actions rebuild</button>
    <div class="status-msg" id="dispatch-status"></div>
  </div>

</div>

<script>
const DEFAULT_STOCKS = {stocks_json};
const LS_STOCKS = 'thesis_stocks_v2';
const LS_GH     = 'thesis_gh_v1';

function load(){{
  try{{ return JSON.parse(localStorage.getItem(LS_STOCKS)) || DEFAULT_STOCKS; }}
  catch{{ return DEFAULT_STOCKS; }}
}}
function save(s){{ localStorage.setItem(LS_STOCKS, JSON.stringify(s)); }}

function render(){{
  const stocks = load();
  const list   = document.getElementById('stock-list');
  list.innerHTML = '';

  const active   = stocks.filter(s => !s.archived);
  const archived = stocks.filter(s =>  s.archived);

  // Group active by month
  const months = {{}};
  active.forEach((s,idx) => {{
    const realIdx = stocks.indexOf(s);
    const m = (s.report_date||'').slice(0,7) || 'No date';
    if (!months[m]) months[m] = [];
    months[m].push([realIdx, s]);
  }});

  // Sort months descending
  Object.keys(months).sort().reverse().forEach(m => {{
    const mLabel = m === 'No date' ? 'No date' :
      new Date(m+'-01').toLocaleDateString('en', {{year:'numeric',month:'long'}});
    const hdr = document.createElement('div');
    hdr.style.cssText='font-family:var(--font-mono,monospace);font-size:10px;font-weight:600;'
      +'letter-spacing:.1em;text-transform:uppercase;color:var(--sub);'
      +'padding:10px 0 6px;border-bottom:1px solid var(--border);margin-bottom:8px;margin-top:14px';
    hdr.textContent = mLabel;
    list.appendChild(hdr);
    months[m].forEach(([realIdx, s]) => list.appendChild(makeCard(s, realIdx)));
  }});

  // Archived section
  if (archived.length) {{
    const ahdr = document.createElement('div');
    ahdr.style.cssText='font-family:var(--font-mono,monospace);font-size:10px;font-weight:600;'
      +'letter-spacing:.1em;text-transform:uppercase;color:var(--sub);'
      +'padding:10px 0 6px;border-top:1px solid var(--border);'
      +'border-bottom:1px solid var(--border);margin:20px 0 8px';
    ahdr.textContent = 'Archived';
    list.appendChild(ahdr);
    archived.forEach(s => {{
      const realIdx = stocks.indexOf(s);
      list.appendChild(makeCard(s, realIdx));
    }});
  }}

  const gh = JSON.parse(localStorage.getItem(LS_GH)||'{{}}');
  if (gh.owner) document.getElementById('gh-owner').value = gh.owner;
  if (gh.repo)  document.getElementById('gh-repo').value  = gh.repo;
}}

function makeCard(s,i){{
  const div = document.createElement('div');
  div.className='stock-card'+(s.archived?' archived-card':''); div.id='card-'+i;
  div.innerHTML=`
    <div class="sch">
      <span class="sticker" id="mt-${{i}}">${{s.ticker}}${{s.archived?' <span style="font-size:9px;color:var(--sub);font-weight:400">ARCHIVED</span>':''}}</span>
      <div class="btn-g">
        <button class="btn-sv" onclick="saveCard(${{i}})">Save</button>
        <span class="saved-ok" id="sok-${{i}}">✓ saved</span>
        <button class="btn-arch" onclick="archiveCard(${{i}})">${{s.archived?'Unarchive':'Archive'}}</button>
        <button class="btn-dl" onclick="deleteCard(${{i}})">Delete</button>
      </div>
    </div>
    <div class="fgrid" style="grid-template-columns:repeat(5,minmax(0,1fr))">
      <div class="field search-wrap">
        <label>Ticker <span class="req">*</span></label>
        <input id="f-ticker-${{i}}" value="${{s.ticker||''}}" autocomplete="off"
          placeholder="e.g. AAPL, 005930.KS"
          oninput="onTickerInput(${{i}})">
        <div class="search-results" id="sr-${{i}}" style="display:none"></div>
      </div>
      <div class="field"><label>Report date <span class="req">*</span></label>
        <input id="f-date-${{i}}" type="date" value="${{s.report_date||''}}"></div>
      <div class="field"><label>Past days (default)</label>
        <input id="f-pre-${{i}}" type="number" min="10" max="365" value="${{s.default_pre||60}}"></div>
      <div class="field"><label>Future days (default)</label>
        <input id="f-post-${{i}}" type="number" min="10" max="365" value="${{s.default_post||60}}"></div>
      <div class="field"><label>Target price <span style="color:var(--sub);font-weight:400">(optional)</span></label>
        <input id="f-tp-${{i}}" type="number" step="0.01" min="0" placeholder="e.g. 250.00" value="${{s.target_price||''}}"></div>
    </div>
    <div class="field" style="margin-bottom:10px"><label>Label</label>
      <input id="f-label-${{i}}" value="${{s.label||''}}"></div>
    <div class="field" style="margin-bottom:10px">
      <label>Tags <span style="color:var(--sub);font-weight:400">(comma separated, e.g. growth, korea, defense)</span></label>
      <input id="f-tags-${{i}}" value="${{(s.tags||[]).join(', ')}}" placeholder="growth, korea, defense"></div>
    <div class="field"><label>Thesis</label>
      <textarea id="f-thesis-${{i}}">${{s.thesis||''}}</textarea></div>`;
  return div;
}}

function saveCard(i){{
  const ticker = document.getElementById('f-ticker-'+i).value.toUpperCase().trim();
  const date   = document.getElementById('f-date-'+i).value;
  if (!ticker) {{ alert('Ticker is required.'); return; }}
  if (!date)   {{ alert('Report date is required.'); return; }}
  const stocks=load();
  stocks[i]={{
    ...stocks[i],
    ticker,
    report_date:  date,
    label:        document.getElementById('f-label-'+i).value.trim(),
    tags:         document.getElementById('f-tags-'+i).value
                    .split(',').map(t=>t.trim().toLowerCase()).filter(Boolean),
    thesis:       document.getElementById('f-thesis-'+i).value.trim(),
    default_pre:  parseInt(document.getElementById('f-pre-' +i).value)||60,
    default_post: parseInt(document.getElementById('f-post-'+i).value)||60,
    target_price: parseFloat(document.getElementById('f-tp-'+i).value)||null,
  }};
  save(stocks);
  document.getElementById('mt-'+i).textContent=ticker;
  const b=document.getElementById('sok-'+i);
  b.style.opacity='1'; setTimeout(()=>b.style.opacity='0',1400);
}}

function archiveCard(i){{
  const stocks=load();
  stocks[i].archived = !stocks[i].archived;
  save(stocks); render();
}}

function deleteCard(i){{
  if(!confirm('Delete this stock?'))return;
  const stocks=load(); stocks.splice(i,1); save(stocks); render();
}}

function addStock(){{
  const stocks=load();
  stocks.push({{ticker:'NEW',report_date:'',label:'',thesis:'',default_pre:60,default_post:60}});
  save(stocks); render();
  setTimeout(()=>document.getElementById('card-'+(stocks.length-1))
    .scrollIntoView({{behavior:'smooth'}}),50);
}}

async function triggerActions(){{
  const owner  = document.getElementById('gh-owner').value.trim();
  const repo   = document.getElementById('gh-repo').value.trim();
  const pat    = document.getElementById('gh-pat').value.trim();
  const status = document.getElementById('dispatch-status');

  if(!owner||!repo||!pat){{
    status.style.color='var(--red)';
    status.textContent='Fill in owner, repo, and PAT.'; return;
  }}
  localStorage.setItem(LS_GH,JSON.stringify({{owner,repo}}));

  const headers={{
    'Authorization':'Bearer '+pat,
    'Content-Type':'application/json',
    'Accept':'application/vnd.github+json',
  }};
  const fileUrl=`https://api.github.com/repos/${{owner}}/${{repo}}/contents/stocks.yaml`;

  status.style.color='#5a6070'; status.textContent='Committing stocks.yaml…';

  let sha='';
  try{{
    const r=await fetch(fileUrl,{{headers}});
    if(r.ok) sha=(await r.json()).sha;
  }}catch{{}}

  try{{
    const body={{
      message:'chore: update stocks.yaml via manage UI',
      content:btoa(unescape(encodeURIComponent(buildYaml(load())))),
      ...(sha?{{sha}}:{{}}),
    }};
    const r=await fetch(fileUrl,{{method:'PUT',headers,body:JSON.stringify(body)}});
    if(!r.ok) throw new Error((await r.json()).message);
  }}catch(e){{
    status.style.color='var(--red)';
    status.textContent='Commit failed: '+e.message; return;
  }}

  status.textContent='Committed. Triggering workflow…';

  try{{
    const r=await fetch(
      `https://api.github.com/repos/${{owner}}/${{repo}}/actions/workflows/update-charts.yml/dispatches`,
      {{method:'POST',headers,body:JSON.stringify({{ref:'main'}})}}
    );
    if(r.status===204){{
      status.style.color='var(--green)';
      status.textContent='GitHub Actions triggered — charts rebuild in ~1–2 min.';
    }}else{{
      throw new Error((await r.json()).message);
    }}
  }}catch(e){{
    status.style.color='var(--red)';
    status.textContent='Dispatch failed: '+e.message;
  }}
}}

function buildYaml(stocks){{
  const esc=str=>(str||'').replace(/"/g,'\\\\"').replace(/\\n/g,' ');
  let out='# stocks.yaml — managed via /manage\\nstocks:\\n';
  stocks.forEach(s=>{{
    out+=`  - ticker: ${{s.ticker}}\\n`;
    out+=`    report_date: "${{s.report_date}}"\\n`;
    out+=`    label: "${{esc(s.label)}}"\\n`;
    out+=`    thesis: "${{esc(s.thesis)}}"\\n`;
    out+=`    default_pre: ${{s.default_pre||60}}\\n`;
    out+=`    default_post: ${{s.default_post||60}}\\n`;
    if (s.target_price) out+=`    target_price: ${{s.target_price}}\\n`;
    if (s.tags && s.tags.length) out+=`    tags: [${{s.tags.map(t=>'"'+t+'"').join(', ')}}]\\n`;
    if (s.archived) out+=`    archived: true\\n`;
  }});
  return out;
}}

// ── Ticker search: local search-index.json (서버사이드 빌드, CORS 없음) ────────
const searchTimers = {{}};
let searchIndex = null;

// 첫 검색 시 한 번만 로드
async function loadIndex() {{
  if (searchIndex !== null) return searchIndex;
  try {{
    const r = await fetch('data/search-index.json');
    searchIndex = r.ok ? await r.json() : [];
  }} catch(e) {{ searchIndex = []; }}
  return searchIndex;
}}

function onTickerInput(i) {{
  const q  = document.getElementById('f-ticker-'+i).value.trim();
  const sr = document.getElementById('sr-'+i);
  clearTimeout(searchTimers[i]);
  if (q.length < 1) {{ sr.style.display='none'; return; }}
  searchTimers[i] = setTimeout(() => doSearch(i, q), 200);
}}

async function doSearch(i, q) {{
  const sr = document.getElementById('sr-'+i);
  sr.style.display='block';
  sr.innerHTML='<div class="search-loading">Searching…</div>';

  const idx = await loadIndex();
  if (!idx.length) {{ sr.innerHTML = manualEntry(i, q); return; }}

  const ql = q.toLowerCase();
  // Match: ticker starts-with first, then name contains
  const byTicker = idx.filter(r => r.s.toLowerCase().startsWith(ql));
  const byName   = idx.filter(r => !r.s.toLowerCase().startsWith(ql)
    && r.n.toLowerCase().includes(ql));
  const results  = [...byTicker, ...byName].slice(0, 10);

  if (!results.length) {{ sr.innerHTML = manualEntry(i, q); return; }}

  sr.innerHTML = results.map(r => {{
    const sym  = (r.s || '').replace(/'/g, "\\'");
    const name = (r.n || '').replace(/'/g, "\\'").slice(0, 45);
    const exch =  r.e || '';
    return `<div class="search-item" onclick="selectTicker(${{i}},'${{sym}}','${{name}}','${{exch}}')">
      <span class="si-ticker">${{sym}}</span>
      <span class="si-name">${{name}}</span>
      <span class="si-exch">${{exch}}</span>
    </div>`;
  }}).join('');
}}

function manualEntry(i, q) {{
  const sym = q.toUpperCase();
  return `<div class="search-item" onclick="selectTicker(${{i}},'${{sym}}','','')">
    <span class="si-ticker">${{sym}}</span>
    <span class="si-name" style="color:var(--sub)">직접 입력: "${{sym}}"</span>
  </div>`;
}}

function selectTicker(i, symbol, name, exch) {{
  document.getElementById('f-ticker-'+i).value = symbol;
  if (name && !document.getElementById('f-label-'+i).value)
    document.getElementById('f-label-'+i).value = name;
  document.getElementById('sr-'+i).style.display = 'none';
  document.getElementById('mt-'+i).textContent   = symbol;
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'Enter' && e.target.id && e.target.id.startsWith('f-ticker-')) {{
    const i = e.target.id.split('-')[2];
    const val = e.target.value.trim().toUpperCase();
    if (val) selectTicker(i, val, '', '');
  }}
}});

document.addEventListener('click', e => {{
  if (!e.target.closest('.search-wrap'))
    document.querySelectorAll('.search-results').forEach(el => el.style.display='none');
}});

render();
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    cfg_path = ROOT / "stocks.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    stocks = config["stocks"]
    print(f"Fetching {len(stocks)} stock(s)  [window: -{MAX_PRE}d / +{MAX_POST}d]\n")

    for s in stocks:
        if s.get("archived"):
            print(f"  ⏭  {s['ticker']} (archived, skipping)")
            continue
        ticker      = s["ticker"]
        report_date = datetime.strptime(s["report_date"], "%Y-%m-%d")
        print(f"  → {ticker}  (report: {s['report_date']})")
        try:
            df      = fetch(ticker, report_date)
            payload = df_to_payload(df, report_date)
            out     = DATA / f"{ticker}.json"
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
            print(f"     ✓  {out.name}  ({len(payload['rows'])} rows)")
        except Exception as e:
            print(f"     ⚠  Skipped: {e}")

    # Remove JSON files for tickers no longer active (deleted or archived)
    active = {s["ticker"] for s in stocks if not s.get("archived")}
    for f in DATA.iterdir():
        if f.suffix == ".json" and f.stem not in active and f.stem != "search-index":
            f.unlink()
            print(f"  🗑  Removed stale {f.name}")

    # Build search index
    print("\nBuilding search index…")
    build_search_index()

    plain = [dict(s) for s in stocks]
    (ROOT / "index.html").write_text(build_index(plain),  encoding="utf-8")
    (ROOT / "manage.html").write_text(build_manage(plain), encoding="utf-8")
    print(f"\n✅  index.html + manage.html written")
    print(f"    data/ → {[f.name for f in DATA.iterdir() if f.suffix == '.json']}")


def build_search_index():
    """
    Download ticker lists from public NASDAQ/NYSE/AMEX APIs and
    write data/search-index.json  [{symbol, name, exchange}, ...]
    Falls back gracefully if any source fails.
    """
    import urllib.request, csv, io

    results = []
    seen    = set()

    # NASDAQ public API — returns CSV with Symbol, Name, Market Cap, etc.
    sources = [
        ("NASDAQ", "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange=NASDAQ&download=true"),
        ("NYSE",   "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange=NYSE&download=true"),
        ("AMEX",   "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange=AMEX&download=true"),
    ]

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept":     "application/json",
    }

    for exch, url in sources:
        try:
            req  = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            rows = data.get("data", {}).get("rows", [])
            for r in rows:
                sym  = (r.get("symbol") or "").strip()
                name = (r.get("name")   or "").strip()
                if sym and sym not in seen:
                    seen.add(sym)
                    results.append({"s": sym, "n": name, "e": exch})
            print(f"     ✓  {exch}: {len(rows)} tickers")
        except Exception as ex:
            print(f"     ⚠  {exch} failed: {ex}")

    if not results:
        print("     ⚠  Search index empty — all sources failed")
        return

    out = DATA / "search-index.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, separators=(",", ":"))
    print(f"     ✓  search-index.json written ({len(results)} tickers)")


if __name__ == "__main__":
    main()
