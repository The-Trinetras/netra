"""Initialize Netra's non-master PostgreSQL identity through an SSM tunnel.

Credentials stay in process memory and are never printed. This script performs
no Alembic migration and does not mutate application tables.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import boto3
import psycopg
from botocore.exceptions import ClientError
from psycopg import sql


def _secret_value(client: Any, secret_arn: str) -> dict[str, str] | None:
    try:
        payload = client.get_secret_value(SecretId=secret_arn)["SecretString"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise
    value = json.loads(payload)
    if not isinstance(value, dict) or not isinstance(value.get("username"), str) or not isinstance(value.get("password"), str):
        raise RuntimeError("database secret has an invalid shape")
    return {"username": value["username"], "password": value["password"]}


def _application_identity(client: Any, secret_arn: str, username: str) -> dict[str, str]:
    existing = _secret_value(client, secret_arn)
    if existing is not None:
        if existing["username"] != username:
            raise RuntimeError("application secret username does not match configuration")
        return existing
    password = client.get_random_password(PasswordLength=40, ExcludePunctuation=True)["RandomPassword"]
    value = {"username": username, "password": password}
    client.put_secret_value(
        SecretId=secret_arn,
        SecretString=json.dumps(value, separators=(",", ":")),
    )
    return value


def bootstrap(*, region: str, master_secret_arn: str, application_secret_arn: str,
              application_username: str, database_name: str, host: str, port: int) -> None:
    secrets = boto3.client("secretsmanager", region_name=region)
    master = _secret_value(secrets, master_secret_arn)
    if master is None:
        raise RuntimeError("RDS master secret has no current value")
    application = _application_identity(secrets, application_secret_arn, application_username)
    connection_args = {
        "host": host,
        "port": port,
        "user": master["username"],
        "password": master["password"],
        "connect_timeout": 10,
        "autocommit": True,
    }
    with psycopg.connect(dbname="postgres", **connection_args) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (application_username,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(application_username)))
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(application_username), sql.Literal(application["password"])))
            cursor.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(database_name), sql.Identifier(application_username)))
    with psycopg.connect(dbname=database_name, **connection_args) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(application_username)))
            cursor.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(application_username)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--master-secret-arn", required=True)
    parser.add_argument("--application-secret-arn", required=True)
    parser.add_argument("--application-username", default="netra_app")
    parser.add_argument("--database-name", default="netra")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15432)
    args = parser.parse_args()
    bootstrap(
        region=args.region,
        master_secret_arn=args.master_secret_arn,
        application_secret_arn=args.application_secret_arn,
        application_username=args.application_username,
        database_name=args.database_name,
        host=args.host,
        port=args.port,
    )
    print(f"database_identity_bootstrap=PASS username={args.application_username}")


if __name__ == "__main__":
    main()
