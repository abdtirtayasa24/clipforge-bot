from migrations.sql_runner import run_sql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("008_affiliate_matching.sql")


def downgrade() -> None:
    run_sql("008_affiliate_matching_down.sql")
