#!/usr/bin/env python3
"""Seed Atlas Shrugged-themed demo servers into a running Roundhouse stack.

Idempotent: existing demo servers (anything in DEMO_NAMES) are deleted first.
Run with --cleanup to delete and exit. Run with no args to seed.

Usage:
    python docs/capture/seed_demo.py --base http://localhost:3080 \
        --email admin@mcp.local --password admin

The screenshot pipeline in docs/capture/capture.mjs assumes the names below.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any


# Atlas Shrugged industrial empire — these are the names that will appear in
# every dashboard, table, and editor shot. Each tells a story matched to the
# Roundhouse feature it showcases.
DEMO_NAMES = [
    "taggart-transcontinental",
    "rearden-metal",
    "danconia-copper",
    "wyatt-oil",
    "galt-engine",
    "mulligan-bank",
    "stockton-foundry",
]


def http(method: str, base: str, path: str, token: str | None, body: dict | None = None) -> Any:
    """Issue an HTTP request via curl. Python's urllib trips this stack's auth
    middleware on token-bearing requests for reasons we never tracked down;
    curl works identically and avoids the rabbit hole."""
    url = base.rstrip("/") + path
    cmd = ["curl", "-sS", "-X", method, url, "-H", "Content-Type: application/json"]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    cmd += ["-w", "\nHTTP_STATUS=%{http_code}"]
    raw = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    body_text, _, status_line = raw.rpartition("\nHTTP_STATUS=")
    status = int(status_line.strip() or "0")
    if status >= 400:
        print(f"  HTTP {status} {method} {path}: {body_text[:400]}", file=sys.stderr)
        raise RuntimeError(f"HTTP {status} on {method} {path}")
    return json.loads(body_text) if body_text.strip() else None


def login(base: str, email: str, password: str) -> str:
    out = http("POST", base, "/api/auth/login", None, {"email": email, "password": password})
    return out["access_token"]


def delete_demo_servers(base: str, token: str) -> None:
    """Drop any prior demo servers so the seed is repeatable."""
    servers = http("GET", base, "/api/servers", token) or []
    existing = {s["name"] for s in servers}
    for name in DEMO_NAMES:
        if name in existing:
            print(f"  - deleting prior {name}")
            try:
                http("DELETE", base, f"/api/servers/{name}", token)
            except Exception:
                pass


def create_from_template(base: str, token: str, name: str, description: str) -> None:
    print(f"  + creating {name} (template)")
    http("POST", base, "/api/servers", token, {
        "name": name,
        "description": description,
        "template": "hello-world",
        "mode": "structured",
        "config": {},
    })


def create_from_code(base: str, token: str, name: str, description: str, source: str) -> None:
    print(f"  + creating {name} (code mode)")
    http("POST", base, "/api/servers", token, {
        "name": name,
        "description": description,
        "mode": "code",
        "source": source,
    })


def add_primitive(base: str, token: str, server: str, prim: dict) -> None:
    # API uses {"code": "..."} for the body; accept either {body} or {code}.
    if "body" in prim and "code" not in prim:
        prim = {**prim, "code": prim.pop("body")}
    http("POST", base, f"/api/servers/{server}/primitives", token, {"primitive": prim})


def set_env(base: str, token: str, server: str, env_vars: list[dict]) -> None:
    http("PUT", base, f"/api/servers/{server}/env", token, {
        "env_global_imports": [],
        "env_vars": env_vars,
    })


def set_pip(base: str, token: str, server: str, pip: list[str]) -> None:
    http("PUT", base, f"/api/servers/{server}/packages", token, {"pip_packages": pip})


def set_description(base: str, token: str, server: str, description: str) -> None:
    http("PUT", base, f"/api/servers/{server}/description", token, {"description": description})


def stop(base: str, token: str, server: str) -> None:
    try:
        http("POST", base, f"/api/servers/{server}/stop", token)
    except Exception:
        pass


# -------- Server definitions --------

def seed_taggart(base: str, token: str) -> None:
    """Flagship structured server, running. Many tools + prompts + env vars."""
    name = "taggart-transcontinental"
    create_from_template(
        base, token, name,
        "Continental rail operations: scheduling, routing, and dispatch for the John Galt Line.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "schedule_train",
        "description": "Schedule a new train run between two terminals.",
        "parameters": [
            {"name": "origin", "type": "string", "required": True},
            {"name": "destination", "type": "string", "required": True},
            {"name": "departure", "type": "string", "required": True, "description": "ISO 8601 timestamp"},
        ],
        "body": 'return {"train_id": "JGL-' + '4242", "origin": origin, "destination": destination, "departure": departure}',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "list_routes",
        "description": "List all active continental routes.",
        "parameters": [],
        "body": 'return [{"id": "JGL", "name": "John Galt Line", "miles": 2700},\n            {"id": "TAG-N", "name": "Northern Mainline", "miles": 1840}]',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "get_train_status",
        "description": "Return current status of a train.",
        "parameters": [
            {"name": "train_id", "type": "string", "required": True},
        ],
        "body": 'return {"train_id": train_id, "status": "on_time", "location": "Mile 1402, Wyatt Junction"}',
    })
    add_primitive(base, token, name, {
        "kind": "resource", "name": "track_map",
        "uri": "taggart://map/continental",
        "description": "SVG of the continental track network.",
        "body": 'return "<svg>...continental network...</svg>"',
    })
    add_primitive(base, token, name, {
        "kind": "prompt", "name": "morning_briefing",
        "description": "Summarize overnight rail activity for executive review.",
        "parameters": [
            {"name": "region", "type": "string", "required": False},
        ],
        "body": 'return f"Summarize rail ops for {region or \\"the whole continent\\"} overnight."',
    })
    set_env(base, token, name, [
        {"name": "TAGGART_API_KEY", "value": "tg_live_4242", "secret": True},
        {"name": "DISPATCH_REGION", "value": "continental", "secret": False},
        {"name": "JOHN_GALT_LINE_ENABLED", "value": "true", "secret": False},
        {"name": "LOG_LEVEL", "value": "INFO", "secret": False},
    ])


def seed_rearden(base: str, token: str) -> None:
    """Structured server with pip deps + resources."""
    name = "rearden-metal"
    create_from_template(
        base, token, name,
        "Alloy assay and smelter telemetry for Rearden Metal production.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "assay_sample",
        "description": "Run a chemical assay on a metal sample.",
        "parameters": [
            {"name": "sample_id", "type": "string", "required": True},
        ],
        "body": ('return {\n'
                 '    "sample_id": sample_id,\n'
                 '    "alloy": "Rearden Metal",\n'
                 '    "tensile_strength_mpa": 4250,\n'
                 '    "density_g_cm3": 6.1,\n'
                 '}'),
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "smelter_status",
        "description": "Get live telemetry from a smelter.",
        "parameters": [
            {"name": "smelter", "type": "string", "required": True},
        ],
        "body": 'return {"smelter": smelter, "temperature_c": 2680, "throughput_tph": 412}',
    })
    add_primitive(base, token, name, {
        "kind": "resource_template", "name": "smelter_log",
        "uri_template": "rearden://smelters/{id}/log",
        "description": "Tail log lines for the given smelter id.",
        "body": 'return f"Smelter {id} log entries..."',
    })
    set_pip(base, token, name, ["numpy"])
    set_env(base, token, name, [
        {"name": "REARDEN_VAULT_TOKEN", "value": "rdn_vault_91a2", "secret": True},
        {"name": "FOUNDRY_REGION", "value": "pittsburgh", "secret": False},
    ])


def seed_danconia(base: str, token: str) -> None:
    """Structured, stopped — shows the gray status badge in the table."""
    name = "danconia-copper"
    create_from_template(
        base, token, name,
        "Mine telemetry and ore-grade reporting for d'Anconia Copper operations.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "list_mines",
        "description": "List active d'Anconia copper mines.",
        "parameters": [],
        "body": 'return [{"id": "CHX-1", "country": "Chile", "depth_m": 1100},\n            {"id": "MTN-2", "country": "Montana", "depth_m": 820}]',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "ore_grade",
        "description": "Latest ore-grade percentage for a mine.",
        "parameters": [
            {"name": "mine_id", "type": "string", "required": True},
        ],
        "body": 'return {"mine_id": mine_id, "grade_pct": 1.84, "trend": "improving"}',
    })
    set_env(base, token, name, [
        {"name": "DANCONIA_API_TOKEN", "value": "dc_chx_4901", "secret": True},
    ])
    # Stop it so the badge reads "stopped".
    stop(base, token, name)


def seed_wyatt(base: str, token: str) -> None:
    """Code-mode server, running, custom server.py. Showcases code editor."""
    source = '''"""Wyatt Oil — shale extraction telemetry MCP."""
from fastmcp import FastMCP

mcp = FastMCP("wyatt-oil")


@mcp.tool
def well_pressure(well_id: str) -> dict:
    """Return real-time pressure (psi) at a Wyatt Oil shale well."""
    return {"well_id": well_id, "pressure_psi": 3210, "trend": "steady"}


@mcp.tool
def list_wells(field: str = "colorado") -> list[dict]:
    """List Wyatt Oil wells in the given field."""
    return [
        {"id": "WYT-1", "field": field, "depth_m": 2400, "status": "producing"},
        {"id": "WYT-2", "field": field, "depth_m": 2150, "status": "producing"},
        {"id": "WYT-7", "field": field, "depth_m": 2880, "status": "maintenance"},
    ]


@mcp.tool
def shale_yield(field: str) -> dict:
    """Daily shale-oil yield (barrels) for a field."""
    return {"field": field, "daily_barrels": 184_200, "unit": "bbl/day"}


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
        json_response=True,
    )
'''
    create_from_code(
        base, token, "wyatt-oil",
        "Shale-well pressure, yield, and field telemetry. Custom code-mode server.",
        source,
    )
    set_env(base, token, "wyatt-oil", [
        {"name": "WYATT_WELL_TOKEN", "value": "wo_p4_9301", "secret": True},
        {"name": "DEFAULT_FIELD", "value": "colorado", "secret": False},
    ])


def seed_galt(base: str, token: str) -> None:
    """Code-mode server with LOG_LEVEL=DEBUG set — showcases the new dropdown.

    Created as structured so it lands in 'running' state and the logs/env tabs
    look meaningful. The LOG_LEVEL env var will be visible in both the Logs
    tab dropdown and the Env vars editor.
    """
    name = "galt-engine"
    create_from_template(
        base, token, name,
        "Static-electricity motor research — generator output, atmospheric draw, conversion efficiency.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "generator_output",
        "description": "Current output of the Galt motor (kW).",
        "parameters": [],
        "body": 'return {"output_kw": 18_420, "efficiency_pct": 99.2, "atmosphere_draw": "nominal"}',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "atmospheric_draw",
        "description": "Static-charge intake reading at the experimental site.",
        "parameters": [
            {"name": "site", "type": "string", "required": False},
        ],
        "body": 'return {"site": site or "colorado-lab", "charge_kv_m": 142.7, "weather": "clear"}',
    })
    add_primitive(base, token, name, {
        "kind": "resource", "name": "schematic",
        "uri": "galt://motor/schematic",
        "description": "Engineering schematic of the motor.",
        "body": 'return "Schematic redacted — see Mr. Galt for the full drawing."',
    })
    # DEBUG level showcases the new platform-wide log level dropdown.
    set_env(base, token, name, [
        {"name": "LOG_LEVEL", "value": "DEBUG", "secret": False},
        {"name": "GALT_LAB_SECRET", "value": "g_lab_1957", "secret": True},
        {"name": "OBSERVATORY_REGION", "value": "colorado", "secret": False},
    ])


def seed_mulligan(base: str, token: str) -> None:
    """Structured server with a richer description, stopped to show variety."""
    name = "mulligan-bank"
    create_from_template(
        base, token, name,
        "Reserve-currency clearing and account telemetry for Mulligan Bank (denominated in Mulligan dollars).",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "account_balance",
        "description": "Look up the balance of a Mulligan Bank account.",
        "parameters": [
            {"name": "account_id", "type": "string", "required": True},
        ],
        "body": 'return {"account_id": account_id, "balance_mb": 142_900, "currency": "MB"}',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "clear_transfer",
        "description": "Clear a wire transfer between Mulligan accounts.",
        "parameters": [
            {"name": "from_account", "type": "string", "required": True},
            {"name": "to_account", "type": "string", "required": True},
            {"name": "amount_mb", "type": "number", "required": True},
        ],
        "body": 'return {"cleared": True, "from": from_account, "to": to_account, "amount_mb": amount_mb}',
    })
    set_env(base, token, name, [
        {"name": "MULLIGAN_CLEARING_KEY", "value": "mb_clr_88aa", "secret": True},
        {"name": "RESERVE_LEDGER_URL", "value": "https://ledger.mulligan.bank/v1", "secret": False},
    ])
    stop(base, token, name)


def seed_stockton(base: str, token: str) -> None:
    """Simple structured server with a single tool — round-out the dashboard."""
    name = "stockton-foundry"
    create_from_template(
        base, token, name,
        "Iron foundry batch tracking — pours, billets, and quench yields.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "list_pours",
        "description": "List today's foundry pours.",
        "parameters": [],
        "body": ('return [\n'
                 '    {"batch": "SF-1842", "tons": 18.4, "alloy": "carbon steel"},\n'
                 '    {"batch": "SF-1843", "tons": 22.1, "alloy": "rearden metal"},\n'
                 ']'),
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "quench_yield",
        "description": "Quench yield (%) for a billet batch.",
        "parameters": [
            {"name": "batch", "type": "string", "required": True},
        ],
        "body": 'return {"batch": batch, "yield_pct": 97.4}',
    })
    set_env(base, token, name, [
        {"name": "STOCKTON_OPERATOR", "value": "wisconsin-line", "secret": False},
    ])


SEEDERS = [
    seed_taggart,
    seed_rearden,
    seed_danconia,
    seed_wyatt,
    seed_galt,
    seed_mulligan,
    seed_stockton,
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:3080")
    p.add_argument("--email", default="admin@mcp.local")
    p.add_argument("--password", default="admin")
    p.add_argument("--cleanup", action="store_true", help="delete demo servers and exit")
    args = p.parse_args()

    print(f"Logging in to {args.base} as {args.email}...")
    token = login(args.base, args.email, args.password)
    print("Cleaning up any prior demo servers...")
    delete_demo_servers(args.base, token)
    if args.cleanup:
        print("Cleanup complete.")
        return 0

    print("Seeding Atlas Shrugged demo servers...")
    for seeder in SEEDERS:
        try:
            seeder(args.base, token)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {seeder.__name__} failed: {e}", file=sys.stderr)
        time.sleep(0.4)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
