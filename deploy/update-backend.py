#!/usr/bin/env python3
"""Update this stack's backend image through SSM, rolling back on failed health.

Requires boto3 (included in requirements.txt). Does not retrieve the YouTube key.
The image must already be pushed to the existing ECR repository.
"""

import argparse
import json
import re
import shlex
import time

import boto3

IMAGE_PATTERN = re.compile(
    r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/audio-fastapi-app"
    r"(?::[A-Za-z0-9_.-]+|@sha256:[a-f0-9]{64})$"
)
ORIGINS_PATTERN = re.compile(r"^https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:,https://[A-Za-z0-9.-]+(?::[0-9]+)?)*$")


def update_script(image, origins=None):
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError("Use a tagged/digested audio-fastapi-app ECR image URI.")
    if origins is not None and not ORIGINS_PATTERN.fullmatch(origins):
        raise ValueError("CORS origins must be comma-separated HTTPS origins.")
    script = """set -euo pipefail
exec 9>/var/lock/audiotube-deploy.lock
flock -n 9 || { echo 'Another AudioTube deployment is active.'; exit 1; }
previous_deployment=$(mktemp /etc/audiotube/deployment.previous.XXXXXX)
previous_app=$(mktemp /etc/audiotube/app.previous.XXXXXX)
cp /etc/audiotube/deployment.env "$previous_deployment"
cp /etc/audiotube/app.env "$previous_app"
finish() {
  result=$?
  trap - EXIT
  if [ "$result" -ne 0 ]; then
    cp "$previous_deployment" /etc/audiotube/deployment.env
    cp "$previous_app" /etc/audiotube/app.env
    systemctl restart audiotube-api || true
    echo 'Deployment failed; restored previous backend configuration.'
  fi
  rm -f "$previous_deployment" "$previous_app"
  exit "$result"
}
trap finish EXIT
"""
    script += "new_image=" + shlex.quote(image) + "\n"
    script += "sed -i \"s|^BACKEND_IMAGE_URI=.*|BACKEND_IMAGE_URI=$new_image|\" /etc/audiotube/deployment.env\n"
    if origins is not None:
        script += "new_origins=" + shlex.quote(origins) + "\n"
        script += "sed -i \"s|^CORS_ORIGINS=.*|CORS_ORIGINS=$new_origins|\" /etc/audiotube/app.env\n"
    script += """/usr/local/bin/audiotube-pull
systemctl restart audiotube-api
ready=false
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null; then ready=true; break; fi
  sleep 2
 done
if [ "$ready" != true ]; then exit 1; fi
actual_image=$(docker inspect --format '{{.Config.Image}}' audiotube-api)
if [ "$actual_image" != "$new_image" ]; then exit 1; fi
curl -fsS http://127.0.0.1:8000/audio/status
printf '\\nBackend health passed for %s\\n' "$new_image"
"""
    return script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack-name", default="audiotube-backend")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--image-uri", required=True)
    parser.add_argument("--cors-origins")
    args = parser.parse_args()
    try:
        script = update_script(args.image_uri, args.cors_origins)
    except ValueError as error:
        parser.error(str(error))
    cloudformation = boto3.client("cloudformation", region_name=args.region)
    stack = cloudformation.describe_stacks(StackName=args.stack_name)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    instance_id = outputs.get("InstanceId")
    if not instance_id:
        parser.error("The selected stack has no InstanceId output.")
    ssm = boto3.client("ssm", region_name=args.region)
    result = ssm.send_command(
        InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
        Comment="AudioTube image update with automatic health rollback",
        TimeoutSeconds=600,
        Parameters={"commands": ["bash -lc " + shlex.quote(script)], "executionTimeout": ["600"]},
    )
    command_id = result["Command"]["CommandId"]
    print(json.dumps({"commandId": command_id, "instanceId": instance_id}))
    deadline = time.monotonic() + 660
    while time.monotonic() < deadline:
        try:
            invocation = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(2)
            continue
        status = invocation["Status"]
        if status in {"Success", "Cancelled", "Failed", "TimedOut"}:
            print(invocation.get("StandardOutputContent", ""))
            if invocation.get("StandardErrorContent"):
                print(invocation["StandardErrorContent"])
            if status != "Success":
                raise SystemExit("Backend deployment status: " + status)
            return
        time.sleep(2)
    raise SystemExit("Timed out waiting for SSM; inspect command " + command_id + " before retrying.")


if __name__ == "__main__":
    main()
