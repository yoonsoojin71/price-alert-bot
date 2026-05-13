"""
소싱처 가격 변동 알림 봇 v2
- 대상: G마켓
- 알림: 텔레그램
- 추가: 카드 할인가 / 쿠폰가 파싱 + 혜택 키워드 등장/소멸 감지
"""

import csv
import os
import re
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# 로깅 설정
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 환경변수
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PRODUCTS_CSV     = os.environ.get("PRODUCTS_CSV", "products.csv")

# ─────────────────────────────────────────────
# 감지할 혜택 키워드 목록
# ─────────────────────────────────────────────
BENEFIT_KEYWORDS = [
    "카드할인", "카드 할인",
    "쿠폰", "쿠폰할인",
    "즉시할인", "즉시 할인",
    "특가", "행사가",
    "무이자", "할부",
    "적립",
    "혜택",
    "타임딜", "타임세일",
    "오늘만",
]

# ─────────────────────────────────────────────
# 요청 헤더
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.gmarket.co.kr/",
}


# ─────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────
@dataclass
class Product:
    id: str
    name: str
    url: str
    last_price: int
    last_card_price: int = 0
    last_coupon_price: int = 0
    last_benefit_keys: str = ""


@dataclass
class PageInfo:
    price: Optional[int] = None
    card_price: Optional[int] = None
    coupon_price: Optional[int] = None
    found_keywords: list = field(default_factory=list)


# ─────────────────────────────────────────────
# CSV 입출력
# ─────────────────────────────────────────────
CSV_FIELDS = ["id", "name", "url", "last_price",
              "last_card_price", "last_coupon_price", "last_benefit_keys"]


def load_products(filepath):
    products = []
    try:
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    products.append(Product(
                        id=row["id"].strip(),
                        name=row["name"].strip(),
                        url=row["url"].strip(),
                        last_price=_to_int(row.get("last_price", "0")),
                        last_card_price=_to_int(row.get("last_card_price", "0")),
                        last_coupon_price=_to_int(row.get("last_coupon_price", "0")),
                        last_benefit_keys=row.get("last_benefit_keys", "").strip(),
                    ))
                except (KeyError, ValueError) as e:
                    logger.warning(f"행 파싱 오류 (id={row.get('id','?')}): {e}")
    except FileNotFoundError:
        logger.error(f"CSV 파일 없음: {filepath}")
    return products


def save_products(filepath, products):
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for p in products:
            writer.writerow({
                "id": p.id,
                "name": p.name,
                "url": p.url,
                "last_price": p.last_price,
                "last_card_price": p.last_card_price,
                "last_coupon_price": p.last_coupon_price,
                "last_benefit_keys": p.last_benefit_keys,
            })
    logger.info(f"CSV 저장 완료: {filepath}")


def _to_int(value):
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else 0


# ─────────────────────────────────────────────
# G마켓 페이지 파싱
# ─────────────────────────────────────────────

def fetch_page_info(url, timeout=10):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.Timeout:
        logger.error(f"타임아웃: {url}")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP 오류 {e.response.status_code}: {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"요청 오류: {e}")
        return None

    info = PageInfo()
    info.price         = _parse_main_price(soup, url)
    info.card_price    = _parse_card_price(soup)
    info.coupon_price  = _parse_coupon_price(soup)
    info.found_keywords = _detect_keywords(soup)
    return info


def _parse_main_price(soup, url):
    tag = soup.select_one("span.price__real strong")
    if tag:
        return _price_int(tag.get_text())

    tag = soup.select_one("div.box__price-sale strong")
    if tag:
        return _price_int(tag.get_text())

    tag = soup.select_one("#itemcase_basic .price strong")
    if tag:
        return _price_int(tag.get_text())

    meta = soup.find("meta", property="og:description")
    if meta:
        m = re.search(r"([\d,]+)원", meta.get("content", ""))
        if m:
            return _price_int(m.group(1))

    logger.warning(f"일반가 셀렉터 미매칭: {url}")
    return None


def _parse_card_price(soup):
    for sel in ["span.price__card strong", "div.box__price-card strong",
                "em.price_card", ".card-discount-price strong"]:
        tag = soup.select_one(sel)
        if tag:
            p = _price_int(tag.get_text())
            if p:
                return p

    text = soup.get_text(separator=" ", strip=True)
    m = re.search(r"카드[^\d]{0,15}([\d,]+)\s*원", text)
    if m:
        p = _price_int(m.group(1))
        if p:
            return p
    return None


def _parse_coupon_price(soup):
    for sel in ["span.price__coupon strong", "div.box__price-coupon strong",
                "em.price_coupon", ".coupon-discount-price strong"]:
        tag = soup.select_one(sel)
        if tag:
            p = _price_int(tag.get_text())
            if p:
                return p

    text = soup.get_text(separator=" ", strip=True)
    m = re.search(r"쿠폰[^\d]{0,15}([\d,]+)\s*원", text)
    if m:
        p = _price_int(m.group(1))
        if p:
            return p
    return None


def _detect_keywords(soup):
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()
    page_text = soup.get_text(separator=" ", strip=True)
    return [kw for kw in BENEFIT_KEYWORDS if kw in page_text]


def _price_int(text):
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


# ─────────────────────────────────────────────
# 텔레그램 알림
# ─────────────────────────────────────────────

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 토큰/채팅ID 미설정")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")
        return False


