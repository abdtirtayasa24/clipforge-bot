from migrations.sql_runner import run_sql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("011_stage_running_names.sql")


def downgrade() -> None:
    run_sql("011_stage_running_names_down.sql")
