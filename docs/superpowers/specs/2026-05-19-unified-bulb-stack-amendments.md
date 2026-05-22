# Unified Bulb Stack — amendments (2026-05-19)

**Status:** approved 2026-05-19
**Amends:** [`2026-05-17-unified-bulb-stack-design.md`](2026-05-17-unified-bulb-stack-design.md)
**Scope:** resolves two open decisions left in the approved design, and
formalizes the parallel-execution plan for the remaining phases.

This document does NOT supersede the original spec — read it together
with the original. Any conflict between this and the original is
resolved in favor of this document.

## A1. ESP-TOUCH implementation — standalone clean-room `esptouch` library

The original spec §4.1 said "esptouch Python lib (or roll our own)"
without picking one. The memo
[[wiz-onboard-esptouch-deferred]] documented that no PyPI ESP-TOUCH
library is currently installable. The decision was deferred at that
time.

### Resolution

> **Decision history (2026-05-22):**
> 1. The originally-named `qiyongzhong0/esptouch-python` does not exist
>    (verified: GitHub 404, PyPI 404 on every candidate name).
> 2. The user re-approved vendoring the one extant pure-Python
>    implementation, `KurdyMalloy/EsptouchPython` (Unlicense).
> 3. The stream #1 Task 1 audit cloned it and found it unsuitable for
>    vendoring: a 2020 single-file procedural script with 8
>    module-level mutable globals (not thread-safe) and a hardcoded
>    6 s blocking broadcast with no caller timeout — failing audit
>    Checks 4 and 5.
> 4. **Final decision (user, 2026-05-22): clean-room reimplementation,
>    published as a standalone `esptouch` library repo.**

ESP-TOUCH is implemented from scratch — clean-room, from the published
Espressif SmartConfig wire format, no third-party code — and lives in
its **own repository**, `github.com/deepanshutr/esptouch`. Both
`wiz-core` and `tuya-core` depend on it as an ordinary library. This
adds a **7th repo** to the stack, but a genuinely reusable one.

### `esptouch` repo layout

```
esptouch/                  (new standalone repo, MIT)
  esptouch/
    __init__.py            # public API: run(), NewBulb, EsptouchError
    encoder.py             # pure ESP-TOUCH packet-length encoder (no I/O)
    audit.py               # encoding-correctness smoke test
  tests/
  pyproject.toml           # distribution name: esptouch
  README.md  CLAUDE.md  LICENSE (MIT)
  .github/workflows/{ci,release}.yml
```

Public API — import-stable, `wiz-core` and `tuya-core` both import it:
`from esptouch import run, NewBulb, EsptouchError`, where
`run(ssid, password, *, timeout_s, on_join, bssid, local_ip,
poll_interval_s=5.0) -> list[NewBulb]`.

### Correctness checklist (must pass before the `esptouch` repo merges)

The code is now our own clean-room implementation, so the §A1 audit
reframes as a correctness/safety checklist on our code:

1. **License** — repo is MIT; no third-party code vendored.
2. **Outbound network only on `255.255.255.255` UDP.** No HTTP, no
   telemetry, no DNS. Verified via grep.
3. **No dynamic code execution.** No `eval`/`exec`/`compile`/
   `__import__`/unsafe deserialization. Verified via grep.
4. **No global state mutation.** `encoder.py` is pure functions plus
   frozen dataclasses; `run()` holds its socket in local scope — safe
   to call concurrently from a FastAPI handler. (This is precisely the
   property `KurdyMalloy/EsptouchPython` lacked.)
5. **Bound CPU/memory.** Single encoding pass per `(ssid, password)`;
   the broadcast loop is hard-capped by the caller-supplied
   `timeout_s`.
6. **Encoding-correctness smoke test** (`audit.py`): encode a known
   `(ssid, password, bssid, ip)` tuple and assert the packet-length
   sequence matches the documented Espressif format. Runs in CI.

### Handler contract — unchanged

`POST /onboard {ssid, password, timeout_s?: 60, setup_ssid?}` returns:

- **200** `{onboarded: [{mac, ip, name, rssi}, ...]}` — one or more bulbs joined within timeout
- **408** `{error: "timeout", attempted_seconds: 60}` — broadcast ran full duration, no new bulbs appeared
- **422** — bad ssid/password input. FastAPI/pydantic auto-returns
  422 with its standard `{detail: [...]}` envelope; the handler
  asserts the **status code**, not a custom body.
