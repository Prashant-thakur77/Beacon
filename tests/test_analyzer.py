from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from beacon.analyzer import analyze_logs
from beacon.config import BeaconConfig

_CONFIG = BeaconConfig(
    log_group_patterns=[],
    sns_topic_arn="arn:x",
    cordon_window_size=4,
    cordon_k_neighbors=5,
)


class TestAnalyzeLogs:
    @patch("beacon.analyzer.SemanticLogAnalyzer")
    def test_calls_cordon_with_correct_config(
        self, mock_analyzer_cls: MagicMock, cordon_output: str
    ) -> None:
        mock_instance = MagicMock()
        mock_instance.analyze_file.return_value = cordon_output
        mock_analyzer_cls.return_value = mock_instance

        result = analyze_logs("line1\nline2\nline3", 0.45, _CONFIG)

        assert result == cordon_output
        mock_instance.analyze_file.assert_called_once()

        # Verify the temp file path was passed
        call_args = mock_instance.analyze_file.call_args[0]
        assert isinstance(call_args[0], Path)

    @patch("beacon.analyzer.SemanticLogAnalyzer")
    def test_passes_anomaly_percentile(self, mock_analyzer_cls: MagicMock) -> None:
        mock_analyzer_cls.return_value.analyze_file.return_value = "<cordon_output/>"

        analyze_logs("test logs", 0.25, _CONFIG)

        config_arg = mock_analyzer_cls.call_args[0][0]
        assert config_arg.anomaly_percentile == 0.25

    @patch("beacon.analyzer.SemanticLogAnalyzer")
    def test_cleans_up_temp_file(self, mock_analyzer_cls: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_instance.analyze_file.return_value = "<cordon_output/>"
        mock_analyzer_cls.return_value = mock_instance

        analyze_logs("temp file content", 0.1, _CONFIG)

        call_args = mock_instance.analyze_file.call_args[0]
        tmp_path: Path = call_args[0]
        assert not tmp_path.exists()
