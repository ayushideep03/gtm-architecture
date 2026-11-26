"""Row-level security must hold even when the connection's role bypasses it.

Hosted Postgres (Neon's ``neondb_owner``, many managed services' owner roles) has BYPASSRLS,
which makes PostgreSQL skip every policy — tenant isolation would silently disappear. This
creates ``oxygen_app_<database>``: a NOLOGIN role WITHOUT BYPASSRLS, with data privileges only (no DDL,
not an owner). The application switches to it (``SET LOCAL ROLE``) in every transaction that
runs for an organisation, so the policies apply whatever role the connection uses.

Needs CREATEROLE (or superuser). Where the migrating role has neither (e.g. a local role that
does not bypass RLS anyway), the migration only logs a notice; the app then keeps the
connection role, which is already subject to RLS.

Revision ID: 0013
Revises: 0012
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Roles are global to a PostgreSQL server; name it after the database so several databases on one
# server (local development, tests) never share or fight over it. Must match app/core/database.py.
ROLE_SQL = "'oxygen_app_' || left(regexp_replace(lower(current_database()), '[^a-z0-9_]', '_', 'g'), 50)"


def upgrade() -> None:
    op.execute(f"""
DO $$
DECLARE can_create boolean; role_name text := {ROLE_SQL};
BEGIN
  SELECT rolsuper OR rolcreaterole INTO can_create FROM pg_roles WHERE rolname = current_user;
  IF NOT can_create THEN
    RAISE NOTICE 'oxygen: cannot create role % (no CREATEROLE); RLS relies on the connection role', role_name;
    RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
    EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOBYPASSRLS', role_name);
  END IF;
  -- PostgreSQL 16+: a role created by a CREATEROLE user is granted back WITHOUT the SET right, so grant
  -- it explicitly (WITH SET TRUE). Older servers have no such option; a plain GRANT means SET there.
  BEGIN
    EXECUTE format('GRANT %I TO %I WITH SET TRUE, INHERIT FALSE', role_name, current_user);
  EXCEPTION
    WHEN syntax_error THEN
      EXECUTE format('GRANT %I TO %I', role_name, current_user);
    WHEN insufficient_privilege THEN
      RAISE NOTICE 'oxygen: cannot grant role % (no ADMIN option)', role_name;
      RETURN;
  END;
  EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', current_schema(), role_name);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I', current_schema(), role_name);
  EXECUTE format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I', current_schema(), role_name);
  EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
                 current_schema(), role_name);
  EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
                 current_schema(), role_name);
END $$;
""")


def downgrade() -> None:
    op.execute(f"""
DO $$
DECLARE role_name text := {ROLE_SQL};
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) AND
     (SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user) THEN
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON TABLES FROM %I', current_schema(), role_name);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON SEQUENCES FROM %I', current_schema(), role_name);
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', current_schema(), role_name);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I', current_schema(), role_name);
    EXECUTE format('REVOKE USAGE ON SCHEMA %I FROM %I', current_schema(), role_name);
  END IF;
END $$;
""")
