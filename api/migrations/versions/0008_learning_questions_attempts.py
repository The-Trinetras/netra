"""Durable pending questions and assessment attempts (INT-08, M4 semantics).

Revision ID: 0008_learning_questions_attempts
Revises: 0007_outbox_dead_letter

M4's PendingQuestionRepository and AssessmentHistoryRepository had only
in-memory implementations, so no answer could be committed durably and the
attempt, its pending-question closure and its projection outbox event could
not share a transaction. This adds the two canonical tables; the outbox table
already exists. Shapes follow netra_api.learning.quiz.models.ApprovedQuestion
and netra_api.learning.assessment.models.AssessmentAttempt field for field.

One attempt may finalize a question version (unique constraint), and an
attempt must reference a persisted question (foreign key). No learning label,
status or review column is added: those are removed requirements. The
proposed factual activity/assistance record (D3) is not included; it still
awaits review.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_learning_questions_attempts"
down_revision = "0007_outbox_dead_letter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()
    tz = sa.DateTime(timezone=True)
    op.create_table(
        "learning_pending_questions",
        sa.Column("question_id", sa.String(200), primary_key=True),
        sa.Column("question_version", sa.Integer, primary_key=True),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("concept_id", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("options", jsonb, nullable=False),
        sa.Column("answer_key", jsonb, nullable=False),
        sa.Column("evidence_refs", jsonb, nullable=False),
        sa.Column("created_at", tz, nullable=False),
        sa.Column("answered_at", tz, nullable=True),
        sa.Column("answered_attempt_id", uuid, nullable=True),
        sa.CheckConstraint("question_version >= 1", name="ck_learning_pending_questions_version"),
    )
    op.create_index("ix_learning_pending_questions_account", "learning_pending_questions",
                    ["account_id", "question_id"])
    op.create_table(
        "learning_assessment_attempts",
        sa.Column("attempt_id", uuid, primary_key=True),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("concept_id", sa.String(200), nullable=False),
        sa.Column("question_id", sa.String(200), nullable=False),
        sa.Column("question_version", sa.Integer, nullable=False),
        sa.Column("answer", jsonb, nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("hints_used", sa.Integer, nullable=False),
        sa.Column("evaluated_by", sa.String(16), nullable=False),
        sa.Column("created_at", tz, nullable=False),
        sa.UniqueConstraint("question_id", "question_version", name="uq_learning_attempts_question_version"),
        sa.CheckConstraint("outcome IN ('correct', 'incorrect', 'partial')", name="ck_learning_attempts_outcome"),
        sa.CheckConstraint("evaluated_by IN ('tutor', 'grader')", name="ck_learning_attempts_evaluated_by"),
        sa.CheckConstraint("hints_used >= 0", name="ck_learning_attempts_hints_used"),
        sa.ForeignKeyConstraint(["question_id", "question_version"],
                                ["learning_pending_questions.question_id",
                                 "learning_pending_questions.question_version"],
                                name="fk_learning_attempts_question", ondelete="RESTRICT"),
    )
    op.create_index("ix_learning_attempts_account_concept", "learning_assessment_attempts",
                    ["account_id", "concept_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_learning_attempts_account_concept", table_name="learning_assessment_attempts")
    op.drop_table("learning_assessment_attempts")
    op.drop_index("ix_learning_pending_questions_account", table_name="learning_pending_questions")
    op.drop_table("learning_pending_questions")
