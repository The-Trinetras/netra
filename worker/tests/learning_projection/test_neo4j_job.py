from uuid import uuid4

import pytest

from netra_worker.jobs.learning_projection.neo4j import (
    ProjectAssessmentAttemptJob,
    ProjectAssessmentAttemptPayload,
)


async def test_project_assessment_attempt_job_is_not_yet_implemented():
    payload = ProjectAssessmentAttemptPayload(
        idempotency_key="job-1", account_id=uuid4(), attempt_id=uuid4()
    )
    with pytest.raises(NotImplementedError):
        await ProjectAssessmentAttemptJob().handle(payload)
