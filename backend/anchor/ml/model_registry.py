"""MLflow model registry wrapper.

Tracks which model version is currently in production per instrument.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

import mlflow
import structlog

from anchor.config import settings

logger = structlog.get_logger(__name__)

MODEL_PREFIX = "anchor_xgb"


class ModelRegistry:
    def __init__(self) -> None:
        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        self._client = mlflow.tracking.MlflowClient()

    def model_name(self, instrument: str) -> str:
        return f"{MODEL_PREFIX}_{instrument.replace('_', '').lower()}"

    async def promote(self, instrument: str, run_id: str, oos_accuracy: float) -> None:
        """Register run as a new model version and promote to Production."""
        name = self.model_name(instrument)

        # Create registered model if it doesn't exist
        try:
            self._client.create_registered_model(name)
        except Exception:
            pass  # already exists

        # Create model version from run artifacts
        try:
            version = self._client.create_model_version(
                name=name,
                source=f"runs:/{run_id}/model",
                run_id=run_id,
                tags={"oos_accuracy": str(oos_accuracy), "promoted_at": datetime.now(timezone.utc).isoformat()},
            )
            # Archive any previous Production versions
            for mv in self._client.search_model_versions(f"name='{name}'"):
                if mv.current_stage == "Production" and mv.version != version.version:
                    self._client.transition_model_version_stage(
                        name=name, version=mv.version, stage="Archived"
                    )
            # Promote new version
            self._client.transition_model_version_stage(
                name=name, version=version.version, stage="Production"
            )
            logger.info("model_promoted", instrument=instrument, version=version.version)
        except Exception as exc:
            logger.error("model_promote_failed", instrument=instrument, error=str(exc))

    def get_production_run_id(self, instrument: str) -> Optional[str]:
        name = self.model_name(instrument)
        try:
            versions = self._client.search_model_versions(
                f"name='{name}' and tag.stage='Production'"
            )
            if versions:
                return versions[0].run_id
        except Exception:
            pass
        return None

    def get_all_production(self) -> Dict[str, Optional[str]]:
        from anchor.config import settings
        return {instr: self.get_production_run_id(instr) for instr in settings.instruments}
