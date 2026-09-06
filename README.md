# LINE Gift Watch — GitHub Actions 每小時監控（台北）

台北時間自動檢查 LINE 禮物公開活動，並推播到 Discord（或 LINE）。

來源包含：`coupons.txt` 優惠券、`slugs.txt` landpress 活動頁、以及 **`giftshop-tw.line.me/home` 首頁掃描**。

## 會抓什麼

| 類型 | 來源 | 剩餘數量 | 推播內容 |
|------|------|----------|----------|
| 優惠券 | `giftshop-tw.line.me/collection/coupon/{id}` | ✅ 發行／已領／剩餘 | 名稱、滿額折抵、張數、領取／使用期限、連結 |
| 1 元／新客活動頁 | `gift-shop.landpress.line.me/{YYYYMM}_{slug}/` | 活動頁本身通常無庫存；**商品頁**可查 `stockQuantity` | 標題、期間、活動連結；商品名稱／價格／剩餘庫存（售完也顯示 0） |
| 首頁發現 | `giftshop-tw.line.me/home` | 商品可查庫存；券可查剩餘 | 標題含新客／1元等關鍵字的商品；發現的券 ID／landpress 路徑會寫回清單 |

### 推播過濾（重要）

- **優惠券**：只推標題含「新客／新朋友／1元／1點／心意禮／體驗品／請客禮」者；`EXPIRED` 或已過期 → **不推**  
  - 一般品牌滿額券（例如歐舒丹秋日煥新禮券）→ **不推**
- **1 元活動頁**：只推「**今天落在活動期間內**」的頁  
  - 期間尚未開始（例如 9/18 才開始的早餐頁）→ **不推**  
  - 期間已結束 → **不推**  
  - 頁面已公開但解析不到期間（例如部分 1點活動）→ 仍可能推播
- **商品連結**：從活動頁抽出 `products/{id}` 或 LIFF `voucher/{id}`，再到 `giftshop-tw.line.me/products/{id}` 查 `saleStatusType`  
  - `SALE` → 推播商品連結＋剩餘庫存  
  - `OUTOFSTOCK`／庫存 0（活動仍在期限內）→ **仍推播**，標示剩餘庫存 0（已售完）  
  - `CLOSE` 等舊體驗品 → **不推**
- 推播文案**不顯示**：狀態（如 `ISSUING`）、「剩餘數量：無法查詢」、無法解析商品時的提示句

### 追蹤清單

- `coupons.txt`：優惠券 collection ID（一行一個；可留已過期 ID 當探測基準，推播仍會略過）
- `slugs.txt`：活動路徑片段，會自動組「這個月 + 下個月」網址，目前包含例如：
  - `7-11_1dollarcafe` → `…/202609_7-11_1dollarcafe/`（9 月初 $1 冰美式）
  - `7-11_breakfast`、`7-11_coffee`、`family_icecream`、`wootea_drinks`、`KFC_Eggtart`、`1point`、`1dollar`
- 腳本也會從最大券 ID 往後探測 `PROBE_AHEAD` 個新 ID，命中新客／優惠券關鍵字會寫回 `coupons.txt`
- **首頁掃描**（`SCAN_HOME=1`，預設開啟）：抓 home 上的商品／券／landpress 連結；符合關鍵字的商品進「首頁發現」區塊；新券 ID、新 slug 會自動 append 到清單

## 排程

`.github/workflows/watch.yml`

**建議主力：外掛 cron 觸發**（見下方「外掛 cron」），較不易漏跑。

| 觸發 | 說明 |
|------|------|
| `repository_dispatch`（`coupon-watch`） | 外掛 cron／HTTP 呼叫 |
| `workflow_dispatch` | Actions 手動 **Run workflow**，或 cron-job.org／`gh workflow run` |

已停用 GitHub 內建 `schedule`，避免與外掛 cron 同一小時推兩次。

跑完會更新並 commit：`coupon-state.json`、`latest-coupons.txt`（以及探測到的 `coupons.txt`）。

Repo：https://github.com/jackywu88888/line-gift-watch

### 外掛 cron（cron-job.org，免費）

用外部服務準時打 GitHub API，再由 Actions 跑腳本推播。

#### 1. 建立 Fine-grained PAT

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate**
2. Repository access：只選 `jackywu88888/line-gift-watch`
3. Permissions → **Actions：Read and write**（其餘可維持 No access）
4. 產生後**複製 token**（只顯示一次；勿 commit、勿貼到公開處）

#### 2. 在 [cron-job.org](https://cron-job.org/) 新增工作

| 欄位 | 建議值 |
|------|--------|
| Title | `LINE Gift Watch hourly` |
| URL | `https://api.github.com/repos/jackywu88888/line-gift-watch/dispatches` |
| Schedule | 每小時一次（時區選 **Asia/Taipei**；分鐘建議 **5**，例如每小時 `:05`） |
| Request method | `POST` |
| Request timeout | 30s 即可 |

