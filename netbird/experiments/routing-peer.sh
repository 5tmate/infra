#!/usr/bin/env bash
set -euo pipefail
PROFILE=${AWS_PROFILE:-jerry}
MGMT=https://netbird.5tmate.threatreveal.org
NB_VER=0.81.0
TYPE=${TYPE:-t4g.nano}
TAG=netbird-routing-test              # Project 標籤,找機器和清理用
REGION=${REGION:-ap-northeast-2}
ROUTER_HOST=netbird-router-apne2      # routing peer 在 dashboard 的名字

vpc_info() {
  vpc=$(aws ec2 describe-vpcs --profile "$PROFILE" --region "$REGION" \
    --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
  [ "$vpc" != None ] || { echo "no default VPC in $REGION" >&2; exit 1; }
  cidr=$(aws ec2 describe-vpcs --profile "$PROFILE" --region "$REGION" \
    --vpc-ids "$vpc" --query 'Vpcs[0].CidrBlock' --output text)
  subnet=$(aws ec2 describe-subnets --profile "$PROFILE" --region "$REGION" \
    --filters Name=vpc-id,Values="$vpc" Name=default-for-az,Values=true \
    --query 'Subnets[0].SubnetId' --output text)
  ami=$(aws ssm get-parameter --profile "$PROFILE" --region "$REGION" \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
    --query Parameter.Value --output text)
}

ensure_sg() {
  sg=$(aws ec2 describe-security-groups --profile "$PROFILE" --region "$REGION" \
    --filters Name=group-name,Values=$TAG Name=vpc-id,Values="$vpc" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
  if [ "$sg" = None ] || [ -z "$sg" ]; then
    sg=$(aws ec2 create-security-group --profile "$PROFILE" --region "$REGION" \
      --group-name $TAG --description "netbird routing peer test" --vpc-id "$vpc" \
      --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=$TAG}]" \
      --query GroupId --output text)
    aws ec2 authorize-security-group-ingress --profile "$PROFILE" --region "$REGION" \
      --group-id "$sg" --ip-permissions \
      "IpProtocol=icmp,FromPort=-1,ToPort=-1,IpRanges=[{CidrIp=$cidr}]" \
      "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=$cidr}]" >/dev/null
  fi
}

router_userdata() {
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
netbird up --management-url ${MGMT} --setup-key ${SETUP_KEY} --hostname ${ROUTER_HOST}
EOF
}

target_userdata() {
  cat <<'EOF'
#!/bin/bash
set -e
echo "routing-peer test target OK on $(hostname -f)" >/root/index.html
systemd-run --unit=httptest /bin/bash -c 'cd /root && exec /usr/bin/python3 -m http.server 80'
EOF
}

launch() {
  aws ec2 run-instances --profile "$PROFILE" --region "$REGION" \
    --image-id "$ami" --instance-type "$TYPE" --security-group-ids "$sg" --subnet-id "$subnet" \
    $3 --user-data "$2" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$1},{Key=Project,Value=$TAG}]" \
    --query 'Instances[0].InstanceId' --output text
}

router() {
  : "${SETUP_KEY:?set SETUP_KEY=<netbird setup key from the dashboard>}"
  vpc_info
  ensure_sg
  iid=$(launch "$ROUTER_HOST" "$(router_userdata)" "--associate-public-ip-address")
  aws ec2 wait instance-running --profile "$PROFILE" --region "$REGION" --instance-ids "$iid"
  pub=$(aws ec2 describe-instances --profile "$PROFILE" --region "$REGION" --instance-ids "$iid" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  prv=$(aws ec2 describe-instances --profile "$PROFILE" --region "$REGION" --instance-ids "$iid" \
    --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)
  echo "routing peer  $iid  public $pub  private $prv  hostname $ROUTER_HOST"
  echo "VPC CIDR = $cidr   <-- dashboard 加 route 用這個"
  echo "give it ~2 min to install and join, then the peer $ROUTER_HOST shows up in the dashboard"
}

target() {
  vpc_info
  ensure_sg
  iid=$(launch "netbird-routing-target" "$(target_userdata)" "--no-associate-public-ip-address")
  aws ec2 wait instance-running --profile "$PROFILE" --region "$REGION" --instance-ids "$iid"
  prv=$(aws ec2 describe-instances --profile "$PROFILE" --region "$REGION" --instance-ids "$iid" \
    --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)
  echo "target (no netbird)  $iid  private $prv"
  echo "ping / curl this private IP from the laptop: $prv"
}

down() {
  ids=$(aws ec2 describe-instances --profile "$PROFILE" --region "$REGION" \
    --filters Name=tag:Project,Values=$TAG \
    Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[].Instances[].InstanceId' --output text)
  if [ -n "$ids" ]; then
    aws ec2 terminate-instances --profile "$PROFILE" --region "$REGION" --instance-ids $ids >/dev/null
    echo "terminating $ids"
    aws ec2 wait instance-terminated --profile "$PROFILE" --region "$REGION" --instance-ids $ids
  else
    echo "no instances"
  fi
  sg=$(aws ec2 describe-security-groups --profile "$PROFILE" --region "$REGION" \
    --filters Name=group-name,Values=$TAG --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
  if [ "$sg" != None ] && [ -n "$sg" ]; then
    aws ec2 delete-security-group --profile "$PROFILE" --region "$REGION" --group-id "$sg" && echo "deleted sg $sg"
  fi
  echo "done. remember to delete the peer and the network route in the netbird dashboard too."
}

case "${1:-}" in
  router) router ;;
  target) target ;;
  down) down ;;
  *) echo "usage: SETUP_KEY=<key> AWS_PROFILE=jerry $0 router   |   AWS_PROFILE=jerry $0 target   |   AWS_PROFILE=jerry $0 down"; exit 1 ;;
esac
