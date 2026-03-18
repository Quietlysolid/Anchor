#!/bin/sh
set -eu

mlflow_db="${MLFLOW_DB_NAME:-anchor_mlflow}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname postgres <<-SQL
  SELECT 'CREATE DATABASE "${mlflow_db}"'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${mlflow_db}')\gexec
SQL
