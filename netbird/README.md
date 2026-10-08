# netbird

## 網域

| 網域 | 指到 | 用途 |
|---|---|---|
| `netbird.5tmate…` | 回源 443 的 CloudFront | 登入、API、management、signal、relay |
| `admin-netbird.5tmate…` | 回源 80 的 CloudFront | dashboard |
| `stun-netbird.5tmate…` | EIP | STUN，3478/udp |
| `origin-netbird.5tmate…` | EIP | CloudFront 回源用，Let's Encrypt 簽這個 |

## 結構

`__main__.py` 是組裝層，模組只定義 class 跟純函式。

```
__main__.py            組裝層
policies.py            IAM policy 文件的純函式
Pulumi.prod.yaml       region、zone_name、secret、default tags
vpc/                   Network
s3/                    PrivateBucket、NetBirdConfig、config.yaml 樣板
ec2/                   SpotHost、render_user_data、user_data.sh
failover/              Failover、lambda/handler.py
ecs/                   NetBirdService、containers.py
cloudfront/            Cdn
experiments/           切換實驗的腳本
```
