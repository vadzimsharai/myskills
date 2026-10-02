#!/usr/bin/env python3
import argparse
import fcntl
import fnmatch
import glob
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import concurrent.futures
import gzip
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "netmon.db"
EVE_GLOB = str(DATA_DIR / "suricata" / "eve-*.json")
TETRAGON_DIR = DATA_DIR / "tetragon"
ALLOWLIST_PATH = ROOT / "allowlist.toml"
DOCKER_SOCKET = "/var/run/docker.sock"
HOST_LOG_DIR = Path("/hostlog")
AUTH_LOG_FILES = ("auth.log", "auth.log.1")
FAIL2BAN_LOG_FILES = ("fail2ban.log", "fail2ban.log.1")
TRUSTED_IPS_PATH = ROOT / "trusted-ips.txt"
TRUSTED_DYNAMIC_PATH = DATA_DIR / "trusted-dynamic.json"
BAN_LOG_PATH = DATA_DIR / "netmon-ban.log"
SQLITE_BUSY_TIMEOUT_SECONDS = 60
SSH_HISTORY_SECONDS = 86400
AUTH_CHECK_WINDOW = "5m"
SUBNET_BANS_PATH = DATA_DIR / "subnet-bans.json"
PENDING_ACTIONS_PATH = DATA_DIR / "pending-actions.json"
HOST_HELPER_IMAGE = "alpine:3"
NFT_TABLE = "inet netmon"
NFT_SET = "blocked4"
SUBNET_BAN_DAYS = 28
SUBNET_MIN_IPS = 3
SUBNET_MIN_FAILURES_PER_IP = 10
SUBNET_MIN_FAILURES = 100
SUBNET_REVIEW_HOURS = 48
PENDING_TTL_SECONDS = 7 * 86400
CLAUDE_MODEL = os.environ.get("NETMON_MODEL", "sonnet")
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
GEO_DIR = DATA_DIR / "geo"
GEO_TSV_PATH = GEO_DIR / "ip2asn-v4.tsv.gz"
GEO_DB_PATH = GEO_DIR / "ip2asn-v4.db"
WEB_PROBE_PATTERN = re.compile(
    r"(/\.env|phpinfo|/\.htpasswd|/\.git/|wp-config|/\.aws|id_rsa|\.sql(\.gz)?$|/\.ssh|/\.DS_Store|"
    r"/server-status|/\.svn/|/config\.(php|ya?ml)|/backup\.(zip|tar|gz)|/\.vscode|/actuator/env)", re.I)
WEB_PROBE_BAN_THRESHOLD = 3
EXPOSURE_USER_AGENT = "netmon-exposure-check/1"
EXPOSURE_TIMEOUT_SECONDS = 8
EXPOSURE_MAX_BODY_BYTES = 512 * 1024
EXPOSURE_RECHECK_SECONDS = 86400
EXPOSURE_MAX_PER_RUN = 60
EXPOSURE_WORKERS = 6
EXPOSURE_HOSTNAME = re.compile(r"^(?=.*[a-z])[a-z0-9-]+(\.[a-z0-9-]+)+$")
EXPOSURE_FALLBACK = "fallback"
EXPOSURE_DIFFERS = "differs"
EXPOSURE_SECRET = "secret"
EXPOSURE_GONE = "gone"
EXPOSURE_ERROR = "error"
# Only marker names are stored or reported; the matched text may itself be a secret.
EXPOSURE_MARKERS = {
    "dotenv": re.compile(rb"(?m)^\s*(export\s+)?[A-Z][A-Z0-9_]{2,}\s*=\s*\S+(\r?\n\s*(export\s+)?[A-Z][A-Z0-9_]{2,}\s*=)"),
    "aws_key": re.compile(rb"AKIA[0-9A-Z]{16}|aws_secret_access_key", re.I),
    "private_key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "git_config": re.compile(rb"(?m)^\[core\]|repositoryformatversion"),
    "git_head": re.compile(rb"^ref: refs/"),
    "phpinfo": re.compile(rb"<title>phpinfo\(\)|PHP Version \d"),
    "sql_dump": re.compile(rb"CREATE TABLE|INSERT INTO|-- MySQL dump|PostgreSQL database dump"),
    "htpasswd": re.compile(rb"(?m)^[\w.-]+:(\$apr1\$|\$2[aby]\$|\{SHA\})"),
    "wp_config": re.compile(rb"DB_PASSWORD|AUTH_KEY"),
    "api_token": re.compile(rb"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[bp]-[A-Za-z0-9-]{20,}"),
    "password_field": re.compile(rb"(?i)(password|passwd|secret|api_?key|token)\s*[:=]\s*['\"]?[^\s'\"<]{6,}"),
    "archive": re.compile(rb"^(PK\x03\x04|\x1f\x8b|ustar)"),
}
# Reputation-list hits ban on their own; exploit signatures need repeats so a real user's odd request does not.
IDS_REPUTATION_SIGNATURE = re.compile(r"^(ET DROP |ET CINS |ET COMPROMISED )|Dshield|Spamhaus", re.I)
IDS_EXPLOIT_SIGNATURE = re.compile(r"^(ET EXPLOIT |ET WEB_SERVER |ET WEB_SPECIFIC_APPS |GPL WEB_SERVER )", re.I)
IDS_EXPLOIT_MIN_ALERTS = 2
DOCKER_LOG_HEADER_BYTES = 8
PTR_TIMEOUT_SECONDS = 2
PTR_CACHE_SECONDS = 86400
PTR_WORKERS = 10
IP_INFO_LIMIT = 80
ALERT_ATTRIBUTION_SECONDS = 5
# Cloud metadata endpoint: not global, but a connection to it is worth attributing.
METADATA_IPS = {"169.254.169.254"}
HOSTING_KEYWORDS = (
    "hosting", "cloud", "server", "vps", "data center", "datacenter", "colo", "dedicated", "cdn")
ACCESS_ISP_KEYWORDS = (
    "telecom", "mobile", "broadband", "cable", "dsl", "fiber", "fibre", "wireless", "cellular",
    "internet service", "isp")
BLOCKLIST_MARKERS = {"Dshield": "DShield", "CINS": "CINS", "Spamhaus": "Spamhaus", "Tor ": "Tor",
                     "Compromised": "ET Compromised", "3CORESec": "3CORESec"}

EVE_RETENTION_DAYS = 14
DB_RETENTION_DAYS = 30
FINDING_DEDUP_HOURS = 6
CHECK_DEDUP_HOURS = 2 * 24
SSH_LOGIN_DEDUP_HOURS = 24
READ_LIMIT_BYTES = 256 * 1024 * 1024
SUSPICIOUS_PATH_PREFIXES = ("/tmp/", "/dev/shm/", "/var/tmp/", "/run/user/")
TWO_LETTER_SLD = {"co", "com", "net", "org", "gov", "ac", "edu"}
VERSION_SEGMENT = re.compile(r"\d+(?:\.\d+)+")
PROC_NET_TCP_LISTEN = "0A"
PROC_NET_UDP_UNCONNECTED = "07"

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
OUTBOUND_ALERT_SEVERITY = {1: SEVERITY_HIGH, 2: SEVERITY_MEDIUM}

SCHEMA = """
CREATE TABLE IF NOT EXISTS offsets (key TEXT PRIMARY KEY, path TEXT, byte_offset INTEGER);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS connects (
  ts REAL, binary TEXT, args TEXT, parent TEXT, container TEXT, pid INTEGER, uid INTEGER,
  daddr TEXT, dport INTEGER, name TEXT);
CREATE INDEX IF NOT EXISTS connects_ts ON connects(ts);
CREATE TABLE IF NOT EXISTS flows (
  ts REAL, src_ip TEXT, dest_ip TEXT, dest_port INTEGER, proto TEXT, app_proto TEXT,
  bytes_out INTEGER, bytes_in INTEGER, name TEXT);
CREATE INDEX IF NOT EXISTS flows_ts ON flows(ts);
CREATE TABLE IF NOT EXISTS alerts (
  ts REAL, sid INTEGER, signature TEXT, category TEXT, severity INTEGER,
  src_ip TEXT, src_port INTEGER, dest_ip TEXT, dest_port INTEGER, proto TEXT,
  outbound INTEGER, name TEXT);
CREATE INDEX IF NOT EXISTS alerts_ts ON alerts(ts);
CREATE TABLE IF NOT EXISTS http (
  ts REAL, src_ip TEXT, dest_ip TEXT, dest_port INTEGER, hostname TEXT, method TEXT,
  url TEXT, user_agent TEXT, status INTEGER);
CREATE INDEX IF NOT EXISTS http_ts ON http(ts);
CREATE TABLE IF NOT EXISTS names (ip TEXT, name TEXT, source TEXT, ts REAL, PRIMARY KEY (ip, name));
CREATE TABLE IF NOT EXISTS seen (
  kind TEXT, key TEXT, first_seen REAL, last_seen REAL, hits INTEGER, PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS reported (fingerprint TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS ssh_auth (ts REAL, ip TEXT, user TEXT, result TEXT, method TEXT);
CREATE INDEX IF NOT EXISTS ssh_auth_ts ON ssh_auth(ts);
CREATE INDEX IF NOT EXISTS ssh_auth_ip ON ssh_auth(ip, ts);
CREATE TABLE IF NOT EXISTS f2b_events (ts REAL, jail TEXT, action TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS ptr_cache (ip TEXT PRIMARY KEY, ptr TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS web_probes (ts REAL, ip TEXT, host TEXT, uri TEXT, status INTEGER);
CREATE INDEX IF NOT EXISTS web_probes_ip ON web_probes(ip, ts);
CREATE TABLE IF NOT EXISTS web_exposure (
  host TEXT, uri TEXT, ts REAL, status INTEGER, bytes INTEGER, content_type TEXT, sha TEXT,
  verdict TEXT, markers TEXT, PRIMARY KEY (host, uri));
CREATE INDEX IF NOT EXISTS f2b_events_ip ON f2b_events(ip, ts);
"""

