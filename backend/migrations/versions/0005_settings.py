from migrations.sql_runner import run_sql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("005_settings.sql")


def downgrade() -> None:
    run_sql("005_settings_down.sql")
