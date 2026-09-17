# Netra AWS development bootstrap

This M2 runbook implements the RDS/PgBouncer target described by the
[AgentSpec](../../docs/architecture/Netra-SPEC.md). The API and worker deployment
boundary remains Docker Compose on one EC2 instance; PostgreSQL remains
authoritative, S3 owns durable source bytes, and Pinecone and Neo4j remain
rebuildable projections. The earlier documentation-migration snapshot predated
this implementation and is retained in the
[migration report](../../docs/audits/documentation-migration-report.md).

Terraform creates RDS with an AWS-managed master credential and an empty
Secrets Manager container for the `netra_app` credential. Plaintext credentials
are never Terraform inputs, outputs, or state values. The PgBouncer host can
read only the application secret; it cannot read the RDS master secret.

After `terraform apply`, open an SSM remote-host tunnel from a separate terminal
to private RDS. This is the only point where the operator bootstrap uses the
master credential:

```powershell
$instance = terraform -chdir=infrastructure/terraform output -raw pgbouncer_instance_id
$rds = terraform -chdir=infrastructure/terraform output -raw rds_endpoint
aws ssm start-session --target $instance --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "host=$rds,portNumber=5432,localPortNumber=15432"
```

With that tunnel running, initialize or repair the application role. The script
generates the application password in Secrets Manager, transfers ownership of
the Netra database and `public` schema, and is safe to rerun. It does not run
Alembic or touch application tables.

```powershell
$master = terraform -chdir=infrastructure/terraform output -raw database_master_secret_arn
$application = terraform -chdir=infrastructure/terraform output -raw database_application_secret_arn
uv run --locked python infrastructure/aws/bootstrap_database_identity.py --region ap-south-1 --master-secret-arn $master --application-secret-arn $application
```

The EC2 bootstrap waits for the application secret. If it has already timed
out, rerun only its idempotent PgBouncer configuration through SSM:

```powershell
$instance = terraform -chdir=infrastructure/terraform output -raw pgbouncer_instance_id
aws ssm send-command --instance-ids $instance --document-name AWS-RunShellScript --parameters 'commands=["sudo /usr/local/sbin/netra-bootstrap-pgbouncer"]'
```

After opening the SSM tunnel to PgBouncer on local port 6432, retrieve the
application secret into the current shell without printing it and construct the
SQLAlchemy URL there. Run Alembic separately; Terraform and the identity
bootstrap do not run application migrations.

```powershell
$secretArn = terraform -chdir=infrastructure/terraform output -raw database_application_secret_arn
$secretEnvelope = aws secretsmanager get-secret-value --secret-id $secretArn --output json | ConvertFrom-Json
$databaseIdentity = $secretEnvelope.SecretString | ConvertFrom-Json
$escapedPassword = [uri]::EscapeDataString($databaseIdentity.password)
$env:DATABASE_URL = "postgresql+asyncpg://$($databaseIdentity.username):$escapedPassword@localhost:6432/netra"
uv run --locked alembic -c api/alembic.ini upgrade head
```

The PgBouncer pool mode is `session`, the conservative mode compatible with
session-level PostgreSQL behavior. Transaction pooling must not be enabled for
LangGraph checkpoint connections without separate compatibility validation.

Open the application tunnel to PgBouncer itself before starting the local API:

```powershell
$instance = terraform -chdir=infrastructure/terraform output -raw pgbouncer_instance_id
aws ssm start-session --target $instance --document-name AWS-StartPortForwardingSession --parameters "portNumber=6432,localPortNumber=6432"
```

PgBouncer listens only on the instance loopback interface, which is compatible
with this SSM document and does not require public ingress.

## Optional Prometheus-2 evaluator

GPU evaluation stays disabled in the normal plan. When explicitly enabled,
Terraform resolves AWS's regional Ubuntu 24.04 single-CUDA DLAMI through its
public SSM parameter. The default `g5.xlarge` supplies one NVIDIA A10G with
24 GiB GPU memory. The bootstrap runs the pinned
`vllm/vllm-openai:v0.29.0-cu129-ubuntu2404` image and the pinned model revision
`66ffb1fc20beebfb60a3964a957d9011723116c5`. The service binds to
`127.0.0.1:8000`; the evaluator security group has no ingress, and its IAM role
has SSM access but no database-secret permission.

Review the optional plan before creating the temporary instance:

```powershell
terraform -chdir=infrastructure/terraform plan -var="aws_region=ap-south-1" -var='availability_zones=["ap-south-1a","ap-south-1b"]' -var="enable_prometheus_gpu=true"
```

After an operator explicitly applies that configuration and the service reports
healthy through SSM, open the local evaluator tunnel:

```powershell
$gpu = terraform -chdir=infrastructure/terraform output -raw prometheus_instance_id
aws ssm start-session --target $gpu --document-name AWS-StartPortForwardingSession --parameters "portNumber=8000,localPortNumber=18000"
```

The model is public, so no Hugging Face token is stored. GPU service quota and
`g5.xlarge` availability in the selected availability zone remain operator
preflight checks. Disable the evaluator again with a reviewed plan/apply using
`-var="enable_prometheus_gpu=false"` after evaluation.
