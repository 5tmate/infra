#!/usr/bin/env bash
set -euo pipefail
PROFILE=${AWS_PROFILE:-jerry}
MGMT=https://netbird.5tmate.threatreveal.org
NB_VER=0.81.0
TYPE=${TYPE:-t4g.nano}
TAG=netbird-exit-test
REGIONS=${REGIONS:-"ap-northeast-2 us-west-1"}

userdata() {
  cat <<EOF
#!/bin/bash
set -e
cd /tmp
for i in \$(seq 1 5); do
  curl -fsSL -o nb.tar.gz https://github.com/netbirdio/netbird/releases/download/v${NB_VER}/netbird_${NB_VER}_linux_arm64.tar.gz && break
  sleep 5
done
tar xzf nb.tar.gz
install -m755 netbird /usr/bin/netbird
sysctl -w net.ipv4.ip_forward=1
netbird service install
netbird service start
sleep 5
netbird up --management-url ${MGMT} --setup-key ${SETUP_KEY} --hostname $1
EOF
}

up() {
  : "${SETUP_KEY:?set SETUP_KEY=<reusable netbird setup key from the dashboard>}"
  for region in $REGIONS; do
    echo "=== $region ==="
    ami=$(aws ssm get-parameter --profile "$PROFILE" --region "$region" \
      --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
      --query Parameter.Value --output text)
    vpc=$(aws ec2 describe-vpcs --profile "$PROFILE" --region "$region" \
      --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
    [ "$vpc" != None ] || { echo "no default VPC in $region, skipping"; continue; }
    subnet=$(aws ec2 describe-subnets --profile "$PROFILE" --region "$region" \
      --filters Name=vpc-id,Values="$vpc" Name=default-for-az,Values=true \
      --query 'Subnets[0].SubnetId' --output text)
    sg=$(aws ec2 describe-security-groups --profile "$PROFILE" --region "$region" \
      --filters Name=group-name,Values=$TAG Name=vpc-id,Values="$vpc" \
      --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
    if [ "$sg" = None ] || [ -z "$sg" ]; then
      sg=$(aws ec2 create-security-group --profile "$PROFILE" --region "$region" \
        --group-name $TAG --description "netbird exit node test" --vpc-id "$vpc" \
        --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=$TAG}]" \
        --query GroupId --output text)
    fi
    host="netbird-exit-${region}"
    iid=$(aws ec2 run-instances --profile "$PROFILE" --region "$region" \
      --image-id "$ami" --instance-type "$TYPE" --security-group-ids "$sg" --subnet-id "$subnet" \
      --associate-public-ip-address --user-data "$(userdata "$host")" \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$host},{Key=Project,Value=$TAG}]" \
      --query 'Instances[0].InstanceId' --output text)
    aws ec2 wait instance-running --profile "$PROFILE" --region "$region" --instance-ids "$iid"
    ip=$(aws ec2 describe-instances --profile "$PROFILE" --region "$region" --instance-ids "$iid" \
      --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
    echo "$region  $iid  public IP $ip  hostname $host"
  done
  echo "give the agents ~2 min to install and join, then check the netbird dashboard for the two peers"
}

down() {
  for region in $REGIONS; do
    ids=$(aws ec2 describe-instances --profile "$PROFILE" --region "$region" \
      --filters Name=tag:Project,Values=$TAG \
      Name=instance-state-name,Values=pending,running,stopping,stopped \
      --query 'Reservations[].Instances[].InstanceId' --output text)
    if [ -n "$ids" ]; then
      aws ec2 terminate-instances --profile "$PROFILE" --region "$region" --instance-ids $ids >/dev/null
      echo "$region: terminating $ids"
      aws ec2 wait instance-terminated --profile "$PROFILE" --region "$region" --instance-ids $ids
    else
      echo "$region: no instances"
    fi
    sg=$(aws ec2 describe-security-groups --profile "$PROFILE" --region "$region" \
      --filters Name=group-name,Values=$TAG --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
    if [ "$sg" != None ] && [ -n "$sg" ]; then
      aws ec2 delete-security-group --profile "$PROFILE" --region "$region" --group-id "$sg" && echo "$region: deleted sg $sg"
    fi
  done
  echo "done. remember to delete the two peers (and the setup key) in the netbird dashboard too."
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *) echo "usage: SETUP_KEY=<key> AWS_PROFILE=jerry $0 up    |    AWS_PROFILE=jerry $0 down"; exit 1 ;;
esac