- **500** `{error: "esptouch_internal", detail: "..."}` — encoding or socket failure

The `/onboard` route lives in `wiz-core` (`wiz_core/onboard.py` +
`wiz_core/api.py`); it imports `esptouch` and owns discovery/registry
polling, keeping the `esptouch` library protocol-pure. The route layer
no longer returns 501.

### Tuya-core dependency — simplified

`tuya-core` reuses ESP-TOUCH by depending on the same `esptouch`
library — an ordinary dependency, no editable-install or
source-copying workaround. The original spec §4.3 and this section's
earlier "factor out / re-vendor" choice are **superseded**: there is
now one shared `esptouch` package. Until `tuya-core` wires it in, its
`/onboard` returns 501 with the same envelope structure.

### Dependency mechanism

`esptouch` is not published to PyPI. `wiz-core` and `tuya-core`
declare it as a git dependency in `pyproject.toml`
(`esptouch @ git+https://github.com/deepanshutr/esptouch.git`); during
local development it is installed editable from the local clone.

### Stream impact — stream #1 splits in two

- **Part A** — build and publish the standalone `esptouch` repo:
  clean-room `encoder.py`, `run()`/`NewBulb`/`EsptouchError`,
  `audit.py`, full repo scaffold, CI. This is greenfield.
- **Part B** — `wiz-core` integration: `wiz_core/onboard.py` handler
  importing `esptouch`, `OnboardIn` widening (§A6.1), wire the
  `/onboard` route, add `esptouch` to `wiz-core`'s dependencies, docs.
  `wiz-core` gains **no** `wiz_core/esptouch/` sub-package — the
  obsolete Task-1 scaffold of that directory is dropped.

Stream #5 (`tuya-core`) Task 17 simplifies to "add `esptouch` to
dependencies and wire the real `/onboard`" — no re-vendoring.

## A2. `"all"` broadcast — best-effort fan-out, HTTP 200, results array

The original spec §1 listed `"all"` in target-resolution rules but
explicitly deferred it: "still not handled at HTTP layer; daemons 404
it for now." This amendment specifies it.

### Per-daemon contract

