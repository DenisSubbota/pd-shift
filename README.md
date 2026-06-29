# pd-shift

PagerDuty shift helper for Percona MySQL DBAs — list open alerts, ack, rename, merge, and pull alert history.

Designed for the **MySQL Managed Services** team in PagerDuty. All commands scope to your configured team unless you pass `--team` or omit `team_id` (then the whole account is searched — slow and noisy).

**Requires Python 3.10+.** On macOS, `python` is often **2.7** and `python3` may be older than 3.10 — **do not remove system Python**. Use Homebrew's 3.12 explicitly.

## Quick start

```bash
git clone git@github.com:DenisSubbota/pd-shift.git
cd pd-shift

# macOS: install once if needed, then always use python3.12 (not `python` or bare `python3`)
brew install python@3.12
python3.12 --version    # must show 3.10+

rm -rf venv             # if you already created a venv with the wrong Python
python3.12 -m venv venv
./venv/bin/python --version   # should also be 3.12.x
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -e .

# optional: shell alias
alias pd="$PWD/venv/bin/pd"
```

**Do not use `python -m venv`** — on many Macs that is Python 2.7. You do not need to uninstall 2.7; just call `python3.12` by name.

HTTPS clone:

```bash
git clone https://github.com/DenisSubbota/pd-shift.git
```

1. Create a PagerDuty API token (below).
2. Find your team id (below).
3. Write `~/.config/pd-shift/conf` (never commit this file).
4. Run `pd list`.

---

## PagerDuty credentials

### 1. API token (user token)

Use a **User API Token** — not an account-level REST API key unless you know you need one.

1. Log in to PagerDuty (`https://your-org.pagerduty.com`).
2. Click your avatar (top right) → **My Profile**.
3. **User Settings** tab → **Create API User Token**.
4. Name it e.g. `pd-shift`, copy the token (shown once).

Put it in config as `token=` or export `PD_TOKEN`.

Account REST keys also work but require `from_email=` in config; user tokens do not.

### 2. Team id (`MySQL Managed Services`)

Commands filter open incidents and stats history to one team. For Percona MS DBAs this is usually **MySQL Managed Services**.

**From the PagerDuty UI**

1. Go to **People** → **Teams** (or **Configuration** → **Teams**, depending on UI version).
2. Open **MySQL Managed Services**.
3. Look at the browser URL — the team id is the segment starting with `P`:

```text
https://your-org.pagerduty.com/teams/PXXXXXX
                                   ^^^^^^^^  ← this is team_id
```

**From the API** (if you already have a token):

```bash
curl -s -H "Authorization: Token token=$PD_TOKEN" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  "https://api.pagerduty.com/teams?query=MySQL%20Managed%20Services" \
  | python3 -c "import sys,json; t=json.load(sys.stdin)['teams']; print(t[0]['id'], t[0]['name']) if t else print('not found')"
```

Put the id in config as `team_id=` or export `PD_TEAM_ID`.

---

## Config file (recommended)

```bash
mkdir -p ~/.config/pd-shift
chmod 700 ~/.config/pd-shift

cat > ~/.config/pd-shift/conf <<'EOF'
token=YOUR_PD_USER_TOKEN
team_id=PTEAMID_FOR_MYSQL_MANAGED_SERVICES
# from_email=you@example.com   # only for account REST keys, not user tokens
EOF

chmod 600 ~/.config/pd-shift/conf
```

Show the config path:

```bash
pd config-path
```

**Priority:** environment variables override the file.

| Variable      | Config key  | Purpose                               |
|---------------|-------------|---------------------------------------|
| `PD_TOKEN`    | `token`     | PagerDuty API token                   |
| `PD_TEAM_ID`  | `team_id`   | Team id (MySQL Managed Services)      |
| `PD_FROM`     | `from_email`| Your email — account REST keys only   |

```bash
export PD_TOKEN="..."
export PD_TEAM_ID="PXXXXXX"
```

If `team_id` / `PD_TEAM_ID` is missing, `pd list` and `pd stats` warn and query **all open incidents in the account** — avoid that on shift.

---

## Commands

### List open alerts

