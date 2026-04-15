from __future__ import annotations

import html
import os
import time
from pathlib import Path

from ezsk.config import Config
from ezsk.paths import default_dashboard_path
from ezsk.state import load_state

DASHBOARD_PATH = str(default_dashboard_path())


def _fmt_timestamp(iso: str) -> str:
    if not iso:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %-d, %-I:%M %p")
    except (ValueError, TypeError):
        return iso


def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def generate_dashboard(config: Config, *, active: bool = False) -> str:
    state = load_state(config.state_file)
    queued = _read_lines(os.path.join(config.output_dir, "queued.txt"))
    dead = _read_lines(os.path.join(config.output_dir, "dead.txt"))
    completed = sum(1 for e in state.values() if e.get("status") == "completed")
    failed = sum(1 for e in state.values() if e.get("status") == "failed")
    in_progress = sum(1 for e in state.values() if e.get("status") in ("searching", "downloading"))
    active = active or in_progress > 0
    total = len(state) + len(queued) + len(dead)
    # Build state table rows
    entries = sorted(
        state.values(),
        key=lambda e: e.get("last_attempt", ""),
        reverse=True,
    )
    table_rows = []
    for e in entries:
        status = e.get("status", "unknown")
        badge_cls = {
            "completed": "badge-ok",
            "failed": "badge-fail",
            "searching": "badge-search",
            "downloading": "badge-dl",
        }.get(status, "badge-other")
        attempts = e.get('attempts', 0)
        last_attempt = e.get('last_attempt', '')
        progress_html = ""
        if status == "downloading" and e.get("total_size"):
            pct = min(100, int(e["progress"] / e["total_size"] * 100))
            progress_html = f"<div class='progress'><div class='progress-bar' style='width:{pct}%'></div></div><span class='progress-pct'>{pct}%</span>"
        table_rows.append(
            f"<tr class='state-row' data-status='{html.escape(status)}'>"
            f"<td>{html.escape(e.get('query', ''))}</td>"
            f"<td><span class='badge {badge_cls}'>{html.escape(status)}</span>{progress_html}</td>"
            f"<td data-sort-value='{attempts}'>{attempts}</td>"
            f"<td data-sort-value='{html.escape(last_attempt)}'>{html.escape(_fmt_timestamp(last_attempt))}</td>"
            f"<td>{html.escape(e.get('error', ''))}</td>"
            f"</tr>"
        )

    for q in queued:
        table_rows.append(
            f"<tr class='state-row' data-status='queued'>"
            f"<td>{html.escape(q)}</td>"
            f"<td><span class='badge badge-queued'>queued</span></td>"
            f"<td data-sort-value='0'></td><td data-sort-value=''></td><td></td>"
            f"</tr>"
        )
    for d in dead:
        table_rows.append(
            f"<tr class='state-row' data-status='dead'>"
            f"<td>{html.escape(d)}</td>"
            f"<td><span class='badge badge-dead'>dead</span></td>"
            f"<td data-sort-value='0'></td><td data-sort-value=''></td><td></td>"
            f"</tr>"
        )

    timestamp = time.strftime("%b %-d, %Y at %-I:%M %p")

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ezsk status</title>
<style>
:root {{
  --bg: #fff; --fg: #222; --card-bg: #f5f5f5; --border: #ddd;
  --ok: #2e7d32; --fail: #c62828; --search: #1565c0; --dl: #6a1b9a;
  --other: #555; --badge-fg: #fff;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #1a1a1a; --fg: #e0e0e0; --card-bg: #2a2a2a; --border: #444;
    --ok: #66bb6a; --fail: #ef5350; --search: #42a5f5; --dl: #ab47bc;
    --other: #999; --badge-fg: #1a1a1a;
  }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: system-ui, sans-serif; background: var(--bg); color: var(--fg); padding: 2rem; max-width: 1100px; margin: auto; }}
h1 {{ margin-bottom: 1rem; font-size: 1.5rem; }}
.cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
.card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.5rem; min-width: 120px; text-align: center; cursor: pointer; user-select: none; transition: border-color 0.15s, box-shadow 0.15s; }}
.card:hover {{ border-color: var(--fg); }}
.card.active {{ border-color: var(--fg); box-shadow: 0 0 0 2px var(--fg); }}
.card .num {{ font-size: 2rem; font-weight: 700; }}
.card .label {{ font-size: 0.85rem; color: var(--other); }}
input[type=text] {{ width: 100%; padding: 0.5rem; margin-bottom: 1rem; border: 1px solid var(--border); border-radius: 4px; background: var(--card-bg); color: var(--fg); font-size: 0.95rem; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; }}
th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
th {{ font-weight: 600; cursor: pointer; user-select: none; }}
th::after {{ content: ''; margin-left: 4px; font-size: 0.75rem; }}
th.sort-asc::after {{ content: ' ▲'; }}
th.sort-desc::after {{ content: ' ▼'; }}
.badge {{ padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; color: var(--badge-fg); cursor: pointer; }}
.badge-ok {{ background: var(--ok); }}
.badge-fail {{ background: var(--fail); }}
.badge-search {{ background: var(--search); }}
.badge-dl {{ background: var(--dl); }}
.badge-other {{ background: var(--other); }}
.badge-queued {{ background: var(--search); }}
.badge-dead {{ background: var(--fail); }}
.progress {{ height: 6px; background: var(--border); border-radius: 3px; margin-top: 6px; overflow: hidden; }}
.progress-bar {{ height: 100%; background: var(--dl); transition: width 0.3s; }}
.progress-pct {{ font-size: 0.75rem; color: var(--other); margin-left: 4px; }}
.ts {{ font-size: 0.8rem; color: var(--other); margin-top: 1rem; }}
</style>
</head>
<body>
<h1>ezsk status <span style="font-size:0.9rem;font-weight:400;color:var(--other)">{completed} / {total}</span></h1>
<div style="font-size:0.85rem;color:var(--other);margin:-0.5rem 0 1rem;font-family:monospace">{html.escape(config.output_dir)}</div>
<div class="cards">
  <div class="card" data-status="completed"><div class="num">{completed}</div><div class="label">Completed</div></div>
  <div class="card" data-status="failed"><div class="num">{failed}</div><div class="label">Failed</div></div>
  <div class="card" data-status="in_progress"><div class="num">{in_progress}</div><div class="label">In Progress</div></div>
  <div class="card" data-status="queued"><div class="num">{len(queued)}</div><div class="label">Queued</div></div>
  <div class="card" data-status="dead"><div class="num">{len(dead)}</div><div class="label">Dead</div></div>
