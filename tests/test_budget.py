from __future__ import annotations

from unittest.mock import patch

from beacon.budget import plan_token_budget
from beacon.config import BeaconConfig

_CONFIG = BeaconConfig(
    log_group_patterns=[],
    sns_topic_arn="arn:x",
    nova_model_id="bedrock/test-model",
)


def _make_text(n_tokens: int) -> str:
    """Create text that estimates to ~n_tokens (4 chars per token heuristic)."""
    return "x" * (n_tokens * 4)


def _mock_token_counter(text: str, model: str) -> int:
    return len(text) // 4


class TestPlanTokenBudget:
    @patch("beacon.budget.estimate_tokens", side_effect=_mock_token_counter)
    def test_single_source_fits(self, _mock: object) -> None:
        sources = {"/app": _make_text(500)}
        plans = plan_token_budget(sources, available_tokens=1000, config=_CONFIG)

        assert len(plans) == 1
        assert not plans[0].needs_reduction
        assert plans[0].anomaly_percentile is None

    @patch("beacon.budget.estimate_tokens", side_effect=_mock_token_counter)
    def test_single_source_needs_reduction(self, _mock: object) -> None:
        sources = {"/app": _make_text(2000)}
        plans = plan_token_budget(sources, available_tokens=1000, config=_CONFIG)

        assert len(plans) == 1
        assert plans[0].needs_reduction
        assert plans[0].anomaly_percentile is not None
        assert 0.49 < plans[0].anomaly_percentile < 0.51

    @patch("beacon.budget.estimate_tokens", side_effect=_mock_token_counter)
    def test_multiple_sources_all_fit(self, _mock: object) -> None:
        sources = {"/a": _make_text(200), "/b": _make_text(300)}
        plans = plan_token_budget(sources, available_tokens=1000, config=_CONFIG)

        assert all(not p.needs_reduction for p in plans)

    @patch("beacon.budget.estimate_tokens", side_effect=_mock_token_counter)
    def test_fair_share_small_source_kept_raw(self, _mock: object) -> None:
        sources = {"/small": _make_text(100), "/large": _make_text(800)}
        plans = plan_token_budget(sources, available_tokens=500, config=_CONFIG)

        by_group = {p.log_group: p for p in plans}

        # Small source fits in fair share (250), kept raw
        assert not by_group["/small"].needs_reduction

        # Large source gets remaining budget (400 out of 800 = 50%)
        assert by_group["/large"].needs_reduction
        p = by_group["/large"].anomaly_percentile
        assert p is not None
        assert 0.49 < p < 0.51

    @patch("beacon.budget.estimate_tokens", side_effect=_mock_token_counter)
    def test_all_sources_need_reduction(self, _mock: object) -> None:
        sources = {"/a": _make_text(1000), "/b": _make_text(1000)}
        plans = plan_token_budget(sources, available_tokens=500, config=_CONFIG)

        assert all(p.needs_reduction for p in plans)
        for p in plans:
            assert p.anomaly_percentile is not None
            assert 0.24 < p.anomaly_percentile < 0.26

    @patch("beacon.budget.estimate_tokens", side_effect=_mock_token_counter)
    def test_empty_sources(self, _mock: object) -> None:
        plans = plan_token_budget({}, available_tokens=1000, config=_CONFIG)
        assert plans == []

    @patch("beacon.budget.estimate_tokens", side_effect=_mock_token_counter)
    def test_percentile_clamped_at_minimum(self, _mock: object) -> None:
        sources = {"/huge": _make_text(100_000)}
        plans = plan_token_budget(sources, available_tokens=10, config=_CONFIG)

        assert plans[0].needs_reduction
        assert plans[0].anomaly_percentile == 0.01
