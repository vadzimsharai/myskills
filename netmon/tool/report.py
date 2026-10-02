#!/usr/bin/env python3
import argparse
import html
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

REPORTS_SUBDIR = "reports"
MANIFEST_NAME = "manifest.json"
REPORT_RETENTION_DAYS = 30
MANIFEST_LIMIT = 200

STATUS_BY_SEVERITY = {
    "high": ("alarm", "Alert"),
    "medium": ("warn", "Warning"),
    "low": ("ok", "Normal"),
    "info": ("ok", "Normal"),
}
ALERT_SEVERITY = {1: "high", 2: "medium"}
ALERT_ROWS = 12
SEVERITY_LABEL = {"high": "high", "medium": "medium", "low": "low"}
FINDING_TYPE_LABEL = {
    "outbound_alert": "IDS, outbound",
    "inbound_alert": "IDS, inbound",
    "suspicious_binary_path": "suspicious path",
    "new_binary": "new process",
    "new_destination": "new destination",
    "unusual_port": "unusual port",
    "large_upload": "large upload",
}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5d6472;--line:#e3e6eb;--accent:#3b6fd8;
--ok:#1f8a4c;--ok-bg:#e3f4ea;--warn:#a86400;--warn-bg:#fdf0da;--alarm:#c62e2e;--alarm-bg:#fbe3e3;
--bar:#3b6fd8;--bar2:#8aa9ea;--alert:#c62e2e;--edge:rgba(59,111,216,.28)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e7e9ee;--muted:#9aa2b1;--line:#262b35;
--accent:#7aa2f7;--ok:#5fcf8d;--ok-bg:#15291e;--warn:#f0b454;--warn-bg:#2d2312;--alarm:#f07575;--alarm-bg:#321616;
--bar:#7aa2f7;--bar2:#3d5a99;--alert:#f07575;--edge:rgba(122,162,247,.30)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
.sub{color:var(--muted);font-size:13px}a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-top:16px;overflow-x:auto}
.head{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.pill{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:600;font-size:13px}
.pill.ok{color:var(--ok);background:var(--ok-bg)}.pill.warn{color:var(--warn);background:var(--warn-bg)}
.pill.alarm{color:var(--alarm);background:var(--alarm-bg)}
.sev{display:inline-block;padding:1px 8px;border-radius:6px;font-size:12px;font-weight:600}
.sev.high{color:var(--alarm);background:var(--alarm-bg)}.sev.medium{color:var(--warn);background:var(--warn-bg)}
.sev.low{color:var(--muted);background:var(--bg)}.sev.trusted{color:var(--ok);background:var(--ok-bg)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
td.num,th.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}code.ip{word-break:normal;white-space:nowrap}.cc{white-space:nowrap;font-weight:600}
.subline{color:var(--muted);font-size:12px;margin-top:2px}.note{font-size:13px}
h2.section{font-size:20px;margin:32px 0 0}.headline{font-size:18px;font-weight:600;margin:16px 0 0}.ok-line{color:var(--ok);font-weight:600;margin:0}
ul.checks,ol.recs{margin:0;padding-left:0;list-style:none}ul.checks li,ol.recs li{padding:6px 0;border-bottom:1px solid var(--line)}
ul.checks li:last-child,ol.recs li:last-child{border-bottom:0}ul.checks li::before{content:"✓ ";color:var(--ok);font-weight:700}
ol.recs{counter-reset:rec}ol.recs li{counter-increment:rec}ol.recs li::before{content:counter(rec) ". ";color:var(--accent);font-weight:700}
code.cmd{display:inline-block;margin-top:4px;padding:3px 8px;border-radius:6px;background:var(--bg);border:1px solid var(--line)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:760px){.grid2{grid-template-columns:1fr}}
svg text{fill:var(--muted);font-size:11px}svg .lbl{fill:var(--ink);font-size:12px}
.empty{color:var(--muted);font-style:italic}
"""


def esc(value):
    return html.escape("" if value is None else str(value))


def fmt_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def fmt_time(ts, with_date=True):
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return dt.strftime("%d.%m %H:%M UTC" if with_date else "%H:%M")


def fmt_window(seconds):
    return f"{seconds // 3600} h" if seconds >= 3600 else f"{seconds // 60} min"


def ip_cell(ip, infos):
    info = infos.get(ip or "") or {}
    country = f'<span class="cc">{esc(info.get("flag") or "")} {esc(info.get("cc") or "")}</span>' if info.get("cc") else ""
    details = [f"AS{info['asn']} {info['org']}" if info.get("asn") else "", info.get("kind") or ""]
    details += [f"PTR {info['ptr']}"] if info.get("ptr") else []
    details += [f"domains: {', '.join(info['domains'][:3])}"] if info.get("domains") else []
    sub = " · ".join(d for d in details if d)
    return (f'<code class="ip">{esc(ip)}</code> {country}' + (f'<div class="subline">{esc(sub)}</div>' if sub else ""))


def dest_cells(dests, dest_ips, infos):
    names = [d for d in (dests or "").split(",") if d]
    ips = [d for d in (dest_ips or "").split(",") if d]
    cells = [ip_cell(ip, infos) for ip in ips if ip in names or ip in infos]
    cells += [esc(n) for n in names if n not in ips]
    return "<br>".join(cells)


def note_cell(notes, key):
    note = notes.get(key)
    return f'<span class="note">{esc(note)}</span>' if note else '<span class="empty">—</span>'


EXPOSURE_LABELS = {
    "secret": ("high", "looks like a secret"),
    "differs": ("medium", "distinct content"),
    "fallback": ("trusted", "site fallback"),
    "gone": ("low", "no longer HTTP 200"),
    "error": ("low", "not checked"),
}


def exposure_cell(counts):
    if not counts:
        return '<span class="empty">—</span>'
    return " ".join(f'<span class="sev {EXPOSURE_LABELS.get(v, ("low", v))[0]}">'
                    f'{esc(EXPOSURE_LABELS.get(v, ("low", v))[1])} ×{n}</span>' for v, n in sorted(counts.items()))


def table(headers, rows, numeric=()):
    if not rows:
        return '<p class="empty">No data</p>'
    head = "".join(f'<th class="num">{esc(h)}</th>' if i in numeric else f"<th>{esc(h)}</th>"
                   for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="num">{c}</td>' if i in numeric else f"<td>{c}</td>"
                         for i, c in enumerate(row)) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def timeline_svg(timeline, key, title, color_var, fmt):
    points = timeline["points"]
    width, height, pad_l, pad_b, pad_t = 1000, 170, 56, 24, 18
    plot_w = width - pad_l - 30
    peak = max((p[key] for p in points), default=0) or 1
    bar_w = plot_w / max(len(points), 1)
    bars, alerts = [], []
    for i, p in enumerate(points):
        h = (height - pad_b - pad_t) * p[key] / peak
        x = pad_l + i * bar_w
        bars.append(f'<rect x="{x + 1:.1f}" y="{height - pad_b - h:.1f}" width="{max(bar_w - 2, 1):.1f}" '
                    f'height="{h:.1f}" rx="2" fill="var({color_var})"><title>{esc(fmt(p[key]))}</title></rect>')
        if p.get("alerts"):
            alerts.append(f'<circle cx="{x + bar_w / 2:.1f}" cy="{pad_t - 8}" r="4" fill="var(--alert)">'
                          f'<title>outbound alerts: {p["alerts"]}</title></circle>')
    ticks = []
    step = max(1, len(points) // 6)
    for i in range(0, len(points), step):
        ts = timeline["start"] + i * timeline["bucket_seconds"]
        ticks.append(f'<text x="{pad_l + i * bar_w:.1f}" y="{height - 6}">{fmt_time(ts, with_date=False)}</text>')
    axis = (f'<text x="0" y="{pad_t + 10}">{esc(fmt(peak))}</text><text x="0" y="{height - pad_b}">0</text>'
            f'<line x1="{pad_l}" y1="{height - pad_b}" x2="{width}" y2="{height - pad_b}" stroke="var(--line)"/>')
    return (f'<h2>{esc(title)}</h2><svg viewBox="0 0 {width} {height}" width="100%" role="img" '
            f'aria-label="{esc(title)}">{axis}{"".join(bars)}{"".join(alerts)}{"".join(ticks)}</svg>')


def hbar_svg(rows, label_key, value_key, fmt):
    if not rows:
        return '<p class="empty">No data</p>'
    width, row_h, label_w = 520, 26, 230
    peak = max(r[value_key] or 0 for r in rows) or 1
    parts = []
    for i, r in enumerate(rows):
        y = i * row_h
        w = (width - label_w - 70) * (r[value_key] or 0) / peak
        label = str(r[label_key])
        short = label if len(label) <= 34 else "…" + label[-33:]
        parts.append(
            f'<text class="lbl" x="0" y="{y + 17}"><title>{esc(label)}</title>{esc(short)}</text>'
            f'<rect x="{label_w}" y="{y + 5}" width="{max(w, 2):.1f}" height="16" rx="3" fill="var(--bar)"/>'
            f'<text x="{label_w + w + 6:.1f}" y="{y + 17}">{esc(fmt(r[value_key]))}</text>')
    return (f'<svg viewBox="0 0 {width} {len(rows) * row_h}" width="100%" role="img">'
            f'{"".join(parts)}</svg>')


def flow_svg(graph):
    processes, dests, edges = graph["processes"], graph["destinations"], graph["edges"]
    if not edges:
        return '<p class="empty">No connections</p>'
    width, row_h, node_w = 1000, 34, 250
    height = max(len(processes), len(dests)) * row_h + 10
    left = {p["key"]: (i * row_h + 20) for i, p in enumerate(processes)}
    right = {d["key"]: (i * row_h + 20) for i, d in enumerate(dests)}
    peak = max(e["n"] for e in edges)
    x1, x2 = node_w + 10, width - node_w - 10
    paths = []
    for e in edges:
        y1, y2 = left[e["from"]], right[e["to"]]
        stroke = 1.5 + 10 * math.sqrt(e["n"] / peak)
        mid = (x1 + x2) / 2
        paths.append(f'<path d="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}" fill="none" stroke="var(--edge)" '
                     f'stroke-width="{stroke:.1f}"><title>{esc(e["from"])} → {esc(e["to"])}: {e["n"]}</title></path>')

    def node(label, n, y, anchor_right):
        short = label if len(label) <= 36 else label[:35] + "…"
        x = node_w if anchor_right else width - node_w
        anchor = "end" if anchor_right else "start"
        return (f'<text class="lbl" x="{x}" y="{y + 4}" text-anchor="{anchor}"><title>{esc(label)}</title>'
                f'{esc(short)} <tspan fill="var(--muted)">({n})</tspan></text>')

    nodes = [node(p["key"], p["n"], left[p["key"]], True) for p in processes]
    nodes += [node(d["key"], d["n"], right[d["key"]], False) for d in dests]
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Processes and destinations">'
            f'{"".join(paths)}{"".join(nodes)}</svg>')


def attention_table(items):
    rows = [(f'<span class="sev {esc(i.get("level"))}">{esc(i.get("level"))}</span>', esc(i.get("what")),
             f"<code>{esc(i.get('who'))}</code>", esc(i.get("where")), esc(i.get("action"))) for i in items]
    if not rows:
        return '<p class="ok-line">✓ Nothing needs attention</p>'
    return table(["Severity", "What", "Who", "Where", "Action"], rows)


def bullet_list(items, marker_class):
    if not items:
        return '<p class="empty">—</p>'
    return f'<ul class="{marker_class}">' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"


def recommendation_list(items):
    if not items:
        return '<p class="empty">—</p>'
    parts = []
    for item in items:
        command = f'<br><code class="cmd">{esc(item["command"])}</code>' if item.get("command") else ""
        parts.append(f"<li>{esc(item.get('what'))}{command}</li>")
    return "<ol class=\"recs\">" + "".join(parts) + "</ol>"


def finding_details(f):
    d = f.get("details", {})
    keys = ("binary", "container", "destination", "peer_name", "peer", "port", "count", "args")
    return "<br>".join(f"<code>{esc(k)}: {esc(d[k])}</code>" for k in keys if d.get(k) not in (None, ""))


def ssh_section(ssh):
    if not ssh:
        return ""
    infos = ssh.get("ip_info", {})
    notes = ssh.get("notes", {})
    t = ssh["totals"]
    kpis = "".join(f'<div class="kpi"><b>{esc(v)}</b><span>{esc(l)}</span></div>' for v, l in (
        (t.get("failures") or 0, "failed SSH attempts"), (t.get("ips") or 0, "IPs guessing passwords"),
        (t.get("bans") or 0, "fail2ban bans")))
    trust = lambda ok: '<span class="sev trusted">trusted</span>' if ok else '<span class="sev high">unfamiliar</span>'
    logins = [(f"<code>{esc(r['user'])}</code>", ip_cell(r["ip"], infos), trust(r["trusted"]),
               esc(r["method"]), r["n"], esc(fmt_time(r["last"]))) for r in ssh["logins"]]
    protected = [(f"<code>{esc(r['user'])}</code>", ip_cell(r["ip"], infos), trust(r["trusted"]),
                  r["failures"], esc(r["ban"] or "—"), note_cell(notes, r["ip"])) for r in ssh["protected"]]
    brute = [(ip_cell(r["ip"], infos), r["failures"], r["users"], esc(r["sample_users"]),
              esc(r["ban"] or "—"), note_cell(notes, r["ip"])) for r in ssh["bruteforce"]]
    return f"""<h2 class="section">SSH</h2><div class="kpis">{kpis}</div>
<section class="card"><h2>Successful logins</h2>{table(["User", "IP", "", "Method", "Count", "Latest"], logins, numeric=(4,))}</section>
<section class="card"><h2>Attempts against protected users</h2>{table(["User", "IP", "", "Attempts", "Ban", "Explanation"], protected, numeric=(3,))}</section>
<section class="card"><h2>Password guessing sources</h2>{table(["IP", "Attempts", "Usernames", "Example usernames", "Ban", "Explanation"], brute, numeric=(1, 2))}</section>"""


def render(kind, data, verdict, findings):
    severity = verdict.get("severity", "info")
    status_class, status_label = STATUS_BY_SEVERITY.get(severity, STATUS_BY_SEVERITY["info"])
    t = data["totals"]
    window = data["window_seconds"]
    title = "netmon alert" if kind == "alert" else "netmon summary"
    kpis = [
        (t.get("tcp_connects") or 0, "Process TCP connections"),
        (t.get("outbound_flows") or 0, "outbound flows"),
        (fmt_bytes(t.get("bytes_out")), "sent"),
        (fmt_bytes(t.get("bytes_in")), "received"),
        (t.get("outbound_alerts") or 0, "outbound IDS alerts"),
        (t.get("inbound_alerts") or 0, "inbound IDS alerts"),
    ]
    kpi_html = "".join(f'<div class="kpi"><b>{esc(v)}</b><span>{esc(l)}</span></div>' for v, l in kpis)

    findings_rows = [(f'<span class="sev {esc(f["severity"])}">{esc(SEVERITY_LABEL.get(f["severity"], f["severity"]))}</span>',
                      esc(FINDING_TYPE_LABEL.get(f["type"], f["type"])), esc(f["summary"]), finding_details(f))
                     for f in findings]
    process_rows = [(f"<code>{esc(r['binary'])}</code>", esc(r["container"]), r["connects"], r["dests"])
                    for r in data["top_processes"]]
    upload_rows = [dict(r, dest=r["dest"]) for r in data["top_upload_destinations"][:12]]
    new_dest_rows = []
    for r in data["new_destinations"]:
        binary, dest = r["key"].split("|", 1)
        new_dest_rows.append((f"<code>{esc(binary)}</code>", esc(dest), esc(r["first_seen"]), r["hits"]))
    infos = data.get("ip_info", {})
    notes = {n["key"]: n["note"] for n in verdict.get("notes", []) if n.get("key")}
    out_alert_rows = [(f'<span class="sev {ALERT_SEVERITY.get(r["severity"], "low")}">{ALERT_SEVERITY.get(r["severity"], "low")}</span>',
                       esc(r["signature"]), dest_cells(r["dests"], r.get("dest_ips"), infos),
                       esc(r.get("processes") or "—"), r["hits"], note_cell(notes, r["signature"]))
                      for r in data["outbound_alerts"][:ALERT_ROWS]]
    in_alert_rows = [(esc(r["signature"]), r["hits"], r["sources"]) for r in data["inbound_alerts"]]
    direct_rows = [(f"<code>{esc(r['binary'])}</code>", esc(r["container"]), ip_cell(r["daddr"], infos) + f" :{r['dport']}",
                    r["n"], note_cell(notes, r["daddr"])) for r in data["direct_ip_connects"]]
    web_rows = [(ip_cell(r["ip"], infos), r["probes"], esc(", ".join((r["host_list"] or "").split(",")[:3])),
                 esc(", ".join((r["uris"] or "").split(",")[:4])[:140]), exposure_cell(r.get("exposure")),
                 esc(r.get("ban") or "—"), note_cell(notes, r["ip"])) for r in data.get("web_probes", [])]
    exposure = data.get("web_exposure", [])
    exposed_rows = [(exposure_cell({r["verdict"]: 1}), esc(r["host"]), f"<code>{esc(r['uri'])}</code>",
                     esc(r["markers"] or "—"), f"{esc(r['status'] or '—')} · {fmt_bytes(r['bytes'])} · {esc(r['content_type'])}",
                     esc(r["ips"])) for r in exposure if r["verdict"] in ("secret", "differs", "error")]
    fallback_count = sum(r["verdict"] == "fallback" for r in exposure)
    exposure_html = (table(["Verdict", "Site", "Path", "Markers", "Response", "Scanners"], exposed_rows)
                     if exposed_rows else '<p class="ok-line">✓ No sensitive path returned its own content</p>')
    exposure_html += f'<p class="sub">Paths returning HTTP 200 checked: {len(exposure)}, site fallbacks: {fallback_count}.</p>' if exposure else ""
    source_rows = [(ip_cell(r["ip"], infos), r["hits"], esc(", ".join((r["sample"] or "").split(",")[:2])[:120]),
                    esc(r.get("ban") or "—"), note_cell(notes, r["ip"])) for r in data.get("inbound_sources", [])]

    findings_card = ""
    if kind == "alert":
        findings_card = f'<section class="card"><h2>Findings</h2>{table(["Severity", "Type", "What", "Details"], findings_rows)}</section>'

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<style>{CSS}</style></head><body><main>
<div class="head"><div><h1>{esc(title)}</h1>
<div class="sub">Window {fmt_window(window)} · through {fmt_time(data["generated_at"])} · host {esc(data.get("host_ip") or "")}
 · <a href="../index.html">all reports</a> · <a href="../architecture.html">how it works</a></div></div>
<span class="pill {status_class}">{esc(status_label)}</span></div>
<p class="headline">{esc(verdict.get("headline"))}</p>
<section class="card"><h2>Needs attention</h2>{attention_table(verdict.get("attention", []))}</section>
<div class="grid2">
<section class="card"><h2>Expected</h2>{bullet_list(verdict.get("benign", []), "checks")}</section>
<section class="card"><h2>Recommendations</h2>{recommendation_list(verdict.get("recommendations", []))}</section>
</div>
{ssh_section(dict(data.get("ssh") or {}, ip_info=infos, notes=notes) if data.get("ssh") else None)}
<h2 class="section">Web scanners</h2>
<section class="card"><p class="sub">Requests for sensitive paths (.env, .git, phpinfo, .htpasswd…) in web access logs.</p>{table(["IP", "Requests", "Sites", "Paths", "HTTP 200 check", "Ban", "Explanation"], web_rows, numeric=(1,))}</section>
<section class="card"><h2>What the server actually returned</h2><p class="sub">netmon requests each sensitive path that returned HTTP 200 to a scanner and compares it with the homepage and a missing page; it stores markers, not response bodies.</p>{exposure_html}</section>
<h2 class="section">Network</h2>
{findings_card}
<div class="kpis">{kpi_html}</div>
<section class="card">{timeline_svg(data["timeline"], "bytes_out", "Outbound volume (red dots mark outbound alerts)", "--bar", fmt_bytes)}</section>
<section class="card">{timeline_svg(data["timeline"], "connects", "Process TCP connections", "--bar2", str)}</section>
<section class="card"><h2>Traffic destinations</h2>{flow_svg(data["flow_graph"])}</section>
<section class="card"><h2>Outbound IDS alerts</h2>{table(["Severity", "Signature", "Destination", "Process", "Count", "Explanation"], out_alert_rows, numeric=(4,))}</section>
<div class="grid2">
<section class="card"><h2>Largest outbound transfers</h2>{hbar_svg(upload_rows, "dest", "bytes_out", fmt_bytes)}</section>
<section class="card"><h2>Inbound scanner noise</h2>{table(["Signature", "Count", "Sources"], in_alert_rows, numeric=(1, 2))}</section>
</div>
<h2 class="section">Processes and destinations</h2>
<section class="card"><h2>Processes</h2>{table(["Binary", "Container", "Connections", "Destinations"], process_rows, numeric=(2, 3))}</section>
<section class="card"><h2>New destinations</h2>{table(["Process", "Destination", "First seen", "Count"], new_dest_rows, numeric=(3,))}</section>
<section class="card"><h2>IP connections without DNS</h2>{table(["Process", "Container", "Address", "Count", "Explanation"], direct_rows, numeric=(3,))}</section>
<section class="card"><h2>Inbound sources (scanners)</h2>{table(["IP", "Alerts", "Signatures", "Ban", "Explanation"], source_rows, numeric=(1,))}</section>
</main></body></html>"""


def render_index(manifest):
    rows = []
    for m in manifest:
        status_class, status_label = STATUS_BY_SEVERITY.get(m["severity"], STATUS_BY_SEVERITY["info"])
        kind = "Alert" if m["kind"] == "alert" else "Summary"
        rows.append((esc(fmt_time(m["ts"])), esc(kind), f'<span class="pill {status_class}">{esc(status_label)}</span>',
                     f'<a href="{esc(m["path"])}">{esc(m["brief"][:160])}</a>'))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>netmon</title>
<style>{CSS}</style></head><body><main>
<h1>netmon — host traffic</h1><div class="sub">Alerts and daily summaries · <a href="architecture.html">how it works</a></div>
<section class="card">{table(["When", "Type", "Status", "Summary"], rows)}</section>
</main></body></html>"""


def publish(share_dir, kind, data, verdict, findings):
    reports_dir = Path(share_dir) / REPORTS_SUBDIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    name = f"{datetime.fromtimestamp(now, timezone.utc):%Y%m%d-%H%M%S}-{kind}.html"
    (reports_dir / name).write_text(render(kind, data, verdict, findings), encoding="utf-8")
    manifest_path = Path(share_dir) / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    cutoff = now - REPORT_RETENTION_DAYS * 86400
    for m in manifest:
        if m["ts"] < cutoff:
            (Path(share_dir) / m["path"]).unlink(missing_ok=True)
    manifest = [m for m in manifest if m["ts"] >= cutoff]
    manifest.insert(0, {"ts": now, "kind": kind, "severity": verdict.get("severity", "info"),
                        "brief": verdict.get("headline", ""), "path": f"{REPORTS_SUBDIR}/{name}"})
    manifest = manifest[:MANIFEST_LIMIT]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (Path(share_dir) / "index.html").write_text(render_index(manifest), encoding="utf-8")
    for path in [reports_dir / name, manifest_path, Path(share_dir) / "index.html"]:
        os.chmod(path, 0o644)
    return f"{REPORTS_SUBDIR}/{name}"


def main():
    parser = argparse.ArgumentParser(description="Render a netmon HTML report into the share folder")
    parser.add_argument("--kind", choices=["alert", "daily"], required=True)
    parser.add_argument("--data", required=True, help="netmon.py report-data JSON")
    parser.add_argument("--verdict", required=True, help="Claude verdict JSON")
    parser.add_argument("--findings", help="netmon.py check JSON")
    args = parser.parse_args()
    share_dir = os.environ.get("NETMON_SHARE_DIR_IN_CONTAINER") or os.environ.get("NETMON_SHARE_DIR")
    share_url = os.environ.get("NETMON_SHARE_URL", "").rstrip("/")
    if not share_dir or not share_url:
        return
    data = json.loads(Path(args.data).read_text())
    verdict = json.loads(Path(args.verdict).read_text())
    findings = json.loads(Path(args.findings).read_text())["findings"] if args.findings else []
    print(f"{share_url}/{publish(share_dir, args.kind, data, verdict, findings)}")


if __name__ == "__main__":
    main()
