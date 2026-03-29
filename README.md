# 📈 Stock Thesis Tracker

Notion-embeddable candlestick dashboard.  
Report date는 **고정** — 차트는 매일 오른쪽으로 자동 성장합니다.

---

## 페이지

| URL | 용도 |
|---|---|
| `index.html` | 대시보드 — Notion 임베드 대상 |
| `manage.html` | 종목·thesis 추가/수정/삭제, 차트 재빌드 트리거 |

---

## 아키텍처

```
stocks.yaml  ←── manage.html (브라우저 UI → GitHub API로 직접 커밋)
     ↓
GitHub Actions  (매일 01:00 UTC + 수동 트리거)
     ↓
src/generate.py
  → data/<TICKER>.json   전체 ±365일 OHLCV (슬라이더 필터는 프론트엔드)
  → index.html           차트 대시보드
  → manage.html          관리 UI
     ↓
GitHub Pages → Notion 임베드
```

---

## 주요 기능

- **거래일만 표시** — 주말·공휴일 gap 없음 (Plotly category axis)
- **Past / Future 슬라이더** — 드래그하는 즉시 차트 반응 (120ms debounce)
- **Today 라인** — Future window 안에 있으면 표시, 벗어나면 자동 숨김
- **Ticker 검색** — JSONP 방식으로 Yahoo Finance autocomplete 직접 호출 (CORS 불필요), 한국·일본 등 Non-US 종목 지원
- **MA 20 / MA 50** — 서버에서 충분한 워밍업 기간으로 정확하게 계산
- **JSON 캐시** — ±365일 전체를 JSON에 저장, 슬라이더는 클라이언트 필터링으로 즉시 반응

---

## 셋업 (5분)

### 1. 레포 생성 후 파일 업로드

```
stock-thesis-tracker/
├── .github/workflows/update-charts.yml
├── src/generate.py
├── data/                  ← Actions가 자동 생성
├── stocks.yaml            ← 직접 편집하는 유일한 파일
├── index.html             ← Actions가 자동 생성
├── manage.html            ← Actions가 자동 생성
├── requirements.txt
└── .gitignore
```

### 2. GitHub Pages 활성화

Settings → Pages → Source: `main` / `/ (root)`

URL: `https://YOUR_USERNAME.github.io/stock-thesis-tracker/`

### 3. PAT 발급

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained

필요 권한 (해당 레포):
- **Contents** — Read and write
- **Actions** — Read and write

### 4. Secret 등록

Settings → Secrets and variables → Actions → New repository secret
- Name: `MANAGE_PAT`
- Value: 발급한 PAT

### 5. 첫 실행

Actions → Update Charts → Run workflow

약 1~2분 후 `data/*.json`, `index.html`, `manage.html`이 자동 커밋됩니다.

### 6. Notion 임베드

`/embed` → `https://YOUR_USERNAME.github.io/stock-thesis-tracker/`

---

## Manage 페이지 사용법

`https://YOUR_USERNAME.github.io/stock-thesis-tracker/manage.html`

1. 종목 카드 편집 → **Save** (브라우저 localStorage 저장)
2. GitHub owner / repo / PAT 입력
3. **Trigger GitHub Actions rebuild** 클릭
4. ~1~2분 후 차트 자동 갱신

PAT은 저장되지 않으며 세션마다 입력합니다. Owner/repo는 localStorage에 기억됩니다.

**Ticker 검색:** 입력란에 종목명이나 ticker를 타이핑하면 자동완성 드롭다운이 뜹니다.
- 한국: `삼성전자` 또는 `005930` → `005930.KS` 선택
- 일본: `Toyota` → `7203.T` 선택
- 직접 입력 후 Enter도 가능

---

## stocks.yaml 필드

| 필드 | 기본값 | 설명 |
|---|---|---|
| `ticker` | — | Yahoo Finance 심볼 (예: `AAPL`, `005930.KS`, `7203.T`) |
| `report_date` | — | 고정 기준일 `YYYY-MM-DD` |
| `label` | `""` | 카드 제목 |
| `thesis` | `""` | 투자 thesis |
| `default_pre` | `60` | 기본 과거 표시 일수 |
| `default_post` | `60` | 기본 미래 표시 일수 |

---

## 스케줄 변경

`.github/workflows/update-charts.yml`:

```yaml
schedule:
  - cron: "0 1 * * *"   # 매일 01:00 UTC
```

[crontab.guru](https://crontab.guru) 참고
