#!/usr/bin/env python3
"""Seed Taggart Transcontinental demo state into a running Roundhouse stack.

The demo fleet is one railroad's internal MCP servers — dispatch, maintenance
of way, crew calling, signals, billing, yard ops, and motive power — so every
screenshot tells a coherent operational story that matches the Roundhouse
rail theme.

Sub-commands:
    seed       Create the seven Taggart servers (idempotent).
    users      Create Taggart staff users + department teams + memberships
               (idempotent).
    traffic    Call every tool/resource/prompt on the deployed Taggart servers
               via /api/servers/{name}/... so dashboard + usage charts
               populate. Mixes in deliberate errors so error rate > 0.
    hide-real  Export the user's real servers (default: audit-test,
               logic-monitor) to docs/capture/.backups/ then delete them
               from the stack. Use this before capturing screenshots.
    restore    Re-import every spec under docs/capture/.backups/ via
               /api/servers/import and redeploy.
    cleanup    Delete demo servers + users + teams.
    full       Run hide-real → seed → users → traffic in sequence.

All commands accept --base / --email / --password.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# Taggart Transcontinental's internal tooling — these are the names that
# appear in every dashboard, table, and editor shot. Each tells a story
# matched to the Roundhouse feature it showcases.
DEMO_NAMES = [
    "dispatch",
    "track-maintenance",
    "crew-scheduling",
    "signal-telemetry",
    "freight-billing",
    "yard-inventory",
    "motive-power",
]

# The user's "real" servers — exported + deleted before the docs run so they
# don't bleed into screenshots. Restored via the `restore` sub-command.
REAL_SERVERS = ["audit-test", "logic-monitor", "test-server"]

# Where exported specs land. Kept inside the repo so a partial run is
# recoverable across reboots; ignored by capture/.gitignore.
BACKUP_DIR = Path(__file__).resolve().parent / ".backups"

# Taggart Transcontinental staff.
DEMO_USERS = [
    # email                    display_name      role
    ("dagny@taggart.rail",     "Dagny Taggart",  "user"),
    ("eddie@taggart.rail",     "Eddie Willers",  "user"),
    ("james@taggart.rail",     "James Taggart",  "user"),
    ("owen@taggart.rail",      "Owen Kellogg",   "user"),
    ("gwen@taggart.rail",      "Gwen Ives",      "user"),
]
DEMO_TEAMS = [
    # team_name              description                                              members (email, role)
    ("Operations",           "Dispatch, crew, and motive power for the continental network.",
        [("dagny@taggart.rail", "admin"), ("eddie@taggart.rail", "member")]),
    ("Maintenance of Way",   "Track, signal, and structures inspection and repair.",
        [("owen@taggart.rail", "admin")]),
    ("Revenue Service",      "Waybill rating, freight settlement, and yard inventory.",
        [("james@taggart.rail", "admin"), ("gwen@taggart.rail", "member")]),
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
    for name in DEMO_NAMES + LEGACY_DEMO_NAMES:
        if name in existing:
            print(f"  - deleting prior {name}")
            try:
                http("DELETE", base, f"/api/servers/{name}", token)
            except Exception:
                pass


# Prior Atlas-empire cast — still deleted on reseed/cleanup so upgrading an
# existing stack doesn't leave orphans behind.
LEGACY_DEMO_NAMES = [
    "taggart-transcontinental",
    "rearden-metal",
    "danconia-copper",
    "wyatt-oil",
    "galt-engine",
    "mulligan-bank",
    "stockton-foundry",
]
LEGACY_DEMO_TEAMS = ["Taggart Operations", "Rearden Industries", "Galt's Gulch"]
LEGACY_DEMO_USERS = [
    "dagny@taggart.rail",
    "henry@rearden.metal",
    "francisco@danconia.copper",
    "john@galt.engine",
    "hugh@galt.engine",
]


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

def seed_dispatch(base: str, token: str) -> None:
    """Flagship structured server, running. Many tools + prompts + env vars."""
    name = "dispatch"
    create_from_template(
        base, token, name,
        "Train dispatch for the Taggart continental network — scheduling, routing, and movement authority.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "schedule_train",
        "description": "Schedule a new train run between two terminals.",
        "parameters": [
            {"name": "origin", "type": "string", "required": True},
            {"name": "destination", "type": "string", "required": True},
            {"name": "departure", "type": "string", "required": True, "description": "ISO 8601 timestamp"},
        ],
        "body": 'return {"train_id": "TT-' + '4208", "origin": origin, "destination": destination, "departure": departure}',
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
        # Avoid escaped quotes inside the f-string body — the codegen pastes
        # this verbatim into server.py and pre-3.12 Python rejects backslash
        # escapes inside f-string expressions.
        "body": ('scope = region or "the whole continent"\n'
                 'return f"Summarize rail ops for {scope} overnight."'),
    })
    set_env(base, token, name, [
        {"name": "DISPATCH_BOARD_KEY", "value": "tt_disp_4208", "secret": True},
        {"name": "DISPATCH_REGION", "value": "continental", "secret": False},
        {"name": "JOHN_GALT_LINE_ENABLED", "value": "true", "secret": False},
        {"name": "LOG_LEVEL", "value": "INFO", "secret": False},
    ])


def seed_track_maintenance(base: str, token: str) -> None:
    """Structured server with pip deps + resource template."""
    name = "track-maintenance"
    create_from_template(
        base, token, name,
        "Maintenance of way — track inspections, rail-wear analytics, and defect reporting.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "inspect_section",
        "description": "Latest inspection readings for a track section.",
        "parameters": [
            {"name": "section_id", "type": "string", "required": True},
        ],
        "body": ('return {\n'
                 '    "section_id": section_id,\n'
                 '    "rail_wear_mm": 3.2,\n'
                 '    "gauge_mm": 1435,\n'
                 '    "ballast": "good",\n'
                 '    "speed_restriction_mph": None,\n'
                 '}'),
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "defect_report",
        "description": "Open track defects for a division.",
        "parameters": [
            {"name": "division", "type": "string", "required": True},
        ],
        "body": ('return [\n'
                 '    {"id": "DEF-2214", "section": "JGL-MP-1388", "kind": "rail wear", "severity": "monitor"},\n'
                 '    {"id": "DEF-2217", "section": "TAG-N-MP-204", "kind": "ballast fouling", "severity": "repair"},\n'
                 ']'),
    })
    add_primitive(base, token, name, {
        "kind": "resource_template", "name": "section_inspections",
        "uri_template": "track://sections/{id}/inspections",
        "description": "Inspection history for the given track section id.",
        "body": 'return f"Inspection history for section {id}..."',
    })
    set_pip(base, token, name, ["numpy"])
    set_env(base, token, name, [
        {"name": "MOW_DB_TOKEN", "value": "mow_db_91a2", "secret": True},
        {"name": "HOME_DIVISION", "value": "colorado", "secret": False},
    ])


def seed_crew_scheduling(base: str, token: str) -> None:
    """Structured server with LOG_LEVEL=DEBUG — showcases the logs dropdown.

    Carries the heaviest traffic profile in the seed so its usage tab shows
    the most chart variety. The LOG_LEVEL env var is visible in both the
    Logs tab dropdown and the Env vars editor.
    """
    name = "crew-scheduling"
    create_from_template(
        base, token, name,
        "Crew calling and hours-of-service tracking for road and yard crews.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "next_assignment",
        "description": "Next call for a crew on the board.",
        "parameters": [
            {"name": "crew_id", "type": "string", "required": True},
        ],
        "body": 'return {"crew_id": crew_id, "train_id": "TT-93", "on_duty": "2026-06-12T05:30:00Z", "terminal": "Cheyenne"}',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "hours_of_service",
        "description": "Remaining hours of service for a crew.",
        "parameters": [
            {"name": "crew_id", "type": "string", "required": True},
        ],
        "body": 'return {"crew_id": crew_id, "hours_remaining": 4.5, "rest_required": False}',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "call_crew",
        "description": "Call the next rested crew for a train.",
        "parameters": [
            {"name": "train_id", "type": "string", "required": True},
        ],
        "body": 'return {"train_id": train_id, "engineer": "Pat Logan", "conductor": "Bill Brent", "report_at": "Cheyenne yard office"}',
    })
    add_primitive(base, token, name, {
        "kind": "resource", "name": "crew_roster",
        "uri": "crew://roster/active",
        "description": "Active crew roster with rest status.",
        "body": 'return "engineer Pat Logan: rested · conductor Bill Brent: rested · ..."',
    })
    # DEBUG level showcases the platform-wide log level dropdown.
    set_env(base, token, name, [
        {"name": "LOG_LEVEL", "value": "DEBUG", "secret": False},
        {"name": "CREW_BOARD_KEY", "value": "tt_crew_0093", "secret": True},
        {"name": "HOS_LIMIT_HOURS", "value": "12", "secret": False},
    ])


def seed_signal_telemetry(base: str, token: str) -> None:
    """Code-mode server, running, custom server.py. Showcases code editor."""
    source = '''"""Signal telemetry — block signals and grade crossings on the Taggart network."""
from fastmcp import FastMCP

mcp = FastMCP("signal-telemetry")


@mcp.tool
def signal_status(block_id: str) -> dict:
    """Current aspect and health of a block signal."""
    return {"block_id": block_id, "aspect": "clear", "lamp_voltage": 11.8, "battery_pct": 96}


@mcp.tool
def list_blocks(division: str = "colorado") -> list[dict]:
    """List signal blocks in the given division."""
    return [
        {"id": "JGL-114", "division": division, "aspect": "clear", "occupied": False},
        {"id": "JGL-115", "division": division, "aspect": "approach", "occupied": False},
        {"id": "JGL-116", "division": division, "aspect": "stop", "occupied": True},
    ]


@mcp.tool
def crossing_health(crossing_id: str) -> dict:
    """Gate, lamp, and battery health for a grade crossing."""
    return {"crossing_id": crossing_id, "gates": "ok", "lamps": "ok", "battery_pct": 94}


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
        base, token, "signal-telemetry",
        "Block-signal aspects, lamp voltage, and grade-crossing health. Custom code-mode server.",
        source,
    )
    set_env(base, token, "signal-telemetry", [
        {"name": "SIGNAL_NET_TOKEN", "value": "sig_net_9301", "secret": True},
        {"name": "DEFAULT_DIVISION", "value": "colorado", "secret": False},
    ])


def seed_freight_billing(base: str, token: str) -> None:
    """Structured, stopped — shows the gray status badge in the table."""
    name = "freight-billing"
    create_from_template(
        base, token, name,
        "Waybill rating and freight revenue settlement for the continental network.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "rate_quote",
        "description": "Quote a freight rate between two terminals.",
        "parameters": [
            {"name": "origin", "type": "string", "required": True},
            {"name": "destination", "type": "string", "required": True},
            {"name": "tons", "type": "number", "required": True},
        ],
        "body": 'return {"origin": origin, "destination": destination, "tons": tons, "rate_usd": round(tons * 14.25, 2)}',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "waybill_lookup",
        "description": "Look up a waybill by number.",
        "parameters": [
            {"name": "waybill_id", "type": "string", "required": True},
        ],
        "body": 'return {"waybill_id": waybill_id, "shipper": "Rearden Steel", "commodity": "structural rail", "status": "in_transit"}',
    })
    set_env(base, token, name, [
        {"name": "BILLING_LEDGER_KEY", "value": "rev_clr_88aa", "secret": True},
    ])
    # Stop it so the badge reads "stopped".
    stop(base, token, name)


def seed_yard_inventory(base: str, token: str) -> None:
    """Structured server, stopped, with more env-var variety."""
    name = "yard-inventory"
    create_from_template(
        base, token, name,
        "Classification-yard car inventory from trackside AEI tag readers.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "car_location",
        "description": "Locate a freight car by reporting mark.",
        "parameters": [
            {"name": "car_id", "type": "string", "required": True},
        ],
        "body": 'return {"car_id": car_id, "yard": "cheyenne", "track": "C-14", "last_aei_read": "2026-06-11T14:02:00Z"}',
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "classify_cut",
        "description": "Record a cut of cars classified to a bowl track.",
        "parameters": [
            {"name": "track", "type": "string", "required": True},
            {"name": "car_count", "type": "number", "required": True},
        ],
        "body": 'return {"track": track, "classified": car_count, "remaining_capacity": 31 - car_count}',
    })
    set_env(base, token, name, [
        {"name": "YARD_OFFICE_KEY", "value": "yard_chy_4411", "secret": True},
        {"name": "AEI_READER_URL", "value": "https://aei.taggart.rail/v1", "secret": False},
        {"name": "HOME_YARD", "value": "cheyenne", "secret": False},
    ])
    stop(base, token, name)


def seed_motive_power(base: str, token: str) -> None:
    """Simple structured server with two tools — round-out the dashboard."""
    name = "motive-power"
    create_from_template(
        base, token, name,
        "Locomotive fleet status — assignments, fuel, and shop schedule.",
    )
    add_primitive(base, token, name, {
        "kind": "tool", "name": "list_locomotives",
        "description": "List locomotives and their current assignments.",
        "parameters": [],
        "body": ('return [\n'
                 '    {"unit": "TT-601", "class": "road freight", "status": "on JGL-93"},\n'
                 '    {"unit": "TT-415", "class": "yard switcher", "status": "cheyenne yard"},\n'
                 '    {"unit": "TT-228", "class": "road freight", "status": "shop — roundhouse stall 4"},\n'
                 ']'),
    })
    add_primitive(base, token, name, {
        "kind": "tool", "name": "fuel_level",
        "description": "Fuel remaining for a locomotive.",
        "parameters": [
            {"name": "unit", "type": "string", "required": True},
        ],
        "body": 'return {"unit": unit, "gallons": 3800, "pct": 76}',
    })
    set_env(base, token, name, [
        {"name": "SHOP_LOCATION", "value": "cheyenne-roundhouse", "secret": False},
    ])


SEEDERS = [
    seed_dispatch,
    seed_track_maintenance,
    seed_crew_scheduling,
    seed_signal_telemetry,
    seed_freight_billing,
    seed_yard_inventory,
    seed_motive_power,
]


# -------- Sub-command: seed (Taggart servers) --------

def cmd_seed(base: str, token: str) -> None:
    print("Cleaning up any prior demo servers...")
    delete_demo_servers(base, token)
    print("Seeding Taggart Transcontinental demo servers...")
    for seeder in SEEDERS:
        try:
            seeder(base, token)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {seeder.__name__} failed: {e}", file=sys.stderr)
        time.sleep(0.4)
    # Primitives added through the API don't take effect until the running
    # container is rebuilt; without this, traffic generation just hits an
    # empty hello-world server and metrics never accumulate.
    print("Redeploying servers so seeded primitives register...")
    for name in DEMO_NAMES:
        try:
            info = http("GET", base, f"/api/servers/{name}", token) or {}
            # Don't redeploy stopped/exited servers — they were stopped on
            # purpose for screenshot variety.
            if info.get("status") != "running":
                continue
            http("POST", base, f"/api/servers/{name}/redeploy", token)
            print(f"  ~ redeployed {name}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! redeploy {name}: {e}", file=sys.stderr)


# -------- Sub-command: users + teams --------

def cmd_users(base: str, token: str) -> None:
    print("Seeding demo users + teams...")
    existing_emails = {u["email"]: u for u in (http("GET", base, "/api/users", token) or [])}
    user_ids: dict[str, str] = {}

    # Retire the prior Atlas cast so re-seeded stacks don't show both.
    for email in LEGACY_DEMO_USERS:
        if email in existing_emails and email not in {e for e, _, _ in DEMO_USERS}:
            try:
                http("DELETE", base, f"/api/users/{existing_emails[email]['id']}", token)
                print(f"  - retired legacy user: {email}")
                del existing_emails[email]
            except Exception:
                pass
    legacy_teams = {t["name"]: t for t in (http("GET", base, "/api/teams", token) or [])}
    for team_name in LEGACY_DEMO_TEAMS:
        if team_name in legacy_teams:
            try:
                http("DELETE", base, f"/api/teams/{legacy_teams[team_name]['id']}", token)
                print(f"  - retired legacy team: {team_name}")
            except Exception:
                pass

    for email, display_name, role in DEMO_USERS:
        if email in existing_emails:
            user_ids[email] = existing_emails[email]["id"]
            print(f"  = user exists: {email}")
            continue
        print(f"  + creating user: {display_name} <{email}>")
        # /api/auth/register requires superadmin (which we are) and creates
        # users without going through the email-verification flow.
        try:
            user = http("POST", base, "/api/auth/register", token, {
                "email": email,
                "password": "taggart-comet-1957",
                "display_name": display_name,
                "role": role,
            })
            user_ids[email] = user["id"]
        except Exception as e:  # noqa: BLE001
            print(f"    ! register failed for {email}: {e}", file=sys.stderr)

    existing_teams = {t["name"]: t for t in (http("GET", base, "/api/teams", token) or [])}
    for team_name, description, members in DEMO_TEAMS:
        team = existing_teams.get(team_name)
        if team is None:
            print(f"  + creating team: {team_name}")
            try:
                team = http("POST", base, "/api/teams", token, {
                    "name": team_name, "description": description,
                })
            except Exception as e:  # noqa: BLE001
                print(f"    ! team create failed: {e}", file=sys.stderr)
                continue
        else:
            print(f"  = team exists: {team_name}")
        team_id = team["id"]

        # Reload members so re-runs don't 409.
        team_full = http("GET", base, f"/api/teams/{team_id}", token) or {}
        member_ids = {m["user_id"] for m in (team_full.get("members") or [])}
        for email, member_role in members:
            uid = user_ids.get(email)
            if not uid or uid in member_ids:
                continue
            try:
                http("POST", base, f"/api/teams/{team_id}/members", token, {
                    "user_id": uid, "role": member_role,
                })
                print(f"    + member: {email} ({member_role})")
            except Exception as e:  # noqa: BLE001
                print(f"    ! add member failed: {e}", file=sys.stderr)


# -------- Sub-command: traffic --------

# Per-server invocation plan. The platform middleware records every call so
# both the dashboard and per-server usage charts populate. Counts vary so
# the "top servers by calls" chart has clear leaders + trailers; one
# deliberate failure per heavy server creates a non-zero error rate.
TRAFFIC_PLAN = {
    # crew-scheduling carries the heaviest profile → busiest usage shot.
    "crew-scheduling": {
        "tools": [
            ("next_assignment", {"crew_id": "CHY-ENG-12"}, 21),
            ("hours_of_service", {"crew_id": "CHY-ENG-12"}, 17),
            ("hours_of_service", {"crew_id": "DEN-CON-04"}, 9),
            ("call_crew", {"train_id": "TT-93"}, 11),
        ],
        "resources": [("crew://roster/active", 6)],
        "prompts": [],
    },
    "dispatch": {
        "tools": [
            ("list_routes", {}, 14),
            ("get_train_status", {"train_id": "TT-4208"}, 22),
            ("schedule_train", {"origin": "New York", "destination": "Cheyenne", "departure": "2026-06-12T08:00:00Z"}, 9),
            # Missing required arg → ToolError → bumps error_rate.
            ("schedule_train", {"origin": "Wyatt Junction"}, 3),
        ],
        "resources": [("taggart://map/continental", 5)],
        "prompts": [("morning_briefing", {"region": "Colorado"}, 4)],
    },
    "track-maintenance": {
        "tools": [
            ("inspect_section", {"section_id": "JGL-MP-1388"}, 11),
            ("defect_report", {"division": "colorado"}, 16),
        ],
        "resources": [("track://sections/JGL-MP-1388/inspections", 7)],
        "prompts": [],
    },
    "signal-telemetry": {
        "tools": [
            ("signal_status", {"block_id": "JGL-114"}, 13),
            ("list_blocks", {"division": "colorado"}, 6),
            ("crossing_health", {"crossing_id": "XING-204"}, 7),
        ],
        "resources": [],
        "prompts": [],
    },
    "motive-power": {
        "tools": [
            ("list_locomotives", {}, 8),
            ("fuel_level", {"unit": "TT-601"}, 12),
        ],
        "resources": [],
        "prompts": [],
    },
}


def cmd_traffic(base: str, token: str) -> None:
    print("Generating traffic against deployed Taggart servers...")
    total_tool, total_res, total_prompt, total_err = 0, 0, 0, 0
    for server, plan in TRAFFIC_PLAN.items():
        # Skip stopped/exited servers — they'd error every call. Check first.
        info = http("GET", base, f"/api/servers/{server}", token) or {}
        if info.get("status") != "running":
            print(f"  - skipping {server}: {info.get('status')}")
            continue
        print(f"  → {server}")
        for tool, args, count in plan.get("tools", []):
            for _ in range(count):
                try:
                    http("POST", base, f"/api/servers/{server}/tools/invoke", token, {
                        "tool": tool, "arguments": dict(args),
                    })
                    total_tool += 1
                except Exception:
                    total_err += 1
        for uri, count in plan.get("resources", []):
            for _ in range(count):
                try:
                    http("POST", base, f"/api/servers/{server}/resources/read", token, {
                        "uri": uri,
                    })
                    total_res += 1
                except Exception:
                    total_err += 1
        for prompt, args, count in plan.get("prompts", []):
            for _ in range(count):
                try:
                    http("POST", base, f"/api/servers/{server}/prompts/get", token, {
                        "prompt": prompt, "arguments": dict(args),
                    })
                    total_prompt += 1
                except Exception:
                    total_err += 1
    print(f"  tools={total_tool} resources={total_res} prompts={total_prompt} errors={total_err}")


# -------- Sub-command: hide-real / restore --------

def cmd_hide_real(base: str, token: str) -> None:
    """Export every server in REAL_SERVERS to BACKUP_DIR then delete it from
    the stack. Idempotent: re-running with no real servers present is a no-op
    and prior backups are preserved."""
    BACKUP_DIR.mkdir(exist_ok=True)
    servers = {s["name"] for s in (http("GET", base, "/api/servers", token) or [])}
    for name in REAL_SERVERS:
        if name not in servers:
            print(f"  - {name}: not present, skipping")
            continue
        try:
            export = http("GET", base, f"/api/servers/{name}/export", token)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name}: export failed ({e}); leaving server intact", file=sys.stderr)
            continue
        out = BACKUP_DIR / f"{name}.json"
        out.write_text(json.dumps(export, indent=2), encoding="utf-8")
        print(f"  + {name}: exported to {out.relative_to(Path.cwd())}")
        try:
            http("DELETE", base, f"/api/servers/{name}", token)
            print(f"  - {name}: deleted from stack")
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name}: delete failed: {e}", file=sys.stderr)


def cmd_restore(base: str, token: str) -> None:
    """Re-import every spec under BACKUP_DIR via /api/servers/import. After
    a successful import the file is moved aside (suffix .restored) so a
    second restore run doesn't 409."""
    if not BACKUP_DIR.is_dir():
        print(f"  no {BACKUP_DIR} directory; nothing to restore")
        return
    for spec_file in sorted(BACKUP_DIR.glob("*.json")):
        try:
            payload = json.loads(spec_file.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  ! {spec_file.name}: parse failed: {e}", file=sys.stderr)
            continue
        # `export` returns {"version", "exported_at", "spec"}; import wants
        # the spec dict under `spec`.
        spec = payload.get("spec") if isinstance(payload, dict) else None
        if not isinstance(spec, dict):
            print(f"  ! {spec_file.name}: no spec block", file=sys.stderr)
            continue
        try:
            http("POST", base, "/api/servers/import", token, {"spec": spec})
            spec_file.rename(spec_file.with_suffix(".json.restored"))
            print(f"  + restored {spec.get('name')}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! {spec_file.name}: import failed: {e}", file=sys.stderr)


# -------- Sub-command: cleanup --------

def cmd_cleanup(base: str, token: str) -> None:
    print("Cleaning up demo servers, teams, and users...")
    delete_demo_servers(base, token)
    teams = {t["name"]: t for t in (http("GET", base, "/api/teams", token) or [])}
    for team_name in [t[0] for t in DEMO_TEAMS] + LEGACY_DEMO_TEAMS:
        if team_name in teams:
            try:
                http("DELETE", base, f"/api/teams/{teams[team_name]['id']}", token)
                print(f"  - team: {team_name}")
            except Exception as e:  # noqa: BLE001
                print(f"  ! team {team_name}: {e}", file=sys.stderr)
    users = {u["email"]: u for u in (http("GET", base, "/api/users", token) or [])}
    for email in [u[0] for u in DEMO_USERS] + LEGACY_DEMO_USERS:
        if email in users:
            try:
                http("DELETE", base, f"/api/users/{users[email]['id']}", token)
                print(f"  - user: {email}")
            except Exception as e:  # noqa: BLE001
                print(f"  ! user {email}: {e}", file=sys.stderr)


# -------- CLI --------

CMDS = {
    "seed":      cmd_seed,
    "users":     cmd_users,
    "traffic":   cmd_traffic,
    "hide-real": cmd_hide_real,
    "restore":   cmd_restore,
    "cleanup":   cmd_cleanup,
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", nargs="?", default="seed",
                   help="seed | users | traffic | hide-real | restore | cleanup | full")
    p.add_argument("--base", default="http://localhost:3080")
    p.add_argument("--email", default="admin@mcp.local")
    p.add_argument("--password", default="admin")
    # Back-compat: the original script took `--cleanup` instead of a sub-command.
    p.add_argument("--cleanup", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()

    print(f"Logging in to {args.base} as {args.email}...")
    token = login(args.base, args.email, args.password)

    cmd = "cleanup" if args.cleanup else args.command
    if cmd == "full":
        # Full docs build sequence: hide the user's real servers, then seed
        # everything, generate traffic, and tell the operator how to restore.
        cmd_hide_real(args.base, token)
        cmd_seed(args.base, token)
        # Server containers take a couple of seconds to warm before
        # /tools/invoke succeeds — settle before the load run.
        time.sleep(4)
        cmd_users(args.base, token)
        cmd_traffic(args.base, token)
        print("\nDone. After capture, run:")
        print("  python3 docs/capture/seed_demo.py restore")
        return 0
    if cmd not in CMDS:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    CMDS[cmd](args.base, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
