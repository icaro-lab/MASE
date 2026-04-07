"""Unit tests for bounded HTTP response telemetry snapshots."""

from app.http_snapshot import SNAPSHOT_SCHEMA, TRUNCATED_MARKER, build_response_snapshot


def test_build_response_snapshot_redacts_sensitive_keys_and_truncates_strings() -> None:
    snapshot, meta = build_response_snapshot(
        {
            "posts": [
                {
                    "id": "p1",
                    "score": 7,
                    "title": "A" * 80,
                    "api_token": "TOPSECRET",
                }
            ]
        },
        enabled=True,
        max_depth=4,
        max_dict_keys=8,
        max_list_items=8,
        max_string_chars=24,
        max_total_nodes=128,
    )

    assert meta is not None
    assert meta["schema"] == SNAPSHOT_SCHEMA
    assert meta["redacted"] is True
    assert meta["truncated"] is True
    assert snapshot["posts"][0]["id"] == "p1"
    assert snapshot["posts"][0]["score"] == 7
    assert snapshot["posts"][0]["api_token"] == "***"
    assert snapshot["posts"][0]["title"].endswith("...")
    assert len(snapshot["posts"][0]["title"]) == 27


def test_build_response_snapshot_limits_large_lists() -> None:
    snapshot, meta = build_response_snapshot(
        {"items": [{"id": f"p{i}", "score": i} for i in range(5)]},
        enabled=True,
        max_depth=4,
        max_dict_keys=8,
        max_list_items=3,
        max_string_chars=40,
        max_total_nodes=128,
    )

    assert meta is not None
    assert meta["truncated"] is True
    items = snapshot["items"]
    assert len(items) == 4
    assert items[-1][TRUNCATED_MARKER]["reason"] == "max_list_items"
    assert items[-1][TRUNCATED_MARKER]["remaining"] == 2
