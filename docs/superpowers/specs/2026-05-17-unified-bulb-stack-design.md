# Unified Bulb Stack — design

**Status:** approved 2026-05-17
**Owner:** deepanshutr
**Supersedes:** [`2026-05-14-philips-wiz-bulb-stack-design.md`](2026-05-14-philips-wiz-bulb-stack-design.md) (WiZ-only design; consolidated into this multi-protocol design)

## Goal

Local-LAN control of every smart bulb in the user's home from one
unified CLI/MCP surface, regardless of brand or protocol. Today's
philips-wiz-bulb stack handles WiZ only; the home also has Yeelight
(Xiaomi) and Tuya-based (Amazon Basics) bulbs that need separate
protocols. This design splits each protocol into its own daemon and
multiplexes them behind a thin CLI/MCP that aggregates per-bulb
operations across daemons by MAC.

Current LAN inventory (as of 2026-05-17):

| Class | Count | Notes |
|---|---|---|
| WiZ | 7 live | All ESP01_SHRGB1C_31 modules, fw 1.35.0 |
| Yeelight Color 3 | 2 in setup mode | SSIDs `yeelink-light-color3_miap{2F2C,5E13}` |
| Likely Tuya | 1 in setup mode | SSID `ESP_9D4039` (generic Espressif default; almost certainly the Amazon bulb) |

## Non-goals

- **Cloud control of any bulb.** All control is LAN-only. We never
  call WiZ/Yeelight/Tuya cloud APIs at runtime. (Tuya local keys are
  obtained out-of-band via the operator; see §6.4.)
- **Hue / Hub-based protocols.** No Bridge support, no Zigbee. If the
  user adds Hue later, that's a separate `hue-core` sibling daemon, not
  a feature of this design.
- **Replacing per-protocol semantics with a lowest-common-denominator
  API.** Multiplexer exposes the same atomic-tool surface as today's
  WiZ stack (`on/off/brightness/temp/color/scene/state/list/discover`);
  protocol-specific extras (e.g., Yeelight's music mode, Tuya's scene
  builder) stay accessible via the per-daemon HTTP API but are not
  promoted into the multiplexer.

## Architecture

```
                                        ┌─ wiz-core      :8766 ─UDP:38899──▶ WiZ bulbs
Claude   ─MCP/stdio─▶ bulb-mcp  ─HTTP─┬─▶ ├─ yeelight-core :8767 ─TCP:55443──▶ Yeelight bulbs
Shell    ─exec─▶      bulb      ─HTTP─┴─▶ └─ tuya-core     :8768 ─TCP:6668───▶ Tuya bulbs
Telegram ─poll─▶      orchctl-v2/bulb ─Go-pkg─▶ bulb-cli/pkg/multiplex ──▶ (same)
```

Five repos under `github.com/deepanshutr`:

| Repo | Lang | Role | Port |
|---|---|---|---|
| `wiz-core` | Python 3.11 / FastAPI | WiZ daemon (renamed from philips-wiz-bulb-core) | 8766 |
| `yeelight-core` | Python 3.11 / FastAPI | Yeelight TCP/JSON-RPC daemon (new) | 8767 |
| `tuya-core` | Python 3.11 / FastAPI | Tuya local daemon (new) | 8768 |
| `bulb-cli` | Go 1.23 | Cobra CLI + reusable `pkg/multiplex` package (replaces philips-wiz-bulb-cli) | — |
| `bulb-mcp` | Go 1.23 | MCP stdio server importing `bulb-cli/pkg/multiplex` (replaces philips-wiz-bulb-mcp) | — |

## 1. Shared per-daemon HTTP contract

