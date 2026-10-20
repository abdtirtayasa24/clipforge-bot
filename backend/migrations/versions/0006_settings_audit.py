from migrations.sql_runner import run_sql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("006_settings_audit.sql")


def downgrade() -> None:
    run_sql("006_settings_audit_down.sql")
