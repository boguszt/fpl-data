"""Price and ownership series for web/data JSON, from snap_player_day."""

from __future__ import annotations

import json
import lzma
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from ingest.client import season_from_bootstrap
from ingest.paths import MARTS
from transform.fplcache import fplcache_dir, list_fplcache_files
from transform.load import latest_bootstrap

PRICE_FROM_SEASON = "2021-22"
NULL_PRICE_SEASONS = {
    "2016-17",
    "2017-18",
    "2018-19",
    "2019-20",
    "2020-21",
}
SEASON_PRICE_KEYS = (
    "price_now",
    "price_start",
    "price_delta",
    "own_now",
    "own_7d",
    "own_30d",
)


def _money(now_cost) -> float | None:
    if now_cost is None or pd.isna(now_cost):
        return None
    return round(float(now_cost) / 10.0, 1)


def _own(v) -> float | None:
    if v is None or pd.isna(v):
        return None
    return round(float(v), 1)


def _null_season_cols() -> dict:
    return {k: None for k in SEASON_PRICE_KEYS}


def load_snap_frame(con) -> pd.DataFrame:
    path = (MARTS / "snap_player_day.parquet").as_posix()
    df = con.execute(
        f"""
        SELECT season, player_code, snapshot_ts, now_cost, selected_by_percent
        FROM read_parquet('{path}')
        WHERE player_code IS NOT NULL
        """
    ).df()
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["snapshot_ts"], utc=True)
    df["player_code"] = pd.to_numeric(df["player_code"], errors="coerce").astype("Int64")
    df["season"] = df["season"].astype(str)
    df = df.dropna(subset=["ts", "player_code"])
    df["player_code"] = df["player_code"].astype("int64")
    df["day"] = df["ts"].dt.date
    return df.sort_values(["season", "player_code", "ts"]).reset_index(drop=True)


def _events_from_payload(payload: dict) -> list[dict]:
    season = season_from_bootstrap(payload)
    rows = []
    for ev in payload.get("events") or []:
        raw = str(ev.get("deadline_time") or "").replace("Z", "+00:00")
        if not raw or ev.get("id") is None:
            continue
        rows.append(
            {
                "season": season,
                "gw": int(ev["id"]),
                "deadline": datetime.fromisoformat(raw).astimezone(timezone.utc),
            }
        )
    return rows


def load_gw_deadlines() -> pd.DataFrame:
    """GW deadline_time from bootstrap events, not fixture kickoff.

    Completed seasons: last May fplcache file of each year (end of the PL
    campaign, so postponed deadlines are in). Current season: latest own
    bootstrap, which wins on duplicate (season, gw).
    """
    rows: list[dict] = []
    cache = fplcache_dir()
    if cache is not None:
        files = list_fplcache_files(cache)
        picked: dict[str, Path] = {}
        for path in files:
            month = int(path.parent.parent.name)
            if month == 5:
                picked[f"may-{path.parent.parent.parent.name}"] = path
        if files:
            picked["latest"] = files[-1]
        for path in picked.values():
            try:
                with lzma.open(path, "rt", encoding="utf-8") as fh:
                    payload = json.load(fh)
                rows.extend(_events_from_payload(payload))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print(f"  deadline load skip {path}: {exc}", flush=True)

    try:
        _, bootstrap = latest_bootstrap()
        rows.extend(_events_from_payload(bootstrap))
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        print(f"  deadline load: own bootstrap skipped ({exc})", flush=True)

    if not rows:
        return pd.DataFrame(columns=["season", "gw", "deadline"])
    out = pd.DataFrame(rows)
    out = out.drop_duplicates(subset=["season", "gw"], keep="last")
    print(
        f"  gw deadlines: {len(out)} rows across {out['season'].nunique()} seasons",
        flush=True,
    )
    return out


def _own_ago_frame(keys: pd.DataFrame, snaps: pd.DataFrame, days: int) -> pd.DataFrame:
    left = pd.DataFrame(
        {
            "season": keys["season"],
            "player_code": keys["player_code"],
            "target": keys["ts_last"] - pd.Timedelta(days=days),
        }
    ).sort_values("target")
    right = snaps[["season", "player_code", "ts", "selected_by_percent"]].sort_values(
        "ts"
    )
    merged = pd.merge_asof(
        left,
        right,
        left_on="target",
        right_on="ts",
        by=["season", "player_code"],
        direction="backward",
        tolerance=pd.Timedelta(hours=48),
    )
    merged["past_own"] = merged["selected_by_percent"].map(_own)
    return merged[["season", "player_code", "past_own"]]


