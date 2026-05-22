# CLAUDE.md — yeelight-core

Local HTTP daemon for Xiaomi Yeelight smart bulbs. Part of the unified
bulb stack; exposes the same HTTP contract as wiz-core.

## Conventions

- Python 3.11+
- All identifiers (bulb IPs, multicast group) come from env or
  `~/.config/yeelight/state.json`. **Never hard-code IPs in source.**
- Yeelight uses JSON-RPC over TCP 55443. The `yeelight` PyPI library is
  the driver; it is **synchronous** — every call runs in `asyncio.to_thread`.
- Discovery is M-SEARCH multicast to `239.255.255.250:1982` (Yeelight's
  own port — NOT standard UPnP 1900).
- Tests use `pytest`; `asyncio_mode = "auto"`. The `yeelight` library is
  mocked at the `yeelight_core.driver.Bulb` boundary — no live bulb in CI.
- Run `ruff check`, `mypy`, `pytest -v` before commit.

## Local state (gitignored)

- `~/.config/yeelight/state.json` — bulb registry (mode 0600)
- `~/.config/yeelight/state.env` — env overrides

## systemd

- User unit: `~/.config/systemd/user/yeelight-core.service`
  (copied from `systemd/yeelight-core.service`)
- `systemctl --user restart yeelight-core`
- `journalctl --user -u yeelight-core -f`

## Don't repeat

- Only `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`,
  `ReadWritePaths=` work in user-scope systemd. The heavier `Protect*`
  knobs trip `status=218`.
- Boot discovery MUST run as `asyncio.create_task` inside the lifespan,
  never `await`'d before `yield` — awaiting an M-SEARCH sweep blocks
  uvicorn's port bind.
- "LAN Control" is OFF by default on every Yeelight bulb — it must be
  enabled once per bulb in the Mi Home app or TCP 55443 stays closed.
- Yeelight enforces a 60-message/minute rate limit per bulb; the
  `yeelight` library handles it, but avoid tight control loops.