Every `-core` daemon exposes the **identical** surface, modeled on the
existing `wiz-core`. This lets the multiplexer drive all three with
one HTTP client.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/health` | — | liveness; the multiplexer's `bulb health` aggregates all 3 |
| GET | `/bulbs` | — | `{"bulbs": [...]}`; each entry includes `"protocol": "wiz"\|"yeelight"\|"tuya"` |
| GET | `/bulbs/default` | — | earliest-discovered bulb in this daemon + live state (409 if empty) |
| POST | `/discover` | `{passive?: bool}` | re-scan LAN for this protocol |
| POST | `/onboard` | `{ssid, password, timeout_s?: 60}` | protocol-specific Wi-Fi onboarding |
| GET | `/bulb/{target}` | — | resolve + live state |
| POST | `/bulb/{target}/on` | — | |
| POST | `/bulb/{target}/off` | — | |
| POST | `/bulb/{target}/brightness` | `{level: 10..100}` | |
| POST | `/bulb/{target}/temp` | `{kelvin: 2200..6500}` | |
| POST | `/bulb/{target}/color` | `{r,g,b: 0..255}` | |
| POST | `/bulb/{target}/scene` | `{scene: name\|id, speed?}` | |
| POST | `/bulb/{target}/name` | `{name: string}` | |
| GET | `/scenes` | — | protocol-specific scene catalog |

### Target resolution rules (same as today's wiz-core)

In order:
1. None / `""` / `"_default"` → daemon's `default()` (earliest discovered)
2. `"all"` → broadcast op (still not handled at HTTP layer; daemons 404 it for now)
3. MAC (with/without colons, case-insensitive) → exact match
4. IPv4 → `last_ip` exact match
5. else → case-insensitive friendly-name match
6. otherwise 404

### Protocol-agnostic /bulbs entry shape

```json
{
  "protocol": "wiz",                    // NEW required field
  "mac": "d8a0118dc5c3",
  "name": "bulb-1",
  "ip": "192.168.1.4",
  "rssi": -59,
  "module": "ESP01_SHRGB1C_31",         // optional
  "fw_version": "1.35.0",               // optional
  "cct_range": [2200, 6500],            // optional
  "discovered_at": "...",
  "last_seen": "...",
  // protocol-specific extras may follow; multiplexer treats them as opaque
  "key_missing": false                  // tuya-core only
}
```

## 2. Multiplexer — `bulb-cli` + `bulb-mcp`

### Config

`~/.config/bulb/daemons.json`:
```json
{
  "wiz":      "http://127.0.0.1:8766",
  "yeelight": "http://127.0.0.1:8767",
  "tuya":     "http://127.0.0.1:8768"
}
```

Default values used if the file is missing. Env override:
`BULB_DAEMONS_JSON` (full inline JSON) or per-protocol `WIZ_URL` /
`YEELIGHT_URL` / `TUYA_URL`.

### Dispatch (in `pkg/multiplex`)

In-memory cache: `MAC → (daemon_name, expires_at)` with 60s TTL.

```
dispatch(target, op):
  if target is a MAC (or "_default" → use default-protocol):
    if MAC in cache and not expired:
      return op against cache[MAC].daemon
    else:
      probe all 3 daemons in parallel: GET /bulb/{target}
      whichever returns 200 → owner
      cache it for 60s
      return op against owner
  if target looks like a friendly name or IP:
    same probe (no MAC lookup possible until owner resolves)
```

**Default protocol** for `bulb _default` / `bulb on` (no target):
configurable via `BULB_DEFAULT_PROTOCOL` env, defaults to `wiz`
(preserves existing UX where the original bulb-1 was the implicit
default).

### Aggregator endpoints

| Multiplexer op | Behavior |
|---|---|
| `bulb list` | parallel `GET /bulbs` across all 3 daemons; merge into flat array; preserve `protocol` field on each entry |
| `bulb discover` | parallel `POST /discover`; aggregate `{discovered: N, total: M, by_protocol: {wiz:X, yeelight:Y, tuya:Z}}` |
| `bulb health` | parallel `GET /health`; report which daemons reachable |
| `bulb onboard` | see §3 |
| `bulb scenes` | per-protocol scenes are different; multiplexer returns `{wiz: [...], yeelight: [...], tuya: [...]}` |

### Failure isolation

A daemon being down (HTTP error, connection refused) does NOT fail the
multiplexer call. The response includes a partial-success envelope:

```json
{
  "bulbs": [/* whatever succeeded */],
  "errors": [
    {"daemon": "tuya", "status": 0, "error": "connection refused"}
  ]
}
```

CLI surface: lists what succeeded, prints errors to stderr, exit 0 if
at least one daemon responded, exit 1 if all 3 failed.

## 3. Unified onboarding — `bulb onboard <ssid> <password> [--timeout 60]`

```
1. Scan Wi-Fi (nmcli dev wifi list --rescan yes -t -f SSID,SIGNAL)
2. Filter to setup-mode SSIDs:
     wiz_*          → wiz protocol
     yeelink-*      → yeelight protocol
     ESP_*          → tuya protocol (assumed; user must confirm if ambiguous)
