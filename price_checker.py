"""
소싱처 가격 변동 알림 봇 v3
- 초보자 친화적: 유지보수 쉽고 명확한 코드
- 상품당 5초 대기 (IP 보호)
- 실패 시 텔레그램으로 "사이트 확인 필요" 알림
- 모든 실행 결과 로그 기록
"""

import csv
import os
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# 로그 설정 (콘솔 + GitHub Actions 실행 기록에 남음)
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 환경변수 (GitHub Secrets에서 주입)
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PRODUCTS_CSV     = os.environ.get("PRODUCTS_CSV", "products.csv")

# ─────────────────────────────────────────────
# 설정값 (여기만 수정하면 됩니다)
# ─────────────────────────────────────────────
WAIT_SECONDS = 5          # 상품 1개 체크 후 대기 시간 (초)
REQUEST_TIMEOUT = 10      # 페이지 요청 제한 시간 (초)

# 감지할 혜택 키워드 목록
BENEFIT_KEYWORDS = [
    "카드할인", "카드 할인",
    "쿠폰", "쿠폰할인",
    "즉시할인", "즉시 할인",
    "특가", "행사가",
    "무이자", "할부",
    "적립", "혜택",
    "타임딜", "타임세일",
    "오늘만",
]

# 요청 헤더 (봇 차단 방지)
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
# 데이터 구조
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
# CSV 읽기 / 저장
# ─────────────────────────────────────────────
CSV_FIELDS = [
    "id", "name", "url", "last_price",
    "last_card_price", "last_coupon_price", "last_benefit_keys"
]

def load_products(filepath: str) -> list:
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
                except Exception as e:
                    logger.warning(f"  ⚠️ 행 파싱 오류 (id={row.get('id','?')}): {e}")
    except FileNotFoundError:
        logger.error(f"❌ CSV 파일을 찾을 수 없습니다: {filepath}")
    return products


def save_products(filepath: str, products: list) -> None:
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
    logger.info(f"✅ CSV 저장 완료: {filepath}")


def _to_int(value: str) -> int:
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else 0


# ─────────────────────────────────────────────
# 페이지 파싱
# ─────────────────────────────────────────────

