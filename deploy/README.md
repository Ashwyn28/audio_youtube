# AudioTube backend deployment

This is a prepared deployment plan. Creating the stack incurs AWS charges. The
legacy metadata Lambda and its API Gateway are not changed by this template.

## Verified account resources (2026-10-24)

- AWS account `542248535090`, region `us-east-1`; the configured user has deployment permissions.
- Existing ECR repository: `542248535090.dkr.ecr.us-east-1.amazonaws.com/audio-fastapi-app`.
- Preserve the existing `v52` image and `fastapi_lambda_docker` Lambda.
- Existing secret: SSM SecureString `youtube_api_key`, encrypted with `alias/aws/ssm`.
- Default VPC: `vpc-06616fd1911eed391`.
- Public subnet: `subnet-0813eca92ade2943f` (`us-east-1a`), with Internet Gateway routing.
- Public Route53 zone: `Z03021913DY45MXDOBAW5` (`orbbits.io`), with matching public NS delegation.
- `audio-api.orbbits.io` has no existing record.
- AL2023 ARM64 public AMI parameter currently resolves to `ami-07890d146f8d6ee5b`.

Refresh these facts before a later deployment. The image tag/digest and actual
frontend origin must be supplied for the release being deployed.

## Hosting shape and cost

`backend-ec2.yaml` creates one ARM64 `t4g.micro` instance (1 GiB RAM), a stable
Elastic IP, a 10 GiB encrypted gp3 root disk, an IAM role/profile, an HTTP/HTTPS
security group and optionally a DNS A record. Docker runs the backend on loopback;
Caddy terminates HTTPS and streams responses without a total response timeout.

