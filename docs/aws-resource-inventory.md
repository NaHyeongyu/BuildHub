# AWS Resource Inventory

Snapshot date: 2026-07-12

## Account

```text
AWS account id: 435917083683
Default region: ap-southeast-2
Deployment profile: promty-prod
```

## Domains

```text
Hosted zone: promty.org
Hosted zone id: Z0817292287LJZGIOAXWU
Frontend domains: promty.org, www.promty.org
API domain: api.promty.org
```

Current API DNS:

```text
api.promty.org A 13.237.112.139
Record file: infra/aws/promty-api-ec2-dns-change.json
```

## Certificates

CloudFront-compatible ACM certificate:

```text
Region: us-east-1
Status: ISSUED
Certificate ARN: arn:aws:acm:us-east-1:435917083683:certificate/8827f663-4b1e-4f94-b8e7-9b2b6544f49e
Domains:
  promty.org
  www.promty.org
  api.promty.org
```

Validation records are tracked in:

```text
infra/aws/promty-acm-validation-records.json
```

The API is currently served by Caddy on EC2. Caddy obtains and renews the
`api.promty.org` TLS certificate through Let's Encrypt.

Historical App Runner domain validation records were removed from Route 53.
The cleanup batch is tracked in:

```text
infra/aws/promty-apprunner-api-domain-validation-delete-records.json
```

## Frontend

CloudFront:

```text
Distribution id: E3RJ7YU3NUZQSF
Distribution domain: d20rjon9u3lu41.cloudfront.net
Aliases: promty.org, www.promty.org
Status: Deployed
Origin: promty-prod-frontend-435917083683.s3.ap-southeast-2.amazonaws.com
Origin access control id: ETUAJRENPIY53
Response headers policy: 67f7725c-6f97-4210-82d7-5512b31e9d03 Managed-SecurityHeadersPolicy
Viewer request function: promty-spa-rewrite
HTTP versions: HTTP/2 and HTTP/3
Price class: PriceClass_100
```

S3 frontend bucket:

```text
Name: promty-prod-frontend-435917083683
ARN: arn:aws:s3:::promty-prod-frontend-435917083683
Public access: blocked
Versioning: enabled
Default encryption: AES256
```

Frontend configuration files:

```text
infra/aws/promty-cloudfront-distribution.json
infra/aws/promty-cloudfront-spa-rewrite.js
infra/aws/configure-promty-cloudfront.sh
infra/aws/promty-cloudfront-oac.json
infra/aws/promty-frontend-bucket-policy.json
infra/aws/promty-frontend-dns-records.json
```

## Backend Image

ECR:

```text
Repository name: promty/backend
Repository ARN: arn:aws:ecr:ap-southeast-2:435917083683:repository/promty/backend
Repository URI: 435917083683.dkr.ecr.ap-southeast-2.amazonaws.com/promty/backend
Scan on push: enabled
Encryption: AES256
```

## Active API Runtime

The production API currently runs on one low-cost EC2 instance with Docker.

```text
Instance id: i-066ab5e01b9685b6a
Instance type: t3a.micro
AMI: ami-077acc0a911fb6286
Elastic IP allocation: eipalloc-007935ede4ad5e019
Elastic IP address: 13.237.112.139
Subnet: subnet-0d63d0c4ab49394a4
Security group: sg-03e4e45c3aaee5581
IAM role: promty-ec2-api-instance
Instance profile: promty-ec2-api-instance
```

Inbound rules:

```text
tcp/80 from 0.0.0.0/0 and ::/0
tcp/443 from 0.0.0.0/0 and ::/0
No SSH inbound rule
```

Runtime containers:

```text
promty-postgres: postgres:18-alpine
promty-backend: 435917083683.dkr.ecr.ap-southeast-2.amazonaws.com/promty/backend:latest
promty-caddy: caddy:2-alpine
```

EC2 bootstrap and IAM files:

```text
infra/aws/promty-ec2-api-user-data.sh
infra/aws/promty-ec2-api-instance-trust.json
infra/aws/promty-ec2-api-instance-policy.json
```

The `promty-ec2-api-runtime` inline policy was synchronized and verified on
2026-07-21, including scoped access to `account-deletion-ledger/*`.

## Database

Active database:

```text
Engine: PostgreSQL 18 Docker container
Container: promty-postgres
Database name: promty
Username: promty_admin
Data path on EC2: /opt/promty/postgresql
```

Backups:

```text
S3 bucket: promty-prod-assets-435917083683
S3 prefix: database-backups/
Systemd timer: promty-db-backup.timer
Schedule: daily at 03:17 UTC
Latest verified manual backup: s3://promty-prod-assets-435917083683/database-backups/promty-20260712T053417Z.dump
```

Previous RDS database:

```text
Identifier: promty-prod-db
Endpoint: promty-prod-db.cvaakqisupj8.ap-southeast-2.rds.amazonaws.com
Engine: PostgreSQL 18
Class: db.t4g.micro
Status: deleted on 2026-07-12
Final snapshot: promty-prod-db-final-20260712-ec2-cutover
Final snapshot status: available
Final snapshot size: 20 GB
```

## Private Asset Bucket

```text
Name: promty-prod-assets-435917083683
ARN: arn:aws:s3:::promty-prod-assets-435917083683
Public access: blocked
Versioning: enabled
Default encryption: AES256
Application asset prefix: published-flow-assets/
Database backup prefix: database-backups/
Account deletion replay prefix: account-deletion-ledger/
Lifecycle configuration: infra/aws/promty-assets-lifecycle.json
Lifecycle status: applied and verified on 2026-07-21 (6 rules enabled)
Database backup retention: 30 days
Deleted asset noncurrent-version retention: 30 days
Deletion tombstone retention: 35 days
```

## Networking

```text
VPC: vpc-05abd8dcef72223fa
Public API subnet: subnet-0d63d0c4ab49394a4 ap-southeast-2a
Other existing subnets:
  subnet-04375abf87cc69d46 ap-southeast-2c
  subnet-0e3be0293016e6f06 ap-southeast-2b
Previous private App Runner subnets:
  subnet-0af40d93ce08cdcdb ap-southeast-2a 172.31.240.0/24
  subnet-0429cf93c3adaf1da ap-southeast-2b 172.31.241.0/24
  subnet-0ce13e28691072dcf ap-southeast-2c 172.31.242.0/24
```

Cost-reduction cleanup:

```text
Deleted NAT gateway: nat-0fed17885361650db
Released NAT elastic IP allocation: eipalloc-0f5803a28421ddec2
Released NAT public IP: 32.236.253.122
Deleted App Runner service: promty-prod-api
Deleted App Runner VPC connectors:
  promty-prod-vpc-connector
  promty-prod-vpc-connector-nat
Deleted App Runner IAM roles:
  promty-apprunner-ecr-access
  promty-apprunner-instance
```

Legacy networking objects may remain because they do not materially affect the
monthly run rate:

```text
Private route table: rtb-06de908c3a504844d
Legacy App Runner security group: sg-07bcfdc3e060768d9
NAT App Runner security group: sg-0fb1480facd964b0e
RDS security group: sg-0c72a43cc84b5deef
```

## Secrets Manager

```text
promty/prod/database-url
promty/prod/ec2-postgres-password
promty/prod/app-encryption-key
promty/prod/github-client-id
promty/prod/github-client-secret
promty/prod/github-token-encryption-key
promty/prod/oauth-state-secret
promty/prod/jwt-secret
promty/prod/global-ingest-token
promty/prod/openai-api-key
```

`promty/prod/database-url` is the previous RDS URL and was used for one-time
migration. The active EC2 backend now uses a local Postgres URL generated in
`/opt/promty/backend.env`.

## Legacy App Runner

App Runner is no longer the production API target and the service has been
deleted.

```text
Service name: promty-prod-api
Service ARN: arn:aws:apprunner:ap-southeast-2:435917083683:service/promty-prod-api/04be6335c00f43fb86dd2d3506f95700
Default URL: https://xcyfny8pb3.ap-southeast-2.awsapprunner.com
Status: DELETED
Previous custom domain: https://api.promty.org
Image: 435917083683.dkr.ecr.ap-southeast-2.amazonaws.com/promty/backend:latest
VPC connectors: deleted
IAM roles: deleted
Route 53 validation records: deleted
```

Legacy App Runner configuration files:

