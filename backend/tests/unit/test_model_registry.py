from types import SimpleNamespace

from anchor.ml.model_registry import ModelRegistry


def test_get_production_run_id_filters_on_current_stage():
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._client = SimpleNamespace(
        search_model_versions=lambda query: [
            SimpleNamespace(current_stage="Staging", run_id="run-staging"),
            SimpleNamespace(current_stage="Production", run_id="run-production"),
        ]
    )

    assert registry.get_production_run_id("EUR_USD") == "run-production"
