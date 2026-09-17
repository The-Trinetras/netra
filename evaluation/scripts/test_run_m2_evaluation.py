from retrieval_experiments import ExperimentSummary
from run_m2_evaluation import _hard_failure, _rubric_text


def summary(**updates):
    values = dict(
        experiment_id="E3",
        description="hybrid",
        dataset_id="d",
        dataset_version="1",
        status="executed",
        cases=1,
        successful_cases=1,
        failed_cases=0,
    )
    values.update(updates)
    return ExperimentSummary(**values)


def test_security_or_execution_failure_returns_nonzero() -> None:
    assert not _hard_failure(summary())
    assert _hard_failure(summary(unauthorized_context_count=1))
    assert _hard_failure(summary(stale_source_version_count=1))
    assert _hard_failure(summary(status="partial", failed_cases=1))


def test_repository_rubric_is_loadable() -> None:
    from run_m2_evaluation import DEFAULT_RUBRIC

    assert "ground" in _rubric_text(DEFAULT_RUBRIC).lower()
