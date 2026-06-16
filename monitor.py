import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "seen_posts.json"

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

# ── 공고 목록 파싱 ─────────────────────────────────────
def fetch_posts():
    posts = []
    seen = load_seen()

    known_ids = [int(uid) for uid in seen if uid.isdigit()]
    start_id = max(known_ids) + 1 if known_ids else 6400
    end_id = start_id + 30

    print(f"  → boardId {start_id} ~ {end_id} 스캔 중...")

    for board_id in range(start_id, end_id):
        url = f"{BASE_URL}/youth/bbs/BMSR00015/view.do?boardId={board_id}&menuNo=400008"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            page_text = soup.get_text()

            if "민간" in page_text and ("모집공고" in page_text or "입주자" in page_text):
                title_tag = soup.select_one(".bbs-view-title, .board-view-title, h3.tit, .subject, h4")
                title = title_tag.get_text(strip=True) if title_tag else f"민간임대 공고 #{board_id}"
                posts.append({
                    "uid": str(board_id),
                    "title": title,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "url": url,
                })
                print(f"  → 발견: {title}")

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


# ── 텔레그램 전송 (plain text) ─────────────────────────
def send_telegram(message: str):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        # parse_mode 제거 → plain text로 안전하게 전송
    }
    res = requests.post(api_url, json=payload, timeout=10)
    res.raise_for_status()
    print(f"  → 텔레그램 전송 완료")


# ── 초기 실행: 기존 공고 씨딩 ─────────────────────────
def seed_initial_state():
    # 로그에서 확인된 기존 공고 boardId 6401~6421 모두 등록
    seen = set()
    for uid in range(6332, 6422):
        seen.add(str(uid))
    save_seen(seen)
    print(f"  → 초기 상태 등록 완료 (6332~6421)")
    return seen


# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    seen = load_seen()

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
            f"🏠 청년안심주택 민간임대 새 공고!\n\n"
            f"📌 {p['title']}\n"
            f"📅 공고일: {p['date']}\n"
            f"🔗 {p['url']}"
        )
        send_telegram(msg)
        seen.add(p["uid"])
        print(f"  → 알림 전송: {p['title']}")

    save_seen(seen)
    print(f"[{datetime.now()}] 완료")


if __name__ == "__main__":
    main()
