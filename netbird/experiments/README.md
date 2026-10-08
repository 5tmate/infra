# 切換實驗

在本機跑，模擬故障、量服務斷線多久。腳本會真的動到 `5tmate-netbird` 的主機，跑的時候服務會斷線。

## 需要

- AWS CLI，profile 要能操作 ASG、EC2、ECS、CloudWatch
- `uv`，第一次跑會在這個資料夾建 `.venv` 並裝 boto3
- `curl`

## 腳本

| 腳本 | 做什麼 | 大約多久 |
|---|---|---|
| `run-b.sh` | 把 spot 出價調成 0.001 讓 ASG 買不到，終止主機，等服務切到備援機，改回出價，再等 Lambda 切回主機 | 12 分鐘 |
| `run-d.sh` | 把主機在 ECS 設成 DRAINING，模擬 spot 收回，看舊 task 多撐的時間跟換機 | 6 分鐘 |
| `monitor.py` | 前兩個腳本會自己開。每秒打一次登入網址，每 5 秒記一次 EIP、ASG、task、備援機的狀態，新主機穩定 3 分鐘後結束 | |
| `save_events.py` | 跑完之後另外存 ECS service 事件、ASG 活動、task 跟機器的時間 | |

## 用法

```bash
AWS_PROFILE=<profile> ./run-b.sh
AWS_PROFILE=<profile> ./run-d.sh
AWS_PROFILE=<profile> .venv/bin/python save_events.py results/<日期>/<資料夾>
```

- 前面加 `DRY_RUN=1` 只檢查服務狀態，不會動到主機
- 第一個參數是資料夾名稱的前綴，預設是 `實驗B`、`實驗D`
- 結果預設存在這個資料夾的 `results/<日期>/`，用 `RESULTS_DIR` 可以改到 repo 外面

每次跑的資料夾裡有：

| 檔案 | 內容 |
|---|---|
| `actions.log` | 腳本做了什麼 |
| `events.log` | 每個狀態變化的時間 |
| `probe.csv` | 每一秒的結果 |
| `summary.txt` | 斷線幾次、各多久 |

## 注意

- 一次只跑一個。開始前會確認服務正常、EIP 在主機上，不正常就不跑
- `run-b.sh` 中途按 Ctrl-C 也會把出價改回去
- 斷線時間是從本機經過 CloudFront 量的，連續兩次失敗才算斷，連續兩次成功才算恢復，誤差約 1 到 3 秒
