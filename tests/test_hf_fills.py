"""Offline tests for the HuggingFace fill transform."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from src.data.hf_fills import (
    EXCHANGE_ADDRESS,
    FILL_SCHEMA,
    STOP_TS,
    fill_object_name,
    fills_from_orderfilled,
    interpolate_timestamps,
    is_short_crypto_slug,
    next_ts_window,
    row_groups_overlapping,
    u256_to_float,
)


def _u256(n: int) -> bytes:
    return int(n).to_bytes(32, "little")


def _raw(rows: list[dict]) -> pa.Table:
    cols = {name: [] for name in (
        "timestamp", "block_number", "transaction_hash", "log_index", "contract", "maker", "taker",
        "maker_asset_id", "taker_asset_id", "maker_amount_filled", "taker_amount_filled",
        "maker_fee", "taker_fee", "protocol_fee",
    )}
    for row in rows:
        for key in cols:
            cols[key].append(row[key])
    cols["maker_amount_filled"] = pa.array(cols["maker_amount_filled"], type=pa.binary(32))
    cols["taker_amount_filled"] = pa.array(cols["taker_amount_filled"], type=pa.binary(32))
    cols["maker_fee"] = pa.array(cols["maker_fee"], type=pa.binary(32))
    cols["taker_fee"] = pa.array(cols["taker_fee"], type=pa.binary(32))
    cols["protocol_fee"] = pa.array(cols["protocol_fee"], type=pa.binary(32))
    return pa.table(cols)


def _row(**over) -> dict:
    base = dict(
        timestamp=1_789_376_400,
        block_number=93_900_000,
        transaction_hash="0xabc",
        log_index=7,
        contract="CTF_EXCHANGE_V2",
        maker="0xMAKER",
        taker="0xUSER",
        maker_asset_id="999",
        taker_asset_id="0",
        maker_amount_filled=_u256(11_000_000),
        taker_amount_filled=_u256(22_000),
        maker_fee=_u256(0),
        taker_fee=_u256(0),
        protocol_fee=_u256(0),
    )
    base.update(over)
    return base


def test_u256_little_endian_micro_units():
    arr = pa.array([_u256(22_000), _u256(0)], type=pa.binary(32))
    got = u256_to_float(arr)
    assert got[0] == 0.022 and got[1] == 0.0


def test_maker_sell_and_taker_summary_match_fill_shape():
    exchange = EXCHANGE_ADDRESS["CTF_EXCHANGE_V2"]
    raw = _raw([
        _row(),
        _row(
            log_index=8,
            taker=exchange.upper(),
            maker_asset_id="0",
            taker_asset_id="999",
            maker_amount_filled=_u256(500_000),
            taker_amount_filled=_u256(1_000_000),
            maker_fee=_u256(1_500),
        ),
        _row(block_number=94_184_078, log_index=9),
        _row(log_index=10, maker_asset_id="999", taker_asset_id="888"),
    ])
    out, stats = fills_from_orderfilled(raw, block_lo=93_000_000, block_hi=94_184_078, short_tokens=None)
    assert stats["rows_out_of_window"] == 1
    assert stats["rows_no_usdc"] == 1
    assert stats["rows_kept"] == 2
    assert out.schema == FILL_SCHEMA
    rows = out.to_pylist()
    sell = rows[0]
    assert sell["id"] == "log_0xabc_7"
    assert sell["user_id"] == "0xmaker"
    assert sell["address"] == exchange
    assert sell["order_type"] == "maker"
    assert sell["side"] == "SELL"
    assert sell["asset"] == "999"
    assert sell["amount_shares"] == 11.0
    assert abs(sell["amount_usdc"] - 0.022) < 1e-9
    assert abs(sell["price"] - 0.002) < 1e-9
    assert sell["order_hash"] is None and sell["_gs_op"] == "hf"
    buy = rows[1]
    assert buy["order_type"] == "taker" and buy["side"] == "BUY"
    assert buy["counterparty_id"] == exchange
    assert abs(buy["fee"] - 0.0015) < 1e-12
    assert abs(buy["amount_usdc"] - 0.5) < 1e-9
    assert buy["block_number"] < 94_184_078


def test_short_crypto_token_is_dropped():
    raw = _raw([_row(maker_asset_id="555"), _row(log_index=2, maker_asset_id="999")])
    out, stats = fills_from_orderfilled(
        raw, block_lo=1, block_hi=10**9, short_tokens=pa.array(["555"])
    )
    assert stats["rows_short_crypto"] == 1 and stats["rows_kept"] == 1
    assert out.column("asset").to_pylist() == ["999"]


def test_slug_rule_keeps_hourly_up_down():
    assert is_short_crypto_slug("btc-updown-5m-1750000000")
    assert is_short_crypto_slug("eth-up-or-down-15m-1750000000")
    assert not is_short_crypto_slug("bitcoin-up-or-down-september-14-1pm-et")
    assert not is_short_crypto_slug("will-btc-hit-100k")


def test_row_group_overlap_is_half_open():
    stats = [(0, 10, 19), (1, 20, 30), (2, 0, 9)]
    assert row_groups_overlapping(stats, 20, 25) == [1]


def test_weeks_walk_backward_and_stop_at_october_2024():
    cursor = 1_789_376_400  # 2026-09-14 09:00:00Z
    start, end = next_ts_window(cursor, 7)
    assert end == cursor and start == cursor - 7 * 86400
    assert next_ts_window(STOP_TS, 7) is None
    partial = next_ts_window(STOP_TS + 3 * 86400, 7)
    assert partial == (STOP_TS, STOP_TS + 3 * 86400)


def test_week_files_do_not_share_a_name():
    assert fill_object_name(100, 7) != fill_object_name(200, 7)
    assert fill_object_name(100, 7) == "hf_w100_rg00007.parquet"


def test_timestamp_interpolation_between_anchors():
    anchors = np.array([0, 1000])
    anchor_ts = np.array([1_000, 2_500])
    got = interpolate_timestamps(np.array([0, 500, 1000]), anchors, anchor_ts)
    assert list(got) == [1000, 1750, 2500]