```bash
pd list              # INC - customer - description
pd list -t           # + TRIGGERED column, sorted newest first
pd list --mine       # only incidents assigned to you
```

```text
TICKET       CUSTOMER      TRIGGERED  DESCRIPTION
INC0011223   Zephyr Labs   2d ago     Disk Space Low - zephyr-db-01
INC0044556   Northwind LLC 4h ago     Replication Lag - db-replica-2
```

- **Grey** — headers · **Red** — triggered · **White** — acknowledged
- Rows with **INC** grouped by customer at top; rows without INC at bottom
- `INC*` from ServiceNow **Linked Records** in incident metadata
- Customer from `[brackets]` in title or service name

Default scope is **all open incidents for your team**, not just yours. Use `--mine` to narrow.

### Acknowledge

```bash
pd ack                    # all triggered for the team
pd ack INC0011223         # one by ServiceNow INC
pd ack 123456              # one by PD incident number
pd ack --dry-run
```

### Rename title

Strips `Percona_MS_* - CRITICAL/WARNING -` noise so the PD title matches the **DESCRIPTION** column in `pd list`.

```bash
pd rename INC0011223
pd rename INC0011223 -d "MySQL Threads Running - zephyr-db-01"
pd rename INC0011223 --dry-run
```

### Merge (same customer only)

```bash
pd merge INC0011223 INC0044556
pd merge INC0011223 INC0044556 --dry-run
pd merge --example          # sample interactive session
```

### Alert history (`pd stats`)

Same **customer + cleaned description** (e.g. `MySQL Threads Running - db1`). Default **60 days**, notes **on** (`--no-notes` to skip). History is per **PD service** for that customer, not the whole team.

```bash
pd stats 123456             # fast — use PD incident number when you can
pd stats INC0011223         # fast if alert is still open
pd stats INC0011223 --yes   # slow INC search when not open (many API calls)
pd stats 123456 --no-notes
pd stats 123456 --days 30
```

```text
INC0011223  —  MySQL Threads Running - db1  (Zephyr Labs)

INC          STARTED              RESOLVED             DURATION
INC0011223   2026-06-27 08:12 UTC 2026-06-27 10:05 UTC 1h 53m
INC0011180   2026-06-25 03:41 UTC —                    open (4h)

Last 60 days:  9 incidents  (~1.1/week)
Avg duration:   1h 22m  (8 resolved)
Typical fire:   03:00–05:00 UTC  (6/9)
Last seen:      2d ago
Current:        triggered (4h)
```

**Resolved INC not open?** The tool stops with a hint — find **Incidents → Incident #123456** in PagerDuty and run `pd stats 123456`. Notes are plain text (HTML and ServiceNow URLs stripped).

### Debug

```bash
pd inspect PXXXXXX          # raw JSON for one PD incident id
pd config-path
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python --version` shows 2.7 | Normal on macOS — ignore `python`. Use `python3.12 -m venv venv` after `brew install python@3.12` |
| `No matching distribution found for httpx>=0.28` | venv was created with Python older than 3.10. `rm -rf venv`, then `python3.12 -m venv venv` and reinstall |
| `editable mode requires a setup.py` | Upgrade pip in the venv: `./venv/bin/pip install --upgrade pip`, then retry `pip install -e .` |
| `PD token is not set` | Set `token=` in conf or `PD_TOKEN` |
| `no PD_TEAM_ID` warning | Add `team_id` for **MySQL Managed Services** |
| `Write actions require... PD_FROM` | Use a user API token, or set `from_email=` |
| `pd stats INC…` very slow | Use PD number `pd stats 123456`, or `--yes` to force INC scan |
| `no incident found … last 60 days` | Incident older than window — try `--days 90` or use PD id |
| `UnicodeEncodeError` / `charmap` | Fixed in recent versions (UTF-8 stdout + ASCII output). `git pull` and reinstall: `pip install -e .` |

---

## Development

Requires Python **3.10+** and dev deps for tests:

```bash
./venv/bin/pip install -e ".[dev]" 2>/dev/null || ./venv/bin/pip install -e .
./venv/bin/pip install pytest
./venv/bin/pytest -q
```
