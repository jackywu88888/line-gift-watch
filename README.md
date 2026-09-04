# LINE Gift Watch — GitHub Actions 每小時監控（台北）

台北時間自動檢查 LINE 禮物公開活動，並推播到 Discord（或 LINE）。

## 會抓什麼

| 類型 | 來源 | 剩餘數量 | 推播內容 |
|------|------|----------|----------|
| 優惠券 | `giftshop-tw.line.me/collection/coupon/{id}` | ✅ 發行／已領／剩餘 | 名稱、滿額折抵、張數、領取／使用期限、連結 |
| 1 元／新客活動頁 | `gift-shop.landpress.line.me/{YYYYMM}_{slug}/` | 活動頁本身通常無庫存；**商品頁**可查 `stockQuantity` | 標題、期間、活動連結；並附 **可購買** 商品連結（名稱／價格／剩餘庫存） |

### 推播過濾（重要）

- **優惠券**：`EXPIRED` 或領取／使用期限已過 → **不推**（例如已結束的 9/1～9/3 $90 券）
- **1 元活動頁**：只推「**今天落在活動期間內**」的頁  
  - 期間尚未開始（例如 9/18 才開始的早餐頁）→ **不推**  
  - 期間已結束 → **不推**  
  - 頁面已公開但解析不到期間（例如部分 1點活動）→ 仍可能推播
- **商品連結**：從活動頁抽出 `products/{id}` 或 LIFF `voucher/{id}`，再到 `giftshop-tw.line.me/products/{id}` 查 `saleStatusType`  
  - `SALE` 且在販售期間、庫存 > 0 → 推播商品連結＋剩餘庫存  
  - `CLOSE` 等舊體驗品（常仍看得到頁面、畫面上「無法購買」）→ **不推**
- 推播文案**不顯示**：狀態（如 `ISSUING`）、「剩餘數量：無法查詢」、無法解析商品時的提示句

### 追蹤清單

- `coupons.txt`：優惠券 collection ID（一行一個；可留已過期 ID 當探測基準，推播仍會略過）
- `slugs.txt`：活動路徑片段，會自動組「這個月 + 下個月」網址，目前包含例如：
  - `7-11_1dollarcafe` → `…/202609_7-11_1dollarcafe/`（9 月初 $1 冰美式）
  - `7-11_breakfast`、`7-11_coffee`、`family_icecream`、`wootea_drinks`、`KFC_Eggtart`、`1point`、`1dollar`
- 腳本也會從最大券 ID 往後探測 `PROBE_AHEAD` 個新 ID，命中新客／優惠券關鍵字會寫回 `coupons.txt`

## 排程

`.github/workflows/watch.yml`

- 台北時間：**00:05**，以及 **01:00～23:00** 每整點（約每小時一次）
- cron（UTC）：`5 16 * * *` ＋ `0 17-23,0-15 * * *`
- 也可在 Actions 手動 **Run workflow**
- 跑完會更新並 commit：`coupon-state.json`、`latest-coupons.txt`（以及探測到的 `coupons.txt`）

Repo：https://github.com/jackywu88888/line-gift-watch

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
- **舊的 `[新客限定1元體驗品]` 商品網址**（如 `products/322419346`）多半 `saleStatusType=CLOSE`，頁面還在但已結束；腳本會略過，只推 `SALE` 商品。
- 未知的新 slug 不會自動發明路徑，需手動加入 `slugs.txt`。
- GitHub 免費帳號的 `schedule` 可能有數分鐘延遲，屬正常現象。
- 推送 `.github/workflows/*.yml` 需要 `gh` token 含 `workflow` scope。
