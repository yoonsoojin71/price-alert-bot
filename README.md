# 소싱처 가격 변동 알림 봇 🤖

G마켓 상품 가격을 주기적으로 모니터링하고 텔레그램으로 변동 알림을 보내는 봇입니다.
GitHub Actions를 무료 서버로 활용합니다.

---

## 📁 파일 구조

```
├── price_checker.py              # 메인 크롤러 + 알림 스크립트
├── products.csv                  # 모니터링할 상품 목록
├── requirements.txt              # Python 패키지 목록
├── .env.example                  # 환경변수 예시 (복사해서 .env로 사용)
├── .gitignore
└── .github/
    └── workflows/
        └── price_alert.yml       # GitHub Actions 스케줄 설정
```

---

## 🚀 설치 및 설정 가이드

### Step 1. 텔레그램 봇 준비

1. 텔레그램에서 `@BotFather` 검색 → `/newbot` 명령으로 봇 생성
2. 발급받은 **봇 토큰** 저장 (예: `7123456789:AAFxxxx...`)
3. 본인의 텔레그램에서 봇에게 `/start` 전송
4. `https://api.telegram.org/bot<봇토큰>/getUpdates` 접속
   → `"chat":{"id": 123456789}` 부분이 **CHAT_ID**

### Step 2. GitHub 저장소 생성 및 업로드

```bash
git init
git add .
git commit -m "feat: 초기 설정"
git remote add origin https://github.com/본인계정/저장소명.git
git push -u origin main
```

### Step 3. GitHub Secrets 등록 (보안 핵심!)

GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름       | 값                          |
|-------------------|-----------------------------|
| `TELEGRAM_TOKEN`  | BotFather에서 받은 봇 토큰  |
| `TELEGRAM_CHAT_ID`| 본인 텔레그램 채팅 ID       |

### Step 4. products.csv 상품 추가

```csv
id,name,url,last_price
1,상품명,https://item.gmarket.co.kr/Item?goodscode=상품코드,0
```

> ⚠️ `last_price`를 `0`으로 설정하면 첫 실행 시 반드시 알림이 옵니다.
> 실제 가격을 미리 입력하면 다음 변동부터 알림이 옵니다.

### Step 5. Actions 활성화 확인

GitHub 저장소 → **Actions** 탭 → 워크플로우 활성화 확인

---

## ⏰ 실행 스케줄

| 시간대 (KST)       | 주기     | 비고               |
|--------------------|----------|--------------------|
| 09:00 ~ 24:00      | 30분마다 | 활발한 거래 시간대 |
| 00:00 ~ 09:00      | 3시간마다| 새벽 최소 실행     |

**월간 Actions 사용량 예상:**
- 낮(30분) 실행 횟수: 30회/일 × 30일 = 900회
- 새벽(3시간) 실행 횟수: 3회/일 × 30일 = 90회
- 합계 약 990회 × 약 1~2분 = **약 1,000~2,000분/월** (무료 한도 2,000분 내 운영 가능)

---

## 🔧 로컬 테스트 방법

```bash
# 1. 패키지 설치
pip install -r requirements.txt

# 2. .env 파일 생성
cp .env.example .env
# .env 파일에 실제 토큰 입력

# 3. 실행
python price_checker.py
```

---

## 📈 VPS 이전 시 (상품 200개 이상)

표준 Python으로 작성되어 코드 수정 없이 이전 가능합니다.

```bash
# VPS (Ubuntu 기준)
git clone https://github.com/본인계정/저장소명.git
cd 저장소명
pip install -r requirements.txt

# .env 파일 생성 후 크론탭 등록
crontab -e
# 낮: */30 9-23 * * * cd /path/to/bot && python price_checker.py
# 새벽: 0 0,3,6 * * * cd /path/to/bot && python price_checker.py
```

---

## ⚠️ 주의사항

- G마켓 HTML 구조 변경 시 셀렉터 재확인 필요 (`fetch_gmarket_price` 함수)
- `last_price` 컬럼은 콤마 없는 숫자로 저장 (예: `189000`)
- `.env` 파일은 절대 Git에 올리지 마세요