</div>
<input type="text" id="filter" placeholder="Filter by query..." autocomplete="off">
<table>
<thead><tr><th>Query</th><th>Status</th><th>Attempts</th><th>Last Attempt</th><th>Error</th></tr></thead>
<tbody>
{"".join(table_rows) if table_rows else "<tr><td colspan='5'>No data yet</td></tr>"}
</tbody>
</table>
<div class="ts">Generated {html.escape(timestamp)}</div>
<script>
(function() {{
  function readHash() {{
    var h = location.hash.slice(1);
    var params = {{}};
    h.split('&').forEach(function(p) {{ var kv = p.split('='); if (kv[0]) params[kv[0]] = decodeURIComponent(kv[1] || ''); }});
    return params;
  }}
  function writeHash() {{
    var parts = [];
    if (activeStatus !== 'all') parts.push('status=' + encodeURIComponent(activeStatus));
    if (filterText) parts.push('q=' + encodeURIComponent(filterText));
    if (sortCol >= 0) parts.push('sort=' + sortCol + (sortAsc ? 'a' : 'd'));
    location.hash = parts.length ? parts.join('&') : '';
  }}
  var params = readHash();
  var activeStatus = params.status || 'all';
  var filterText = params.q || '';
  function applyFilters() {{
    document.querySelectorAll('.state-row').forEach(function(row) {{
      var ds = row.getAttribute('data-status');
      var matchStatus = activeStatus === 'all' || ds === activeStatus || (activeStatus === 'in_progress' && (ds === 'searching' || ds === 'downloading'));
      var matchText = !filterText || row.textContent.toLowerCase().includes(filterText);
      row.style.display = matchStatus && matchText ? '' : 'none';
    }});
  }}
  function setStatus(status) {{
    if (activeStatus === status) {{
      activeStatus = 'all';
    }} else {{
      activeStatus = status;
    }}
    document.querySelectorAll('.card[data-status]').forEach(function(c) {{
      c.classList.toggle('active', c.getAttribute('data-status') === activeStatus);
    }});
    applyFilters();
    writeHash();
  }}
  document.querySelectorAll('.card[data-status]').forEach(function(card) {{
    card.addEventListener('click', function() {{ setStatus(card.getAttribute('data-status')); }});
  }});
  document.querySelectorAll('.badge').forEach(function(badge) {{
    badge.addEventListener('click', function() {{ setStatus(badge.textContent.trim()); }});
  }});
  var filterInput = document.getElementById('filter');
  filterInput.value = filterText;
  filterInput.addEventListener('input', function() {{
    filterText = this.value.toLowerCase();
    applyFilters();
    writeHash();
  }});
  var sortCol = -1;
  var sortAsc = true;
  if (params.sort) {{ sortCol = parseInt(params.sort); sortAsc = params.sort.slice(-1) !== 'd'; }}
  var headers = document.querySelectorAll('th');
  function doSort(idx) {{
    headers.forEach(function(h) {{ h.classList.remove('sort-asc', 'sort-desc'); }});
    headers[idx].classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
    var tbody = document.querySelector('tbody');
    var rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort(function(a, b) {{
      var av = a.cells[idx].dataset.sortValue !== undefined ? a.cells[idx].dataset.sortValue : a.cells[idx].textContent.trim();
      var bv = b.cells[idx].dataset.sortValue !== undefined ? b.cells[idx].dataset.sortValue : b.cells[idx].textContent.trim();
      if (idx === 2) {{ av = Number(av) || 0; bv = Number(bv) || 0; return sortAsc ? av - bv : bv - av; }}
      return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    }});
    rows.forEach(function(row) {{ tbody.appendChild(row); }});
  }}
  headers.forEach(function(th, idx) {{
    th.addEventListener('click', function() {{
      if (sortCol === idx) {{ sortAsc = !sortAsc; }} else {{ sortCol = idx; sortAsc = true; }}
      doSort(idx);
      writeHash();
    }});
  }});
  // Restore card highlight and apply saved state
  if (activeStatus !== 'all') {{
    document.querySelectorAll('.card[data-status]').forEach(function(c) {{
      c.classList.toggle('active', c.getAttribute('data-status') === activeStatus);
    }});
  }}
  applyFilters();
  if (sortCol >= 0) doSort(sortCol);
  {"setTimeout(function() { location.reload(); }, 5000);" if active else ""}
}})();
</script>
</body>
</html>"""

    Path(DASHBOARD_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_PATH, "w") as f:
        f.write(page)
    return DASHBOARD_PATH