At 730 hours/month, the approximate baseline is **$10.58/month**: t4g.micro
$0.0084/hour ($6.13), one public IPv4 $0.005/hour ($3.65), and 10 GiB gp3 at
$0.08/GiB-month ($0.80). This excludes outbound audio bandwidth, ECR storage,
Route53 charges, taxes and other account usage. Traffic can cost more than the
host. The EC2 rate was checked against the AWS Price List API on 2026-10-24;
verify current [EC2](https://aws.amazon.com/ec2/pricing/on-demand/),
[IPv4](https://aws.amazon.com/vpc/pricing/), and
[EBS](https://aws.amazon.com/ebs/pricing/) rates before reuse.

CPU credits use Standard mode to avoid unlimited-credit surcharges. A 1 GiB swap
file and container memory limits bound the small host's working set. This is a
single-user starting point, not high availability. Two simultaneous extraction
workers plus Node may pressure 1 GiB: verify peak memory during real playback;
`t4g.small` is an explicit higher-cost alternative in the template.

## Access and secret handling

- Only TCP 80/443 are open. There is no SSH key or port 22 rule.
- Administration uses AWS Systems Manager Session Manager / Run Command.
- The instance role can read only this ECR repository and the `youtube_api_key`
  parameter, plus the SSM connection channels and its own CloudFormation health signal.
- IMDSv2 is required; hop limit 2 lets boto3 in the Docker bridge obtain its role credentials.
- The application loads the key directly from SSM. No API key is placed in user data,
  the template, command arguments, repository files or static environment files.
- ECR login credentials are piped over stdin with shell tracing disabled; a temporary
  Docker configuration in `/run` is removed on success or failure after the pull.
- Backend and proxy container logs are rotated. Caddy certificate state stays on the
  host volume; it is lost if the instance is replaced.

## Initial deployment

1. Build and test the committed backend production image for `linux/arm64`.
2. Push it to a **new** tag in `audio-fastapi-app`; prefer its immutable digest as
   `AUDIOTUBE_IMAGE_URI`. Do not overwrite `v52` or point the old Lambda at the HTTP image.
3. Confirm the frontend's actual HTTPS origin, then create/review a CloudFormation
   change set or run the command below when deployment is authorized.

```sh
aws cloudformation validate-template \
  --region us-east-1 \
  --template-body file://deploy/backend-ec2.yaml

# Set this to the actual pushed release digest before running the deployment.
AUDIOTUBE_IMAGE_URI='542248535090.dkr.ecr.us-east-1.amazonaws.com/audio-fastapi-app@sha256:REPLACE_WITH_RELEASE_DIGEST'
AUDIOTUBE_CORS_ORIGINS='https://audio-youtube.orbbits.io,https://orbbits.io'

aws cloudformation deploy \
  --region us-east-1 \
  --stack-name audiotube-backend \
  --template-file deploy/backend-ec2.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    VpcId=vpc-06616fd1911eed391 \
    SubnetId=subnet-0813eca92ade2943f \
    HostedZoneId=Z03021913DY45MXDOBAW5 \
    DomainName=audio-api.orbbits.io \
    BackendImageUri="$AUDIOTUBE_IMAGE_URI" \
    CorsOrigins="$AUDIOTUBE_CORS_ORIGINS"
```

An empty `HostedZoneId` leaves DNS management external; point the chosen domain's
A record at the stack's `PublicIp`. An empty `DomainName` enables HTTP-only testing,
which is unsuitable for the production HTTPS frontend.

Bootstrap uses the current AL2023 ARM64 AMI, installs Docker, enables SSM, prepares
swap, pulls the image, and waits for `/health` before signalling CloudFormation.
Caddy starts before that signal, but public certificate issuance may finish after
DNS propagation. The stack's successful creation is **not** proof of live extraction,
background playback, or certificate readiness.

## Validation after creation

```sh
curl --fail https://audio-api.orbbits.io/health
curl --fail https://audio-api.orbbits.io/audio/status

aws cloudformation describe-stacks \
  --region us-east-1 --stack-name audiotube-backend \
  --query 'Stacks[0].Outputs'
```

Then verify the frontend uses `https://audio-api.orbbits.io`, CORS matches its
production origin, a real item resolves and streams (including byte seeking), and
playback survives the intended device/background scenarios. The health endpoints
check process/configuration availability; they do not consume YouTube search quota
and do not establish provider reachability.

Use the stack's `SessionCommand` output for administration. Useful host checks:

```sh
sudo systemctl status audiotube-api audiotube-proxy
sudo journalctl -u audiotube-api -u audiotube-proxy --since '15 minutes ago'
sudo tail -n 100 /var/log/cloud-init-output.log
sudo docker stats --no-stream
free -m
```

Do not dump application environment variables, decrypted SSM values, Docker login
files, or signed upstream stream URLs into logs or chat.

## Updating and rolling back the application

After pushing another unique image tag/digest, update the running backend through
SSM without exposing credentials:

```sh
python deploy/update-backend.py \
  --stack-name audiotube-backend --region us-east-1 \
  --image-uri "$AUDIOTUBE_IMAGE_URI" \
  --cors-origins "$AUDIOTUBE_CORS_ORIGINS"
```

The helper validates inputs, targets only the stack's instance, locks deployment,
pulls the candidate before stopping the current container, restarts the backend,
and checks local health and actual image identity. On failure it restores the
previous environment/image and restarts it. A successful update briefly interrupts
active streams. It does not prove upstream extraction works; perform the live
checks above and explicitly deploy the previous digest with the same helper if
those fail.

`BackendImageUri` and `CorsOrigins` in CloudFormation describe initial bootstrap.
The update helper changes the host's runtime configuration, not those stack
parameters. Supply the latest values when intentionally rebuilding/replacing the
instance. Updating user data alone does not replay cloud-init on an existing host.

To abandon this new host, first point the frontend back to a working target or take
it offline, then delete **only** the `audiotube-backend` stack. Deletion removes its
instance, EIP, disk, generated DNS record and IAM resources. It does not delete the
existing ECR repository, SSM key, legacy Lambda/API Gateway or original website.
Stopping the instance alone does not stop all EIP/EBS charges.

## Scope and operational limits

- No AWS resources are created merely by adding or validating these files.
- The template uses a public subnet, outbound network access and public Docker Hub
  access to bootstrap Caddy. No NAT Gateway or load balancer is created.
- If the existing SSM key moves to a customer-managed KMS key, add a narrowly scoped
  decrypt permission before deployment; the current `alias/aws/ssm` key needs none.
- The proxy image tag is `caddy:2`; pin a reviewed digest for stricter reproducibility.
- Egress, host failure, certificate renewal, extractor changes, bot challenges and
  memory pressure need monitoring. The current extractor can still be blocked by
  YouTube independently of server health. A host migration does not guarantee that
  provider access works from AWS addresses.
- This host has no automatic OS/image update pipeline, centralized logs, backups,
  multi-instance failover or fleet-wide request limiter. Those are separate operating
  decisions; the template keeps the initial deployment small and reviewable.
