from migrations.sql_runner import run_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("001_initial.sql")


def downgrade() -> None:
    run_sql("001_initial_down.sql")