SSHD_PREFIX = r"^(?P<ts>\S+) \S+ sshd(?:-session)?\[\d+\]: "
SSH_AUTH_PATTERNS = (
    (re.compile(SSHD_PREFIX + r"Failed (?P<method>password|publickey) for (?:invalid user )?(?P<user>\S*) from (?P<ip>\S+) port"), "failed"),
    (re.compile(SSHD_PREFIX + r"Accepted (?P<method>\S+) for (?P<user>\S+) from (?P<ip>\S+) port"), "accepted"),
    (re.compile(SSHD_PREFIX + r"Invalid user (?P<user>\S*) from (?P<ip>\S+) port"), "invalid"),
)
FAIL2BAN_ACTION = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ fail2ban\.actions\s+\[\d+\]: NOTICE\s+"
    r"\[(?P<jail>[^\]]+)\] (?P<action>Ban|Unban|Restore Ban) (?P<ip>\S+)")


def connect_db():
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def load_allowlist():
    with open(ALLOWLIST_PATH, "rb") as f:
        return tomllib.load(f)


def parse_ts(value):
    value = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
    return datetime.fromisoformat(value).timestamp()


def parse_duration(value):
    units = {"m": 60, "h": 3600, "d": 86400}
    return int(value[:-1]) * units[value[-1]]


HOST_ADDRESSES = set(tomllib.loads(ALLOWLIST_PATH.read_text())["host"]["addresses"]) | (
    {os.environ["NETMON_HOST_IP"]} if os.environ.get("NETMON_HOST_IP") else set())
AS_ROOT = os.geteuid() == 0


def as_root(cmd):
    return cmd if AS_ROOT else ["sudo", "-n", *cmd]


def is_external(ip):
    if ip in HOST_ADDRESSES:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return address.is_global and not address.is_multicast


def is_ip_literal(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def base_domain(name):
    labels = name.rstrip(".").lower().split(".")
    if len(labels) >= 3 and labels[-2] in TWO_LETTER_SLD and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalize_binary(binary, parent):
    if binary == "/proc/self/exe" and parent:
        binary = f"{parent} (self)"
    return VERSION_SEGMENT.sub("*", binary or "?")


def glob_match(value, patterns):
    return any(fnmatch.fnmatch(value, p) for p in patterns)


def domain_trusted(domain, patterns):
    domain = (domain or "").lower()
    for pattern in patterns:
        bare = pattern.removeprefix("*.")
        if fnmatch.fnmatch(domain, pattern) or domain == bare or domain.endswith("." + bare):
            return True
    return False


def resolve_name(db, ip):
    row = db.execute("SELECT name FROM names WHERE ip=? ORDER BY ts DESC LIMIT 1", (ip,)).fetchone()
    return row["name"] if row else None


def remember_name(db, ip, name, source, ts):
    if not name or is_ip_literal(name) or not is_external(ip):
        return
    db.execute(
        "INSERT INTO names VALUES (?,?,?,?) ON CONFLICT(ip, name) DO UPDATE SET ts=excluded.ts",
        (ip, name.lower().rstrip("."), source, ts))


def mark_seen(db, kind, key, ts):
    db.execute(
        "INSERT INTO seen VALUES (?,?,?,?,1) ON CONFLICT(kind, key) DO UPDATE SET "
        "last_seen=max(last_seen, excluded.last_seen), hits=hits+1",
        (kind, key, ts, ts))


def dest_label(name, ip):
    return base_domain(name) if name else ip


def list_files(paths_with_privilege):
    files = []
    for pattern, privileged in paths_with_privilege:
        if privileged:
            directory, name_glob = os.path.split(pattern)
            if not os.path.isdir(directory):
                continue
            out = subprocess.run(
                as_root(["find", directory, "-maxdepth", "1", "-name", name_glob,
                         "-printf", "%i %s %p\\n"]),
                capture_output=True, text=True, check=True).stdout
            for line in out.splitlines():
                inode, size, path = line.split(" ", 2)
                files.append((int(inode), int(size), path, True))
        else:
            for path in glob.glob(pattern):
                st = os.stat(path)
                files.append((st.st_ino, st.st_size, path, False))
    return files


def read_from(path, offset, privileged):
    if privileged:
        return subprocess.run(
            as_root(["dd", f"if={path}", "bs=1M", "iflag=skip_bytes,count_bytes",
                     f"skip={offset}", f"count={READ_LIMIT_BYTES}", "status=none"]),
            capture_output=True, check=True).stdout
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(READ_LIMIT_BYTES)


def iter_new_lines(db, kind, pattern, privileged, parse_json=True):
    for inode, size, path, priv in list_files([(pattern, privileged)]):
        key = f"{kind}:{inode}"
        row = db.execute("SELECT byte_offset FROM offsets WHERE key=?", (key,)).fetchone()
        offset = row["byte_offset"] if row else 0
        if offset > size:
            offset = 0
        if offset == size:
            continue
        chunk = read_from(path, offset, priv)
        complete = chunk[: chunk.rfind(b"\n") + 1]
        for raw in complete.splitlines():
            if not parse_json:
                yield raw.decode("utf-8", "replace")
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue
        db.execute(
            "INSERT INTO offsets VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "path=excluded.path, byte_offset=excluded.byte_offset",
            (key, path, offset + len(complete)))


def ingest_eve_event(db, ev, ports):
    kind = ev.get("event_type")
    ts = parse_ts(ev["timestamp"])
    src, dst = ev.get("src_ip", ""), ev.get("dest_ip", "")
    outbound = not is_external(src) and is_external(dst)
    if kind == "dns":
        dns = ev.get("dns", {})
        queries = dns.get("queries") or []
        qname = queries[0]["rrname"] if queries else dns.get("rrname")
        for answer in dns.get("answers") or []:
            if answer.get("rrtype") in ("A", "AAAA"):
                remember_name(db, answer.get("rdata", ""), qname or answer.get("rrname"), "dns", ts)
    elif kind == "tls" and outbound:
        remember_name(db, dst, ev.get("tls", {}).get("sni"), "sni", ts)
    elif kind == "quic" and outbound:
        remember_name(db, dst, ev.get("quic", {}).get("sni"), "sni", ts)
    elif kind == "http" and outbound:
        http = ev.get("http", {})
        remember_name(db, dst, http.get("hostname"), "http", ts)
        db.execute(
            "INSERT INTO http VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, src, dst, ev.get("dest_port"), http.get("hostname"), http.get("http_method"),
             (http.get("url") or "")[:500], http.get("http_user_agent"), http.get("status")))
    elif kind == "flow" and outbound and not is_host_serving(ev, ports) and (
            ev.get("proto") != "TCP" or ev.get("tcp", {}).get("syn")):
        flow = ev.get("flow", {})
        name = resolve_name(db, dst)
        db.execute(
            "INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, src, dst, ev.get("dest_port"), ev.get("proto"), ev.get("app_proto"),
             flow.get("bytes_toserver", 0), flow.get("bytes_toclient", 0), name))
        label = dest_label(name, dst)
        mark_seen(db, "dest", label, ts)
        mark_seen(db, "dest_port", f"{label}:{ev.get('dest_port')}/{ev.get('proto')}", ts)
    elif kind == "alert":
        alert = ev.get("alert", {})
        is_outbound = not is_external(src)
        peer = dst if is_outbound else src
        db.execute(
            "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, alert.get("signature_id"), alert.get("signature"), alert.get("category"),
             alert.get("severity"), src, ev.get("src_port"), dst, ev.get("dest_port"),
             ev.get("proto"), int(is_outbound), resolve_name(db, peer)))


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self.socket_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def listening_ports():
    # Needs the host network namespace; Suricata may start a flow mid-session at the server's reply,
    # so traffic from a port the host serves on is inbound, not outbound.
    ports = {"TCP": set(), "UDP": set()}
    for proto, files, state in (("TCP", ("tcp", "tcp6"), PROC_NET_TCP_LISTEN),
                                ("UDP", ("udp", "udp6"), PROC_NET_UDP_UNCONNECTED)):
        for name in files:
            try:
                lines = Path(f"/proc/net/{name}").read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if fields[3] == state:
                    ports[proto].add(int(fields[1].rsplit(":", 1)[1], 16))
    return ports


def is_host_serving(ev, ports):
    return (ev.get("src_port") or 0) in ports.get(ev.get("proto"), set())


def container_names():
    if os.access(DOCKER_SOCKET, os.R_OK | os.W_OK):
        conn = UnixHTTPConnection(DOCKER_SOCKET)
        conn.request("GET", "/containers/json?all=1")
        containers = json.loads(conn.getresponse().read())
        return {c["Id"]: c["Names"][0].lstrip("/") for c in containers if c.get("Names")}
    out = subprocess.run(
        as_root(["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}\t{{.Names}}"]),
        capture_output=True, text=True).stdout
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


def container_name(names, short_id):
    if not short_id:
        return "host"
    for full_id, name in names.items():
        if full_id.startswith(short_id):
            return name
    return short_id[:12]


def ingest_tetragon_event(db, ev, names):
    kp = ev.get("process_kprobe")
    if not kp or not kp.get("args"):
        return
    sock = kp["args"][0].get("sock_arg") or {}
    daddr, dport = sock.get("daddr"), sock.get("dport")
    if not daddr or not (is_external(daddr) or daddr in METADATA_IPS):
        return
    proc, parent = kp.get("process", {}), kp.get("parent", {})
    ts = parse_ts(ev["time"])
    name = resolve_name(db, daddr)
    binary = normalize_binary(proc.get("binary"), parent.get("binary"))
    db.execute(
        "INSERT INTO connects VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, binary, (proc.get("arguments") or "")[:500], parent.get("binary"),
         container_name(names, proc.get("docker")), proc.get("pid"), proc.get("uid"),
         daddr, dport, name))
    label = dest_label(name, daddr)
    mark_seen(db, "bin", binary, ts)
    mark_seen(db, "bin_dest", f"{binary}|{label}", ts)
    mark_seen(db, "dest", label, ts)
    mark_seen(db, "dest_port", f"{label}:{dport}/TCP", ts)


