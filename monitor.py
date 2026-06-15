import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "seen_posts.json"

# 공고 목록 페이지 (POST 방식으로 데이터 로드)
LIST_URL = "https://soco.seoul.go.kr/youth/bbs/BMSR00015/list.do"
BASE_URL = "https://soco.seoul.go.kr"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://soco.seoul.go.kr/youth/bbs/BMSR00015/list.do?menuNo=400008",
}

# ── 공고 목록 파싱 (여러 방식 시도) ───────────────────
def fetch_posts():
    posts = []

    # 방법 1: GET 요청 + 다양한 파라미터 조합 시도
    params_list = [
        {"menuNo": "400008"},
        {"menuNo": "400008", "pageIndex": "1"},
        {"menuNo": "400008", "searchCondition": "", "searchKeyword": "", "pageIndex": "1"},
    ]

    for params in params_list:
        try:
            res = requests.get(LIST_URL, params=params, headers=HEADERS, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")

            # 테이블 행 탐색
            rows = soup.select("table tbody tr")
            print(f"  [시도] params={params} → 행 수: {len(rows)}")

            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 3:
                    continue

                full_text = row.get_text()
                if "민간" not in full_text:
                    continue

                title_tag = row.find("a")
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")
                post_date = cols[-2].get_text(strip=True) if len(cols) >= 2 else ""

                uid = href.split("boardId=")[-1].split("&")[0] if "boardId=" in href else title
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href

                posts.append({
                    "uid": uid,
                    "title": title,
                    "date": post_date,
                    "url": full_url,
                })

            if posts:
                break

        except Exception as e:
            print(f"  [오류] {e}")
            continue

    # 방법 2: 목록 파싱 실패 시 → 최근 boardId 범위 직접 스캔
    if not posts:
        print("  → 목록 파싱 실패, boardId 스캔 방식으로 전환...")
        posts = scan_by_board_id()

    return posts


def scan_by_board_id():
    """최근 boardId 범위를 스캔해서 민간임대 공고 찾기"""
    posts = []
    
    # 현재 알려진 최신 boardId: 6400 (왕십리역 라봄성동, 2025-12)
    # 그 이후 번호부터 스캔
    seen = load_seen()
    
    # 저장된 최대 boardId 파악
    known_ids = [int(uid) for uid in seen if uid.isdigit()]
    start_id = max(known_ids) + 1 if known_ids else 6400
    end_id = start_id + 20  # 최대 20개 앞까지 탐색

    print(f"  → boardId {start_id} ~ {end_id} 스캔 중...")

    for board_id in range(start_id, end_id):
        url = f"{BASE_URL}/youth/bbs/BMSR00015/view.do?boardId={board_id}&menuNo=400008"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            
            # 페이지가 실제 공고인지 확인 (제목 존재 여부)
            title_tag = soup.select_one(".board-view-title, .bbs-view-title, h3, .subject")
            page_text = soup.get_text()
            
            # 민간임대 공고인지 확인
            if "민간" in page_text and ("모집공고" in page_text or "입주자" in page_text):
                title = title_tag.get_text(strip=True) if title_tag else f"공고 #{board_id}"
                posts.append({
                    "uid": str(board_id),
                    "title": title,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "url": url,
                })
                print(f"  → 민간임대 공고 발견: {title}")

        except Exception as e:
            print(f"  [boardId {board_id}] 오류: {e}")
            continue

    return posts


# ── 이전 상태 로드/저장 ────────────────────────────────
def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False)


# ── 텔레그램 전송 ──────────────────────────────────────
def send_telegram(message: str):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    res = requests.post(api_url, json=payload, timeout=10)
    res.raise_for_status()
    print(f"[{datetime.now()}] 텔레그램 전송 완료")


# ── 초기 실행: 기존 공고 boardId 씨딩 ─────────────────
def seed_initial_state():
    """최초 실행 시 기존 공고들을 seen에 등록 (과거 공고 알림 방지)"""
    # 알려진 기존 민간임대 공고 boardId들
    known_ids = ["6332", "6400"]  # 장한평역, 왕십리역
    seen = load_seen()
    for uid in known_ids:
        seen.add(uid)
    save_seen(seen)
    print(f"  → 초기 상태 등록: {known_ids}")
    return seen


# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    seen = load_seen()
    
    # 최초 실행이면 기존 공고 씨딩
    if not seen:
        seen = seed_initial_state()

    posts = fetch_posts()
    print(f"  → 민간임대 공고 {len(posts)}건 조회")

    new_posts = [p for p in posts if p["uid"] not in seen]

    if not new_posts:
        print("  → 새 공고 없음")
        save_seen(seen)
        return

    for p in new_posts:
        msg = (
            f"🏠 <b>청년안심주택 민간임대 새 공고!</b>\n\n"
            f"📌 <b>{p['title']}</b>\n"
            f"📅 공고일: {p['date']}\n"
            f"🔗 <a href='{p['url']}'>공고 바로가기</a>"
        )
        send_telegram(msg)
        seen.add(p["uid"])
        print(f"  → 알림 전송: {p['title']}")

    save_seen(seen)
    print(f"[{datetime.now()}] 완료")


if __name__ == "__main__":
    main()
