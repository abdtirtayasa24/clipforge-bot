from migrations.sql_runner import run_sql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("004_security.sql")


def downgrade() -> None:
    run_sql("004_security_down.sql")