def docker_log_lines(container, since):
    conn = UnixHTTPConnection(DOCKER_SOCKET)
    conn.request("GET", f"/containers/{container}/logs?stdout=1&stderr=1&since={int(since)}")
    response = conn.getresponse()
    if response.status != 200:
        return []
    raw, lines, pos = response.read(), [], 0
    # Non-TTY containers multiplex stdout/stderr: 8-byte header (stream, 0, 0, 0, uint32 size) per frame.
    while pos + DOCKER_LOG_HEADER_BYTES <= len(raw):
        size = int.from_bytes(raw[pos + 4:pos + DOCKER_LOG_HEADER_BYTES], "big")
        start = pos + DOCKER_LOG_HEADER_BYTES
        lines.append(raw[start:start + size].decode("utf-8", "replace"))
        pos = start + size
    return lines


def ingest_web_logs(db):
    container = os.environ.get("NETMON_WEB_LOG_CONTAINER")
    if not container or not os.access(DOCKER_SOCKET, os.R_OK | os.W_OK):
        return 0
    row = db.execute("SELECT value FROM meta WHERE key='web_log_ts'").fetchone()
    last_ts = float(row["value"]) if row else 0.0
    newest, count = last_ts, 0
    for line in docker_log_lines(container, last_ts):
        if '"http.log.access' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        ts = entry.get("ts", 0)
        if ts <= last_ts:
            continue
        newest = max(newest, ts)
        request = entry.get("request", {})
        uri = request.get("uri", "")
        if own_exposure_check(request):
            continue
        if WEB_PROBE_PATTERN.search(uri.split("?", 1)[0]):
            db.execute("INSERT INTO web_probes VALUES (?,?,?,?,?)",
                       (ts, request.get("client_ip") or request.get("remote_ip"), request.get("host"),
                        uri[:300], entry.get("status")))
            count += 1
    db.execute("INSERT INTO meta VALUES ('web_log_ts', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (str(newest),))
    db.commit()
    return count


def own_exposure_check(request):
    agents = (request.get("headers") or {}).get("User-Agent") or []
    client = request.get("client_ip") or request.get("remote_ip") or ""
    return EXPOSURE_USER_AGENT in agents and is_trusted(client, [])


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to this host's own address whatever the name resolves to; the certificate still has to match."""

    def __init__(self, hostname, address):
        super().__init__(hostname, 443, timeout=EXPOSURE_TIMEOUT_SECONDS, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch_own_site(hostname, uri):
    conn = PinnedHTTPSConnection(hostname, os.environ.get("NETMON_HOST_IP") or "127.0.0.1")
    try:
        conn.request("GET", uri, headers={"User-Agent": EXPOSURE_USER_AGENT, "Accept": "*/*"})
        response = conn.getresponse()
        return response.status, response.getheader("Content-Type") or "", response.read(EXPOSURE_MAX_BODY_BYTES)
    finally:
        conn.close()


def classify_exposure(hostname, uri, baselines):
    try:
        status, content_type, body = fetch_own_site(hostname, uri)
    except (OSError, http.client.HTTPException) as error:
        return {"status": None, "bytes": 0, "content_type": "", "sha": None,
                "verdict": EXPOSURE_ERROR, "markers": type(error).__name__}
    sha = hashlib.sha256(body).hexdigest()
    markers = [name for name, pattern in EXPOSURE_MARKERS.items() if pattern.search(body)]
    if status != 200:
        verdict = EXPOSURE_GONE
    elif sha in baselines:
        verdict = EXPOSURE_FALLBACK
    elif markers:
        verdict = EXPOSURE_SECRET
    else:
        verdict = EXPOSURE_DIFFERS
    return {"status": status, "bytes": len(body), "content_type": content_type[:80], "sha": sha,
            "verdict": verdict, "markers": ",".join(markers)}


def site_baselines(hostname):
    """Bodies a site returns for its home page and for a path that cannot exist: SPA fallbacks match them."""
    hashes = set()
    for uri in ("/", f"/netmon-missing-{uuid.uuid4().hex}"):
        try:
            status, _, body = fetch_own_site(hostname, uri)
        except (OSError, http.client.HTTPException):
            continue
        if status == 200:
            hashes.add(hashlib.sha256(body).hexdigest())
    return hashes


def verify_web_exposure(db, since):
    """Re-fetches every secret path that answered 200 to a scanner and records whether real content was served."""
    now = time.time()
    pending = [(r["host"], r["uri"]) for r in db.execute(
        "SELECT p.host, p.uri, max(p.ts) AS last FROM web_probes p "
        "LEFT JOIN web_exposure e ON e.host = p.host AND e.uri = p.uri "
        "WHERE p.ts>=? AND p.status=200 AND (e.ts IS NULL OR e.ts<?) "
        "GROUP BY p.host, p.uri ORDER BY last DESC LIMIT ?",
        (since, now - EXPOSURE_RECHECK_SECONDS, EXPOSURE_MAX_PER_RUN))
        if r["host"] and EXPOSURE_HOSTNAME.match(r["host"].lower())]
    if not pending:
        return 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=EXPOSURE_WORKERS) as pool:
        hosts = sorted({h for h, _ in pending})
        baselines = dict(zip(hosts, pool.map(site_baselines, hosts)))
        results = list(pool.map(lambda item: classify_exposure(item[0], item[1], baselines[item[0]]), pending))
    db.executemany(
        "INSERT OR REPLACE INTO web_exposure VALUES (?,?,?,?,?,?,?,?,?)",
        [(h, u, now, r["status"], r["bytes"], r["content_type"], r["sha"], r["verdict"], r["markers"])
         for (h, u), r in zip(pending, results)])
    db.commit()
    return len(pending)


def exposure_rows(db, since):
    return [dict(r) for r in db.execute(
        "SELECT e.host, e.uri, e.verdict, e.markers, e.status, e.bytes, e.content_type, "
        "datetime(e.ts,'unixepoch') AS checked_at, count(*) AS probes, group_concat(DISTINCT p.ip) AS ips "
        "FROM web_exposure e JOIN web_probes p ON p.host = e.host AND p.uri = e.uri AND p.status = 200 "
        "WHERE p.ts>=? GROUP BY e.host, e.uri ORDER BY e.verdict, e.host, e.uri", (since,))]


def detect_web_exposure(db, since):
    verify_web_exposure(db, since)
    findings = []
    for row in exposure_rows(db, since):
        if row["verdict"] not in (EXPOSURE_SECRET, EXPOSURE_DIFFERS):
            continue
        secret = row["verdict"] == EXPOSURE_SECRET
        findings.append(finding(
            "web_secret_exposed" if secret else "web_probe_real_content",
            SEVERITY_HIGH if secret else SEVERITY_MEDIUM, (row["host"], row["uri"], row["verdict"]),
            f"https://{row['host']}{row['uri']} served {row['bytes']} bytes to scanners"
            + (f" with secret markers: {row['markers']}" if secret else ", not the site's fallback page"),
            host=row["host"], uri=row["uri"], markers=row["markers"], bytes=row["bytes"],
            content_type=row["content_type"], scanner_ips=row["ips"], probes=row["probes"]))
    return findings


def web_probe_bans(db, mark):
    trusted = load_trusted_networks()
    rows = db.execute(
        "SELECT ip, count(*) AS probes FROM web_probes WHERE ts>=? GROUP BY ip HAVING probes>=?",
        (time.time() - 86400, WEB_PROBE_BAN_THRESHOLD)).fetchall()
    banned = []
    for row in rows:
        ip = row["ip"]
        if not parse_ban_target(ip) or is_trusted(ip, trusted) or ban_state(db, ip):
            continue
        if db.execute("SELECT 1 FROM reported WHERE fingerprint=? AND ts>=?",
                      (fingerprint("web-ban", ip), time.time() - SSH_HISTORY_SECONDS)).fetchone():
            continue
        if mark:
            request_ban(ip, "web-probe")
            db.execute("INSERT INTO reported VALUES (?,?) ON CONFLICT(fingerprint) DO UPDATE SET ts=excluded.ts",
                       (fingerprint("web-ban", ip), time.time()))
        banned.append({"ip": ip, "probes": row["probes"]})
    db.commit()
    return banned


def ids_bans(db, mark):
    trusted = load_trusted_networks()
    rows = db.execute(
        "SELECT src_ip AS ip, group_concat(DISTINCT signature) AS signatures, "
        "sum(signature LIKE 'ET EXPLOIT %' OR signature LIKE 'ET WEB_SERVER %' OR signature LIKE 'ET WEB_SPECIFIC_APPS %' "
        "OR signature LIKE 'GPL WEB_SERVER %') AS exploit_alerts FROM alerts "
        "WHERE outbound=0 AND ts>=? GROUP BY src_ip", (time.time() - 86400,)).fetchall()
    banned = []
    for row in rows:
        ip = row["ip"]
        signatures = (row["signatures"] or "").split(",")
        matched = [sig for sig in signatures if IDS_REPUTATION_SIGNATURE.search(sig)]
        if not matched and row["exploit_alerts"] >= IDS_EXPLOIT_MIN_ALERTS:
            matched = [sig for sig in signatures if IDS_EXPLOIT_SIGNATURE.search(sig)]
        if not matched or not parse_ban_target(ip) or is_trusted(ip, trusted) or ban_state(db, ip):
            continue
        if db.execute("SELECT 1 FROM reported WHERE fingerprint=? AND ts>=?",
                      (fingerprint("ids-ban", ip), time.time() - SSH_HISTORY_SECONDS)).fetchone():
            continue
        if mark:
            request_ban(ip, "ids")
            db.execute("INSERT INTO reported VALUES (?,?) ON CONFLICT(fingerprint) DO UPDATE SET ts=excluded.ts",
                       (fingerprint("ids-ban", ip), time.time()))
        banned.append({"ip": ip, "signature": matched[0][:80]})
    db.commit()
    return banned


def ingest_host_logs(db):
    counts = {"ssh_auth": 0, "fail2ban": 0}
    for name in AUTH_LOG_FILES:
        for line in iter_new_lines(db, "auth", str(HOST_LOG_DIR / name), privileged=False, parse_json=False):
            for pattern, result in SSH_AUTH_PATTERNS:
                m = pattern.match(line)
                if m:
                    db.execute("INSERT INTO ssh_auth VALUES (?,?,?,?,?)",
                               (parse_ts(m["ts"]), m["ip"], m["user"], result, m.groupdict().get("method")))
                    counts["ssh_auth"] += 1
                    break
    for name in FAIL2BAN_LOG_FILES:
        for line in iter_new_lines(db, "fail2ban", str(HOST_LOG_DIR / name), privileged=False, parse_json=False):
            m = FAIL2BAN_ACTION.match(line)
            if m:
                ts = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                db.execute("INSERT INTO f2b_events VALUES (?,?,?,?)", (ts, m["jail"], m["action"], m["ip"]))
                counts["fail2ban"] += 1
    db.commit()
    return counts


def ingest(db):
    counts = {"eve": 0, "tetragon": 0}
    counts.update(ingest_host_logs(db))
    ports = listening_ports()
    for ev in iter_new_lines(db, "eve", EVE_GLOB, privileged=False):
        ingest_eve_event(db, ev, ports)
        counts["eve"] += 1
    names = container_names()
    for ev in iter_new_lines(db, "tetragon", str(TETRAGON_DIR / "tetragon*.log"), privileged=True):
        ingest_tetragon_event(db, ev, names)
        counts["tetragon"] += 1
    if not db.execute("SELECT 1 FROM meta WHERE key='baseline_start'").fetchone():
        db.execute("INSERT INTO meta VALUES ('baseline_start', ?)", (str(time.time()),))
    db.commit()
    return counts


class DynamicTrust:
    """IPs trusted via the Telegram button: {ip: expires_at}; guarded by a lock for the bot and auth loop."""

    def __init__(self):
        self.lock_path = TRUSTED_DYNAMIC_PATH.with_suffix(".lock")

    def __enter__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self.lock = open(self.lock_path, "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.entries = read_dynamic_trust()
        return self

    def __exit__(self, *exc):
        now = time.time()
        live = {ip: exp for ip, exp in self.entries.items() if exp > now}
        tmp = TRUSTED_DYNAMIC_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(live, indent=1, sort_keys=True))
        os.chmod(tmp, 0o644)
        os.replace(tmp, TRUSTED_DYNAMIC_PATH)
        self.lock.close()

    def trust(self, ip, until):
        self.entries[ip] = max(self.entries.get(ip, 0), until)


def read_dynamic_trust():
    try:
        return {ip: float(exp) for ip, exp in json.loads(TRUSTED_DYNAMIC_PATH.read_text()).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def trusted_ttl_seconds():
    return load_allowlist()["ssh"]["trusted_ttl_days"] * 86400


def extend_dynamic_trust(db, since):
    entries = read_dynamic_trust()
    if not entries:
        return
    now = time.time()
    rows = db.execute(
        "SELECT ip, max(ts) AS last FROM ssh_auth WHERE result='accepted' AND ts>=? GROUP BY ip", (since,)).fetchall()
    renew = [(r["ip"], r["last"]) for r in rows if entries.get(r["ip"], 0) > now]
    if renew:
        with DynamicTrust() as trust:
            for ip, last in renew:
                trust.trust(ip, last + trusted_ttl_seconds())


class JsonStore:
    """Locked read-modify-write of a small JSON dict shared by the scheduler, auth loop and bot."""

    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self.lock = open(self.path.with_suffix(".lock"), "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.data = read_json(self.path)
        return self.data

    def __exit__(self, *exc):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True, ensure_ascii=False))
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)
        self.lock.close()


