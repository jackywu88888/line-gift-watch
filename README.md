# LINE Gift Watch — GitHub Actions 每日 00:05（台北）監控

## 會抓什麼

| 類型 | 來源 | 剩餘數量 | 推播內容 |
|------|------|----------|----------|
| 優惠券 | `giftshop-tw.line.me/collection/coupon/{id}` | ✅ 可查 | 名稱、滿額折抵、發行/已領/剩餘、期限、連結 |
| 1 元新客活動 | `gift-shop.landpress.line.me/{YYYYMM}_{slug}/` | ❌ 通常無法查 | **一定附活動連結**；若頁面有商品 ID 再附商品連結 |

## 排程

`.github/workflows/watch.yml`：每天 **台北 00:05**（UTC `16:05`）+ 可手動 Run workflow。

## 設定步驟

1. 把本資料夾推到 GitHub（新 repo 或既有 repo）
2. Repo → **Settings → Secrets and variables → Actions** 新增：

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

3. 編輯追蹤清單：
   - `coupons.txt` — 優惠券 collection ID
   - `slugs.txt` — 1 元活動路徑（會自動組這個月+下個月）

4. Actions → **LINE Gift Coupon Watch** → **Run workflow** 測一次

## 本地測試

```powershell
cd C:\Users\User\Projects\line-gift-watch
$env:PROBE_AHEAD = "0"   # 本機可先關閉探測加快速度
$env:ALWAYS_NOTIFY = "0"  # 不推播，只產 latest-coupons.txt
python watch_coupons.py
notepad latest-coupons.txt
```

## Discord Webhook

頻道設定 → 整合 → Webhook → 複製 URL 貼到 `DISCORD_WEBHOOK_URL`。

## LINE Messaging API

1. [LINE Developers](https://developers.line.biz/) 建立 Messaging API channel  
2. 發 Channel access token → `LINE_CHANNEL_ACCESS_TOKEN`  
3. 用手機加 Bot 為好友，打 webhook 或用 get profile 取得你的 userId → `LINE_USER_ID`  

（LINE Notify 已停用，改用 Messaging API push。）

## 注意

- 查詢公開頁**不需登入**；真正領券／買 1 元仍要符合新客資格。
- 1 元活動頁多半不公開剩餘庫存，推播會標明「無法查詢」並附上連結。
- GitHub 免費帳號的 `schedule` 可能有數分鐘延遲，屬正常現象。
