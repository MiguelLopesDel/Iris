from dataclasses import FrozenInstanceError

import pytest

from core.ingest_policy import IngestPolicy


def test_default_ingest_policy_is_bounded_and_immutable():
    policy = IngestPolicy()

    assert policy.max_items == 64
    assert policy.max_bytes == 32 * 1024 * 1024
    assert policy.block_bytes == 1024 * 1024
    assert policy.fsync_concurrency == 64
    assert policy.durability_window_s == 0.01
    assert policy.db_group_max_items == 256
    assert policy.packages_in_flight_per_device == 4

    with pytest.raises(FrozenInstanceError):
        policy.max_items = 16


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_items", 0),
        ("max_items", 1025),
        ("max_bytes", -1),
        ("max_bytes", (512 << 20) + 1),
        ("block_bytes", 0),
        ("block_bytes", (1 << 20) + 1),
        ("fsync_concurrency", True),
        ("db_group_max_items", 257),
        ("db_group_max_items", 1.5),
        ("packages_in_flight_per_device", 0),
        ("max_in_flight_bytes", 0),
        ("durability_window_s", -0.1),
        ("durability_window_s", float("inf")),
        ("durability_window_s", 5.1),
        ("db_window_s", float("nan")),
        ("db_window_s", 1.1),
    ],
)
def test_ingest_policy_rejects_invalid_bounds(field, value):
    with pytest.raises(ValueError):
        IngestPolicy(**{field: value})


def test_ingest_policy_rejects_a_block_larger_than_the_package_limit():
    with pytest.raises(ValueError, match="block_bytes"):
        IngestPolicy(max_bytes=1024, block_bytes=2048)


def test_ingest_policy_rejects_an_inflight_budget_smaller_than_one_package():
    with pytest.raises(ValueError, match="max_in_flight_bytes"):
        IngestPolicy(max_in_flight_bytes=16 << 20)


def test_environment_overrides_are_validated_like_any_value():
    from core.ingest_policy import IngestPolicy

    policy = IngestPolicy.from_env({
        "IRIS_INGEST_DURABILITY_WINDOW_S": "0.01", "IRIS_INGEST_FSYNC_CONCURRENCY": "32",
    })
    assert (policy.durability_window_s, policy.fsync_concurrency) == (0.01, 32)
    assert IngestPolicy.from_env({}) == IngestPolicy()
    for bad in ({"IRIS_INGEST_FSYNC_CONCURRENCY": "lots"}, {"IRIS_INGEST_FSYNC_CONCURRENCY": "999"}):
        try:
            IngestPolicy.from_env(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")