def read_json(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run_on_host(argv):
    """Runs argv in the host namespaces via the docker socket (privileged nsenter helper); returns exit code."""
    body = json.dumps({
        "Image": HOST_HELPER_IMAGE,
        "Cmd": ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--", *argv],
        "HostConfig": {"Privileged": True, "PidMode": "host", "AutoRemove": True},
    })
    conn = UnixHTTPConnection(DOCKER_SOCKET)
    conn.request("POST", "/containers/create", body, {"Content-Type": "application/json"})
    container_id = json.loads(conn.getresponse().read())["Id"]
    result = b"{}"
    for path in (f"/containers/{container_id}/start", f"/containers/{container_id}/wait"):
        conn = UnixHTTPConnection(DOCKER_SOCKET)
        conn.request("POST", path)
        result = conn.getresponse().read()
    return json.loads(result).get("StatusCode", 1)


NFT_SETUP = f"""nft list table {NFT_TABLE} >/dev/null 2>&1 || nft -f - <<'EOF'
table {NFT_TABLE} {{
  set {NFT_SET} {{ type ipv4_addr; flags interval, timeout; }}
  chain prerouting {{ type filter hook prerouting priority -300; policy accept; ip saddr @{NFT_SET} drop; }}
}}
EOF"""


def nft_on_host(command):
    return run_on_host(["sh", "-c", NFT_SETUP + "\n" + command])


def parse_ban_target(value, min_prefix=16):
    """Public IPv4 address or network (prefix >= min_prefix), normalized; None when not acceptable."""
    try:
        network = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None
    if network.version != 4 or network.prefixlen < min_prefix or not network.network_address.is_global:
        return None
    return str(network.network_address) if network.prefixlen == 32 else str(network)


def ban_subnet(cidr, reason, days=SUBNET_BAN_DAYS):
    until = time.time() + days * 86400
    code = nft_on_host(f"nft add element {NFT_TABLE} {NFT_SET} {{ {cidr} timeout {days}d }}")
    if code == 0:
        with JsonStore(SUBNET_BANS_PATH) as bans:
            bans[cidr] = {"since": time.time(), "until": until, "reason": reason}
    return code == 0


def unban_subnet(cidr):
    nft_on_host(f"nft delete element {NFT_TABLE} {NFT_SET} {{ {cidr} }} 2>/dev/null || true")
    with JsonStore(SUBNET_BANS_PATH) as bans:
        return bans.pop(cidr, None) is not None


def active_subnet_bans():
    now = time.time()
    return {cidr: ban for cidr, ban in read_json(SUBNET_BANS_PATH).items() if ban.get("until", 0) > now}


def restore_subnet_bans():
    now = time.time()
    elements = [f"{cidr} timeout {max(int(ban['until'] - now), 60)}s" for cidr, ban in active_subnet_bans().items()]
    if not elements:
        return 0
    nft_on_host(f"nft add element {NFT_TABLE} {NFT_SET} {{ {', '.join(elements)} }}")
    return len(elements)


def telegram(method, **params):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return None
    if method == "sendMessage":
        params.setdefault("chat_id", chat)
    data = urllib.parse.urlencode(
        {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}).encode()
    request = urllib.request.Request(TELEGRAM_API.format(token=token, method=method), data=data)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["result"]


def telegram_say(text, markup=None):
    params = {"text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if markup:
        params["reply_markup"] = markup
    return telegram("sendMessage", **params)


def ask_question(text, options):
    """Sends a question with one button per option; options: [{"label", "kind", ...}] handled by bot.py."""
    action_id = uuid.uuid4().hex[:10]
    with JsonStore(PENDING_ACTIONS_PATH) as pending:
        now = time.time()
        for key in [k for k, v in pending.items() if v.get("created", 0) < now - PENDING_TTL_SECONDS]:
            del pending[key]
        pending[action_id] = {"created": now, "text": text, "options": options}
    markup = {"inline_keyboard": [[{"text": o["label"], "callback_data": f"act:{action_id}:{i}"}]
                                  for i, o in enumerate(options)]}
    telegram_say(text, markup)
    return action_id


def load_static_trusted_networks():
    networks = []
    try:
        lines = TRUSTED_IPS_PATH.read_text().splitlines()
    except OSError:
        return networks
    for line in lines:
        entry = line.split("#", 1)[0].strip()
        if entry:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                continue
    return networks


def load_trusted_networks():
    now = time.time()
    dynamic = [ipaddress.ip_network(ip) for ip, exp in read_dynamic_trust().items() if exp > now]
    return dynamic + load_static_trusted_networks()


def is_trusted(ip, networks):
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not address.is_global or ip in HOST_ADDRESSES or any(address in n for n in networks)


def subnet_prefix(ip):
    return ip.rsplit(".", 1)[0] + "." if ip.count(".") == 3 else None


def ban_state(db, ip):
    row = db.execute("SELECT jail, action FROM f2b_events WHERE ip=? ORDER BY ts DESC LIMIT 1", (ip,)).fetchone()
    if row and row["action"] in ("Ban", "Restore Ban"):
        return f"banned ({row['jail']})"
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for cidr in active_subnet_bans():
        if address in ipaddress.ip_network(cidr):
            return f"subnet banned: {cidr}"
    return None


def request_ban(ip, reason):
    BAN_LOG_PATH.parent.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with open(BAN_LOG_PATH, "a") as f:
        f.write(f"{stamp} BAN {ip} {reason}\n")


def ensure_geo_db():
    if not GEO_TSV_PATH.exists():
        return None
    if GEO_DB_PATH.exists() and GEO_DB_PATH.stat().st_mtime >= GEO_TSV_PATH.stat().st_mtime:
        return GEO_DB_PATH
    tmp = GEO_DB_PATH.with_suffix(f".{os.getpid()}.tmp")
    geo = sqlite3.connect(tmp)
    geo.execute("CREATE TABLE ranges (start INTEGER PRIMARY KEY, end INTEGER, asn INTEGER, cc TEXT, org TEXT)")
    with gzip.open(GEO_TSV_PATH, "rt", encoding="utf-8", errors="replace") as f:
        rows = []
        for line in f:
            start, end, asn, cc, org = line.rstrip("\n").split("\t", 4)
            if asn != "0":
                rows.append((int(ipaddress.IPv4Address(start)), int(ipaddress.IPv4Address(end)), int(asn), cc, org))
    geo.executemany("INSERT OR REPLACE INTO ranges VALUES (?,?,?,?,?)", rows)
    geo.commit()
    geo.close()
    os.replace(tmp, GEO_DB_PATH)
    return GEO_DB_PATH


def flag(cc):
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc) if len(cc or "") == 2 and cc.isalpha() else "🏳"


def geo_lookup(ip):
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    path = ensure_geo_db()
    if not path or address.version != 4:
        return None
    geo = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    row = geo.execute("SELECT start, end, asn, cc, org FROM ranges WHERE start <= ? ORDER BY start DESC LIMIT 1",
                      (int(address),)).fetchone()
    geo.close()
    if not row or row[1] < int(address):
        return None
    org = row[4]
    lowered = org.lower()
    if any(k in lowered for k in HOSTING_KEYWORDS):
        kind = "data center/hosting"
    elif any(k in lowered for k in ACCESS_ISP_KEYWORDS):
        kind = "access provider (home/mobile)"
    else:
        kind = "unknown network type"
    return {"cc": row[3], "flag": flag(row[3]), "asn": row[2], "org": org, "kind": kind,
            "short": f"{flag(row[3])} {row[3]} · AS{row[2]} {org[:40]}",
            "label": f"{row[3]} · AS{row[2]} {org[:40]}"}


def reverse_dns(ip):
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(socket.gethostbyaddr, ip).result(timeout=PTR_TIMEOUT_SECONDS)[0]
    except (concurrent.futures.TimeoutError, OSError):
        return None
    finally:
        pool.shutdown(wait=False)


def ip_profile(db, ip):
    week_ago, day_ago = time.time() - 7 * 86400, time.time() - 86400
    history = db.execute(
        "SELECT sum(result!='accepted' AND ts>=:day) AS fails_24h, sum(result!='accepted') AS fails_7d, "
        "min(ts) AS first_seen FROM ssh_auth WHERE ip=:ip AND ts>=:week",
        {"ip": ip, "day": day_ago, "week": week_ago}).fetchone()
    users = [r["user"] for r in db.execute(
        "SELECT user, count(*) AS n FROM ssh_auth WHERE ip=? AND result!='accepted' AND ts>=? "
        "GROUP BY user ORDER BY n DESC LIMIT 5", (ip, week_ago))]
    logins = [f"{r['user']}×{r['n']}" for r in db.execute(
        "SELECT user, count(*) AS n FROM ssh_auth WHERE ip=? AND result='accepted' AND ts>=? GROUP BY user",
        (ip, week_ago))]
    bans = db.execute("SELECT count(*) FROM f2b_events WHERE ip=? AND action='Ban'", (ip,)).fetchone()[0]
    signatures = [r[0] for r in db.execute("SELECT DISTINCT signature FROM alerts WHERE src_ip=? OR dest_ip=?", (ip, ip))]
    lists = sorted({name for marker, name in BLOCKLIST_MARKERS.items() if any(marker in s for s in signatures)})
    return {"geo": geo_lookup(ip), "ptr": reverse_dns(ip), "fails_24h": history["fails_24h"] or 0,
            "fails_7d": history["fails_7d"] or 0, "first_seen": history["first_seen"], "users": users,
            "logins": logins, "bans": bans, "blocklists": lists}


def ip_profile_lines(profile):
    geo = profile["geo"]
    if geo:
        lines = [f"🌍 {geo['flag']} {geo['cc']} · AS{geo['asn']} {html_escape(geo['org'][:60])} · {geo['kind']}"]
    else:
        lines = ["🌍 country unknown"]
    if profile["ptr"]:
        lines.append(f"🔎 PTR: <code>{html_escape(profile['ptr'])}</code>")
    history = f"📜 7 days: {profile['fails_7d']} SSH failures (24 hours: {profile['fails_24h']})"
    if profile["users"]:
        history += f", usernames: {html_escape(', '.join(profile['users']))}"
    lines.append(history)
    if profile["logins"]:
        lines.append(f"🔑 previous logins: {html_escape(', '.join(profile['logins']))}")
    extras = []
    if profile["bans"]:
        extras.append(f"bans: {profile['bans']}")
    if profile["blocklists"]:
        extras.append(f"on blocklists: {', '.join(profile['blocklists'])}")
    if extras:
        lines.append("🚫 " + " · ".join(extras))
    return lines


def fingerprint(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()


def finding(ftype, severity, key, summary, **details):
    return {"type": ftype, "severity": severity, "fingerprint": fingerprint(ftype, key),
            "summary": summary, "details": details}


def alert_suppressed(row, patterns):
    peer = row["dest_ip"] if row["outbound"] else row["src_ip"]
    targets = [str(row["sid"])] + [f"{row['sid']}@{v}" for v in (peer, row["name"]) if v]
    return any(glob_match(t, patterns) for t in targets)


def detect_alerts(db, since, allow):
    suppress = allow["alerts"]["suppress"]
    inbound_max_severity = allow["alerts"]["escalate_inbound_severity"]
    rows = db.execute("SELECT * FROM alerts WHERE ts>=?", (since,)).fetchall()
    grouped = {}
    for row in rows:
        if alert_suppressed(row, suppress):
            continue
        if not row["outbound"] and row["severity"] > inbound_max_severity:
            continue
        peer = row["dest_ip"] if row["outbound"] else row["src_ip"]
        grouped.setdefault((row["sid"], peer, row["outbound"]), []).append(row)
    findings = []
    for (sid, peer, outbound), hits in grouped.items():
        first = hits[0]
        direction = "outbound" if outbound else "inbound"
        if outbound:
            severity = OUTBOUND_ALERT_SEVERITY.get(first["severity"], SEVERITY_LOW)
        else:
            severity = SEVERITY_LOW
        findings.append(finding(
            f"{direction}_alert", severity, (sid, peer),
            f"{first['signature']} ({direction}, peer {first['name'] or peer}, x{len(hits)})",
            sid=sid, peer=peer, peer_name=first["name"], count=len(hits),
            category=first["category"], ports=sorted({h["dest_port"] for h in hits})[:10]))
    return findings


def inbound_alert_summary(db, since):
    rows = db.execute(
        "SELECT signature, count(*) AS hits, count(DISTINCT src_ip) AS sources FROM alerts "
        "WHERE ts>=? AND outbound=0 GROUP BY signature ORDER BY hits DESC LIMIT 10", (since,))
    return [dict(r) for r in rows]


def new_keys(db, kind, since):
    return db.execute(
        "SELECT key, first_seen, hits FROM seen WHERE kind=? AND first_seen>=?", (kind, since)).fetchall()


def detect_novelty(db, since, allow):
    trusted = allow["destinations"]["trusted"]
    normal_ports = set(allow["ports"]["normal"])
    findings = []
    new_bins = {r["key"] for r in new_keys(db, "bin", since) if not binary_trusted(r["key"], allow)}
    for binary in new_bins:
        dests = [r["name"] or r["daddr"] for r in db.execute(
            "SELECT DISTINCT name, daddr FROM connects WHERE binary=? LIMIT 10", (binary,))]
        findings.append(finding(
            "new_binary", SEVERITY_MEDIUM, binary,
            f"New process talking to the internet: {binary}", binary=binary, destinations=dests))
    for row in new_keys(db, "bin_dest", since):
        binary, label = row["key"].split("|", 1)
        if domain_trusted(label, trusted):
            continue
        sample = db.execute(
            "SELECT * FROM connects WHERE binary=? AND (name LIKE ? OR daddr=?) ORDER BY ts LIMIT 1",
            (binary, f"%{label}", label)).fetchone()
        name = sample["name"] if sample else None
        no_dns = sample is not None and name is None
        findings.append(finding(
            "new_destination", SEVERITY_MEDIUM if (no_dns or binary in new_bins) else SEVERITY_LOW,
            row["key"], f"{binary} -> {name or label}" + (" (direct IP, no DNS/SNI)" if no_dns else ""),
            binary=binary, destination=label, name=name, no_dns=no_dns,
            container=sample["container"] if sample else None,
            args=sample["args"][:200] if sample else None,
            parent=sample["parent"] if sample else None))
    for row in new_keys(db, "dest_port", since):
        label, port_proto = row["key"].rsplit(":", 1)
        port = int(port_proto.split("/")[0])
        if port in normal_ports or domain_trusted(label, trusted):
            continue
        findings.append(finding(
            "unusual_port", SEVERITY_MEDIUM, row["key"],
            f"Outbound to unusual port {port_proto} at {label}", destination=label, port=port_proto))
    return findings


def binary_trusted(binary, allow):
    return glob_match(binary.removesuffix(" (deleted)"), allow["binaries"]["trusted"])


def detect_suspicious_paths(db, since, allow):
    findings = []
    rows = db.execute(
        "SELECT binary, container, group_concat(DISTINCT coalesce(name, daddr)) AS dests, count(*) AS n "
        "FROM connects WHERE ts>=? GROUP BY binary, container", (since,))
    for row in rows:
        binary = row["binary"]
        if binary_trusted(binary, allow):
            continue
        if binary.startswith(SUSPICIOUS_PATH_PREFIXES) or "(deleted)" in binary:
            findings.append(finding(
                "suspicious_binary_path", SEVERITY_HIGH, (binary, row["container"]),
                f"Binary from temp/volatile path connecting out: {binary}",
                binary=binary, container=row["container"], destinations=row["dests"], count=row["n"]))
    return findings


def detect_uploads(db, since, allow):
    thresholds = allow["thresholds"]
    trusted = allow["destinations"]["trusted"]
    findings = []
    for row in db.execute(
            "SELECT coalesce(name, dest_ip) AS dest, dest_ip, sum(bytes_out) AS out, max(bytes_out) AS biggest, "
            "count(*) AS flows FROM flows WHERE ts>=? GROUP BY dest", (since,)):
        if domain_trusted(row["dest"], trusted):
            continue
        if row["biggest"] >= thresholds["upload_bytes_per_flow"] or \
                row["out"] >= thresholds["upload_bytes_per_dest_window"]:
            findings.append(finding(
                "large_upload", SEVERITY_MEDIUM, (row["dest"], int(since // 3600)),
                f"{row['out'] / 1e6:.1f} MB sent to {row['dest']} in {row['flows']} flows",
                destination=row["dest"], ip=row["dest_ip"], bytes_out=row["out"], flows=row["flows"]))
    return findings


def failures_before(db, ip, until):
    since = until - SSH_HISTORY_SECONDS
    by_ip = db.execute(
        "SELECT count(*) FROM ssh_auth WHERE ip=? AND result!='accepted' AND ts>=? AND ts<?",
        (ip, since, until)).fetchone()[0]
    prefix = subnet_prefix(ip)
    by_subnet = db.execute(
        "SELECT count(*) FROM ssh_auth WHERE ip LIKE ? AND result!='accepted' AND ts>=? AND ts<?",
        (prefix + "%", since, until)).fetchone()[0] if prefix else 0
    return by_ip, by_subnet


def auth_message(title, urgent, lines):
    head = "🚨 <b>URGENT · " if urgent else "⚠️ <b>"
    return head + html_escape(title) + "</b>\n" + "\n".join(lines)


def html_escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def auth_check(db, window_seconds, mark):
    cfg = load_allowlist()["ssh"]
    protected = set(cfg["protected_users"])
    since = time.time() - window_seconds
    extend_dynamic_trust(db, since)
    trusted = load_trusted_networks()
    trust_hint = "“This is me” trusts the IP for a week and lifts the ban; “Ban” bans it for four weeks"
    findings = []

    for row in db.execute("SELECT * FROM ssh_auth WHERE result='accepted' AND ts>=? ORDER BY ts", (since,)).fetchall():
        ip, user = row["ip"], row["user"]
        if is_trusted(ip, trusted):
            continue
        by_ip, by_subnet = failures_before(db, ip, row["ts"])
        after_bruteforce = by_ip >= cfg["bruteforce_ip_failures"] or by_subnet >= cfg["bruteforce_subnet_failures"]
        when = datetime.fromtimestamp(row["ts"], timezone.utc).strftime("%H:%M:%S UTC")
        lines = [f"<code>{html_escape(user)}</code> ← <code>{html_escape(ip)}</code> ({html_escape(row['method'])}), {when}",
                 f"Before the login, in 24 hours: {by_ip} failures from this IP, {by_subnet} from its /24"]
        if after_bruteforce:
            lines.append("Ban requested (netmon-ban, 4 weeks)")
        lines.append(html_escape(trust_hint))
        # One alert per IP and user per dedup window: a single session opens many SSH connections.
        f = finding("ssh_login_after_bruteforce" if after_bruteforce else "ssh_login_untrusted",
                    SEVERITY_HIGH, (ip, user),
                    f"SSH login {user} from untrusted {ip}" + (" after brute force" if after_bruteforce else ""),
                    ip=ip, user=user, method=row["method"], failures_ip=by_ip, failures_subnet=by_subnet,
                    ban=after_bruteforce)
        f["urgent"] = after_bruteforce
        f["telegram"] = auth_message("SSH: login after password guessing" if after_bruteforce
                                     else "SSH: login from an unfamiliar IP", after_bruteforce, lines)
        findings.append(f)

    placeholders = ",".join("?" * len(protected))
    rows = db.execute(
        f"SELECT ip, user, count(*) AS n, sum(result='failed' AND method='password') AS passwords "
        f"FROM ssh_auth WHERE result!='accepted' AND ts>=? AND user IN ({placeholders}) GROUP BY ip, user",
        (since, *protected)).fetchall()
    for row in rows:
        ip, user = row["ip"], row["user"]
        if is_trusted(ip, trusted):
            continue
        passwords_24h = db.execute(
            "SELECT count(*) FROM ssh_auth WHERE ip=? AND user=? AND result='failed' AND method='password' AND ts>=?",
            (ip, user, time.time() - SSH_HISTORY_SECONDS)).fetchone()[0]
        bruteforce = passwords_24h >= cfg["protected_failures_to_ban"]
        state = ban_state(db, ip)
        lines = [f"<code>{html_escape(user)}</code> ← <code>{html_escape(ip)}</code>: {row['n']} attempts now, "
                 f"{passwords_24h} wrong passwords in 24 hours",
                 f"Ban: {state or ('requested (netmon-ban, 4 weeks)' if bruteforce else 'no, single attempt')}",
                 html_escape(trust_hint)]
        f = finding("ssh_protected_bruteforce" if bruteforce else "ssh_protected_attempt",
                    SEVERITY_HIGH, (ip, user),
                    f"SSH {'password brute force' if bruteforce else 'login attempt'} on {user} from {ip}",
                    ip=ip, user=user, attempts=row["n"], passwords_24h=passwords_24h, ban=bruteforce)
        f["urgent"] = bruteforce
        f["telegram"] = auth_message(f"SSH: password guessing against {user}" if bruteforce
                                     else f"SSH: login attempt as {user}", bruteforce, lines)
        findings.append(f)

    for f in findings:
        f["reply_markup"] = {"inline_keyboard": [[
            {"text": "✅ This is me — trust for 7 days", "callback_data": f"trust:{f['details']['ip']}"},
            {"text": "⛔ Ban", "callback_data": f"ban:{f['details']['ip']}"}]]}
    dedup_since = time.time() - SSH_LOGIN_DEDUP_HOURS * 3600
    unique = {f["fingerprint"]: f for f in findings}.values()
    fresh = [f for f in unique if not db.execute(
        "SELECT 1 FROM reported WHERE fingerprint=? AND ts>=?", (f["fingerprint"], dedup_since)).fetchone()]
    for f in fresh:
        f["telegram"] += "\n" + "\n".join(ip_profile_lines(ip_profile(db, f["details"]["ip"])))
    if mark:
        for f in fresh:
            if f["details"].get("ban"):
                request_ban(f["details"]["ip"], f["type"])
        db.executemany("INSERT INTO reported VALUES (?,?) ON CONFLICT(fingerprint) DO UPDATE SET ts=excluded.ts",
                       [(f["fingerprint"], time.time()) for f in fresh])
        db.commit()
    return {"findings": fresh}


def with_geo(db, rows, ip_key="ip"):
    for row in rows:
        row["geo"] = (geo_lookup(row[ip_key]) or {}).get("label")
        row["ban"] = ban_state(db, row[ip_key])
    return rows


def ssh_summary(db, since):
    trusted = load_trusted_networks()
    protected = load_allowlist()["ssh"]["protected_users"]
    logins = [dict(r) for r in db.execute(
        "SELECT user, ip, method, count(*) AS n, max(ts) AS last FROM ssh_auth WHERE result='accepted' AND ts>=? "
        "GROUP BY user, ip, method ORDER BY last DESC LIMIT 20", (since,))]
    for login in logins:
        login["trusted"] = is_trusted(login["ip"], trusted)
        login["geo"] = (geo_lookup(login["ip"]) or {}).get("label")
    bruteforce = [dict(r) for r in db.execute(
        "SELECT ip, count(*) AS failures, count(DISTINCT user) AS users, "
        "group_concat(DISTINCT user) AS sample_users FROM ssh_auth WHERE result!='accepted' AND ts>=? "
        "GROUP BY ip ORDER BY failures DESC LIMIT 12", (since,))]
    for row in bruteforce:
        row["sample_users"] = ", ".join([u for u in (row["sample_users"] or "").split(",") if u][:6])
        row["ban"] = ban_state(db, row["ip"])
        row["geo"] = (geo_lookup(row["ip"]) or {}).get("label")
    placeholders = ",".join("?" * len(protected))
    protected_rows = [dict(r) for r in db.execute(
        f"SELECT user, ip, count(*) AS failures FROM ssh_auth WHERE result!='accepted' AND ts>=? "
        f"AND user IN ({placeholders}) GROUP BY user, ip ORDER BY failures DESC LIMIT 20", (since, *protected))]
    for row in protected_rows:
        row["trusted"] = is_trusted(row["ip"], trusted)
        row["geo"] = (geo_lookup(row["ip"]) or {}).get("label")
        row["ban"] = ban_state(db, row["ip"])
    totals = dict(db.execute(
        "SELECT count(*) AS failures, count(DISTINCT ip) AS ips FROM ssh_auth "
        "WHERE result!='accepted' AND ts>=?", (since,)).fetchone())
    totals["bans"] = db.execute(
        "SELECT count(*) FROM f2b_events WHERE action='Ban' AND ts>=?", (since,)).fetchone()[0]
    return {"totals": totals, "logins": logins, "bruteforce": bruteforce, "protected": protected_rows}


DEFEND_SCHEMA = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "subnet": {"type": "string"},
            "action": {"type": "string", "enum": ["ban_subnet", "ask", "ignore"]},
            "reason": {"type": "string"}},
        "required": ["subnet", "action", "reason"]}}},
    "required": ["decisions"],
}


