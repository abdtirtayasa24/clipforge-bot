from migrations.sql_runner import run_sql

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("NNN_name.sql")


def downgrade() -> None:
    run_sql("NNN_name_down.sql")