A new HTTP path family is added to every `-core` daemon:

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/bulb/all/on` | — | turn every registered bulb on |
| POST | `/bulb/all/off` | — | turn every registered bulb off |
| POST | `/bulb/all/brightness` | `{level: 10..100}` | |
| POST | `/bulb/all/temp` | `{kelvin: 2200..6500}` | |
| POST | `/bulb/all/color` | `{r,g,b: 0..255}` | |
| POST | `/bulb/all/scene` | `{scene: name\|id, speed?}` | |

`/bulb/all` (GET) is intentionally NOT added — use `/bulbs` for listing.

The target resolver no longer 404s on `"all"`; it routes to the
all-handler instead.

### Response shape — always HTTP 200

```json
{
  "op": "on",
  "total": 7,
  "ok": 6,
  "failed": 1,
  "duration_ms": 412,
  "results": [
    {"mac": "d8a0118dc5c3", "ok": true,  "duration_ms": 38},
    {"mac": "d8a011c0a795", "ok": false, "error": "udp_timeout", "duration_ms": 1500}
  ]
}
```

### Concurrency rules

- Daemon fans out to bulbs concurrently via `asyncio.gather(..., return_exceptions=True)`.
- Concurrency bound: `min(len(bulbs), 16)`. The 16 cap protects local
  Wi-Fi from a 50-bulb burst flooding the router; tunable via
  `WIZ_ALL_CONCURRENCY` env (and the same `<PROTO>_ALL_CONCURRENCY` per
  protocol for yeelight-core / tuya-core).
- Per-bulb timeout reuses the existing single-call timeout (no new knob).
- Exceptions from individual bulbs are caught and surfaced in the per-bulb
  `error` field. No exception ever bubbles out of the all-handler.

### Multiplexer fan-out

`bulb on all` (CLI / MCP) → multiplexer does parallel `POST
/bulb/all/{op}` against all 3 daemons, merges:

```json
{
  "op": "on",
  "total": 10, "ok": 9, "failed": 1, "duration_ms": 480,
  "by_protocol": {
    "wiz":      {"total": 7, "ok": 7, "failed": 0},
    "yeelight": {"total": 2, "ok": 2, "failed": 0},
    "tuya":     {"total": 1, "ok": 0, "failed": 1}
  },
  "results": [/* flat, every per-bulb entry with "protocol" added */],
  "errors":  [/* daemon-level failures, e.g., "tuya-core unreachable" */]
}
```

Daemon-level failure (HTTP error reaching a daemon) lands in `errors[]`
following the existing partial-success pattern in original spec §2.

### CLI exit codes — same as existing `bulb list`

- exit 0 if at least one daemon responded and at least one bulb succeeded
- exit 1 if every daemon was unreachable OR every bulb failed
- stderr prints a one-line summary plus per-failure lines

### Test coverage

- **wiz-core** (stream #2): ≥6 tests covering the six all-ops + ≥3 tests
  for failure modes (single-bulb timeout, all-bulbs-fail, empty registry,
  concurrency cap behavior).
- **yeelight-core / tuya-core** (streams #4/#5): equivalent tests applied
  to each daemon's all-handlers.
- **bulb-cli `pkg/multiplex`** (stream #3): table-driven test with 3 fake
  httptest daemons, covering merge, per-protocol bucketing, and
  daemon-level error pass-through.

## A3. Parallel execution plan

### Stream decomposition

| # | Stream | Repo(s) | Worktree / branch | Depends on |
|---|---|---|---|---|
| 1 | ESP-TOUCH vendor + real `/onboard` | wiz-core | `wiz-core-esptouch` branch in own worktree | — |
| 2 | `all` broadcast HTTP + tests | wiz-core | `wiz-core-broadcast` branch in own worktree | — |
| 3 | Phase B multiplexer (`pkg/multiplex` + bulb-cli + bulb-mcp) — incl. group hierarchy (see A5) | bulb-cli, bulb-mcp (both new) | per-repo `main` (greenfield) | — |
| 4 | Phase C yeelight-core (new repo, incl. broadcast) | yeelight-core (new) | `main` (greenfield) | — |
| 5 | Phase D tuya-core (new repo, incl. broadcast; `/onboard` stub until #1) | tuya-core (new) | `main` (greenfield) | partial #1 only for `/onboard` swap |
| 6 | Phase E orchctl-v2 swap | orchctl-v2 | `bulb-multiplex` branch in own worktree | #3 merged |

Streams #1–#5 launch in parallel. Stream #6 queues behind #3.

### Conflict isolation

- Streams #1 and #2 both touch `wiz-core` → separate git worktrees,
  separate branches. Merge order: whichever finishes first lands on
  `main`; the second rebases and resolves. Both branches add code; the
  intersection is limited to route registration in `wiz_core/api.py`
  (the `create_app()` factory — `wiz-core` has no `router.py`), which
  is a 1-line addition each.
- Streams #3, #4, #5 each modify only their own repo → no conflict.
- Stream #6 modifies only `orchctl-v2` → no conflict with #1–#5.

### Post-merge integration sequence

1. Merge #1 to `wiz-core/main`; deploy daemon; verify `POST /onboard`
   returns 200 against a setup-mode bulb (or accept 408 if none in setup).
2. Merge #2 to `wiz-core/main`; deploy daemon; verify
   `POST /bulb/all/on` returns 200 with results array for all 7 bulbs.
3. Merge #3 to `bulb-cli/main` and `bulb-mcp/main`; install binaries;
   `claude mcp remove philips-wiz-bulb && claude mcp add bulb ~/.local/bin/bulb-mcp -s user`.
4. Merge #4 (yeelight-core) once 2+ Yeelights are onboarded via Mi Home
   app (manual prereq); start daemon; verify in `bulb list`.
5. Merge #5 (tuya-core); if any tuya bulbs are reachable, populate
   `~/.config/tuya/keys.json`; verify in `bulb list` with `key_missing`
   flag where appropriate.
6. Dispatch #6 (orchctl-v2 swap); merge and restart `orchctl-v2.service`;
   verify `/bulb` slash command works from Telegram against any protocol.

### Validation gates (cross-cutting)

After streams #1, #2, #3 land:
- `bulb list` returns all 7 WiZ bulbs
- `bulb on all` flips all 7, returns results array, HTTP 200
- `bulb onboard <ssid> <password>` works against a setup-mode WiZ bulb
  (real-world validation — operator must put one in setup mode)
- `claude mcp list | grep bulb` shows `bulb: ✓ Connected`

After streams #4, #5 land:
- `bulb list` returns ≥10 entries spanning `"protocol": "wiz" | "yeelight" | "tuya"`
- `bulb temp <mac> 4000` succeeds against one bulb of each protocol;
  physical bulbs visibly change

After stream #6 lands:
- orchctl-v2 `/bulb list` command from Telegram returns the same merged list

## A5. Group hierarchy — home / zone / room

The original spec multiplexes per-bulb and supports an `"all"` target.
This amendment adds a three-level grouping hierarchy so the operator
can address logical sets of bulbs: the whole **home**, a **zone**
(e.g. `upstairs`), or a **room** (e.g. `bedroom`).

### A5.0. Grouping is a multiplexer-only feature

Group membership is **cross-protocol**: one room can contain a WiZ
bulb and a Yeelight bulb. A `-core` daemon only ever sees its own
protocol's bulbs, so it physically cannot own group state. Therefore:

- **Daemons (`wiz-core`, `yeelight-core`, `tuya-core`) get ZERO
  changes for grouping.** Streams #1, #2, #4, #5 are unaffected.
- All grouping logic lives in `bulb-cli/pkg/multiplex`, so it is
  shared by `bulb-cli`, `bulb-mcp`, and (via import) `orchctl-v2`.
- Grouping is absorbed entirely by **stream #3**, with a free pickup
  in **stream #6**.

### A5.1. Storage — `~/.config/bulb/groups.json`

Owned by `pkg/multiplex`, mode `0600`. Membership is keyed by **MAC**
(stable across DHCP drift, consistent with the daemon registries).

```json
{
  "home": "deePC Home",
  "zones": ["upstairs", "downstairs"],
  "rooms": {
    "bedroom":     {"zone": "upstairs",   "members": ["d8a0118dc5c3", "d8a011c07ad6"]},
    "study":       {"zone": "upstairs",   "members": ["d8a011c09c4f"]},
    "living-room": {"zone": "downstairs", "members": ["d8a0118dc45a", "d8a011c0a795"]},
    "kitchen":     {"zone": "downstairs", "members": []}
  }
}
```

Schema rules:
- `home` — single display string (one home per host).
- `zones` — flat list of zone names. A zone is purely a named bucket
  of rooms; it has no other properties. Empty zones are allowed (a
  zone with no room pointing at it).
- `rooms` — map of room-name → `{zone, members}`. `zone` must be a
  member of `zones`. `members` is a MAC list; empty rooms are allowed.
- A bulb is in **0 or 1 room** (the CLI enforces single-room
  membership on assign; see A5.4). A room is in **exactly 1 zone**.
- File missing → multiplexer treats it as `{home:"Home", zones:[],
  rooms:{}}` and writes it lazily on first mutation.

**Validation on load** (default = lenient): an unknown `zone` in a
room → hard error (the hierarchy is malformed). A MAC in `members`
that no daemon currently reports → **warn to stderr, keep the entry**
(the bulb may just be offline / not yet rediscovered; dropping it
would silently lose the operator's assignment). This default is
called out in the approval question — strict mode (drop dangling
MACs) is the alternative.

### A5.2. Target grammar — two-tier resolution

Group targets are resolved **at the multiplexer**, never sent to a
daemon. The multiplexer expands a group token into a set of MACs,
then dispatches per-MAC. Daemons keep their existing resolver
unchanged.

| Tier | Resolver | Tokens it handles |
|---|---|---|
| Multiplexer | `pkg/multiplex` | `home`, `zone:<name>`, `room:<name>` → MAC set |
| Daemon | each `-core` | `_default`, `all`, MAC, IPv4, friendly-name |

Full multiplexer resolution order for a `{target}`:

1. empty / `_default` → default-protocol daemon's default bulb
2. `all` → every **discovered** bulb (passes through to each daemon's
   `/bulb/all/{op}`, per A2)
3. `home` → every bulb that is a member of **any** room
4. `zone:<name>` → every bulb in any room whose `zone == <name>`
5. `room:<name>` → every bulb in `rooms[<name>].members`
6. MAC / IPv4 / friendly-name → single-bulb dispatch (existing §2 path)
7. otherwise → error `unknown target`

**`all` vs `home`:** `all` = every bulb the daemons can see. `home` =
every bulb the operator has assigned to a room. They differ when a
freshly-discovered bulb has not been placed yet. This is intentional
— a new bulb should not silently join `home`-wide operations until
the operator files it.

**Collision rule:** `home` and `all` are reserved keywords; `zone:`
and `room:` are reserved prefixes. A bulb may still be *friendly-named*
`kitchen`; it is addressed bare as `kitchen`, while the room is
`room:kitchen`. No ambiguity because group forms are keyword/prefixed.

### A5.3. Group operations — filtered broadcast

A group op (`bulb on room:bedroom`, `bulb temp zone:upstairs 4000`,
`bulb off home`) reuses the A2 broadcast machinery:

1. Multiplexer resolves the group token → MAC set.
2. Buckets MACs by owning daemon (via the existing §2 MAC→daemon
   60s cache; cache-miss MACs are probed in parallel).
3. Dispatches `POST /bulb/{mac}/{op}` concurrently, concurrency cap
   `min(len(macs), 16)` (same cap and `BULB_ALL_CONCURRENCY` env as A2).
4. Returns the **identical** A2 result envelope — `{op, total, ok,
   failed, duration_ms, results:[...], by_protocol:{...}}` — plus a
   `"group": "room:bedroom"` echo field.

No new daemon endpoints, no new response shapes. A group is just a
named subset of `all`. Empty group (room with `members: []`) →
`{total:0, ok:0, failed:0, results:[]}`, HTTP 200.

### A5.4. CRUD surface

**`bulb-cli` subcommands** (read-modify-write `groups.json`):

| Command | Effect |
|---|---|
| `bulb group list` | print the home → zone → room → bulb tree |
| `bulb zone add <name>` / `bulb zone rm <name>` | manage zones (`rm` refuses if rooms still reference it) |
| `bulb room add <name> --zone <zone>` | create an (empty) room |
| `bulb room rm <name>` | delete a room (members become unassigned) |
| `bulb room assign <room> <mac\|name>...` | add bulbs to a room; **removes each from any prior room** |
| `bulb room unassign <mac\|name>...` | remove bulbs from whatever room they're in |

`groups.json` is also hand-editable; the CLI re-validates on every
load per A5.1.

**`bulb-mcp` tools** — control tools need **no change** (they accept
`home` / `zone:` / `room:` as the `target` arg for free). Two new
management tools are added:

- `bulb_group_list` — return the hierarchy tree
- `bulb_group_assign` — assign/unassign bulbs to a room

This takes `bulb-mcp` from **11 → 13 tools**.

### A5.5. orchctl-v2 pickup (stream #6)

Because `orchctl-v2` imports `pkg/multiplex`, `/bulb on room:bedroom`
and friends work for free. One additive subcommand: `/bulb groups`
prints the tree (mirrors `bulb group list`). The `/bulb` grammar is
otherwise unchanged.

### A5.6. Impact on the parallel plan

| Stream | Change from A3 |
|---|---|
| #1 ESP-TOUCH | none |
| #2 broadcast | none |
| #3 multiplexer | **grows**: add `pkg/multiplex/groups` (load/save/validate/resolve), `bulb group\|zone\|room` CLI subcommands, 2 MCP tools |
| #4 yeelight-core | none |
| #5 tuya-core | none |
| #6 orchctl-v2 | **grows slightly**: add `/bulb groups` subcommand |

The dependency graph and conflict isolation in A3 are unchanged —
grouping is contained within the two streams that already touch the
multiplexer layer.

### A5.7. Testing

- `pkg/multiplex/groups`: table-driven tests — load/validate (unknown
  zone = error, dangling MAC = warn+keep), resolve (`home`, `zone:`,
  `room:`, empty room, `all`-vs-`home` divergence, room-name vs
  bulb-name collision), CRUD round-trips (assign moves between rooms,
  zone `rm` guard).
- `bulb-cli`: `bulb group/zone/room` subcommand tests against a temp
  `groups.json`.
- `bulb-mcp`: smoke script asserts **13** tools; exercises
  `bulb_group_list` + `bulb_group_assign`.
- End-to-end (post-#3): create a room with 2 WiZ bulbs, `bulb on
  room:<name>`, assert both flip and the result envelope carries the
  `group` echo.

## A4. Convention reminders (still apply, called out for completeness)

These are inherited from the original spec §9 and apply to every new
repo created in streams #3, #4, #5:

- MIT LICENSE · README.md · CLAUDE.md · `docs/superpowers/specs/`
  copy of both this amendment and the parent spec
- `.github/{workflows/{ci,release}.yml,SECRETS.md,dependabot.yml}`
- gitleaks in CI
- `go-version: "stable"` in GH Actions ([[go-actions-stable-version]])
- systemd user units hardened only with `NoNewPrivileges/PrivateTmp/
  ProtectSystem/ReadWritePaths` ([[systemd-user-hardening]])
- Per-repo `git config user.email "52166434+deepanshutr@users.noreply.github.com"`
- Go pre-flight: `unset GOROOT; export GOPROXY=https://proxy.golang.org,direct`
- Workflow file writes via heredoc (Write tool blocks `.github/workflows/*.yml`)
- Daemon discovery on boot runs as `asyncio.create_task` inside lifespan,
  never `await`'d before `yield` ([[wiz-bg-boot-discover]])
