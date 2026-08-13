# metabase

Honeypot EC2 running a vulnerable Metabase (`v0.46.6`, CVE-2023-38646). The box
is reachable over SSH and SSM; the Metabase service (port 3000) is not reachable
from the internet.

## Deploy

Register an SSH public key and your source IP (generate a keypair first if you
do not have one). The stack turns `ssh_public_key` into an EC2 key pair and
restricts port 22 to `allowed_ssh_cidr`:

```bash
ssh-keygen -t ed25519 -f <your-key>                       # only if you need one
pulumi config set ssh_public_key "$(cat <your-key>.pub)"
pulumi config set allowed_ssh_cidr "$(curl -s https://checkip.amazonaws.com)/32"
```

Then bring it up (`metabase_tag` is optional, defaults to `v0.46.6`):

```bash
pulumi up
```

## Connect

SSH works only from `allowed_ssh_cidr`; pass the private key that matches
`ssh_public_key`:

```bash
ssh -i <your-private-key> ec2-user@$(pulumi stack output public_ip)
```

SSM Session Manager needs no inbound rule (requires `session-manager-plugin`):

```bash
aws ssm start-session --target $(pulumi stack output instance_id) --region ap-northeast-1
```

## Verify

From outside, against the public IP (finds nothing, port 3000 is firewalled):

```bash
nuclei -u http://$(pulumi stack output public_ip):3000 -tags metabase
```

From the box itself (fires the Metabase detection templates and CVE-2023-38646):

```bash
sudo docker run --rm --network host projectdiscovery/nuclei:latest \
  -u http://localhost:3000 -tags metabase
```

Do not complete the Metabase setup wizard. Finishing setup clears the
`setup-token` and CVE-2023-38646 stops firing.

## Teardown

```bash
pulumi destroy
```