**Headers**（逐列新增）：

```
Authorization: Bearer 你的PAT
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

**Body**（raw JSON）：

```json
{"event_type":"coupon-watch"}
```

存檔後先按一次 **Run now**／測試執行，再到 GitHub → **Actions** 確認有出現由 `repository_dispatch` 觸發的 run，且 Discord 有推播。

#### 3. 本機快速驗證（可選）

```powershell
$env:GH_TOKEN = "你的PAT"   # 或已用 gh auth login 亦可
curl.exe -X POST "https://api.github.com/repos/jackywu88888/line-gift-watch/dispatches" `
  -H "Authorization: Bearer $env:GH_TOKEN" `
  -H "Accept: application/vnd.github+json" `
  -H "X-GitHub-Api-Version: 2022-11-28" `
  -H "Content-Type: application/json" `
  -d "{\"event_type\":\"coupon-watch\"}"
```

成功時 HTTP 回應多半是 **204 No Content**。

#### 4. 排程說明

每小時監控由 **cron-job.org**（或同等外掛）觸發；workflow **不再**使用 GitHub `schedule`，以免重複推播。

## 設定步驟

1. Repo → **Settings → Secrets and variables → Actions** 新增：

| Secret | 說明 |
|--------|------|
| `DISCORD_WEBHOOK_URL` | Discord 頻道 Webhook（擇一即可） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API channel access token |
| `LINE_USER_ID` | 你的 LINE userId（要先加 Bot 好友） |

可選 **Variables**：

| Variable | 預設 | 說明 |
|----------|------|------|
| `PROBE_AHEAD` | `15` | 從最大券 ID 往後探測幾個新 ID |
| `ALWAYS_NOTIFY` | `1` | `1`=每天都推；`0`=有變化才推 |
| `COUPON_IDS` | （空） | 額外券 ID，逗號分隔 |
| `CAMPAIGN_DELAY` | `1` | 活動頁請求間隔秒數 |
| `SCAN_HOME` | `1` | `1`=掃描 giftshop 首頁；`0`=關閉 |
| `HOME_DELAY` | `0.25` | 首頁商品逐筆查詢間隔秒數 |

2. 依需要編輯 `coupons.txt`、`slugs.txt`（新活動路徑要加進 `slugs.txt`，否則抓不到，例如曾漏的 `7-11_1dollarcafe`）
3. Actions → **LINE Gift Coupon Watch** → **Run workflow** 測一次

## 本地測試

```powershell
cd C:\Users\User\Projects\line-gift-watch
$env:PROBE_AHEAD = "0"          # 本機可先關閉探測加快速度
$env:ALWAYS_NOTIFY = "1"         # 要推 Discord 時設 1，並準備好 webhook
$env:DISCORD_WEBHOOK_URL = "…"   # 或寫在 gitignore 的 .env.local
python watch_coupons.py
notepad latest-coupons.txt
```

本機可用 `.env.local`（已在 `.gitignore`）存放 webhook，**不要**把 webhook 寫進程式碼或 commit。

## Discord Webhook

頻道設定 → 整合 → Webhook → 複製 URL → 設成 GitHub Secret `DISCORD_WEBHOOK_URL`。

## LINE Messaging API

1. [LINE Developers](https://developers.line.biz/) 建立 Messaging API channel  
2. 發 Channel access token → `LINE_CHANNEL_ACCESS_TOKEN`  
3. 加 Bot 為好友，取得 userId → `LINE_USER_ID`  

（LINE Notify 已停用，改用 Messaging API push。）

## 注意

- 查詢公開頁**不需登入**；真正領券／買 1 元仍要符合新客資格。
- 1 元活動剩餘庫存公開頁多半查不到；有期間就顯示期間與活動連結。
- **舊的 `[新客限定1元體驗品]` 商品網址**（如 `products/322419346`）多半 `saleStatusType=CLOSE`，頁面還在但已結束；腳本會略過，只推 `SALE`／活動期限內的 `OUTOFSTOCK`。
- 首頁掃描可提高發現率，但仍**無法保證**抓到所有未曝光在 home／已知 slug 的全新 landpress 路徑。
- 未知的新 slug 若 home 也沒出現，仍需手動加入 `slugs.txt`。
- 每小時監控請用外掛 cron → `workflow_dispatch`／`repository_dispatch`（已移除 GitHub 內建 `schedule`）。
- Fine-grained PAT 只需 **Actions: Read and write**；workflow 內 commit 仍用 `GITHUB_TOKEN`，不必把 Contents 寫入權限開給 PAT。
- 推送 `.github/workflows/*.yml` 需要 `gh` token 含 `workflow` scope。