- LAN-sweep probes use `retries=0` HTTP client, distinct from the
  retrying client used for known-good targets ([[speculative-no-retry]])

## A6. Cross-stream reconciliations (post-plan-review, 2026-05-22)

The six per-stream implementation plans were written by independent
agents, then cross-checked. Four reconciliations resulted. Two were
fixed inline above; two are specified here.

### A6.1. `/onboard` gains an optional `setup_ssid` field

The shared `/onboard` request body (spec §1) becomes:

```
POST /onboard {ssid, password, timeout_s?: 60, setup_ssid?: string}
```

- **WiZ + Tuya** ignore `setup_ssid` — ESP-TOUCH is an over-the-air
  broadcast; there is no setup AP to select.
- **Yeelight** *requires* it in practice: its onboarding connects to
  the bulb's own setup AP (`yeelink-light-*`) via nmcli, so the daemon
  must be told which AP to join. Optional in the schema, but yeelight
  onboarding is non-deterministic without it when ≥2 Yeelights are in
  setup mode simultaneously.
- The **multiplexer's `bulb onboard`** (spec §3) scans Wi-Fi, matches
  setup-mode SSIDs to protocols, and MUST forward each matched SSID as
  `setup_ssid` when it POSTs `/onboard` to the owning daemon. Stream
  #3's plan implements this forwarding; stream #4's plan expects the
  field.

