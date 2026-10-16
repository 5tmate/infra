# 切換實驗

在本機跑，模擬故障、量 netbird 服務對外斷線多久。腳本會真的動到 `5tmate-netbird` 的主機，跑的時候服務會斷線。

## 需要

- AWS CLI，profile 要能操作 ASG、EC2、ECS、SSM、CloudTrail，FIS 那支還要能建 IAM role 和 FIS 範本
- `uv`，第一次跑會在這個資料夾建 `.venv` 並裝 boto3
- `curl`

## 檔案

| 檔案 | 做什麼 |
| --- | --- |
| `probe.sh` | 每秒打一次登入網址，把 `時間 回應碼` 寫進 `probe.txt`，逾時記 000，可以單獨跑 |
| `lib.sh` | 共用函式，run 腳本都 source 它。開跑前檢查、改出價、終止主機、開關探測、等狀態、收尾時還原出價、拆 FIS、跑 report |
| `report.py` | 只讀 CloudTrail 加 ECS、ASG、EC2 的 describe，不碰 CloudWatch log。算斷線、ASG 活動、ECS task、CloudTrail 動作，合成時間線，寫成 `<run 資料夾>.md`，可以重跑 |
| `run-d.sh` | spot 收回，把主機在 ECS 設成 DRAINING，看換機。約 6 分 |
| `run-b.sh` | 把出價調成 0.001 讓三個 AZ 都買不到，終止主機，切到 standby 後改回出價，等切回。約 12 分 |
| `run-e.sh` | 用 SSM 讓主機核心當機，看整台掛掉的接手。約 10 分 |
| `run-f.sh` | 跟 run-b 一樣，但出價維持一段時間才改回，用來看 ASG 換 AZ 的順序和間隔。約 12 分 |
| `run-fis.sh` | 用 FIS 讓 ASG 在三個 AZ 開機都回容量不足，驗證真的沒貨時的切換。每次約 0.5 美元，約 12 分 |

## 用法

```bash
AWS_PROFILE=jerry RESULTS_DIR=/home/jerry/T5/5tmate/實驗 ./run-b.sh
```

- 第一個參數是資料夾名稱的前綴，預設是 `實驗B`、`實驗D` 等，例如 `./run-b.sh 實驗B-延伸6`
- `RESULTS_DIR` 指到 `實驗` 資料夾，結果就會寫進 `實驗/<日期>/<前綴>-<時間戳>/`，不設的話預設寫在這個資料夾的 `results/`
- `DRY_RUN=1` 只做開跑前檢查，不會動到主機
- `run-f.sh` 可用 `HOLD_MINUTES` 調維持低價的時間，預設 8 分
- `run-fis.sh` 可用 `DURATION` 調注入時間，預設 5 分

CloudTrail 有幾分鐘延遲，腳本結束時會自動跑一次 report.py。如果那時資料還不全，晚點可以再跑一次，它會覆蓋同一份 md：

```bash
.venv/bin/python report.py /home/jerry/T5/5tmate/實驗/<日期>/<資料夾>
```

## 產出

每次跑的資料夾裡有：

| 檔案 | 內容 |
| --- | --- |
| `probe.txt` | 每秒一行 `時間 回應碼` |
| `actions.log` | 腳本做了什麼，附時間 |
| `<資料夾>.md` | report.py 整理的斷線、時間線、ASG 活動、ECS task、CloudTrail 動作 |
| `fis-*.json`、`fis-experiment-id` | 只有 run-fis 會有，FIS 範本和實驗 id |

目的、預期、結論和 `實驗/切換時間.md` 的彙整還是手寫，腳本只產生資料。

## 注意

- 一次只跑一個。開跑前 `preflight` 會確認服務正常、EIP 在主機上、standby 關機、出價是預設值，有一項不對就不跑
- 動到出價的腳本中途按 Ctrl-C 也會把出價改回去
- `run-fis.sh` 不管正常結束還是中斷，都會刪掉它建的 FIS 範本和 IAM role
- 斷線時間是從本機經過 CloudFront 量的，連續兩次失敗才算斷，連續兩次成功才算恢復，誤差約 1 到 3 秒。本機網路或 DNS 偶發變慢造成的單次逾時，report.py 會另外列出來，不算斷線

## exit-node.sh（功能測試，跟上面的切換實驗不同）

驗證 exit node：在別的區域開臨時 EC2 當 netbird exit node，從筆電選它上網，確認對外 IP 變成 EC2 的。不量斷線、不碰 `5tmate-netbird` 主機，是獨立的功能測試。需要一把 dashboard 建的 **Reusable setup key** 讓無頭 EC2 加入。

```bash
# 開兩台（ap-northeast-2、us-west-1 各一台 t4g.nano，裝 netbird 0.81.0、用 key 加入）
SETUP_KEY=<reusable key> AWS_PROFILE=jerry ./exit-node.sh up
# 清掉（終止 EC2、刪 security group）
AWS_PROFILE=jerry ./exit-node.sh down
```

- 區域、機型、netbird 版本在腳本開頭，用 `REGIONS`、`TYPE` 環境變數可以覆蓋
- EC2 加入後要在 dashboard 把那兩個 peer 設成 exit node（加 `0.0.0.0/0` route）
- 兩台一小時約 0.01 美元，測完記得 `down`，並在 dashboard 刪 peer、停用 setup key
