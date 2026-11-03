# Phase 1 — Application database

Seed the upgrade test at revision `3665f6870fa4`, then upgrade to the
new head and verify the same IDs, roles, and hashes. The existing fixture
normally upgrades straight to head, so use an explicit revision in this case.
Allow `create_app` tests to inject the auth dependency. Auth routes return 503
if auth storage is unconfigured or unavailable. Existing injected knowledge
tests should start without the application database.

- **Create `backend/src/storage/app_database.py`.** Add `create_app_engine` and a session factory using only APP_DATABASE_URL. Use the existing SQLAlchemy and psycopg stack. Provide short-lived sessions and dispose the engine at shutdown.
- **Edit `backend/app.py`.** Construct the app database resources in lifespan and expose the session dependency to auth. Preserve existing knowledge startup and keep unrelated entry points usable. Auth must fail clearly if its database is unavailable. Keep migration execution in the setup command.
- **Create `backend/src/storage/orm/auth_sessions.py` and edit `orm/__init__.py`.** Add `AuthSession` with UUIDv7 ID, user foreign key, unique access and refresh digests, access expiry, absolute session expiry, revoked timestamp, and record timestamps. Use timestamptz. Add an index on user ID for account-wide revocation. Store digests of random secrets.
- **Create a new Alembic revision.** Generate the table and indexes against a disposable database, review the SQL, and leave applied revisions intact. Reuse `deployments/migrate.sh`.
- **First failing tests.** Extend `backend/tests/test_database_migrations.py` for session schema and upgrade with representative users. Create `backend/tests/test_app_database.py` to prove runtime uses APP_DATABASE_URL and never the migration URL. Test fresh upgrade, check, downgrade one revision, and re-upgrade. Rerun setup and verify rows persist. Never point test migrations at a developer database.