def subnet_candidates(db):
    week_ago = time.time() - 7 * 86400
    trusted = load_trusted_networks()
    banned = [ipaddress.ip_network(c) for c in active_subnet_bans()]
    host_ips = [ipaddress.ip_address(ip) for ip in HOST_ADDRESSES]
    per_subnet = {}
    for row in db.execute(
            "SELECT ip, count(*) AS fails, count(DISTINCT user) AS users FROM ssh_auth "
            "WHERE result!='accepted' AND ts>=? GROUP BY ip HAVING fails>=?", (week_ago, SUBNET_MIN_FAILURES_PER_IP)):
        if not parse_ban_target(row["ip"]):
            continue
        subnet = str(ipaddress.ip_network(f"{row['ip']}/24", strict=False))
        per_subnet.setdefault(subnet, []).append(dict(row))
    candidates = []
    review_since = time.time() - SUBNET_REVIEW_HOURS * 3600
    for subnet, ips in per_subnet.items():
        network = ipaddress.ip_network(subnet)
        failures = sum(r["fails"] for r in ips)
        if len(ips) < SUBNET_MIN_IPS or failures < SUBNET_MIN_FAILURES:
            continue
        if any(network.overlaps(n) for n in trusted + banned) or any(a in network for a in host_ips):
            continue
        prefix = subnet.rsplit(".", 1)[0] + "."
        if db.execute("SELECT 1 FROM ssh_auth WHERE result='accepted' AND ip LIKE ? LIMIT 1", (prefix + "%",)).fetchone():
            continue
        if db.execute("SELECT 1 FROM reported WHERE fingerprint=? AND ts>=?",
                      (fingerprint("subnet-review", subnet), review_since)).fetchone():
            continue
        geo = geo_lookup(ips[0]["ip"]) or {}
        users = [r["user"] for r in db.execute(
            "SELECT user, count(*) AS n FROM ssh_auth WHERE ip LIKE ? AND result!='accepted' AND ts>=? "
            "GROUP BY user ORDER BY n DESC LIMIT 8", (prefix + "%", week_ago))]
        candidates.append({
            "subnet": subnet, "failures_7d": failures, "ips": [
                {"ip": r["ip"], "failures": r["fails"], "users": r["users"], "ban": ban_state(db, r["ip"])}
                for r in sorted(ips, key=lambda r: -r["fails"])],
            "country": geo.get("cc"), "asn": geo.get("asn"), "org": geo.get("org"), "network_kind": geo.get("kind"),
            "top_users": users})
    return candidates


