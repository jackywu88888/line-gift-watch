# -*- coding: utf-8 -*-
"""
LINE 禮物優惠券／1元新客活動監控（GitHub Actions 可用）

1) coupons.txt → giftshop-tw collection/coupon/{id}（可查剩餘張數）
2) slugs.txt → landpress 活動頁（附活動／可顯示商品連結與庫存）
3) giftshop-tw /home → 掃描首頁上的新客／1元商品與券連結
4) 推播 Discord Webhook 與／或 LINE Messaging API
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from html import unescape
from pathlib import Path
from typing import Any, Optional

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
COUPON_FILE = ROOT / "coupons.txt"
SLUG_FILE = ROOT / "slugs.txt"
STATE_FILE = ROOT / "coupon-state.json"
REPORT_FILE = ROOT / "latest-coupons.txt"
LANDPRESS = "https://gift-shop.landpress.line.me"
HOME_URL = "https://giftshop-tw.line.me/home"
UA = "Mozilla/5.0 LineGiftCouponWatch/1.0 (+GitHubActions)"
CAMPAIGN_KEYWORDS = re.compile(r"新朋友|新客|1元|1點|心意禮|體驗品|請客禮")


@dataclass
class CouponInfo:
    collection_id: str
    url: str
    title: str
    status: str
    discount_text: str
    total: Optional[int]
    claimed: Optional[int]
    remaining: Optional[int]
    issue_start: str
    issue_end: str
    valid_start: str
    valid_end: str
    ok: bool
    error: str = ""


@dataclass
class ProductInfo:
    product_id: str
    url: str
    name: str
    sale_status: str
    stock: Optional[int]
    price: Optional[float]
    discounted_price: Optional[float]
    sale_start: str
    sale_end: str
    buyable: bool
    ok: bool
    error: str = ""


@dataclass
class CampaignInfo:
    url: str
    slug: str
    month: str
    title: str
    period: str
    product_urls: list[str]
    products: list[ProductInfo]
    status: str  # LIVE / DOWN / OTHER
    ok: bool
    error: str = ""


def now_tw() -> datetime:
    return datetime.now(TZ)


def safe_print(*args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("flush", True)
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        print(text.encode("utf-8", errors="replace").decode("ascii", errors="replace"), **kwargs)


def clean_text(s: str) -> str:
    return (s or "").replace("\ufeff", "").strip()


def fmt_ts(ms: Any) -> str:
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y/%m/%d %H:%M")


def http_get(url: str, timeout: int = 25) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return int(e.code), body


def http_post_json(url: str, payload: dict, headers: Optional[dict] = None) -> None:
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=25) as resp:
        resp.read()


def deref(data: list, idx: Any, seen: Optional[set] = None) -> Any:
    if seen is None:
        seen = set()
    if not isinstance(idx, int):
        return idx
    if idx in seen or not (0 <= idx < len(data)):
        return idx
    seen = seen | {idx}
    v = data[idx]
    if isinstance(v, list) and len(v) >= 2 and isinstance(v[0], str) and v[0] in (
        "Reactive", "ShallowReactive", "Ref", "EmptyRef", "Set", "Map"
    ):
        return deref(data, v[1], seen)
    if isinstance(v, dict):
        return {k: deref(data, val, seen) for k, val in v.items()}
    if isinstance(v, list):
        return [deref(data, i, seen) if isinstance(i, int) else i for i in v]
    return v


def parse_coupon_html(collection_id: str, html: str) -> CouponInfo:
    url = f"https://giftshop-tw.line.me/collection/coupon/{collection_id}"
    m = re.search(r'id="__NUXT_DATA__">(.*?)</script>', html, re.S)
    if not m:
        return CouponInfo(
            collection_id=collection_id, url=url, title="", status="UNKNOWN",
            discount_text="", total=None, claimed=None, remaining=None,
            issue_start="", issue_end="", valid_start="", valid_end="",
            ok=False, error="找不到 __NUXT_DATA__",
        )

    data = json.loads(m.group(1))
    root = deref(data, 1)
    dmap = (root or {}).get("data") or {}
    payload = None
    for k, v in dmap.items():
        if str(collection_id) in str(k) and "coupon" in str(k).lower():
            payload = v
            break
    if payload is None and isinstance(dmap, dict) and dmap:
        payload = next(iter(dmap.values()))

    result = (payload or {}).get("result") or {}
    wrap = result.get("couponCollectionCouponPolicy") or {}
    pol = wrap.get("policy") or {}
    cc = result.get("couponCollection") or {}

    title = clean_text(pol.get("title") or cc.get("title") or "")
    status = str(pol.get("status") or "UNKNOWN")
    claimed = pol.get("issuedCount")
    if not isinstance(claimed, int):
        claimed = None

    issue_lim = pol.get("issueLimitation") or {}
    total = None
    if isinstance(issue_lim, dict):
        for key in ("maxIssueCount", "maxCount", "totalCount", "quantity", "limit", "count"):
            if isinstance(issue_lim.get(key), int):
                total = issue_lim[key]
                break

    remaining = None
    if isinstance(total, int) and isinstance(claimed, int):
        remaining = max(0, total - claimed)

    benefit = pol.get("benefit") or {}
    use_lim = pol.get("useLimitation") or {}
    discount_text = ""
    if benefit.get("discountType") == "FIXED" and benefit.get("discountAmount") is not None:
        amt = benefit.get("discountAmount")
        min_amt = use_lim.get("minimumOrderSaleAmount")
        if isinstance(min_amt, (int, float)):
            discount_text = f"滿{int(min_amt)}元折{int(amt)}元"
        else:
            discount_text = f"折{int(amt)}元"
    elif benefit.get("discountRate") is not None:
        discount_text = f"折扣比率 {benefit.get('discountRate')}"

    ip = pol.get("issuePeriod") or {}
    vp = pol.get("validPeriod") or {}

    return CouponInfo(
        collection_id=collection_id,
        url=url,
        title=title or f"coupon/{collection_id}",
        status=status,
        discount_text=discount_text,
        total=total,
        claimed=claimed,
        remaining=remaining,
        issue_start=fmt_ts(ip.get("startTimestamp")),
        issue_end=fmt_ts(ip.get("endTimestamp")),
        valid_start=fmt_ts(vp.get("startTimestamp")),
        valid_end=fmt_ts(vp.get("endTimestamp")),
        ok=True,
    )


def fetch_coupon(collection_id: str) -> CouponInfo:
    url = f"https://giftshop-tw.line.me/collection/coupon/{collection_id}"
    try:
        code, html = http_get(url)
        if code != 200:
            return CouponInfo(
                collection_id=collection_id, url=url, title="", status=f"HTTP_{code}",
                discount_text="", total=None, claimed=None, remaining=None,
                issue_start="", issue_end="", valid_start="", valid_end="",
                ok=False, error=f"HTTP {code}",
            )
        return parse_coupon_html(collection_id, html)
    except Exception as e:
        return CouponInfo(
            collection_id=collection_id, url=url, title="", status="ERROR",
            discount_text="", total=None, claimed=None, remaining=None,
            issue_start="", issue_end="", valid_start="", valid_end="",
            ok=False, error=str(e),
        )


def load_slugs() -> list[str]:
    defaults = [
        "family_icecream", "7-11_coffee", "7-11_breakfast", "7-11_1dollarcafe",
        "wootea_drinks", "KFC_Eggtart", "1point", "1dollar",
    ]
    slugs: list[str] = []
    if SLUG_FILE.exists():
        raw = SLUG_FILE.read_text(encoding="utf-8-sig")
        for line in raw.splitlines():
            line = clean_text(line)
            if line and not line.startswith("#"):
                slugs.append(line)
    return slugs or defaults


def load_coupon_ids() -> list[str]:
    ids: list[str] = []
    if COUPON_FILE.exists():
        for line in COUPON_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = clean_text(line)
            if not line or line.startswith("#"):
                continue
            m = re.search(r"/coupon/(\d+)", line)
            ids.append(m.group(1) if m else line)
    extra = os.environ.get("COUPON_IDS", "").strip()
    if extra:
        for part in re.split(r"[\s,;]+", extra):
            if part:
                ids.append(part)
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def watch_months() -> list[str]:
    now = now_tw()
    nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    return [now.strftime("%Y%m"), nxt.strftime("%Y%m")]


def extract_product_ids(html: str) -> list[str]:
    """From landpress HTML: giftshop products + LIFF voucher links."""
    ids: list[str] = []

    def add(pid: str) -> None:
        if pid and pid.isdigit() and pid not in ids:
            ids.append(pid)

    for mid in re.finditer(
        r"https?://(?:giftshop-tw\.line\.me|liff\.line\.me/[^/\s\"'\\]+)/products/(\d+)",
        html,
    ):
        add(mid.group(1))
    for mid in re.finditer(r"/products/(\d+)", html):
        add(mid.group(1))
    for mid in re.finditer(r"https?://liff\.line\.me/[^/\s\"'\\]+/voucher/(\d+)", html):
        add(mid.group(1))
    for mid in re.finditer(r"voucher/(\d{6,})", html):
        add(mid.group(1))
    return ids


def extract_campaign_meta(html: str) -> tuple[str, str, list[str]]:
    title = ""
    m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if m:
        title = clean_text(unescape(m.group(1)))
    elif re.search(r"<title>([^<]+)</title>", html):
        title = clean_text(unescape(re.search(r"<title>([^<]+)</title>", html).group(1)))

    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(re.sub(r"\s+", " ", text)).strip()

    period = ""
    pm = re.search(
        r"(\d{4}年\d{1,2}月\d{1,2}日.{0,12}00:00\s*[-~～]\s*\d{4}年\d{1,2}月\d{1,2}日.{0,12}23:59)",
        text,
    )
    if pm:
        period = pm.group(1).strip()

    product_ids = extract_product_ids(html)
    product_urls = [f"https://giftshop-tw.line.me/products/{pid}" for pid in product_ids]
    return title, period, product_urls


def fetch_product(product_id: str) -> ProductInfo:
    """giftshop-tw product page — use saleStatusType (SALE vs CLOSE), not UI「無法購買」."""
    url = f"https://giftshop-tw.line.me/products/{product_id}"
    try:
        code, html = http_get(url)
        if code != 200:
            return ProductInfo(
                product_id=product_id, url=url, name="", sale_status=f"HTTP_{code}",
                stock=None, price=None, discounted_price=None, sale_start="", sale_end="",
                buyable=False, ok=False, error=f"HTTP {code}",
            )
        m = re.search(r'id="__NUXT_DATA__">(.*?)</script>', html, re.S)
        if not m:
            return ProductInfo(
                product_id=product_id, url=url, name="", sale_status="UNKNOWN",
                stock=None, price=None, discounted_price=None, sale_start="", sale_end="",
                buyable=False, ok=False, error="no nuxt",
            )
        data = json.loads(m.group(1))
        root = deref(data, 1)
        dmap = (root or {}).get("data") or {}
        payload = None
        for k, v in dmap.items():
            if str(product_id) in str(k):
                payload = v
                break
        if payload is None and dmap:
            payload = next(iter(dmap.values()))

        pe = payload
        if isinstance(payload, dict) and "productEnd" in payload:
            pe = payload.get("productEnd")
        dp = ((pe or {}).get("result") or {}).get("detailProduct") or {}
        status = str(dp.get("saleStatusType") or "")
        stock = dp.get("stockQuantity")
        if not isinstance(stock, int):
            stock = None
        name = clean_text(str(dp.get("name") or ""))
        price = dp.get("price")
        disc = dp.get("discountedPrice")
        start = fmt_ts(dp.get("saleStartTimestamp"))
        end = fmt_ts(dp.get("saleEndTimestamp"))

        now = now_tw()
        in_window = True
        st = dp.get("saleStartTimestamp")
        en = dp.get("saleEndTimestamp")
        if isinstance(st, (int, float)) and now < datetime.fromtimestamp(st / 1000, TZ):
            in_window = False
        if isinstance(en, (int, float)) and now > datetime.fromtimestamp(en / 1000, TZ):
            in_window = False

        buyable = (
            status.upper() == "SALE"
            and in_window
            and (stock is None or stock > 0)
        )
        return ProductInfo(
            product_id=product_id, url=url, name=name or f"products/{product_id}",
            sale_status=status or "UNKNOWN", stock=stock, price=price if isinstance(price, (int, float)) else None,
            discounted_price=disc if isinstance(disc, (int, float)) else None,
            sale_start=start, sale_end=end, buyable=buyable, ok=True,
        )
    except Exception as e:
        return ProductInfo(
            product_id=product_id, url=url, name="", sale_status="ERROR",
            stock=None, price=None, discounted_price=None, sale_start="", sale_end="",
            buyable=False, ok=False, error=str(e),
        )


def product_should_display(p: ProductInfo) -> bool:
    """活動期限內：SALE／售完(OUTOFSTOCK／庫存0)都顯示；CLOSE 等舊檔略過。"""
    if not p.ok:
        return False
    st = (p.sale_status or "").upper()
    if st in ("CLOSE", "CLOSED", "END", "ENDED", "EXPIRED"):
        return False
    if st in ("SALE", "OUTOFSTOCK"):
        return True
    if isinstance(p.stock, int) and p.stock == 0:
        return True
    return False


def fetch_campaign(month: str, slug: str) -> CampaignInfo:
    url = f"{LANDPRESS}/{month}_{slug}/"
    try:
        code, html = http_get(url)
        if code != 200:
            return CampaignInfo(
                url=url, slug=slug, month=month, title="", period="",
                product_urls=[], products=[], status="DOWN", ok=True, error=f"HTTP {code}",
            )
        title, period, product_urls = extract_campaign_meta(html)
        products: list[ProductInfo] = []
        for purl in product_urls:
            pid = purl.rstrip("/").split("/")[-1]
            pinfo = fetch_product(pid)
            products.append(pinfo)
            if pinfo.buyable:
                tag = "BUYABLE"
            elif product_should_display(pinfo):
                tag = f"SHOW/{pinfo.sale_status}"
            else:
                tag = f"skip/{pinfo.sale_status}"
            safe_print(
                f"  [product] {tag} stock={pinfo.stock} {pinfo.name} {pinfo.url}"
            )
            time.sleep(0.3)

        # 活動期限內：可買與售完(庫存0)都列入推播
        shown_urls = [p.url for p in products if product_should_display(p)]
        blob = f"{title} {period}"
        if not CAMPAIGN_KEYWORDS.search(blob):
            plain = re.sub(r"<[^>]+>", " ", html)[:4000]
            blob = f"{blob} {plain}"
        if CAMPAIGN_KEYWORDS.search(blob):
            status = "LIVE"
        else:
            status = "OTHER"
        return CampaignInfo(
            url=url, slug=slug, month=month, title=title or f"{month}_{slug}",
            period=period,
            product_urls=shown_urls,
            products=products,
            status=status,
            ok=True,
        )
    except Exception as e:
        return CampaignInfo(
            url=url, slug=slug, month=month, title="", period="",
            product_urls=[], products=[], status="ERROR", ok=False, error=str(e),
        )


def fetch_live_campaigns(delay: float = 1.0) -> list[CampaignInfo]:
    months = watch_months()
    slugs = load_slugs()
    safe_print(f"campaign months={months} slugs={len(slugs)}")
    results: list[CampaignInfo] = []
    for ym in months:
        for slug in slugs:
            info = fetch_campaign(ym, slug)
            safe_print(f"[campaign] {info.status} {info.url} {info.title}")
            results.append(info)
            if delay > 0:
                time.sleep(delay)
    return results


def scan_home(delay: float = 0.25) -> tuple[list[ProductInfo], list[str], list[str]]:
    """
    掃描 giftshop-tw /home：
    - 回傳符合新客／1元關鍵字且應顯示的商品
    - 發現的優惠券 collection id
    - 發現的 landpress 活動路徑（若有）
    """
    safe_print(f"[home] fetch {HOME_URL}")
    code, html = http_get(HOME_URL)
    if code != 200:
        safe_print(f"[home] HTTP {code}")
        return [], [], []

    product_ids = sorted(set(re.findall(r"/products/(\d+)", html)))
    coupon_ids = sorted(set(
        re.findall(r"/collection/coupon/(\d+)", html)
        + re.findall(r"(?<![/\w])coupon/(\d+)", html)
        + re.findall(r"/coupon/(\d+)", html)
    ))
    landpress_paths = sorted(set(re.findall(
        r"https://gift-shop\.landpress\.line\.me/([0-9]{6}_[A-Za-z0-9_\-]+)/?",
        html,
    )))
    # also bare path fragments
    landpress_paths += sorted(set(re.findall(r"/([0-9]{6}_[A-Za-z0-9_\-]+)/", html)))
    landpress_paths = sorted(set(landpress_paths))

    safe_print(
        f"[home] products={len(product_ids)} coupons={coupon_ids} landpress={landpress_paths}"
    )

    matched_products: list[ProductInfo] = []
    for pid in product_ids:
        p = fetch_product(pid)
        name = p.name or ""
        if not CAMPAIGN_KEYWORDS.search(name):
            safe_print(f"  [home-product] skip-keyword {pid} {name}")
            time.sleep(delay)
            continue
        if not product_should_display(p):
            safe_print(f"  [home-product] skip/{p.sale_status} {pid} {name}")
            time.sleep(delay)
            continue
        tag = "BUYABLE" if p.buyable else f"SHOW/{p.sale_status}"
        safe_print(f"  [home-product] {tag} stock={p.stock} {name} {p.url}")
        matched_products.append(p)
        time.sleep(delay)

    return matched_products, coupon_ids, landpress_paths


def maybe_append_coupon_ids(new_ids: list[str]) -> None:
    if not new_ids:
        return
    existing = set(load_coupon_ids())
    for cid in new_ids:
        if cid in existing:
            continue
        with COUPON_FILE.open("a", encoding="utf-8") as f:
            f.write(f"\n# home-discovered {now_tw().isoformat()}\n{cid}\n")
        safe_print(f"[home] append coupon id {cid}")
        existing.add(cid)


def maybe_append_slugs_from_landpress(paths: list[str]) -> None:
    """從 home 發現的 202609_xxx 路徑抽出 slug 寫入 slugs.txt。"""
    if not paths:
        return
    known = set(load_slugs())
    for path in paths:
        m = re.match(r"(\d{6})_(.+)$", path.strip("/"))
        if not m:
            continue
        slug = m.group(2)
        if slug in known:
            continue
        with SLUG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"\n# home-discovered {now_tw().isoformat()}\n{slug}\n")
        safe_print(f"[home] append slug {slug}")
        known.add(slug)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def probe_newer_ids(base_ids: list[str], ahead: int = 15) -> list[str]:
    if ahead <= 0:
        return []
    nums = [int(x) for x in base_ids if x.isdigit()]
    if not nums:
        return []
    start = max(nums) + 1
    found: list[str] = []
    for cid in range(start, start + ahead):
        info = fetch_coupon(str(cid))
        if not info.ok:
            continue
        title = info.title or ""
        if any(k in title for k in ("新客", "優惠券", "1元", "心意禮")):
            found.append(str(cid))
            print(f"[probe] hit {cid} {info.status} {title}", flush=True)
        elif info.status in ("ACTIVE", "READY") and info.total:
            found.append(str(cid))
            print(f"[probe] active {cid} {title}", flush=True)
    return found


def parse_tw_dt(text: str) -> Optional[datetime]:
    """Parse 'YYYY/MM/DD HH:MM' used in coupon fields."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def is_coupon_current(info: CouponInfo, now: Optional[datetime] = None) -> bool:
    """Only push currently claimable coupons — never EXPIRED / past end time."""
    if not info.ok:
        return False
    status = (info.status or "").upper()
    if status in ("EXPIRED", "INACTIVE", "DISABLED", "ENDED"):
        return False
    now = now or now_tw()
    for end in (info.issue_end, info.valid_end):
        dt = parse_tw_dt(end)
        if dt and now > dt:
            return False
    return status in ("ACTIVE", "READY", "ISSUING", "DOWNLOADABLE")


