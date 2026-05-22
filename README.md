# yeelight-core

Local HTTP daemon that controls Xiaomi **Yeelight** smart bulbs on the LAN
via TCP/JSON-RPC (port 55443). Sister daemon to
[wiz-core](https://github.com/deepanshutr/wiz-core); part of the unified
bulb stack driven by the `bulb` multiplexer.

- Port: `127.0.0.1:8767`
- Driver: the [`yeelight`](https://pypi.org/project/yeelight/) PyPI library
- Discovery: M-SEARCH multicast to `239.255.255.250:1982`
- Multi-bulb registry keyed by MAC; persists to `~/.config/yeelight/state.json`
- Identical HTTP contract to `wiz-core` (see
  [`docs/superpowers/specs/2026-05-17-unified-bulb-stack-design.md`](docs/superpowers/specs/2026-05-17-unified-bulb-stack-design.md)
  §1 and the [amendments](docs/superpowers/specs/2026-05-19-unified-bulb-stack-amendments.md)).

## One-time-per-bulb prerequisite: enable LAN Control

Yeelight bulbs ship with **"LAN Control" DISABLED**. Until you enable it,
TCP port 55443 stays closed and this daemon cannot reach the bulb.

For **each** bulb, once:

1. Open the **Mi Home** (or **Yeelight**) mobile app.
2. Select the bulb -> **Settings** (gear icon) -> **LAN Control**.
3. Toggle it **on**.

If LAN Control is off, control endpoints return:

    {"detail": "yeelight LAN control disabled for <ip>; enable it in the Mi Home app per bulb (Device -> Settings -> LAN Control)"}

## Quick start

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
yeelight-core serve            # foreground
# or as a systemd user unit:
mkdir -p ~/.config/systemd/user
cp systemd/yeelight-core.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now yeelight-core
```

## Onboarding a new bulb

`POST /onboard {ssid, password}` connects to the bulb's open setup AP
(`yeelink-*`), pushes your home Wi-Fi credentials, restores Wi-Fi, then
polls discovery. After the bulb joins, remember to enable LAN Control
(above) so the daemon can drive it.

## HTTP surface

Identical to `wiz-core`: `/health`, `/bulbs`, `/bulbs/default`,
`/discover`, `/onboard`, `/bulb/{target}/{on,off,brightness,temp,color,scene,name}`,
`/bulb/all/{on,off,brightness,temp,color,scene}` (broadcast), `/scenes`.
Every `/bulbs` entry carries `"protocol": "yeelight"`.

## Sibling repos

- [wiz-core](https://github.com/deepanshutr/wiz-core) — WiZ daemon
- [tuya-core](https://github.com/deepanshutr/tuya-core) — Tuya daemon
- [bulb-cli](https://github.com/deepanshutr/bulb-cli) — Go multiplexer CLI
- [bulb-mcp](https://github.com/deepanshutr/bulb-mcp) — MCP stdio server
