-- ============================================================
-- GTM Autonomous System — PostgreSQL Setup Script
-- ============================================================
-- Run this as the postgres superuser:
--   psql -U postgres -f scripts/setup_db.sql
-- Or paste it directly into pgAdmin Query Tool.
-- ============================================================

-- 1. Create the application role
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'gtm_user') THEN
      CREATE USER gtm_user WITH PASSWORD 'gtm_password';
      RAISE NOTICE 'User gtm_user created.';
   ELSE
      RAISE NOTICE 'User gtm_user already exists.';
   END IF;
END
$$;

-- 2. Create the database (must be run outside a transaction block)
-- If this fails with "already exists", that is fine.
SELECT 'Creating gtm_db...' AS step;

-- 3. Grant privileges (run after connecting to gtm_db)
-- This will be run separately after CREATE DATABASE succeeds.