def campaign_period_ended(period: str, now: Optional[datetime] = None) -> bool:
    """Detect ended landpress periods like … - 2026年09月03日…23:59."""
    end = parse_campaign_period_end(period)
    if not end:
        return False
    now = now or now_tw()
    return now > end


def parse_campaign_period_bounds(period: str) -> tuple[Optional[datetime], Optional[datetime]]:
    if not period:
        return None, None
    parts = re.split(r"\s*[-~～]\s*", period)
    start = None
    end = None
    if parts:
        m0 = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", parts[0])
        if m0:
            start = datetime(int(m0.group(1)), int(m0.group(2)), int(m0.group(3)), 0, 0, 0, tzinfo=TZ)
        end_part = parts[-1]
        m1 = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", end_part)
        if m1:
            end = datetime(int(m1.group(1)), int(m1.group(2)), int(m1.group(3)), 23, 59, 59, tzinfo=TZ)
    return start, end


def parse_campaign_period_end(period: str) -> Optional[datetime]:
    _, end = parse_campaign_period_bounds(period)
    return end


def is_campaign_current(info: CampaignInfo, now: Optional[datetime] = None) -> bool:
    """LIVE and (no period OR today within period). Hide preview pages that start later."""
    if not info.ok or info.status != "LIVE":
        return False
    now = now or now_tw()
    start, end = parse_campaign_period_bounds(info.period)
    if start and now < start:
        return False
    if end and now > end:
        return False
    return True


