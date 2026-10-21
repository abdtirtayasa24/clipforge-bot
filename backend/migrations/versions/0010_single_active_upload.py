from migrations.sql_runner import run_sql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("010_single_active_upload.sql")


def downgrade() -> None:
    run_sql("010_single_active_upload_down.sql")
