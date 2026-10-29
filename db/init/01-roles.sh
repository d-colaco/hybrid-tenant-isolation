#!/bin/sh
# Runs once per fresh volume. The application role can log in and nothing else: it owns nothing, is not a
# superuser and cannot bypass row-level security. Tables are owned by lab_admin, which runs the migrations.
set -e
psql -v ON_ERROR_STOP=1 -U lab_admin -d lab <<SQL
CREATE ROLE lab_app LOGIN PASSWORD '${LAB_APP_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE lab TO lab_app;
SQL
