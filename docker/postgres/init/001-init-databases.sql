CREATE EXTENSION IF NOT EXISTS vector;

SELECT 'CREATE DATABASE researchflow_test OWNER researchflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'researchflow_test')\gexec

\connect researchflow_test researchflow
CREATE EXTENSION IF NOT EXISTS vector;
