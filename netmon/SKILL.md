---
name: netmon
description: Use when inspecting network traffic, outbound connections, security alerts, or host monitoring data collected by the bundled netmon project.
---

# Network monitoring

The monitor's Python scripts, sensor configuration, Docker files, prompts, and optional fail2ban integration are bundled in `tool/`. Runtime logs, database files, credentials, and trusted IPs are not included.

To deploy on a Linux host with Docker, review `tool/.env.example` and `tool/allowlist.toml`, then run `tool/up.sh` only when the user requests deployment. The script detects the local interface and address, downloads public sensor data, and starts the monitor. Telegram and AI analysis require separately configured credentials. Automatic subnet defense is disabled unless `NETMON_AUTO_DEFEND=1` is set.

To enable IP blocking, review `tool/fail2ban/netmon-sshd.local` and both filter files with `protected_users`, set the accounts to protect, and run `tool/fail2ban/install.sh` on the host only when the user requests it. This installs fail2ban and configures its SSH and netmon jails. Without it, the monitor can still collect data, but requests written to `data/netmon-ban.log` do not block IPs.

For an investigation, determine the host and time range, then use the bundled CLI with the smallest relevant window:

```bash
cd tool
./netmon.py --help
./netmon.py summary --since 24h
./netmon.py check --since 1h --no-mark
./netmon.py sql "SELECT ..."
```

If the monitor is running in Docker, execute those commands in its scheduler container. Correlate process connections, network flows, names, alerts, and authentication records when available. State data freshness and visibility limits. Do not publish logs, IP addresses, user names, or report URLs without the user's request. Do not change firewall or trust settings during a read-only investigation.
