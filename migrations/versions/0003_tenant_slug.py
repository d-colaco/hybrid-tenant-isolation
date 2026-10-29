"""app.tenant_slug(): the slug of a tenant, but only for a tenant the transaction has a reviewed scope for

The application role cannot read ``app.tenant``. A cross-tenant exception needs to label its rows, so this function
answers for the tenants in the transaction's scope and returns NULL for any other.

Revision ID: 0003
Revises: 0002
"""
from alembic import context, op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not context.config.attributes["is_shared"]():
        return
    op.execute(
        """
        CREATE FUNCTION app.tenant_slug(p_tenant uuid) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = app, pg_temp AS
        $$ SELECT t.slug FROM app.tenant t WHERE t.id = p_tenant AND app.in_scope(p_tenant) $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app.tenant_slug(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.tenant_slug(uuid) TO lab_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.tenant_slug(uuid)")
