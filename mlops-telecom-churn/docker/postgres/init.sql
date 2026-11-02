-- Runs once, on an empty PostgreSQL data directory.
CREATE ROLE mlflow LOGIN PASSWORD 'mlflowpass';
CREATE DATABASE mlflow OWNER mlflow;