def fetch_page_info(product: Product) -> Optional[PageInfo]:
    """
    상품 페이지에 접속해서 가격/혜택 정보를 가져옵니다.
    실패하면 None을 반환합니다.
    """
    try:
        resp = requests.get(product.url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.Timeout:
        # 실패 이유 1: 페이지가 너무 느림
        logger.error(f"  ❌ 실패 이유: 페이지 응답 시간 초과 ({REQUEST_TIMEOUT}초) | {product.name}")
        return None
    except requests.exceptions.HTTPError as e:
        # 실패 이유 2: 404(상품없음), 403(차단) 등
        logger.error(f"  ❌ 실패 이유: HTTP {e.response.status_code} 오류 | {product.name}")
        return None
    except requests.exceptions.RequestException as e:
        # 실패 이유 3: 네트워크 오류
        logger.error(f"  ❌ 실패 이유: 네트워크 오류 ({e}) | {product.name}")
        return None

    info = PageInfo()
    info.price        = _parse_main_price(soup, product)
    info.card_price   = _parse_card_price(soup)
    info.coupon_price = _parse_coupon_price(soup)
    info.found_keywords = _detect_keywords(soup)
    return info


def _parse_main_price(soup, product: Product) -> Optional[int]:
    """일반 판매가 파싱 — 4단계 시도"""

    # 시도 1: 신규 레이아웃
    tag = soup.select_one("span.price__real strong")
    if tag:
        return _price_int(tag.get_text())

    # 시도 2: 박스 레이아웃
    tag = soup.select_one("div.box__price-sale strong")
    if tag:
        return _price_int(tag.get_text())

    # 시도 3: 구버전 레이아웃
    tag = soup.select_one("#itemcase_basic .price strong")
    if tag:
        return _price_int(tag.get_text())

    # 시도 4: 메타태그 (마지막 수단)
    meta = soup.find("meta", property="og:description")
    if meta:
        m = re.search(r"([\d,]+)원", meta.get("content", ""))
        if m:
            return _price_int(m.group(1))

    # 4가지 모두 실패 → 사이트 구조 변경 의심
    logger.warning(f"  ⚠️ 실패 이유: 가격 위치를 찾지 못함 (사이트 구조 변경 가능성) | {product.name}")
    return None


def _parse_card_price(soup) -> Optional[int]:
    """카드 할인가 파싱"""
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


def _parse_coupon_price(soup) -> Optional[int]:
    """쿠폰 적용가 파싱"""
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


def _detect_keywords(soup) -> list:
    """혜택 키워드 감지"""
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()
    page_text = soup.get_text(separator=" ", strip=True)
    return [kw for kw in BENEFIT_KEYWORDS if kw in page_text]


def _price_int(text: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


# ─────────────────────────────────────────────
# 텔레그램 알림
# ─────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ 텔레그램 토큰/채팅ID가 설정되지 않았습니다.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        resp.raise_for_status()
        logger.info("  📨 텔레그램 전송 완료")
        return True
    except Exception as e:
        logger.error(f"  ❌ 텔레그램 전송 실패: {e}")
        return False


def send_error_alert(product: Product, reason: str) -> None:
    """가격을 못 읽었을 때 사이트 확인 요청 알림"""
    message = (
        f"🚨 <b>사이트 확인 필요</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 <b>{product.name}</b>\n"
        f"🔗 <a href='{product.url}'>상품 링크</a>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚠️ 원인: {reason}\n"
        f"👉 직접 링크 접속해서 확인해주세요!"
    )
    send_telegram(message)


def build_price_alert(product: Product, info: PageInfo) -> str:
    """가격 변동 알림 메시지"""
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


def build_keyword_alert(product: Product, appeared: list, disappeared: list) -> str:
    """혜택 키워드 변동 알림 메시지"""
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
# 상품 1개 처리
# ─────────────────────────────────────────────

def process_product(product: Product) -> bool:
    """
    상품 1개를 처리합니다.
    CSV 업데이트가 필요하면 True 반환.
    """
    info = fetch_page_info(product)

    # 페이지 자체를 못 읽은 경우
    if info is None:
        send_error_alert(product, "페이지 접속 실패 (네트워크/차단 오류)")
        return False

    # 가격을 못 찾은 경우 (사이트 구조 변경 의심)
    if info.price is None:
        send_error_alert(product, "가격 위치를 찾지 못함 (사이트 구조 변경 가능성)")
        return False

    csv_changed = False
    messages = []

    # ── 가격 변동 감지 ──
    price_changed  = info.price        != product.last_price
    card_changed   = info.card_price   is not None and info.card_price   != product.last_card_price
    coupon_changed = info.coupon_price is not None and info.coupon_price != product.last_coupon_price

    if price_changed or card_changed or coupon_changed:
        messages.append(build_price_alert(product, info))
        if price_changed:
            logger.info(f"  💰 일반가 변동: {product.last_price:,} → {info.price:,}원")
            product.last_price = info.price
            csv_changed = True
        if card_changed:
            logger.info(f"  💳 카드가 변동: {product.last_card_price:,} → {info.card_price:,}원")
            product.last_card_price = info.card_price
            csv_changed = True
        if coupon_changed:
            logger.info(f"  🎫 쿠폰가 변동: {product.last_coupon_price:,} → {info.coupon_price:,}원")
            product.last_coupon_price = info.coupon_price
            csv_changed = True
    else:
        logger.info(f"  ✅ 가격 변동 없음 ({info.price:,}원)")

    # ── 혜택 키워드 변동 감지 ──
    prev_keys = set(product.last_benefit_keys.split(",")) - {""} if product.last_benefit_keys else set()
    curr_keys = set(info.found_keywords)
    appeared    = sorted(curr_keys - prev_keys)
    disappeared = sorted(prev_keys - curr_keys)

    if appeared or disappeared:
        logger.info(f"  🔔 키워드 변동 | 등장: {appeared} | 소멸: {disappeared}")
        messages.append(build_keyword_alert(product, appeared, disappeared))
        product.last_benefit_keys = ",".join(sorted(curr_keys))
        csv_changed = True
    else:
        logger.info(f"  ✅ 키워드 변동 없음 (현재: {sorted(curr_keys) or '없음'})")

    for msg in messages:
        send_telegram(msg)

    return csv_changed


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────

def main():
    logger.info("=" * 50)
    logger.info("🚀 가격 변동 알림 봇 시작")
    logger.info("=" * 50)

    products = load_products(PRODUCTS_CSV)
    if not products:
        logger.error("❌ 상품 목록이 비어 있습니다. products.csv를 확인해주세요.")
        return

    total     = len(products)
    success   = 0
    changed   = 0
    failed    = 0
    csv_updated = False

    logger.info(f"📋 총 {total}개 상품 체크 시작")
    logger.info("-" * 50)

    for i, product in enumerate(products):
        logger.info(f"[{i+1}/{total}] 📦 {product.name}")

        try:
            updated = process_product(product)
            if updated:
                changed += 1
                csv_updated = True
            success += 1
        except Exception as e:
            logger.error(f"  ❌ 예상치 못한 오류: {e}")
            failed += 1

        # 마지막 상품이 아니면 5초 대기 (IP 보호)
        if i < total - 1:
            logger.info(f"  ⏱ {WAIT_SECONDS}초 대기 중...")
            time.sleep(WAIT_SECONDS)

    # CSV 저장 (변동 있을 때만)
    if csv_updated:
        save_products(PRODUCTS_CSV, products)

    # 실행 요약 로그
    logger.info("-" * 50)
    logger.info(f"📊 실행 완료 요약")
    logger.info(f"  • 전체: {total}개")
    logger.info(f"  • 성공: {success}개")
    logger.info(f"  • 변동: {changed}개")
    logger.info(f"  • 실패: {failed}개")
    logger.info("=" * 50)

    # 실패가 있으면 요약도 텔레그램으로
    if failed > 0:
        send_telegram(
            f"📊 <b>점검 완료</b>\n"
            f"전체 {total}개 | 변동 {changed}개 | "
            f"⚠️ 실패 {failed}개"
        )


if __name__ == "__main__":
    main()