### A6.2. `pkg/multiplex` public API — stream #3 is canonical

The original spec describes `pkg/multiplex` behaviorally (§2, §A5) but
never pins its Go signatures. Stream #3 freezes the exported API in
`bulb-cli/pkg/multiplex/doc.go`, guarded by a compile-time
`api_contract_test.go`. **That frozen API is the single source of
truth.**

`bulb-mcp` (stream #3, same repo group) and `orchctl-v2` (stream #6)
both import it. Stream #6's plan pins a *provisional* API derived from
the spec; on execution, stream #6 conforms to stream #3's actual
`doc.go`, not its own provisional pin. The execution order (#6 queues
behind #3, per A3) guarantees `doc.go` exists before #6 starts; #6's
plan carries an explicit escalate-on-divergence checkpoint.

### A6.3. Fixed inline

- **ESP-TOUCH vendor target** (§A1): `qiyongzhong0/esptouch-python`
  was a phantom; corrected to `KurdyMalloy/EsptouchPython` (Unlicense).
- **`/onboard` 422** (§A1): asserts status code, not a custom body
  (FastAPI owns the 422 envelope).
- **wiz-core route file** (§A3): `wiz_core/api.py` `create_app()`,
  not a non-existent `router.py`.

### A6.4. `name` (rename) is a multiplexer operation

Spec §non-goals enumerates the multiplexer's atomic-tool surface as
`on/off/brightness/temp/color/scene/state/list/discover` — omitting
`name`. But spec §5 requires orchctl-v2's `/bulb` grammar (which
includes `name`) to stay identical. The two contradict.

Resolved in favor of §5: **`name` IS a multiplexer operation.**
Renaming sets a registry friendly-name via each daemon's existing
`POST /bulb/{target}/name` endpoint — an ordinary per-bulb dispatch,
identical across all protocols, carrying none of the
lowest-common-denominator risk §non-goals guards against (that
concern targets protocol-specific *features* like music mode, not
universal registry operations).

- `pkg/multiplex` exposes a `Rename(ctx, target, newName)` method,
  dispatched exactly like the other single-bulb ops.
- `bulb-cli` has a `bulb name <target> <newName>` subcommand.
- `orchctl-v2` keeps `/bulb name`.
- `bulb-mcp` gains **no** rename tool — the original 9-tool WiZ MCP
  never had one. Tool count stays **13** (9 atomic + onboard + health
  + 2 group), per §A5.4.