```text
infra/aws/promty-apprunner-service.json
infra/aws/promty-api-dns-records.json
infra/aws/promty-apprunner-instance-trust.json
infra/aws/promty-apprunner-instance-policy.json
infra/aws/promty-apprunner-ecr-access-trust.json
```

## GitHub Actions IAM

```text
Role name: promty-github-actions-deploy
Role ARN: arn:aws:iam::435917083683:role/promty-github-actions-deploy
Trusted GitHub repo: NaHyeongyu/promty
Trusted ref: refs/heads/master
```

The deploy role can:

- push backend images to ECR
- sync frontend files to the frontend S3 bucket
- publish and associate the route-aware CloudFront function
- create CloudFront invalidations
- send SSM commands to EC2 instance `i-066ab5e01b9685b6a`

Policy files:

```text
infra/aws/promty-github-actions-trust.json
infra/aws/promty-github-actions-policy.json
```

## GitHub Secrets

```text
AWS_ROLE_TO_ASSUME=arn:aws:iam::435917083683:role/promty-github-actions-deploy
AWS_REGION=ap-southeast-2
AWS_EC2_INSTANCE_ID=i-066ab5e01b9685b6a
ECR_REPOSITORY=promty/backend
FRONTEND_S3_BUCKET=promty-prod-frontend-435917083683
CLOUDFRONT_DISTRIBUTION_ID=E3RJ7YU3NUZQSF
VITE_PROMTY_API_URL=https://api.promty.org
```

These are stored in GitHub repo `NaHyeongyu/promty`.

## Backend Runtime Environment

These values are configured on EC2 in `/opt/promty/backend.env`:

```text
DATABASE_URL=postgresql+psycopg://promty_admin:<secret>@promty-postgres:5432/promty
PROMTY_API_PUBLIC_URL=https://api.promty.org
PROMTY_APP_URL=https://promty.org
PROMTY_CORS_ORIGINS=https://promty.org,https://www.promty.org
PROMTY_SESSION_COOKIE_SECURE=true
PROMTY_SESSION_COOKIE_SAMESITE=lax
PROMTY_ADMIN_GITHUB_IDS=191438254
PROMTY_AUTH_RATE_LIMIT_REQUESTS=30
PROMTY_AUTH_RATE_LIMIT_WINDOW_SECONDS=60
PROMTY_ADMIN_RATE_LIMIT_REQUESTS=120
PROMTY_ADMIN_RATE_LIMIT_WINDOW_SECONDS=60
PROMTY_COMMUNITY_RATE_LIMIT_REQUESTS=120
PROMTY_COMMUNITY_RATE_LIMIT_WINDOW_SECONDS=60
PROMTY_INGEST_RATE_LIMIT_REQUESTS=120
PROMTY_INGEST_RATE_LIMIT_WINDOW_SECONDS=60
PROMTY_TRUSTED_PROXY_CIDRS=127.0.0.0/8,::1/128,172.16.0.0/12
PROMTY_EVENT_BATCH_MAX_BODY_BYTES=8388608
PROMTY_ADMIN_AUDIT_RETENTION_DAYS=180
PROMTY_SUPPORT_EMAIL_PROVIDER=ses
PROMTY_SUPPORT_FROM_EMAIL=support@promty.org
PROMTY_SUPPORT_NOTIFICATION_EMAILS=<secret from promty/prod/support-notification-email>
PROMTY_SUPPORT_RATE_LIMIT_REQUESTS=5
PROMTY_SUPPORT_RATE_LIMIT_WINDOW_SECONDS=300
PROMTY_PUBLISHED_FLOW_ASSET_STORAGE=s3
PROMTY_AWS_REGION=ap-southeast-2
PROMTY_AWS_S3_BUCKET=promty-prod-assets-435917083683
PROMTY_AWS_S3_PREFIX=published-flow-assets
PROMTY_APP_ENCRYPTION_KEY_ID=aws-prod
PROMTY_API_TOKEN=<secret from promty/prod/global-ingest-token>
PROMTY_MEMORY_GENERATOR=local
PROMTY_MEMORY_DRAFT_GENERATOR=local
PROMTY_PROJECT_MEMORY_GENERATOR=local
```

Secret values are fetched from Secrets Manager during EC2 bootstrap and written
to the local env file.
