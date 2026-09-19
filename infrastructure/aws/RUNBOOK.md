# Netra AWS runbook (M2)

Operator steps for the RDS + PgBouncer + application host deployment
(decisions D-INFRA, D-HOST, D-BACKUP, D-TFSTATE). Every `terraform apply`
is reviewed from a saved plan first. No identifier (instance id, endpoint,
bucket name, account id) and no secret belongs in this file, in commits or
in chat: read them from `terraform output` on your own machine.

## 0. Before anything: line endings

Terraform sends `scripts/*.tftpl` to EC2 as boot scripts. A Windows checkout
with `core.autocrlf=true` used to turn them into CRLF files, which bash cannot
run (`#!/bin/bash\r`). `.gitattributes` now forces LF. After pulling it, re-check
out only the scripts once (they have no local edits, so nothing is lost):

```powershell
Remove-Item infrastructure/terraform/scripts/*.tftpl
git checkout -- infrastructure/terraform/scripts
```

Check: `git ls-files --eol infrastructure/terraform/scripts/` shows `w/lf`.

## 1. Shared Terraform state (D-TFSTATE, once)

1. Create the state bucket (tiny S3 cost; it keeps its own local state):

   ```powershell
   cd infrastructure/terraform/state-bucket
   terraform init
   terraform apply -var aws_region=ap-south-1
   ```

2. Copy `backend.hcl.example` to `backend.hcl` (git-ignored), set `bucket` to
   the `state_bucket` output, then move the existing local state into S3:

   ```powershell
   cd ..
   terraform init -backend-config=backend.hcl -migrate-state
   ```

   Answer "yes" to copy the state. Afterwards keep the old local
   `terraform.tfstate*` files somewhere private until the first successful
   plan from the S3 backend, then delete them.

Everyone else then runs `terraform init -backend-config=backend.hcl` with the
same file; `use_lockfile` stops two applies running at once.

## 2. Plan and apply the application host (D-HOST, D-BACKUP)

Add to your git-ignored `terraform.tfvars`:

```hcl
app_instance_type = "<size you approve>"   # no default: a cost decision
# db_backup_retention_days = 7             # default 7, allowed 1-35
```

Then:

```powershell
terraform plan -out netra.tfplan
terraform show netra.tfplan     # review before applying
terraform apply netra.tfplan
```

What the plan should show, and nothing else:

- **create** the application host, its Elastic IP, security group, IAM role
  and policy, four SSM parameters and the empty `…/app/environment` secret;
- **update in place** the RDS instance: backups on (7 days), backup and
  maintenance windows;
- **update** the PgBouncer security group (6432 from the application host);
- **replace** the PgBouncer instance: its boot script now listens on the
  private address, and the old script may never have run (see step 0). The
  new instance reconfigures itself from Secrets Manager; its instance id
  changes, so update your SSM port-forward command.

Stop if the plan replaces or destroys RDS or the documents bucket.

## 3. Provider settings for the host

Fill the environment secret once (values only in your shell, never in files
or chat). It holds `NETRA_*` keys only, for example the agent, embedding,
Pinecone and TwelveLabs settings from your `.env`:

```powershell
aws secretsmanager put-secret-value --region ap-south-1 --secret-id <app_environment_secret_arn> --secret-string file://netra-env.json
```

Delete `netra-env.json` afterwards. The host's `netra-render-env` ignores
anything that is not `NETRA_*` and cannot override the database URL, bucket
or region, which it builds itself.

## 4. Deploy (on the host, through SSM)

```bash
aws ssm start-session --region ap-south-1 --target <app_instance_id>
sudo -i
netra-render-env
git clone https://github.com/The-Trinetras/netra /opt/netra/src   # or pull
cd /opt/netra/src/infrastructure/compose
export NETRA_ENV_FILE=/etc/netra/netra.env NETRA_IMAGE_TAG=$(git rev-parse --short HEAD) \
       NETRA_TLS_DOMAIN=<domain> NETRA_UPLOAD_MAX_BODY=<approved size, e.g. 20m>
docker compose up -d --build
```

`migrate` runs `alembic upgrade head` once before the API and worker start;
if it fails, nothing else starts. TLS certificates and the domain are covered
by item D1 (`certbot certonly --webroot -w /var/www/certbot -d <domain>`).

## Open decisions

- **Domain for TLS** (Ashlin).
- **D-UPLOAD-SIZE**: maximum upload size; sets `NETRA_UPLOAD_MAX_BODY`.
- **Application host size** (`app_instance_type`).