def build_price_alert(product, info):
    lines = [
        "💰 <b>가격 변동 알림</b>",
        "━━━━━━━━━━━━━━━",
        f"📦 <b>{product.name}</b>",
        f"🔗 <a href='{product.url}'>상품 링크</a>",
        "━━━━━━━━━━━━━━━",
    ]
    if info.price is not None and info.price != product.last_price:
        diff = info.price - product.last_price
        sign = "🔺" if diff > 0 else "🔻"
        lines.append(
            f"📌 일반가: <s>{product.last_price:,}원</s> → "
            f"<b>{info.price:,}원</b>  {sign}{abs(diff):,}원"
        )
    if info.card_price and info.card_price != product.last_card_price:
        prev = f"<s>{product.last_card_price:,}원</s> → " if product.last_card_price else "신규 → "
        lines.append(f"💳 카드가: {prev}<b>{info.card_price:,}원</b>")
    if info.coupon_price and info.coupon_price != product.last_coupon_price:
        prev = f"<s>{product.last_coupon_price:,}원</s> → " if product.last_coupon_price else "신규 → "
        lines.append(f"🎫 쿠폰가: {prev}<b>{info.coupon_price:,}원</b>")
    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_keyword_alert(product, appeared, disappeared):
    lines = [
        "🔔 <b>혜택 키워드 변동 알림</b>",
        "━━━━━━━━━━━━━━━",
        f"📦 <b>{product.name}</b>",
        f"🔗 <a href='{product.url}'>상품 링크</a>",
        "━━━━━━━━━━━━━━━",
    ]
    if appeared:
        keys = ", ".join(f"<b>{k}</b>" for k in appeared)
        lines.append(f"✅ 새로 등장: {keys}")
    if disappeared:
        keys = ", ".join(f"<b>{k}</b>" for k in disappeared)
        lines.append(f"❌ 사라짐: {keys}")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📲 지금 확인해보세요!")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 변동 감지 + 알림 + CSV 업데이트
# ─────────────────────────────────────────────

def process_product(product):
    info = fetch_page_info(product.url)
    if info is None:
        logger.warning("  → 페이지 로드 실패, 건너뜁니다.")
        return False

    csv_changed = False
    messages_to_send = []

    # 가격 변동 감지
    price_changed  = info.price        is not None and info.price        != product.last_price
    card_changed   = info.card_price   is not None and info.card_price   != product.last_card_price
    coupon_changed = info.coupon_price is not None and info.coupon_price != product.last_coupon_price

    if price_changed or card_changed or coupon_changed:
        messages_to_send.append(build_price_alert(product, info))
        if price_changed:
            logger.info(f"  → 일반가: {product.last_price:,} → {info.price:,}원")
            product.last_price = info.price
            csv_changed = True
        if card_changed:
            logger.info(f"  → 카드가: {product.last_card_price:,} → {info.card_price:,}원")
            product.last_card_price = info.card_price
            csv_changed = True
        if coupon_changed:
            logger.info(f"  → 쿠폰가: {product.last_coupon_price:,} → {info.coupon_price:,}원")
            product.last_coupon_price = info.coupon_price
            csv_changed = True
    else:
        logger.info("  → 가격 변동 없음")

    # 혜택 키워드 변동 감지
    prev_keys = set(product.last_benefit_keys.split(",")) - {""} if product.last_benefit_keys else set()
    curr_keys = set(info.found_keywords)
    appeared    = sorted(curr_keys - prev_keys)
    disappeared = sorted(prev_keys - curr_keys)

    if appeared or disappeared:
        logger.info(f"  → 키워드 변동 | 등장: {appeared} | 소멸: {disappeared}")
        messages_to_send.append(build_keyword_alert(product, appeared, disappeared))
        product.last_benefit_keys = ",".join(sorted(curr_keys))
        csv_changed = True
    else:
        logger.info(f"  → 키워드 변동 없음 (현재: {sorted(curr_keys)})")

    for msg in messages_to_send:
        send_telegram(msg)

    return csv_changed


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    logger.info("=== 가격 변동 알림 봇 v2 시작 ===")

    products = load_products(PRODUCTS_CSV)
    if not products:
        logger.error("상품 목록이 비어 있습니다. 종료합니다.")
        return

    logger.info(f"총 {len(products)}개 상품 체크 시작")

    changed_count = 0
    error_count   = 0
    csv_updated   = False

    for i, product in enumerate(products):
        logger.info(f"[{i+1}/{len(products)}] {product.name}")
        try:
            if process_product(product):
                changed_count += 1
                csv_updated = True
        except Exception as e:
            logger.error(f"  → 처리 중 예외 발생: {e}")
            error_count += 1

        if i < len(products) - 1:
            wait = random.uniform(3, 7)
            logger.info(f"  ⏱ {wait:.1f}초 대기...")
            time.sleep(wait)

    if csv_updated:
        save_products(PRODUCTS_CSV, products)

    summary = (
        f"✅ <b>점검 완료</b>\n"
        f"총 {len(products)}개 | 변동 {changed_count}개 | 오류 {error_count}개"
    )
    logger.info(summary.replace("<b>", "").replace("</b>", ""))
    if changed_count > 0 or error_count > 0:
        send_telegram(summary)

    logger.info("=== 봇 실행 완료 ===")


if __name__ == "__main__":
    main()
