import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

# ── 설정 ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "seen_posts.json"

BASE_URL = "https://soco.seoul.go.kr"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://soco.seoul.go.kr/youth/bbs/BMSR00015/list.do?menuNo=400008",
}

# ── 상태 파일 구조 ─────────────────────────────────────
# {
#   "max_scanned": 6451,          ← 지금까지 스캔한 최대 boardId
#   "known_posts": [              ← 민간임대 공고만 저장
#     {"uid": "6422", "title": "...", "post_date": "...", "apply_date": "...", "url": "..."},
#     ...
#   ]
# }

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 구 형식(list) → 새 형식(dict) 자동 변환
        if isinstance(data, list):
            print("  → 구 형식 seen_posts.json 감지, 자동 변환 중...")
            return {"max_scanned": 6451, "known_posts": []}
        return data
    return {"max_scanned": 6451, "known_posts": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ── 날짜 파싱 ──────────────────────────────────────────
def parse_dates(soup):
    post_date, apply_date = "", ""
    full_text = soup.get_text("\n")
    for line in full_text.split("\n"):
        line = line.strip()
        if "공고게시일" in line or "공고일" in line:
            dates = re.findall(r"\d{4}[-./]\d{2}[-./]\d{2}", line)
            if dates: post_date = dates[0]
        if "청약신청일" in line or "신청일" in line:
            dates = re.findall(r"\d{4}[-./]\d{2}[-./]\d{2}", line)
            if dates: apply_date = dates[0]
    return post_date, apply_date

# ── 단일 공고 파싱 ─────────────────────────────────────
def parse_post(board_id):
    url = f"{BASE_URL}/youth/bbs/BMSR00015/view.do?boardId={board_id}&menuNo=400008"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.text, "html.parser")
        page_text = soup.get_text()
        if "민간" in page_text and ("모집공고" in page_text or "입주자" in page_text):
            title_tag = soup.select_one(".bbs-view-title, .board-view-title, h3.tit, .subject, h4")
            title = title_tag.get_text(strip=True) if title_tag else f"민간임대 공고 #{board_id}"
            post_date, apply_date = parse_dates(soup)
            return {
                "uid": str(board_id),
                "title": title,
                "post_date": post_date,
                "apply_date": apply_date,
                "url": url,
            }
    except Exception as e:
        print(f"  [boardId {board_id}] 오류: {e}")
    return None

# ── 텔레그램 전송 (재시도 포함) ───────────────────────
def send_telegram(message: str, retries=3):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    for attempt in range(1, retries + 1):
        try:
            res = requests.post(api_url, json=payload, timeout=30)
            res.raise_for_status()
            print(f"  → 텔레그램 전송 완료")
            return
        except Exception as e:
            print(f"  → 텔레그램 전송 실패 (시도 {attempt}/{retries}): {e}")
            if attempt < retries:
                import time
                time.sleep(5)
    print("  → 텔레그램 전송 최종 실패, 계속 진행")

# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    state = load_state()
    max_scanned = state["max_scanned"]
    known_posts = state["known_posts"]  # 민간임대 공고만 담긴 리스트
    known_uids = {p["uid"] for p in known_posts}

    # 1) 새 boardId 범위만 스캔 (최대 30개)
    start_id = max_scanned + 1
    end_id = start_id + 30
    print(f"  → 새 공고 스캔: {start_id} ~ {end_id}")

    new_posts = []
    for board_id in range(start_id, end_id):
        post = parse_post(board_id)
        if post and post["uid"] not in known_uids:
            new_posts.append(post)
            known_posts.append(post)
            known_uids.add(post["uid"])
            print(f"  → 새 공고: {post['title']}")

    # max_scanned 업데이트
    state["max_scanned"] = end_id - 1
    state["known_posts"] = known_posts

    # 2) 새 공고 알림
    if new_posts:
        for p in new_posts:
            post_date_line = f"📅 공고게시일: {p['post_date']}" if p['post_date'] else ""
            apply_date_line = f"📝 청약신청일: {p['apply_date']}" if p['apply_date'] else ""
            date_info = "\n".join(filter(None, [post_date_line, apply_date_line]))
            msg = (
                f"🏠 청년안심주택 민간임대 새 공고!\n\n"
                f"📌 {p['title']}\n"
                f"{date_info}\n"
                f"🔗 {p['url']}"
            )
            send_telegram(msg)
    else:
        send_telegram("새로운 공고가 없습니다😭 내일 다시 확인해보겠습니다!")

    # 3) 최근 5개 (저장된 리스트에서 바로 꺼냄 → 추가 요청 없음)
    recent = sorted(known_posts, key=lambda p: int(p["uid"]), reverse=True)[:5]
    if recent:
        lines = ["📋 최근 민간임대 공고 Newest 5\n"]
        for i, p in enumerate(recent, 1):
            post_date_line = f"📅 공고게시일: {p['post_date']}" if p['post_date'] else ""
            apply_date_line = f"📝 청약신청일: {p['apply_date']}" if p['apply_date'] else ""
            date_info = "  |  ".join(filter(None, [post_date_line, apply_date_line]))
            lines.append(f"{i}. {p['title']}\n{date_info}\n🔗 {p['url']}\n")
        send_telegram("\n".join(lines))

    save_state(state)
    print(f"[{datetime.now()}] 완료")

if __name__ == "__main__":
    main()