3. For each detected SSID, in parallel:
     POST /onboard to the corresponding daemon with {ssid, password, timeout_s}
4. Aggregate:
     {
       "onboarded": [{"mac": "...", "protocol": "wiz", "name": "bulb-8"}, ...],
       "by_protocol": {"wiz": 1, "yeelight": 0, "tuya": 1},
       "skipped": [{"ssid": "yeelink-light-...", "reason": "yeelight-core not reachable"}],
       "errors":  [{"daemon": "tuya", "error": "ESP-TOUCH timeout after 60s"}]
     }
```

ESP_* → Tuya assumption is heuristic. If the user has other ESP-based
non-Tuya devices, they can override with `bulb onboard --protocol tuya
<ssid> <password>` to force a specific protocol regardless of SSID
pattern, OR `bulb onboard --skip-protocol tuya <ssid> <password>` to
exclude.

## 4. Per-daemon detail

### 4.1 `wiz-core` (renamed from `philips-wiz-bulb-core`)

**Migration:** existing repo at github.com/deepanshutr/philips-wiz-bulb-core
is renamed via `gh repo rename`. Local clone's remote URL is updated.
systemd user unit renamed `philips-wiz-bulb-core.service` →
`wiz-core.service`; old unit disabled, new one installed and enabled.
Local state at `~/.config/philips-wiz-bulb/state.json` is moved to
`~/.config/wiz/state.json` (one-time migration script as part of the
rollout).

**Code changes:**
- Add `"protocol": "wiz"` to every `/bulbs` and `/bulb/{target}` response.
- Add `POST /onboard` endpoint:
  - Uses `esptouch` Python lib (or roll our own — protocol is well-documented).
  - Broadcasts the encoded SSID + password as UDP packets of varying lengths to `255.255.255.255`.
  - Concurrently polls `discover()` every 5s to detect newly-joined MACs.
  - Returns the MAC(s) that appeared in the registry within `timeout_s`.
- Package directory: `philips_wiz_bulb_core/` → `wiz_core/`.
- Console-script: `philips-wiz-bulb-core serve` → `wiz-core serve`.
- Env prefix: `PHILIPS_WIZ_BULB_*` → `WIZ_*`.

**Discovery + driver are unchanged** (UDP 38899 broadcast + unicast
sweep; multi-bulb registry keyed by MAC). The 7 currently-discovered
WiZ bulbs survive the rename transparently because the state.json
schema is preserved.

### 4.2 `yeelight-core` (new)

Same scaffold as `wiz-core` (FastAPI on 127.0.0.1:8767, multi-bulb
registry, systemd user unit, identical HTTP contract).

**Driver:** [`yeelight`](https://pypi.org/project/yeelight/) Python lib
(actively maintained, handles TCP/JSON-RPC framing + the 60-msg/min
rate limit + scene presets).

**Discovery:** SSDP-like M-SEARCH to `239.255.255.250:1982` (Yeelight's
custom multicast port; NOT standard UPnP). Response Location header
gives `yeelight://<ip>:<port>` and includes the device's `id`
(equivalent of MAC), supported features list, and model.

**LAN-control prerequisite:** Yeelight bulbs ship with "LAN Control"
DISABLED by default. It must be enabled per-bulb in the Mi Home app
under Device → Settings → LAN Control. Without this toggle, TCP 55443
is closed. README + CLAUDE.md document this as a one-time-per-bulb
manual step. Daemon detects the closed-port condition and surfaces:
`{"error": "yeelight LAN control disabled; enable in Mi Home app per
bulb", "bulb": "<mac>"}`.

