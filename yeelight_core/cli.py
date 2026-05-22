"""Operator CLI: `yeelight-core serve` / `discover` / `list`."""

from __future__ import annotations

import asyncio
import json

import httpx
import typer
import uvicorn

from .config import load as load_settings

app = typer.Typer(no_args_is_help=True, help="yeelight-core operator CLI")


@app.command()
def serve(bind: str = typer.Option(None, "--bind", help="Override YEELIGHT_BIND")) -> None:
    """Run the HTTP daemon."""
    settings = load_settings()
    host_port = bind or settings.bind
    host, port_s = host_port.rsplit(":", 1)
    uvicorn.run(
        "yeelight_core.main:app",
        host=host,
        port=int(port_s),
        log_level=settings.log_level.lower(),
    )


@app.command()
def discover() -> None:
    """Hit a running daemon's /discover endpoint."""
    settings = load_settings()
    url = f"http://{settings.bind}/discover"

    async def go() -> None:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json={"passive": False})
            r.raise_for_status()
            typer.echo(json.dumps(r.json(), indent=2))

    asyncio.run(go())


@app.command(name="list")
def list_bulbs() -> None:
    """Hit a running daemon's /bulbs endpoint."""
    settings = load_settings()
    url = f"http://{settings.bind}/bulbs"

    async def go() -> None:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(url)
            r.raise_for_status()
            typer.echo(json.dumps(r.json(), indent=2))

    asyncio.run(go())


if __name__ == "__main__":
    app()