def claude_decide(prompt_path, data, schema):
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        return None
    prompt = Path(prompt_path).read_text() + "\n<netmon_data>\n" + json.dumps(data, ensure_ascii=False) + "\n</netmon_data>"
    out = subprocess.run(
        [os.environ.get("CLAUDE_BIN", "claude"), "-p", prompt, "--model", CLAUDE_MODEL, "--restricted",
         "--tools", "", "--strict-mcp-config", "--output-format", "json", "--json-schema", json.dumps(schema)],
        capture_output=True, text=True, timeout=300, cwd=DATA_DIR).stdout
    result = json.loads(out)
    result = result[-1] if isinstance(result, list) else result
    return result.get("structured_output") or json.loads(result["result"])


def subnet_question(candidate, reason):
    ips = candidate["ips"]
    lines = [f"❓ <b>What should we do with subnet {candidate['subnet']}?</b>",
             f"🌍 {flag(candidate['country'] or '')} {html_escape(candidate['country'] or '?')} · "
             f"AS{candidate['asn']} {html_escape((candidate['org'] or '')[:50])} · {html_escape(candidate['network_kind'] or '')}",
             f"📜 7 days: {candidate['failures_7d']} SSH failures from {len(ips)} IP, usernames: "
             f"{html_escape(', '.join(candidate['top_users'][:6]))}",
             "No successful logins from this subnet.",
             f"🤖 {html_escape(reason)}"]
    options = [
        {"label": f"⛔ Subnet {candidate['subnet']} for 4 weeks", "kind": "ban_subnet", "cidr": candidate["subnet"]},
        {"label": f"⛔ Only these {len(ips)} IP", "kind": "ban_ips", "ips": [r["ip"] for r in ips]},
        {"label": "🙈 Ignore", "kind": "ignore"},
    ]
    return "\n".join(lines), options


