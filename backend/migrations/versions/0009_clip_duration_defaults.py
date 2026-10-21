from migrations.sql_runner import run_sql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("009_clip_duration_defaults.sql")


def downgrade() -> None:
    run_sql("009_clip_duration_defaults_down.sql")