**`POST /onboard` flow:**
1. Save current Wi-Fi connection name from `nmcli`.
2. `nmcli dev wifi connect <setup-ssid> password ''` (Yeelight setup APs are open).
3. HTTP POST to `http://192.168.4.1/api/v1/wifi/set` with body
   `{"ssid": "<home-ssid>", "passwd": "<home-pw>"}` (exact endpoint
   varies by Yeelight firmware version; daemon tries 2 known paths).
4. Wait ~5s for the bulb to ACK + disconnect.
5. `nmcli connection up <previous-ssid>` to restore.
6. Poll `discover()` every 5s until the newly-joined bulb appears, up
   to `timeout_s`.
7. Return the new MAC.

**Failure modes during onboarding:**
- nmcli connect to setup AP fails → revert to home Wi-Fi immediately, return error.
- Setup HTTP POST fails → still revert to home Wi-Fi, return error.
- Bulb joins Wi-Fi but doesn't appear in discovery within timeout →
  return "joined but not discovered yet; check `bulb discover` in 60s".

**Color/temp/scene mapping:**
- `set_rgb(r, g, b)` for color
- `set_color_temp(kelvin)` for temp (Yeelight's range is 1700-6500K; cap our 2200K floor)
- `set_brightness(level)` for brightness (Yeelight uses 1-100; cap our 10 floor at 1 or hold at 10)
- Yeelight scenes are a different set than WiZ's 32 (e.g., "movie", "night", "tv"); daemon exposes its native list at `/scenes`. Multiplexer surfaces both catalogs separately.

### 4.3 `tuya-core` (new)

Same scaffold as wiz-core (FastAPI on 127.0.0.1:8768).

**Driver:** [`tinytuya`](https://pypi.org/project/tinytuya/) Python lib
(handles AES-128-ECB encryption, protocol versions 3.1/3.2/3.3/3.4,
heartbeats, local-key API).

**Discovery:** Tuya devices broadcast every ~1s on UDP 6666 (3.1/3.2
unencrypted) and 6667 (3.3+ encrypted). Daemon listens on both ports
for 3s; parses each broadcast's plaintext header to extract:
- IP (from packet source addr)
- `gwId` / `devId` (Tuya's MAC-equivalent device identifier)
- protocol version

The MAC is extracted via ARP lookup on the IP (since Tuya doesn't
include it in the broadcast). Registry is keyed by MAC for cross-protocol
consistency with wiz/yeelight.

**Local key file:** `~/.config/tuya/keys.json` (mode 0600):
```json
{
  "d8a011deadbeef": {
    "device_id": "bf01abc1234567890",
    "local_key": "a1b2c3d4e5f6g7h8",
    "version": "3.3"
  }
}
```

Bulbs without a key entry appear in `/bulbs` with `"key_missing": true`.
All control endpoints (`/on`, `/off`, etc.) return HTTP 412 Precondition
Failed: `{"error": "missing local_key for <mac>; populate ~/.config/tuya/keys.json"}`.

**`POST /onboard` flow:**
1. ESP-TOUCH broadcast (same lib as wiz-core; reuses
   `philips_wiz_bulb_core.onboard` factored into a shared module).
2. Concurrently watch UDP 6666/6667 for the new bulb to broadcast.
3. ARP-resolve its MAC.
4. Return `{"mac": "...", "device_id": "...", "key_missing": true}`.

Operator's next step (out of daemon scope): obtain the local key for
that device_id via `tinytuya wizard` (Tuya IoT dev account), Smart
Life APK extraction, or Home Assistant integration export. Once
populated, the daemon's next refresh-loop iteration enables control.

**Color/temp/scene mapping:**
- Tuya RGB DPID is usually 5 (Tuya-protocol "data points"); HSV-encoded.
- Tuya color-temp DPID is usually 4; range 0-1000 (we map our 2200-6500K linearly).
- Scenes are device-specific; daemon exposes a small built-in "white" / "color cycle" / "music" set, returning the rest as opaque scene IDs.

## 5. orchctl-v2 integration

Replace `internal/wiz/` with `internal/bulb/`. The Go multiplex package
lives in `bulb-cli/pkg/multiplex` (exported, public) and is imported by:
- `bulb-cli/cmd/bulb/main.go` (the user-facing CLI)
- `bulb-mcp/cmd/bulb-mcp/main.go` (the MCP stdio server)
- `orchctl-v2/internal/bulb/client.go` (the Telegram /bulb command path)

Single source of truth: dispatch logic, MAC cache, daemon list config
parsing all live in `pkg/multiplex`.

`/bulb` slash command grammar stays identical (existing `wiz.Handle()`
parser in orchctl-v2 already does `/bulb on/off/bri/temp/color/scene/
list/discover/name`). Just the package import + struct names change.

**New `/bulb onboard <ssid> <password>` slash subcommand** added,
proxying to the multiplexer's `bulb onboard`.

## 6. Migration sequence (phased; each phase ships)

### Phase A — Repo rename + `protocol` field
- `gh repo rename` philips-wiz-bulb-core → wiz-core (same for cli, mcp:
  rename them to `wiz-cli-deprecated` and `wiz-mcp-deprecated`, then
  archive via `gh repo archive`).
- Update local clones' remote URLs (`git remote set-url origin ...`).
- Rename Python package dir `philips_wiz_bulb_core/` → `wiz_core/`;
  console-script `wiz-core`; env prefix `WIZ_*`.
- Migrate `~/.config/philips-wiz-bulb/` → `~/.config/wiz/` via one-time
  shell snippet on first daemon start.
- Add `"protocol": "wiz"` to every API response.
- Rename systemd unit; disable old, enable new.
- WiZ bulbs continue working through the rename.

### Phase B — Multiplexer (`bulb-cli` + `bulb-mcp`)
- `bulb-cli/pkg/multiplex`: Go package implementing parallel-fetch, MAC
  cache, partial-success aggregation, daemons.json loading.
- `bulb-cli/cmd/bulb`: cobra CLI subcommands (`list`, `state`, `on`,
  `off`, `bri`, `temp`, `color`, `scene`, `discover`, `health`, `name`,
  `onboard`).
- `bulb-mcp/cmd/bulb-mcp`: MCP stdio server, 11 tools (`bulb_*`):
  `list`, `state`, `on`, `off`, `brightness`, `temp`, `color`, `scene`,
  `discover` (the previous 9) + `onboard` + `health` (new).
- `claude mcp remove philips-wiz-bulb` first, then
  `claude mcp add bulb ~/.local/bin/bulb-mcp -s user`. Document the
  session-restart requirement (MCP schemas snapshot at session init).
- Migration helper: `bulb migrate` subcommand that runs
  `claude mcp remove philips-wiz-bulb && claude mcp add bulb …` for
  one-shot operator convenience.

### Phase C — `yeelight-core`
- Scaffold same as wiz-core; FastAPI + uvicorn; multi-bulb registry.
- Driver via `yeelight` Python lib.
- Discovery + onboarding implementations.
- Test against 2 live Yeelights post-Mi-Home-onboarding.
- Register with multiplexer's `daemons.json`.

### Phase D — `tuya-core`
- Scaffold; tinytuya driver; UDP-broadcast discovery; ARP→MAC.
- ESP-TOUCH onboarding (factor shared module out of wiz-core).
- Local-key file at `~/.config/tuya/keys.json`; 412 surface for missing keys.
- Test against ESP_9D4039 once it's onboarded.

### Phase E — orchctl-v2 swap
- `internal/wiz/` → `internal/bulb/` importing `bulb-cli/pkg/multiplex`.
- Rebuild + restart orchctl-v2.
- `/bulb onboard` subcommand added.

### Phase F — Acceptance
- `bulb list` shows ≥10 bulbs spanning all 3 protocols.
- `bulb temp <wiz-mac> 4000`, `bulb temp <yeelight-mac> 4000`,
  `bulb temp <tuya-mac> 4000` all succeed.
- `bulb onboard <ssid> <password>` with one bulb in each protocol's
  setup mode produces all 3 onboarded entries in `bulb list`.
- Reboot: all 3 daemons come up clean; `bulb list` works.
- All 5 repos green on CI.

## 7. Error handling

| Layer | Failure | Surface |
|---|---|---|
| Daemon → bulb | UDP timeout / TCP refused / encryption fail | 504 with `{detail: "..."}` |
| Daemon → daemon-side error | bad input, unknown scene, etc. | 400 / 412 / 422 |
| Multiplexer → one daemon down | exclude from aggregation | partial-success `{errors: [...]}` |
| Multiplexer → all daemons down | full failure | 503 |
| CLI → multiplexer | bulb missing or all daemons down | non-zero exit, stderr message |
| MCP → multiplexer | bulb missing | tool result error: `"no bulb matches MAC X across any daemon"` |
| Tuya bulb missing local_key | 412 from tuya-core | CLI prints "missing local key; populate ~/.config/tuya/keys.json with `tinytuya wizard` output" |

## 8. Testing

- **wiz-core**: existing 46 tests preserved. Add ~5 tests for `/onboard`
  (mock ESP-TOUCH lib; assert discovery polling).
- **yeelight-core**: new test suite ~25 tests (registry, driver, discovery,
  onboarding, API). Mock `yeelight` lib at the bulb-driver boundary.
- **tuya-core**: new test suite ~25 tests. Mock tinytuya. Test the
  key-missing 412 path. Test the broadcast-listener parsing.
- **bulb-cli (multiplex pkg)**: Go table-driven tests with 3 fake daemon
  httptest servers. Cover MAC-cache TTL, partial-success aggregation,
  SSID-pattern detection for `bulb onboard`.
- **bulb-mcp**: smoke script (stdio JSON-RPC initialize + tools/list)
  verifying 10 tools.
- **End-to-end**: post-Phase F, drive all 3 protocols from one
  `bulb temp` invocation each + reboot test.

## 9. Conventions (identical across all 5 repos)

- MIT LICENSE · README.md · CLAUDE.md · `docs/superpowers/specs/` copy of this spec
- `.github/{workflows/{ci,release}.yml,SECRETS.md,dependabot.yml}`
- gitleaks in CI
- `go-version: "stable"` in GH Actions (per [[go-actions-stable-version]])
- systemd user units, hardened only with `NoNewPrivileges/PrivateTmp/
  ProtectSystem/ReadWritePaths` (per [[systemd-user-hardening]])
- Telegram-notify on release (no-op until secrets configured)
- Per-repo git user.email: `52166434+deepanshutr@users.noreply.github.com`
- Go pre-flight: `unset GOROOT; export GOPROXY=https://proxy.golang.org,direct`
- Workflow file writes via heredoc (the Write tool blocks `.github/workflows/*.yml`)

## 10. Acceptance criteria

Phase F's "done" definition:

1. `claude mcp list | grep bulb` shows `bulb: /home/.../bulb-mcp - ✓ Connected`.
2. `bulb list` returns ≥10 bulb entries across `"protocol": "wiz"`,
   `"yeelight"`, `"tuya"`.
3. `bulb temp <mac> 4000` succeeds against one bulb of each protocol;
   physical bulbs visibly change.
4. `bulb onboard <ssid> <password>` with one new bulb in each
   protocol's setup mode onboards all three; `bulb list` shows them
   within 90s.
5. `systemctl --user reboot`-equivalent: all 3 `*-core` daemons
   restart clean; `bulb list` works ≤30s post-reboot.
6. `gh run list -R deepanshutr/<repo> -L 1` shows latest CI success
   for each of the 5 repos.
7. orchctl-v2's `/bulb` slash command works from Telegram against any
   protocol.
8. The deprecated philips-wiz-bulb-{cli,mcp} repos are archived on
   GitHub; the wiz-core repo is the renamed-in-place evolution of
   philips-wiz-bulb-core.