def defend(db, mark):
    candidates = subnet_candidates(db)
    if not candidates:
        return {"candidates": 0, "decisions": []}
    by_subnet = {c["subnet"]: c for c in candidates}
    verdict = claude_decide(ROOT / "prompts" / "defend.md", {"candidates": candidates}, DEFEND_SCHEMA)
    decisions = verdict["decisions"] if verdict else [
        {"subnet": c["subnet"], "action": "ask", "reason": "Claude unavailable; your decision is needed"} for c in candidates]
    applied, banned = [], []
    for decision in decisions:
        candidate = by_subnet.pop(decision.get("subnet"), None)
        if not candidate:
            continue
        action, reason = decision["action"], decision["reason"]
        if mark:
            if action == "ban_subnet" and ban_subnet(candidate["subnet"], f"ai: {reason}"[:200]):
                banned.append((candidate, reason))
            elif action == "ask":
                ask_question(*subnet_question(candidate, reason))
            db.execute("INSERT INTO reported VALUES (?,?) ON CONFLICT(fingerprint) DO UPDATE SET ts=excluded.ts",
                       (fingerprint("subnet-review", candidate["subnet"]), time.time()))
        applied.append({**decision, "failures_7d": candidate["failures_7d"], "ips": len(candidate["ips"])})
    if mark and banned:
        lines = [f"⛔ <b>AI banned {len(banned)} subnets for 4 weeks</b> (SSH brute force)"]
        for candidate, reason in banned:
            lines.append(f"\n<code>{candidate['subnet']}</code> {flag(candidate['country'] or '')} "
                         f"AS{candidate['asn']} {html_escape((candidate['org'] or '')[:30])}\n"
                         f"   {candidate['failures_7d']} failures from {len(candidate['ips'])} IP · {html_escape(reason)}")
        telegram_say("\n".join(lines), {"inline_keyboard": [
            [{"text": f"↩️ Unban {c['subnet']}", "callback_data": f"unban:{c['subnet']}"}] for c, _ in banned]})
    if mark:
        for leftover in by_subnet:
            db.execute("INSERT INTO reported VALUES (?,?) ON CONFLICT(fingerprint) DO UPDATE SET ts=excluded.ts",
                       (fingerprint("subnet-review", leftover), time.time()))
        db.commit()
    return {"candidates": len(candidates), "decisions": applied}


def in_baseline(db, allow):
    start = float(db.execute("SELECT value FROM meta WHERE key='baseline_start'").fetchone()["value"])
    return time.time() < start + allow["thresholds"]["baseline_hours"] * 3600


def check(db, window_seconds, mark):
    allow = load_allowlist()
    since = time.time() - window_seconds
    findings = detect_alerts(db, since, allow) + detect_suspicious_paths(db, since, allow) \
        + detect_web_exposure(db, since)
    learning = in_baseline(db, allow)
    if not learning:
        findings += detect_novelty(db, since, allow) + detect_uploads(db, since, allow)
    dedup_since = time.time() - CHECK_DEDUP_HOURS * 3600
    fresh = [f for f in findings if not db.execute(
        "SELECT 1 FROM reported WHERE fingerprint=? AND ts>=?", (f["fingerprint"], dedup_since)).fetchone()]
    if mark:
        db.executemany("INSERT INTO reported VALUES (?,?) ON CONFLICT(fingerprint) DO UPDATE SET ts=excluded.ts",
                       [(f["fingerprint"], time.time()) for f in fresh])
        db.commit()
    order = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    fresh.sort(key=lambda f: order[f["severity"]])
    return {"window_seconds": window_seconds, "baseline_learning": learning,
            "findings": fresh, "inbound_alerts": inbound_alert_summary(db, since)}


def summary(db, window_seconds):
    since = time.time() - window_seconds

    def rows(sql):
        return [dict(r) for r in db.execute(sql, {"since": since})]

    return {
        "window_seconds": window_seconds,
        "totals": rows(
            "SELECT (SELECT count(*) FROM connects WHERE ts>=:since) AS tcp_connects, "
            "(SELECT count(*) FROM flows WHERE ts>=:since) AS outbound_flows, "
            "(SELECT sum(bytes_out) FROM flows WHERE ts>=:since) AS bytes_out, "
            "(SELECT count(*) FROM alerts WHERE ts>=:since AND outbound=1) AS outbound_alerts, "
            "(SELECT count(*) FROM alerts WHERE ts>=:since AND outbound=0) AS inbound_alerts")[0],
        "top_processes": rows(
            "SELECT binary, container, count(*) AS connects, count(DISTINCT coalesce(name, daddr)) AS dests "
            "FROM connects WHERE ts>=:since GROUP BY binary, container ORDER BY connects DESC LIMIT 20"),
        "top_upload_destinations": rows(
            "SELECT coalesce(name, dest_ip) AS dest, sum(bytes_out) AS bytes_out, count(*) AS flows "
            "FROM flows WHERE ts>=:since GROUP BY dest ORDER BY bytes_out DESC LIMIT 15"),
        "new_destinations": rows(
            "SELECT key, datetime(first_seen, 'unixepoch') AS first_seen, hits FROM seen "
            "WHERE kind='bin_dest' AND first_seen>=:since ORDER BY first_seen LIMIT 50"),
        "outbound_alerts": rows(
            "SELECT a.signature, min(a.severity) AS severity, group_concat(DISTINCT coalesce(a.name, a.dest_ip)) AS dests, "
            "group_concat(DISTINCT a.dest_ip) AS dest_ips, count(*) AS hits, "
            "(SELECT group_concat(DISTINCT c.binary || ' @ ' || c.container) FROM connects c, alerts a2 "
            " WHERE a2.signature = a.signature AND a2.outbound = 1 AND a2.ts >= :since AND c.daddr = a2.dest_ip "
            f" AND abs(c.ts - a2.ts) <= {ALERT_ATTRIBUTION_SECONDS}) AS processes "
            "FROM alerts a WHERE a.ts>=:since AND a.outbound=1 "
            "GROUP BY a.signature ORDER BY severity, hits DESC LIMIT 20"),
        "inbound_alerts": inbound_alert_summary(db, since),
        "ssh": ssh_summary(db, since),
        "web_probes": with_geo(db, rows(
            "SELECT ip, count(*) AS probes, count(DISTINCT host) AS hosts, group_concat(DISTINCT host) AS host_list, "
            "sum(status=200) AS ok, group_concat(DISTINCT uri) AS uris FROM web_probes WHERE ts>=:since "
            "GROUP BY ip ORDER BY probes DESC LIMIT 15")),
        "inbound_sources": with_geo(db, rows(
            "SELECT src_ip AS ip, count(*) AS hits, count(DISTINCT signature) AS signatures, "
            "group_concat(DISTINCT signature) AS sample FROM alerts WHERE ts>=:since AND outbound=0 "
            "GROUP BY src_ip ORDER BY hits DESC LIMIT 12")),
        "direct_ip_connects": rows(
            "SELECT binary, container, daddr, dport, count(*) AS n FROM connects "
            "WHERE ts>=:since AND name IS NULL GROUP BY binary, daddr, dport ORDER BY n DESC LIMIT 20"),
    }


