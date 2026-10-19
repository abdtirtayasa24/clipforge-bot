from migrations.sql_runner import run_sql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("003_artifacts.sql")


def downgrade() -> None:
    run_sql("003_artifacts_down.sql")