def season_price_columns(snaps: pd.DataFrame) -> dict[tuple[str, int], dict]:
    """(season, player_code) -> season-file price/own fields."""
    out: dict[tuple[str, int], dict] = {}
    if snaps.empty:
        return out
    covered = snaps.loc[snaps["season"] >= PRICE_FROM_SEASON].copy()
    if covered.empty:
        return out
    covered = covered.sort_values(["season", "player_code", "ts"])
    grouped = covered.groupby(["season", "player_code"], sort=False)
    first = grouped.first().reset_index()
    last = grouped.last().reset_index()
    keys = last[["season", "player_code", "ts", "now_cost", "selected_by_percent"]].rename(
        columns={
            "ts": "ts_last",
            "now_cost": "cost_last",
            "selected_by_percent": "own_last",
        }
    )
    keys = keys.merge(
        first[["season", "player_code", "now_cost"]].rename(
            columns={"now_cost": "cost_first"}
        ),
        on=["season", "player_code"],
        how="left",
    )
    keys = keys.merge(
        _own_ago_frame(keys, covered, 7).rename(columns={"past_own": "own_7d_past"}),
        on=["season", "player_code"],
        how="left",
    )
    keys = keys.merge(
        _own_ago_frame(keys, covered, 30).rename(columns={"past_own": "own_30d_past"}),
        on=["season", "player_code"],
        how="left",
    )
    for rec in keys.to_dict("records"):
        price_now = _money(rec["cost_last"])
        price_start = _money(rec["cost_first"])
        own_now = _own(rec["own_last"])
        past7 = rec["own_7d_past"]
        past30 = rec["own_30d_past"]
        if past7 is not None and not (isinstance(past7, float) and pd.isna(past7)):
            own_7d = None if own_now is None else round(own_now - float(past7), 1)
        else:
            own_7d = None
        if past30 is not None and not (isinstance(past30, float) and pd.isna(past30)):
            own_30d = None if own_now is None else round(own_now - float(past30), 1)
        else:
            own_30d = None
        delta = None
        if price_now is not None and price_start is not None:
            delta = round(price_now - price_start, 1)
        out[(str(rec["season"]), int(rec["player_code"]))] = {
            "price_now": price_now,
            "price_start": price_start,
            "price_delta": delta,
            "own_now": own_now,
            "own_7d": own_7d,
            "own_30d": own_30d,
        }
    return out


def matchlog_prices(
    snaps: pd.DataFrame, deadlines: pd.DataFrame, keys: pd.DataFrame
) -> dict[tuple[str, int, int], dict]:
    """(season, player_code, gw) -> {price, price_delta}."""
    empty: dict[tuple[str, int, int], dict] = {}
    if snaps.empty or deadlines.empty or keys.empty:
        return empty
    work = keys.dropna(subset=["season", "player_code", "gw"]).copy()
    work["season"] = work["season"].astype(str)
    work["player_code"] = pd.to_numeric(work["player_code"], errors="coerce").astype("Int64")
    work["gw"] = pd.to_numeric(work["gw"], errors="coerce").astype("Int64")
    work = work.dropna(subset=["player_code", "gw"])
    work = work.merge(deadlines, on=["season", "gw"], how="left")
    work = work.dropna(subset=["deadline"])
    work = work.loc[work["season"] >= PRICE_FROM_SEASON]
    if work.empty:
        return empty

    right = snaps.loc[
        snaps["season"] >= PRICE_FROM_SEASON, ["season", "player_code", "ts", "now_cost"]
    ].dropna(subset=["ts", "player_code"])
    right = right.copy()
    right["player_code"] = right["player_code"].astype("int64")
    work["player_code"] = work["player_code"].astype("int64")
    work["gw"] = work["gw"].astype("int64")

    merged = pd.merge_asof(
        work.sort_values("deadline"),
        right.sort_values("ts"),
        left_on="deadline",
        right_on="ts",
        by=["season", "player_code"],
        direction="backward",
        tolerance=pd.Timedelta(hours=48),
    )
    merged["price"] = merged["now_cost"].map(_money)
    uniq = (
        merged[["season", "player_code", "gw", "price"]]
        .drop_duplicates(subset=["season", "player_code", "gw"])
        .sort_values(["season", "player_code", "gw"])
    )
    uniq["price_delta"] = uniq.groupby(["season", "player_code"], sort=False)["price"].diff()
    uniq["price_delta"] = uniq["price_delta"].map(
        lambda v: None if v is None or pd.isna(v) else round(float(v), 1)
    )
    result = {}
    for rec in uniq.to_dict("records"):
        price = rec["price"]
        if price is None or (isinstance(price, float) and pd.isna(price)):
            price = None
        else:
            price = round(float(price), 1)
        delta = rec["price_delta"]
        if delta is None or (isinstance(delta, float) and pd.isna(delta)):
            delta = None
        else:
            delta = round(float(delta), 1)
        result[(str(rec["season"]), int(rec["player_code"]), int(rec["gw"]))] = {
            "price": price,
            "price_delta": delta,
        }
    return result