SKIPPED_PATH_SEGMENTS = {"bin", "sbin", "versions", "releases", "exe"}


def short_process_name(binary):
    marker = ""
    for suffix in (" (deleted)", " (self)"):
        if binary.endswith(suffix):
            binary, marker = binary[: -len(suffix)], suffix
    for segment in reversed(binary.split("/")):
        if segment and "*" not in segment and segment not in SKIPPED_PATH_SEGMENTS:
            return segment + marker
    return binary + marker


TIMELINE_BUCKETS = 48
FLOW_GRAPH_PROCESSES = 8
FLOW_GRAPH_DESTINATIONS = 10


def cached_ptrs(db, ips):
    now = time.time()
    result = {r["ip"]: r["ptr"] for r in db.execute(
        f"SELECT ip, ptr FROM ptr_cache WHERE ts>=? AND ip IN ({','.join('?' * len(ips))})",
        (now - PTR_CACHE_SECONDS, *ips))} if ips else {}
    missing = [ip for ip in ips if ip not in result]
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=PTR_WORKERS) as pool:
            for ip, ptr in zip(missing, pool.map(reverse_dns, missing)):
                result[ip] = ptr
                db.execute("INSERT OR REPLACE INTO ptr_cache VALUES (?,?,?)", (ip, ptr, now))
        db.commit()
    return result


def ip_infos(db, ips):
    ips = [ip for ip in dict.fromkeys(ips) if ip and is_ip_literal(ip)][:IP_INFO_LIMIT]
    ptrs = cached_ptrs(db, ips)
    infos = {}
    for ip in ips:
        geo = geo_lookup(ip) or {}
        names = [r["name"] for r in db.execute(
            "SELECT name FROM names WHERE ip=? ORDER BY ts DESC LIMIT 5", (ip,))]
        infos[ip] = {"cc": geo.get("cc"), "flag": geo.get("flag"), "asn": geo.get("asn"),
                     "org": (geo.get("org") or "")[:40], "kind": geo.get("kind"),
                     "ptr": ptrs.get(ip), "domains": names}
    return infos


EPHEMERAL_CONTAINER = re.compile(r"^[0-9a-f]{12}$")


def summarize_processes(raw):
    """'bin @ container,...' -> 'ssh · terminal +55 temporary; git-remote-https · terminal +50 temporary'."""
    by_binary = {}
    for item in (raw or "").split(","):
        binary, _, container = item.partition(" @ ")
        if binary:
            by_binary.setdefault(short_process_name(binary), set()).add(container)
    parts = []
    for name, containers in by_binary.items():
        named = sorted(c for c in containers if not EPHEMERAL_CONTAINER.match(c))
        temporary = len(containers) - len(named)
        label = f"{name} · {', '.join(named[:3])}" if named else name
        parts.append(label + (f" +{temporary} temporary" if temporary else ""))
    return "; ".join(parts)


def report_data(db, window_seconds):
    data = summary(db, window_seconds)
    with_geo(db, data["direct_ip_connects"], ip_key="daddr")
    now = time.time()
    since = now - window_seconds
    bucket = max(60, window_seconds // TIMELINE_BUCKETS)
    timeline = {}
    for row in db.execute(
            "SELECT CAST((ts - :since) / :bucket AS INTEGER) AS b, sum(bytes_out) AS out, count(*) AS flows "
            "FROM flows WHERE ts>=:since GROUP BY b", {"since": since, "bucket": bucket}):
        timeline.setdefault(row["b"], {})["bytes_out"] = row["out"]
    for row in db.execute(
            "SELECT CAST((ts - :since) / :bucket AS INTEGER) AS b, count(*) AS n "
            "FROM connects WHERE ts>=:since GROUP BY b", {"since": since, "bucket": bucket}):
        timeline.setdefault(row["b"], {})["connects"] = row["n"]
    for row in db.execute(
            "SELECT CAST((ts - :since) / :bucket AS INTEGER) AS b, count(*) AS n "
            "FROM alerts WHERE ts>=:since AND outbound=1 GROUP BY b", {"since": since, "bucket": bucket}):
        timeline.setdefault(row["b"], {})["alerts"] = row["n"]
    buckets = int(window_seconds // bucket) + 1
    data["timeline"] = {
        "start": since, "bucket_seconds": bucket,
        "points": [{"bytes_out": timeline.get(i, {}).get("bytes_out", 0),
                    "connects": timeline.get(i, {}).get("connects", 0),
                    "alerts": timeline.get(i, {}).get("alerts", 0)} for i in range(buckets)],
    }
    edges = [dict(r) for r in db.execute(
        "SELECT binary AS process, container, coalesce(name, daddr) AS dest, count(*) AS n "
        "FROM connects WHERE ts>=? GROUP BY binary, container, dest", (since,))]
    process_totals, dest_totals = {}, {}
    for e in edges:
        e["process_key"] = f"{short_process_name(e['process'])} · {e['container']}"
        process_totals[e["process_key"]] = process_totals.get(e["process_key"], 0) + e["n"]
        dest_totals[e["dest"]] = dest_totals.get(e["dest"], 0) + e["n"]
    top_processes = sorted(process_totals, key=process_totals.get, reverse=True)[:FLOW_GRAPH_PROCESSES]
    top_dests = sorted(dest_totals, key=dest_totals.get, reverse=True)[:FLOW_GRAPH_DESTINATIONS]
    data["flow_graph"] = {
        "processes": [{"key": k, "n": process_totals[k]} for k in top_processes],
        "destinations": [{"key": k, "n": dest_totals[k]} for k in top_dests],
        "edges": [{"from": e["process_key"], "to": e["dest"], "n": e["n"]} for e in edges
                  if e["process_key"] in top_processes and e["dest"] in top_dests],
    }
    data["totals"]["bytes_in"] = db.execute(
        "SELECT sum(bytes_in) FROM flows WHERE ts>=?", (since,)).fetchone()[0]
    for row in data["outbound_alerts"]:
        row["processes"] = summarize_processes(row["processes"])
    ips = [r["ip"] for key in ("logins", "protected", "bruteforce") for r in data["ssh"][key]]
    ips += [r["ip"] for r in data["inbound_sources"]] + [r["daddr"] for r in data["direct_ip_connects"]]
    ips += [r["ip"] for r in data["web_probes"]]
    ips += [ip for r in data["outbound_alerts"] for ip in (r["dest_ips"] or "").split(",")]
    ips += [r["dest"] for r in data["top_upload_destinations"]]
    data["ip_info"] = ip_infos(db, ips)
    verify_web_exposure(db, since)
    data["web_exposure"] = exposure_rows(db, since)
    verdicts = {}
    for row in data["web_exposure"]:
        for ip in (row["ips"] or "").split(","):
            verdicts.setdefault(ip, {}).setdefault(row["verdict"], 0)
            verdicts[ip][row["verdict"]] += 1
    for row in data["web_probes"]:
        row["exposure"] = verdicts.get(row["ip"], {})
    data["generated_at"] = now
    return data


def prune(db):
    cutoff = time.time() - DB_RETENTION_DAYS * 86400
    for table in ("connects", "flows", "alerts", "http", "names", "reported", "ssh_auth", "f2b_events", "web_probes", "web_exposure"):
        db.execute(f"DELETE FROM {table} WHERE ts<?", (cutoff,))
    eve_cutoff = time.time() - EVE_RETENTION_DAYS * 86400
    removed = 0
    for path in glob.glob(EVE_GLOB):
        if os.stat(path).st_mtime < eve_cutoff:
            os.remove(path)
            removed += 1
    live = {f"eve:{os.stat(p).st_ino}" for p in glob.glob(EVE_GLOB)}
    for row in db.execute("SELECT key FROM offsets WHERE key LIKE 'eve:%'").fetchall():
        if row["key"] not in live:
            db.execute("DELETE FROM offsets WHERE key=?", (row["key"],))
    db.commit()
    db.execute("VACUUM")
    return {"eve_files_removed": removed}


def run_sql(query):
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(query).fetchall()]


def main():
    parser = argparse.ArgumentParser(description="Host egress monitor over Suricata + Tetragon logs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    p_check = sub.add_parser("check")
    p_check.add_argument("--since", default="15m")
    p_check.add_argument("--no-mark", action="store_true", help="do not record findings as reported")
    p_summary = sub.add_parser("summary")
    p_summary.add_argument("--since", default="24h")
    p_report = sub.add_parser("report-data")
    p_report.add_argument("--since", default="24h")
    sub.add_parser("prune")
    p_defend = sub.add_parser("defend")
    p_defend.add_argument("--no-mark", action="store_true", help="only print candidates and decisions")
    sub.add_parser("restore-bans")
    p_auth = sub.add_parser("auth-check")
    p_auth.add_argument("--since", default=AUTH_CHECK_WINDOW)
    p_auth.add_argument("--no-mark", action="store_true", help="do not record findings or request bans")
    p_sql = sub.add_parser("sql")
    p_sql.add_argument("query")
    args = parser.parse_args()

    if args.cmd == "sql":
        result = run_sql(args.query)
    else:
        db = connect_db()
        if args.cmd == "ingest":
            result = ingest(db)
        elif args.cmd == "check":
            ingest(db)
            result = check(db, parse_duration(args.since), mark=not args.no_mark)
            result["ids_bans"] = ids_bans(db, mark=not args.no_mark)
        elif args.cmd == "summary":
            ingest(db)
            result = summary(db, parse_duration(args.since))
        elif args.cmd == "defend":
            ingest_host_logs(db)
            result = defend(db, mark=not args.no_mark)
        elif args.cmd == "restore-bans":
            result = {"restored": restore_subnet_bans()}
        elif args.cmd == "auth-check":
            ingest_host_logs(db)
            ingest_web_logs(db)
            result = auth_check(db, parse_duration(args.since), mark=not args.no_mark)
            result["web_bans"] = web_probe_bans(db, mark=not args.no_mark)
        elif args.cmd == "report-data":
            ingest(db)
            result = report_data(db, parse_duration(args.since))
        else:
            result = prune(db)
    json.dump(result, sys.stdout, indent=1, default=str, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
