#!/usr/bin/env bash
set -euo pipefail
PREFIX=${1:-實驗FIS}
DURATION=${DURATION:-5}
source "$(dirname "$0")/lib.sh"

preflight
start_probe
arm

fis_role=$NAME-fis-test
account=$(aws sts get-caller-identity --query Account --output text)
asg_arn=$(asg AutoScalingGroupARN)

aws iam create-role --role-name "$fis_role" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"fis.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
  --output text >/dev/null
aws iam put-role-policy --role-name "$fis_role" --policy-name inject-asg-ice \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ec2:InjectApiError","Resource":"*","Condition":{"ForAnyValue:StringEquals":{"ec2:FisActionId":["aws:ec2:asg-insufficient-instance-capacity-error"]}}},{"Effect":"Allow","Action":"autoscaling:DescribeAutoScalingGroups","Resource":"*"}]}'
log "created FIS role $fis_role"
sleep 10

cat >"$out/fis-template.json" <<JSON
{"stopConditions":[{"source":"none"}],
 "roleArn":"arn:aws:iam::$account:role/$fis_role",
 "description":"$NAME test: insufficient capacity for the ASG in every AZ",
 "targets":{"ASG":{"resourceType":"aws:ec2:autoscaling-group","resourceArns":["$asg_arn"],"selectionMode":"ALL"}},
 "actions":{"ice":{"actionId":"aws:ec2:asg-insufficient-instance-capacity-error",
   "parameters":{"availabilityZoneIdentifiers":"ap-northeast-1a,ap-northeast-1c,ap-northeast-1d","duration":"PT${DURATION}M","percentage":"100"},
   "targets":{"AutoScalingGroups":"ASG"}}},
 "tags":{"Name":"$fis_role","App":"5tmate"}}
JSON
fis_template=$(aws fis create-experiment-template --cli-input-json "file://$out/fis-template.json" \
  --query 'experimentTemplate.id' --output text)
log "created FIS template $fis_template"

aws fis start-experiment --experiment-template-id "$fis_template" \
  --query 'experiment.id' --output text >"$out/fis-experiment-id"
log "started FIS experiment $(cat "$out/fis-experiment-id"), injecting for ${DURATION} min"

terminate_primary
wait_until 1200 "service up on the standby" on_standby
wait_until 1800 "failed back to a new primary" settled
