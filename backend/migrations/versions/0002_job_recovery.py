from migrations.sql_runner import run_sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("002_job_recovery.sql")


def downgrade() -> None:
    run_sql("002_job_recovery_down.sql")
