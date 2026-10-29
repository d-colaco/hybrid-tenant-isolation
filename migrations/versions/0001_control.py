"""control schema: tenants, users, memberships, the reviewed exceptions and the scope a transaction holds

Shared database: the control tables and the SECURITY DEFINER functions the application role may call (it has no
privilege on the control tables themselves). Heavy database: only ``app.current_tenant()``, which its policies use.

Revision ID: 0001
"""
from alembic import context, op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA app")
    op.execute(
        """
        CREATE FUNCTION app.current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA app TO lab_app")
    op.execute("REVOKE ALL ON FUNCTION app.current_tenant() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.current_tenant() TO lab_app")
    if not context.config.attributes["is_shared"]():
        return

    op.execute(
        """
        CREATE TABLE app.tenant (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug text UNIQUE NOT NULL,
            name text NOT NULL,
            nif text NOT NULL,
            database_key text,                 -- NULL: the shared database; 'heavy': its ledger lines live in the heavy one
            retention_until date,              -- set by the operator; NULL means "not configured", never a guess
            status text NOT NULL DEFAULT 'active'
        )
        """
    )
    op.execute("CREATE TABLE app.app_user (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL)")
    op.execute(
        """
        CREATE TABLE app.membership (
            user_id uuid NOT NULL REFERENCES app.app_user(id),
            tenant_id uuid NOT NULL REFERENCES app.tenant(id),
            role text NOT NULL,
            PRIMARY KEY (user_id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.reviewed_exception (
            id text PRIMARY KEY,
            description text NOT NULL,
            sql_sha256 text NOT NULL,          -- the hash of the reviewed statement: a changed query needs a new review
            reviewer text NOT NULL,
            reviewed_on date NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.scope_grant (
            txid bigint NOT NULL,
            tenant_id uuid NOT NULL,
            exception_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (txid, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.exception_use (
            id bigserial PRIMARY KEY,
            at timestamptz NOT NULL DEFAULT now(),
            user_id uuid NOT NULL,
            exception_id text NOT NULL,
            tenants int NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.resolve_tenant(p_slug text, p_user uuid)
        RETURNS TABLE (id uuid, database_key text, nif text, status text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = app, pg_temp AS
        $$
            SELECT t.id, t.database_key, t.nif, t.status
            FROM app.tenant t JOIN app.membership m ON m.tenant_id = t.id
            WHERE t.slug = p_slug AND m.user_id = p_user AND t.status = 'active'
        $$
        """
    )
    # A transaction gets a multi-tenant read scope only by calling this, for a named exception that was reviewed.
    op.execute(
        """
        CREATE FUNCTION app.enter_scope(p_user uuid, p_exception text) RETURNS int
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = app, pg_temp AS
        $$
        DECLARE n int;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM app.reviewed_exception WHERE id = p_exception) THEN
                RAISE EXCEPTION 'cross-tenant exception % is not on the reviewed list', p_exception USING ERRCODE = '42501';
            END IF;
            DELETE FROM app.scope_grant WHERE created_at < now() - interval '1 hour';
            INSERT INTO app.scope_grant (txid, tenant_id, exception_id)
                SELECT txid_current(), m.tenant_id, p_exception
                FROM app.membership m JOIN app.tenant t ON t.id = m.tenant_id
                WHERE m.user_id = p_user AND t.status = 'active'
                ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS n = ROW_COUNT;
            INSERT INTO app.exception_use (user_id, exception_id, tenants) VALUES (p_user, p_exception, n);
            RETURN n;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.in_scope(p_tenant uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = app, pg_temp AS
        $$ SELECT EXISTS (SELECT 1 FROM app.scope_grant g WHERE g.txid = txid_current() AND g.tenant_id = p_tenant) $$
        """
    )
    for signature in ("app.resolve_tenant(text, uuid)", "app.enter_scope(uuid, text)", "app.in_scope(uuid)"):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO lab_app")


def downgrade() -> None:
    op.execute("DROP SCHEMA app CASCADE")
