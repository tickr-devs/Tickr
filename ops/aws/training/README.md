# Training stack (AWS)

One private bucket and one on-demand training machine for model research, in the owner's AWS
account, apart from the bot (backlog T15; training spec stage 4). The owner applies it from his
Mac; the research thread then uses only the narrow `tickr-research` user.

What it creates:
- `tickr-research-<account id>`: private, encrypted S3 bucket, TLS only. Data derived from the
  owner's personal Massive plan stays here; delete it within 30 days after that plan ends.
- Launch template `tickr-training`: Ubuntu 24.04, `instance_type` (default c7a.16xlarge,
  64 vCPU, 128 GB; m7a.16xlarge gives 256 GB), spot, 500 GB encrypted gp3 deleted with the
  machine, no inbound port, reached only through SSM. It powers itself off (and terminates)
  `max_runtime_hours` after boot.
- IAM user `tickr-research`: may start machines only from that template, stop or terminate
  only machines tagged `tickr:role=training`, run SSM commands and sessions only on them, and
  read and write only the bucket. No Secrets Manager, no bot server, no IAM, no billing.
- Budget `tickr-monthly` (default 280 USD: the bot's ~80 plus 200 for training) with mail at
  50 % spent and at a forecast over 100 %.

## Apply (owner, on the Mac)

Needs `brew install terraform awscli` and an admin sign-in profile for the account.

```sh
cd ~/Development/Tickr/ops/aws/training
terraform init
terraform apply -var budget_email='you@example.com'
```

Then create the research user's key by hand, so it never sits in Terraform state:

```sh
aws iam create-access-key --user-name tickr-research
```

Put the two values into the project's cloud environment as the secrets
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (never into chat, a file in the repo or a log).
Delete the key with `aws iam delete-access-key` when the training round ends.

Every machine start still needs the owner's yes on a named sum.
