from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cordon import AnalysisConfig, SemanticLogAnalyzer

if TYPE_CHECKING:
    from beacon.config import BeaconConfig


def analyze_logs(
    log_text: str,
    anomaly_percentile: float,
    config: BeaconConfig,
) -> str:
    """Reduce *log_text* to its most anomalous sections using Cordon.

    Writes the text to a temporary file, runs Cordon's
    ``SemanticLogAnalyzer`` with Nova Embeddings on Bedrock, and
    returns XML containing the anomalous blocks and their scores.
    The temp file is cleaned up regardless of success or failure.
    """
    kwargs: dict[str, Any] = {
        "backend": config.cordon_backend,
        "model_name": config.embedding_model_id,
        "window_size": config.cordon_window_size,
        "k_neighbors": config.cordon_k_neighbors,
        "anomaly_percentile": anomaly_percentile,
        "batch_size": 64,
    }
    if config.cordon_backend == "remote":
        kwargs["request_timeout"] = 120.0

    analysis_config = AnalysisConfig(**kwargs)
    analyzer = SemanticLogAnalyzer(analysis_config)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write(log_text)
        tmp_path = Path(f.name)

    try:
        return str(analyzer.analyze_file(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)
