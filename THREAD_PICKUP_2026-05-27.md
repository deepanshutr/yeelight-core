# THREAD_PICKUP — 2026-05-27 — yeelight-core

## What was attempted

The unified-bulb-stack project memory said "yeelight-core: built +
unit written, NOT started (needs Yeelight bulbs)". On the live host,
neither the systemd user unit nor the `~/.local/bin/yeelight-core`
symlink existed — claim was outdated.

## What shipped

No code changes in this repo. Operational fix on the host only:

```bash
# unit copied from the repo to the user systemd dir
cp ~/github.com/deepanshutr/yeelight-core/systemd/yeelight-core.service \
   ~/.config/systemd/user/yeelight-core.service

# binary symlinked into PATH (matches the wiz-core install pattern)
ln -s ~/github.com/deepanshutr/yeelight-core/.venv/bin/yeelight-core \
      ~/.local/bin/yeelight-core

systemctl --user daemon-reload
systemctl --user enable --now yeelight-core.service
```

Daemon now serves `127.0.0.1:8767`. `GET /health` → `{"ok":true}`.
`GET /bulbs` → `{"bulbs":[]}` because no Yeelight bulbs are
onboarded yet (LAN Control off, no devices on the network).

## What's blocked

- No Yeelight bulbs on the network. `LAN Control` toggle in Mi Home
  app + bulb onboarding is the next operator action.
- The repo's README says `mkdir -p ~/.config/systemd/user; cp
  systemd/yeelight-core.service ~/.config/systemd/user/` — true but
  silent on the binary install. Consider adding a one-liner to the
  README for the symlink step, OR ship a Makefile target. Skipped
  in this thread to keep the diff zero.

## Resume incantation

```bash
systemctl --user status yeelight-core --no-pager -n 5
curl -s 127.0.0.1:8767/health
curl -s 127.0.0.1:8767/bulbs | jq .
```

If you onboard a Yeelight bulb, hit `POST /discover` to populate
the registry, then a regular `bulb on yeelight-bulb-1` (or the
MCP equivalent) routes to this daemon automatically.