def _midday_daily(snaps: pd.DataFrame) -> pd.DataFrame:
    """One row per player-season-day: snapshot closest to 12:00 UTC."""
    if snaps.empty:
        return snaps
    rows = snaps.copy()
    midday = rows["ts"].dt.normalize() + pd.Timedelta(hours=12)
    rows["midday_dist"] = (rows["ts"] - midday).abs()
    rows = rows.sort_values(["season", "player_code", "day", "midday_dist"])
    return rows.drop_duplicates(subset=["season", "player_code", "day"], keep="first")


def build_pricehistory(snaps: pd.DataFrame) -> dict[str, dict[str, dict]]:
    by_season: dict[str, dict[str, dict]] = {}
    covered = snaps.loc[snaps["season"] >= PRICE_FROM_SEASON].copy()
    if covered.empty:
        return by_season
    covered["price"] = covered["now_cost"].map(_money)
    priced = covered.dropna(subset=["price"]).sort_values(
        ["season", "player_code", "ts"]
    )
    priced["prev_price"] = priced.groupby(["season", "player_code"], sort=False)[
        "price"
    ].shift()
    changes = priced[
        priced["prev_price"].isna() | (priced["price"] != priced["prev_price"])
    ]
    changes = changes.drop_duplicates(
        subset=["season", "player_code", "day"], keep="last"
    )
    daily = _midday_daily(covered)
    daily["own_v"] = daily["selected_by_percent"].map(_own)
    for season, sgrp in covered.groupby("season", sort=False):
        days = sorted(sgrp["day"].unique())
        start: date = days[0]
        end: date = days[-1]
        span = (end - start).days + 1
        ch = changes.loc[changes["season"] == season]
        dgrp = daily.loc[daily["season"] == season]
        logs: dict[int, list[list]] = {}
        for rec in ch.itertuples(index=False):
            code = int(rec.player_code)
            off = (rec.day - start).days
            logs.setdefault(code, []).append([off, float(rec.price)])
        for code, log in logs.items():
            logs[code] = sorted(log, key=lambda pair: pair[0])
        own_by: dict[int, list] = {}
        for rec in dgrp.itertuples(index=False):
            code = int(rec.player_code)
            series = own_by.get(code)
            if series is None:
                series = [None] * span
                own_by[code] = series
            i = (rec.day - start).days
            if 0 <= i < span:
                v = rec.own_v
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    series[i] = float(v)
        players: dict[str, dict] = {}
        for code in sgrp["player_code"].unique():
            code_i = int(code)
            players[str(code_i)] = {
                "start": start.isoformat(),
                "price": logs.get(code_i, []),
                "own": own_by.get(code_i, [None] * span),
            }
        by_season[str(season)] = players
        print(
            f"  pricehistory {season}: {len(players)} players  "
            f"{span} days  {start.isoformat()}..{end.isoformat()}",
            flush=True,
        )
    return by_season


