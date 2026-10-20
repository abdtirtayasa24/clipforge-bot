from migrations.sql_runner import run_sql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("007_affiliate_links.sql")


def downgrade() -> None:
    run_sql("007_affiliate_links_down.sql")