def format_message(
    coupons: list[CouponInfo],
    campaigns: list[CampaignInfo],
    updated_at: str,
    home_products: Optional[list[ProductInfo]] = None,
) -> str:
    lines = ["🎁 LINE禮物追蹤", f"數據更新於：{updated_at}", ""]

    # --- coupons: 只推進行中，過期（如 9/1~9/3）一律不推 ---
    active = [i for i in coupons if is_coupon_current(i)]
    expired_skipped = [
        i for i in coupons
        if i.ok and i not in active and (i.status or "").upper() == "EXPIRED"
    ]
    for i in expired_skipped:
        safe_print(f"[skip-expired] {i.collection_id} {i.title} {i.issue_start}~{i.issue_end}")

    lines.append("=== 優惠券（可查剩餘） ===")
    if active:
        for info in active:
            rem = "—" if info.remaining is None else str(info.remaining)
            total = "—" if info.total is None else str(info.total)
            claimed = "—" if info.claimed is None else str(info.claimed)
            lines.append(f"🎁 {info.title}")
            if info.discount_text:
                lines.append(f"💰 優惠：{info.discount_text}")
            lines.append(f"發行/已領/剩餘：{total} / {claimed} / {rem}")
            lines.append(f"⏳ 領取：{info.issue_start or '—'} ~ {info.issue_end or '—'}")
            lines.append(f"使用：{info.valid_start or '—'} ~ {info.valid_end or '—'}")
            lines.append(f"連結：{info.url}")
            lines.append("")
    else:
        lines.append("目前沒有進行中的優惠券。")
        lines.append("")

    # --- 1元 landpress：只推「今天落在活動期間內」的頁（未開始／已結束不推）---
    live = [c for c in campaigns if is_campaign_current(c)]
    for c in campaigns:
        if c.ok and c.status == "LIVE" and c not in live:
            safe_print(f"[skip-campaign] {c.url} period={c.period}")
    lines.append("=== 1元／新客活動頁 ===")
    if live:
        for c in live:
            lines.append(f"🎁 {c.title}")
            if c.period:
                lines.append(f"⏳ 期間：{c.period}")
            lines.append(f"活動連結：{c.url}")
            shown = [p for p in (c.products or []) if product_should_display(p)]
            if shown:
                lines.append("商品連結：")
                for p in shown[:5]:
                    lines.extend(_format_product_lines(p))
            lines.append("")
    else:
        lines.append("目前沒有偵測到已上線的 1 元／新客活動頁。")
        lines.append("")

    # --- home 發現：排除已在活動頁出現過的商品 ---
    home_products = home_products or []
    seen_ids = {
        p.product_id
        for c in live
        for p in (c.products or [])
        if product_should_display(p)
    }
    home_extra = [
        p for p in home_products
        if product_should_display(p) and p.product_id not in seen_ids
    ]
    lines.append("=== 首頁發現（新客／1元） ===")
    if home_extra:
        for p in home_extra[:10]:
            lines.extend(_format_product_lines(p))
            lines.append("")
    else:
        lines.append("首頁目前沒有額外的新客／1元商品（或已出現在上方活動）。")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _format_product_lines(p: ProductInfo) -> list[str]:
    stock = "—" if p.stock is None else str(p.stock)
    price = ""
    if p.discounted_price is not None:
        price = f"${int(p.discounted_price)}"
        if p.price is not None and p.price != p.discounted_price:
            price += f"（原價${int(p.price)}）"
    bit = f"  {p.name}"
    if price:
        bit += f" {price}"
    bit += f" 剩餘庫存 {stock}"
    if (p.sale_status or "").upper() == "OUTOFSTOCK" or p.stock == 0:
        bit += "（已售完）"
    return [bit, f"  {p.url}"]