def validate_pricehistory(
    histories: dict[str, dict[str, dict]],
    season_cols: dict[tuple[str, int], dict],
    season_rows: dict[str, list[dict]],
    matchlogs: dict[str, dict[str, list[dict]]] | None = None,
) -> None:
    errors: list[str] = []
    for season in NULL_PRICE_SEASONS:
        if season in histories:
            errors.append(f"{season} should not have a pricehistory file")
        for row in season_rows.get(season, []):
            code = row.get("player_code")
            if any(row.get(k) is not None for k in SEASON_PRICE_KEYS):
                errors.append(
                    f"{season} player {code} has non-NULL price columns"
                )
                break
        if matchlogs:
            for code, rows in matchlogs.get(season, {}).items():
                if any(
                    r.get("price") is not None or r.get("price_delta") is not None
                    for r in rows
                ):
                    errors.append(
                        f"{season} player {code} matchlog has non-NULL price"
                    )
                    break

    priced_rows = 0
    if matchlogs:
        for season, players in matchlogs.items():
            if season < PRICE_FROM_SEASON:
                continue
            for rows in players.values():
                priced_rows += sum(1 for r in rows if r.get("price") is not None)
        if priced_rows < 1000:
            errors.append(
                f"only {priced_rows} matchlog rows have a deadline price "
                "(expected thousands from 2021-22 onward)"
            )

    n_priced_cols = sum(
        1
        for (season, _), vals in season_cols.items()
        if season >= PRICE_FROM_SEASON and vals.get("price_now") is not None
    )
    if n_priced_cols < 100:
        errors.append(
            f"only {n_priced_cols} player-seasons have price_now "
            "(expected hundreds from 2021-22 onward)"
        )
    print(
        "NULL price seasons confirmed: " + ", ".join(sorted(NULL_PRICE_SEASONS)),
        flush=True,
    )

    for season, players in histories.items():
        if season < PRICE_FROM_SEASON:
            errors.append(f"unexpected pricehistory season {season}")
            continue
        span = None
        for code, obj in players.items():
            start = date.fromisoformat(obj["start"])
            log = obj["price"]
            own = obj["own"]
            if span is None:
                span = len(own)
            elif len(own) != span:
                errors.append(
                    f"{season} {code}: own length {len(own)} != season span {span}"
                )
            offsets = [pair[0] for pair in log]
            if offsets != sorted(offsets):
                errors.append(f"{season} {code}: price offsets not monotonic")
            if len(offsets) != len(set(offsets)):
                errors.append(f"{season} {code}: duplicate price offsets")
            for off, price in log:
                if off < 0 or (span is not None and off >= span):
                    errors.append(f"{season} {code}: offset {off} outside span")
                if price is None:
                    continue
                tenths = round(price * 10)
                if abs(price * 10 - tenths) > 1e-6:
                    errors.append(f"{season} {code}: price {price} not a multiple of 0.1")

    sample = None
    for season in ("2024-25", "2025-26", "2021-22"):
        players = histories.get(season) or {}
        if "118748" in players:
            sample = (season, "118748", "M.Salah", players["118748"])
            print(
                f"pricehistory Salah {season}  "
                f"{len(players['118748']['price'])} price points  "
                f"own_len={len(players['118748']['own'])}  "
                f"start={players['118748']['start']}",
                flush=True,
            )
            print(f"  {players['118748']['price']}", flush=True)
    if sample is None:
        for season in ("2025-26", "2024-25", "2021-22"):
            players = histories.get(season) or {}
            if players:
                code = next(iter(players))
                sample = (season, code, code, players[code])
                break
    if sample:
        season, code, name, obj = sample
        log = obj["price"]
        if name != "M.Salah":
            print(
                f"pricehistory spot check {name} {season}  "
                f"{len(log)} price points  own_len={len(obj['own'])}  "
                f"start={obj['start']}",
                flush=True,
            )
            print(f"  {log}", flush=True)
        if not 1 <= len(log) <= 80:
            errors.append(
                f"{name} {season} price log has {len(log)} entries "
                "(expected tens, not hundreds or zero)"
            )

    if errors:
        raise RuntimeError("pricehistory validation failed:\n- " + "\n- ".join(errors[:20]))
    print("pricehistory validation passed", flush=True)
