#!/usr/bin/env python3
import html
import ipaddress
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import netmon

API_URL = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT_SECONDS = 50
RETRY_DELAY_SECONDS = 10
UNBAN_IMAGE = "alpine:3"
OFFSET_PATH = netmon.DATA_DIR / "bot-offset"
COMMANDS = [
    ("check", "Quick check for the past hour"),
    ("report", "Full 24-hour report with Claude analysis"),
    ("bans", "Active bans"),
    ("trusted", "Trusted IPs"),
    ("trust", "Trust IP for 7 days: /trust <ip>"),
    ("untrust", "Remove temporary trusted IP: /untrust <ip>"),
    ("ban", "Ban IP or subnet for 4 weeks: /ban <ip|cidr>"),
    ("unban", "Lift a ban: /unban <ip|cidr>"),
]
CHECK_WINDOW = "1h"
CHECK_FINDINGS_SHOWN = 10
REPORT_TIMEOUT_SECONDS = 600
report_running = threading.Lock()
HELP_TEXT = ("Commands:\n" + "\n".join(f"/{name} — {html.escape(desc)}" for name, desc in COMMANDS) +
             "\n\nButtons under an SSH alert: “This is me” trusts an IP for one week (renewed on each login); “Ban” bans it.")


def api(method, **params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode(
        {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}).encode()
    request = urllib.request.Request(API_URL.format(token=token, method=method), data=data)
    with urllib.request.urlopen(request, timeout=POLL_TIMEOUT_SECONDS + 10) as response:
        return json.loads(response.read())["result"]


def say(text):
    api("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], text=text, parse_mode="HTML",
        disable_web_page_preview="true")


def fmt_until(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m %H:%M UTC")


def unban_markup(ip):
    return {"inline_keyboard": [[{"text": "↩️ Unban", "callback_data": f"unban:{ip}"}]]}


def statically_trusted(ip):
    address = ipaddress.ip_address(ip)
    return any(address in n for n in netmon.load_static_trusted_networks())


def ban(target):
    if "/" in target:
        ok = netmon.ban_subnet(target, "telegram")
        api("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], parse_mode="HTML",
            text=f"⛔ Subnet <code>{target}</code> {'banned for 4 weeks' if ok else 'ban failed'}.",
            reply_markup=unban_markup(target) if ok else None)
        return
    ip = target
    with netmon.DynamicTrust() as trust:
        trust.entries.pop(ip, None)
    if statically_trusted(ip):
        say(f"⚠️ <code>{ip}</code> is permanently trusted (trusted-ips.txt); fail2ban will not ban it. "
            "Remove it from the file and retry.")
        return
    netmon.request_ban(ip, "telegram")
    api("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], parse_mode="HTML",
        text=f"⛔ <code>{ip}</code> banned for 4 weeks on all ports (netmon-ban).",
        reply_markup=unban_markup(ip))


def unban(target):
    try:
        ok = netmon.unban_subnet(target) if "/" in target else unban_on_host(target)
    except (OSError, KeyError, ValueError):
        ok = False
    say(f"↩️ <code>{target}</code>: {'ban lifted' if ok else 'could not lift the ban or no ban existed'}")


def unban_on_host(ip):
    return netmon.run_on_host(["fail2ban-client", "unban", ip]) == 0


def parse_public_ip(value):
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return str(address) if address.is_global else None


def handle_action(callback, value):
    action_id, _, index = value.partition(":")
    with netmon.JsonStore(netmon.PENDING_ACTIONS_PATH) as pending:
        entry = pending.pop(action_id, None)
    if not entry or not index.isdigit() or int(index) >= len(entry["options"]):
        api("answerCallbackQuery", callback_query_id=callback["id"], text="Question already resolved")
        return
    option = entry["options"][int(index)]
    api("answerCallbackQuery", callback_query_id=callback["id"], text="Working…")
    if option["kind"] == "ban_subnet" and netmon.parse_ban_target(option["cidr"]):
        ban(netmon.parse_ban_target(option["cidr"]))
    elif option["kind"] == "ban_ips":
        ips = [ip for ip in map(parse_public_ip, option["ips"]) if ip]
        for ip in ips:
            netmon.request_ban(ip, "telegram-question")
        say("⛔ Banned for 4 weeks: " + ", ".join(f"<code>{ip}</code>" for ip in ips))
    else:
        say("🙈 OK, ignoring")


def handle_callback(callback):
    action, _, value = callback["data"].partition(":")
    message = callback.get("message") or {}
    if action == "act":
        if message:
            api("editMessageReplyMarkup", chat_id=message["chat"]["id"], message_id=message["message_id"],
                reply_markup={"inline_keyboard": []})
        handle_action(callback, value)
        return
    ip = netmon.parse_ban_target(value) if action in ("ban", "unban") else parse_public_ip(value)
    if not ip or action not in ("trust", "ban", "unban"):
        api("answerCallbackQuery", callback_query_id=callback["id"], text="Invalid request")
        return
    if message:
        api("editMessageReplyMarkup", chat_id=message["chat"]["id"], message_id=message["message_id"],
            reply_markup={"inline_keyboard": []})
    if action == "trust":
        handle_trust(callback, ip)
        return
    api("answerCallbackQuery", callback_query_id=callback["id"], text="Working…")
    ban(ip) if action == "ban" else unban(ip)


def handle_trust(callback, ip):
    until = trust_ip(ip)
    try:
        unbanned = unban_on_host(ip)
    except (OSError, KeyError, ValueError):
        unbanned = False
    api("answerCallbackQuery", callback_query_id=callback["id"], text=f"{ip} trusted until {fmt_until(until)}")
    say(f"✅ <code>{ip}</code> trusted until {fmt_until(until)}, renewed on every login.\n"
        f"Ban: {'lifted if one existed' if unbanned else 'could not lift; check fail2ban'}")


def run_netmon(*args):
    out = subprocess.run([str(netmon.ROOT / "netmon.py"), *args], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def active_bans():
    db = netmon.connect_db()
    netmon.ingest_host_logs(db)
    rows = db.execute(
        "SELECT ip, jail, action, max(ts) AS last, "
        "(SELECT max(b.ts) FROM f2b_events b WHERE b.ip = e.ip AND b.action = 'Ban') AS ts "
        "FROM f2b_events e GROUP BY ip ORDER BY ts DESC").fetchall()
    return [dict(r) for r in rows if r["action"] in ("Ban", "Restore Ban")]


def quick_check():
    say("⏳ Checking the past hour…")
    auth = run_netmon("auth-check", "--since", CHECK_WINDOW, "--no-mark")["findings"]
    check = run_netmon("check", "--since", CHECK_WINDOW, "--no-mark")
    data = run_netmon("summary", "--since", CHECK_WINDOW)
    t, ssh = data["totals"], data["ssh"]["totals"]
    important = [f for f in check["findings"] if f["severity"] != "low"]
    lines = [f"<b>🔎 Quick check · {CHECK_WINDOW}</b>",
             f"Connections: {t['tcp_connects']} · flows: {t['outbound_flows']} · "
             f"sent: {(t['bytes_out'] or 0) / 1e6:.1f} MB",
             f"IDS: outbound {t['outbound_alerts']}, inbound {t['inbound_alerts']}",
             f"SSH: {ssh['failures']} failures from {ssh['ips']} IPs, bans {ssh['bans']}",
             f"Total active bans: {len(active_bans())}", ""]
    if not auth and not important:
        lines.append("✅ Nothing significant")
    for f in auth:
        lines.append(f"{'🚨' if f.get('urgent') else '⚠️'} {html.escape(f['summary'])}")
    marks = {"high": "🔴", "medium": "🟠"}
    for f in important[:CHECK_FINDINGS_SHOWN]:
        lines.append(f"{marks[f['severity']]} {html.escape(f['summary'][:120])}")
    if len(important) > CHECK_FINDINGS_SHOWN:
        lines.append(f"…and {len(important) - CHECK_FINDINGS_SHOWN}")
    say("\n".join(lines))


def full_report():
    if not report_running.acquire(blocking=False):
        say("Report generation is already in progress")
        return
    say("⏳ Preparing a 24-hour report; this takes about a minute…")

    def worker():
        try:
            subprocess.run([str(netmon.ROOT / "run.sh"), "report"], timeout=REPORT_TIMEOUT_SECONDS, check=True)
        except (subprocess.SubprocessError, OSError) as error:
            say(f"Report failed: {html.escape(str(error))}")
        finally:
            report_running.release()

    threading.Thread(target=worker, daemon=True).start()


def trust_ip(ip):
    until = time.time() + netmon.trusted_ttl_seconds()
    with netmon.DynamicTrust() as trust:
        trust.trust(ip, until)
    return until


def handle_command(text):
    command, _, arg = text.strip().partition(" ")
    command = command.split("@", 1)[0]
    if command == "/trusted":
        now = time.time()
        dynamic = sorted(netmon.read_dynamic_trust().items(), key=lambda x: x[1])
        lines = ["<b>Temporary (button)</b>"] + ([
            f"<code>{html.escape(ip)}</code> until {fmt_until(exp)}" for ip, exp in dynamic if exp > now] or ["—"])
        static = [l.strip() for l in netmon.TRUSTED_IPS_PATH.read_text().splitlines()
                  if l.strip() and not l.strip().startswith("#")] if netmon.TRUSTED_IPS_PATH.exists() else []
        lines += ["", "<b>Permanent (trusted-ips.txt)</b>"] + [f"<code>{html.escape(l)}</code>" for l in static]
        say("\n".join(lines))
    elif command == "/untrust":
        ip = parse_public_ip(arg)
        if not ip:
            say("Usage: /untrust &lt;ip&gt;")
            return
        with netmon.DynamicTrust() as trust:
            removed = trust.entries.pop(ip, None) is not None
        say(f"<code>{ip}</code> {'removed from temporary trusted IPs' if removed else 'not found among temporary trusted IPs'}")
    elif command == "/check":
        quick_check()
    elif command == "/report":
        full_report()
    elif command == "/bans":
        bans = active_bans()
        subnets = netmon.active_subnet_bans()
        subnet_lines = [f"<code>{html.escape(c)}</code> until {fmt_until(b['until'])} · {html.escape(b.get('reason', '')[:60])}"
                        for c, b in sorted(subnets.items(), key=lambda x: -x[1]["since"])]
        say("<b>Subnets (nftables)</b>\n" + ("\n".join(subnet_lines) or "—") + "\n\n<b>IP (fail2ban)</b>\n" + ("\n".join(
            f"<code>{html.escape(b['ip'])}</code> {html.escape((netmon.geo_lookup(b['ip']) or {}).get('short', ''))}"
            f" · {html.escape(b['jail'])} · since {fmt_until(b['ts'] or b['last'])}"
            for b in bans[:40]) or "—") + (f"\n…and {len(bans) - 40}" if len(bans) > 40 else ""))
    elif command == "/trust":
        ip = parse_public_ip(arg)
        if not ip:
            say("Usage: /trust &lt;ip&gt;")
            return
        until = trust_ip(ip)
        unbanned = unban_on_host(ip)
        say(f"✅ <code>{ip}</code> trusted until {fmt_until(until)}" + (", ban lifted if one existed" if unbanned else ""))
    elif command in ("/ban", "/unban"):
        target = netmon.parse_ban_target(arg)
        if not target:
            say(f"Usage: {command} &lt;ip or subnet no broader than /16&gt;")
            return
        ban(target) if command == "/ban" else unban(target)
    elif command in ("/start", "/help"):
        say(HELP_TEXT)


def authorized(user):
    return str((user or {}).get("id")) == os.environ["TELEGRAM_CHAT_ID"]


def main():
    api("setMyCommands", commands=[{"command": name, "description": desc} for name, desc in COMMANDS])
    offset = int(OFFSET_PATH.read_text()) if OFFSET_PATH.exists() else 0
    while True:
        try:
            updates = api("getUpdates", offset=offset, timeout=POLL_TIMEOUT_SECONDS,
                          allowed_updates=["message", "callback_query"])
        except (urllib.error.URLError, OSError, ValueError) as error:
            print(f"getUpdates failed: {error}", flush=True)
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            OFFSET_PATH.write_text(str(offset))
            try:
                callback = update.get("callback_query")
                message = update.get("message")
                if callback and authorized(callback.get("from")):
                    handle_callback(callback)
                elif message and authorized(message.get("from")) and message.get("text", "").startswith("/"):
                    handle_command(message["text"])
            except (urllib.error.URLError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                print(f"update {update.get('update_id')} failed: {error}", flush=True)


if __name__ == "__main__":
    main()
