"""ping_sim and dns_sim: deterministic network probes computed from topology and the scenario's failed set."""

from __future__ import annotations

PING_DEFINITION = {
    "type": "function",
    "name": "ping_sim",
    "description": "Ping a device from the monitoring host by hostname, IP or device id. Returns reachability, "
    "latency and the path of device ids from the gateway toward the target, ending where it breaks.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
    },
}

DNS_DEFINITION = {
    "type": "function",
    "name": "dns_sim",
    "description": "Resolve a DNS name, optionally against a named server. Returns answers and an rcode.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}, "server": {"type": "string"}},
        "required": ["name"],
    },
}


def _resolve(conn, target: str):
    return conn.execute(
        "SELECT id, uplink_device_id FROM devices WHERE id = ? OR hostname = ? COLLATE NOCASE OR mgmt_ip = ?",
        (target, target, target),
    ).fetchone()


def _chain_from_gateway(conn, device_id: str, gateway: str) -> list[str] | None:
    """Device ids from the gateway to the target, or None when the target has no path (passive gear)."""
    chain, current, seen = [], device_id, set()
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        if current == gateway:
            return list(reversed(chain))
        row = conn.execute("SELECT uplink_device_id FROM devices WHERE id = ?", (current,)).fetchone()
        current = row["uplink_device_id"] if row else None
    return None


def probe(ctx, device_id: str) -> dict:
    sim = ctx.sim
    chain = _chain_from_gateway(ctx.conn, device_id, sim.gateway_device_id)
    if chain is None:
        return {"reachable": False, "rtt_ms": None, "loss_pct": 100.0, "path": [device_id]}
    failed = set(sim.failed_device_ids)
    path: list[str] = []
    for hop in chain:
        path.append(hop)
        if hop in failed:
            return {"reachable": False, "rtt_ms": None, "loss_pct": 100.0, "path": path}
    rtt = sim.rtt_ms.get(device_id, round(0.8 + 0.6 * len(chain), 1))
    return {"reachable": True, "rtt_ms": rtt, "loss_pct": 0.0, "path": path}


def ping_sim(ctx, args: dict) -> dict:
    target = str(args.get("target", "")).strip()
    if ctx.sim is None:
        raise ValueError("network simulator is not loaded")
    row = _resolve(ctx.conn, target)
    if row is None:
        return {
            "target": target,
            "reachable": False,
            "rtt_ms": None,
            "loss_pct": 100.0,
            "path": [],
            "note": "unknown target",
        }
    return {"target": target, **probe(ctx, row["id"])}


def dns_sim(ctx, args: dict) -> dict:
    name = str(args.get("name", "")).strip().lower()
    if ctx.sim is None:
        raise ValueError("network simulator is not loaded")
    record = next((r for r in ctx.sim.dns_records if r.name.lower() == name), None)
    server = args.get("server")
    server_row = _resolve(ctx.conn, str(server)) if server else None
    server_id = server_row["id"] if server_row else ctx.sim.default_dns_server_device_id
    if server and server_row is None:
        return {"name": name, "answers": [], "rcode": "SERVFAIL", "note": f"unknown server {server}"}
    if server_id and not probe(ctx, server_id)["reachable"]:
        return {"name": name, "answers": [], "rcode": "TIMEOUT"}
    if record is None:
        return {"name": name, "answers": [], "rcode": "NXDOMAIN"}
    if record.served_by_device_id and not probe(ctx, record.served_by_device_id)["reachable"]:
        return {"name": name, "answers": [], "rcode": "SERVFAIL"}
    return {"name": name, "answers": record.answers, "rcode": "NOERROR"}
