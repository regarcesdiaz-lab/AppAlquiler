#!/bin/sh
# Runs once, on first start with an empty volume. POSTGRES_DB is created by the image itself.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	SELECT 'CREATE DATABASE "${POSTGRES_TEST_DB}" OWNER "${POSTGRES_USER}"'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${POSTGRES_TEST_DB}')\gexec
EOSQL