def notify_discord(text: str) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("[skip] 未設定 DISCORD_WEBHOOK_URL", flush=True)
        return False
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or [text]
    for chunk in chunks:
        http_post_json(url, {"content": chunk})
    print("[ok] Discord 已推播", flush=True)
    return True


def notify_line(text: str) -> bool:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_USER_ID", "").strip()
    if not token or not user_id:
        print("[skip] 未設定 LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID", flush=True)
        return False
    body = text[:4900]
    http_post_json(
        "https://api.line.me/v2/bot/message/push",
        {"to": user_id, "messages": [{"type": "text", "text": body}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    print("[ok] LINE 已推播", flush=True)
    return True


def main() -> int:
    probe_ahead = int(os.environ.get("PROBE_AHEAD", "10"))
    always_notify = os.environ.get("ALWAYS_NOTIFY", "1") == "1"
    campaign_delay = float(os.environ.get("CAMPAIGN_DELAY", "1"))
    home_delay = float(os.environ.get("HOME_DELAY", "0.25"))
    scan_home_enabled = os.environ.get("SCAN_HOME", "1") == "1"

    ids = load_coupon_ids()
    print(f"configured ids: {ids}", flush=True)

    home_products: list[ProductInfo] = []
    if scan_home_enabled:
        home_products, home_coupon_ids, home_landpress = scan_home(delay=home_delay)
        maybe_append_coupon_ids(home_coupon_ids)
        maybe_append_slugs_from_landpress(home_landpress)
        for cid in home_coupon_ids:
            if cid not in ids:
                ids.append(cid)

    probed = probe_newer_ids(ids, ahead=probe_ahead)
    for p in probed:
        if p not in ids:
            ids.append(p)
            with COUPON_FILE.open("a", encoding="utf-8") as f:
                f.write(f"\n# auto-discovered {now_tw().isoformat()}\n{p}\n")

    coupons = [fetch_coupon(i) for i in ids]
    for info in coupons:
        print(
            f"[coupon] {info.collection_id} ok={info.ok} status={info.status} "
            f"remaining={info.remaining} title={info.title}",
            flush=True,
        )

    campaigns = fetch_live_campaigns(delay=campaign_delay)

    updated_at = now_tw().strftime("%Y年%m月%d日 %H:%M")
    message = format_message(coupons, campaigns, updated_at, home_products=home_products)
    print("--- message ---", flush=True)
    print(message, flush=True)

    state = load_state()
    snapshot = {
        "coupons": {
            i.collection_id: {
                "status": i.status,
                "remaining": i.remaining,
                "claimed": i.claimed,
                "title": i.title,
            }
            for i in coupons if i.ok
        },
        "campaigns": {
            c.url: {
                "status": c.status,
                "title": c.title,
                "products": [
                    {
                        "id": p.product_id,
                        "buyable": p.buyable,
                        "saleStatus": p.sale_status,
                        "stock": p.stock,
                        "name": p.name,
                        "url": p.url,
                    }
                    for p in (c.products or [])
                ],
            }
            for c in campaigns if c.ok and c.status == "LIVE"
        },
        "homeProducts": [
            {
                "id": p.product_id,
                "buyable": p.buyable,
                "saleStatus": p.sale_status,
                "stock": p.stock,
                "name": p.name,
                "url": p.url,
            }
            for p in home_products
        ],
    }
    changed = snapshot != state.get("last")
    state["last"] = snapshot
    state["updatedAt"] = now_tw().isoformat()
    save_state(state)
    REPORT_FILE.write_text(message, encoding="utf-8")

    should_notify = always_notify or changed
    if should_notify:
        sent = False
        try:
            sent = notify_discord(message) or sent
        except Exception as e:
            print(f"[err] Discord: {e}", flush=True)
        try:
            sent = notify_line(message) or sent
        except Exception as e:
            print(f"[err] LINE: {e}", flush=True)
        if not sent:
            print("[warn] 沒有成功推播（請設定 Discord 或 LINE secrets）", flush=True)
    else:
        print("[skip] 無變化且 ALWAYS_NOTIFY=0", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
