"""Per-fixture FPL points breakdown from event/{gw}/live/ explain[]."""

from __future__ import annotations

from transform.load import latest_bootstrap, load_live_payloads


def explain_labels(bootstrap: dict | None = None) -> dict[str, str]:
    if bootstrap is None:
        _, bootstrap = latest_bootstrap()
    out: dict[str, str] = {}
    for item in bootstrap.get("element_stats") or []:
        name = item.get("name")
        label = item.get("label")
        if name and label:
            out[str(name)] = str(label)
    return out


def _as_number(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if f.is_integer():
        return int(f)
    return f


def normalize_explain_stats(stats, labels: dict[str, str]) -> list[dict]:
    """Keep FPL order. identifier + FPL label + value + points. No extras."""
    out: list[dict] = []
    for stat in stats or []:
        ident = stat.get("identifier")
        if not ident:
            continue
        label = stat.get("label") or labels.get(str(ident)) or str(ident)
        row = {
            "identifier": str(ident),
            "label": str(label),
            "value": _as_number(stat.get("value")),
            "points": _as_number(stat.get("points")) or 0,
        }
        out.append(row)
    return out


def load_explain_index(
    player_map: dict[tuple[str, int], int] | None = None,
) -> tuple[dict[tuple[str, int, int, int], list[dict]], set[tuple[str, int]]]:
    """(season, gw, player_code, fixture_id) -> explain stats. live_gws = {(season, gw)}."""
    labels = explain_labels()
    lookup: dict[tuple[str, int, int, int], list[dict]] = {}
    live_gws: set[tuple[str, int]] = set()
    payloads = load_live_payloads()
    if not payloads:
        return lookup, live_gws
    if player_map is None:
        player_map = {}
    for season, gw, payload in payloads:
        live_gws.add((str(season), int(gw)))
        for el in payload.get("elements") or []:
            element_id = el.get("id")
            if element_id is None:
                continue
            try:
                eid = int(element_id)
            except (TypeError, ValueError):
                continue
            code = player_map.get((str(season), eid))
            if code is None:
                continue
            for block in el.get("explain") or []:
                fid = block.get("fixture")
                if fid is None:
                    continue
                try:
                    fixture_id = int(fid)
                except (TypeError, ValueError):
                    continue
                lookup[(str(season), int(gw), int(code), fixture_id)] = normalize_explain_stats(
                    block.get("stats") or [], labels
                )
    return lookup, live_gws


def attach_explain(
    row: dict,
    *,
    season: str,
    gw: int | None,
    player_code: int,
    fixture_id: int | None,
    lookup: dict[tuple[str, int, int, int], list[dict]],
    live_gws: set[tuple[str, int]],
) -> None:
    """Mutate row. Omit the key when this GW has no live file."""
    if gw is None or (season, int(gw)) not in live_gws:
        return
    if fixture_id is None:
        return
    stats = lookup.get((season, int(gw), int(player_code), int(fixture_id)))
    if stats is None:
        return
    row["explain"] = stats
