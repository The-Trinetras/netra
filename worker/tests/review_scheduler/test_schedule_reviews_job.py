from uuid import uuid4

import pytest

from netra_worker.jobs.review_scheduler.schedule_reviews import (
    ScheduleReviewsJob,
    ScheduleReviewsPayload,
)


async def test_schedule_reviews_job_is_not_yet_implemented():
    payload = ScheduleReviewsPayload(idempotency_key="job-1", account_id=uuid4())
    with pytest.raises(NotImplementedError):
        await ScheduleReviewsJob().handle(payload)
