import os
import json
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
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
}

SCAN_RANGE = 30       # 앞으로 탐색할 boardId 수
MAX_WORKERS = 10      # 병렬 요청 수 (10개 동시)
REQUEST_TIMEOUT = 8   # 요청 타임아웃

# ── 상태 로드/저장 ─────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "max_scanned" not in data:
            return {"max_scanned": 6530, "known_posts": []}
        return data
    return {"max_scanned": 6530, "known_posts": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ── 날짜 파싱 ──────────────────────────────────────────
def parse_dates(soup):
    post_date, apply_date = "", ""
    for line in soup.get_text("\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if "공고게시일" in line or "공고일" in line:
            dates = re.findall(r"\d{4}[-./]\d{2}[-./]\d{2}", line)
            if dates:
                post_date = dates[0]
        if "청약신청일" in line or "신청일" in line:
            dates = re.findall(r"\d{4}[-./]\d{2}[-./]\d{2}", line)
            if dates:
                apply_date = dates[0]
    return post_date, apply_date

# ── 단일 boardId 체크 (병렬용) ─────────────────────────
def check_board(board_id):
    url = f"{BASE_URL}/youth/bbs/BMSR00015/view.do?boardId={board_id}&menuNo=400008"
    try:
        res = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if res.status_code != 200:
            return board_id, None
        # raw text 빠른 필터
        if "민간" not in res.text or (
            "모집공고" not in res.text and "입주자" not in res.text
        ):
            return board_id, None
        soup = BeautifulSoup(res.text, "html.parser")
        title_tag = soup.select_one(".bbs-view-title, .board-view-title, h3.tit, .subject, h4")
        title = title_tag.get_text(strip=True) if title_tag else f"민간임대 공고 #{board_id}"
        post_date, apply_date = parse_dates(soup)
        return board_id, {
            "uid": str(board_id),
            "title": title,
            "post_date": post_date,
            "apply_date": apply_date,
            "url": url,
        }
    except Exception:
        return board_id, None

# ── 텔레그램 전송 ──────────────────────────────────────
def send_telegram(message: str, retries=2):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    for attempt in range(1, retries + 1):
        try:
            res = requests.post(api_url, json=payload, timeout=15)
            res.raise_for_status()
            print("  → 텔레그램 전송 완료")
            return
        except Exception as e:
            print(f"  → 전송 실패 ({attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(3)

# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    state = load_state()
    max_scanned = state["max_scanned"]
    known_posts = state["known_posts"]
    known_uids = {p["uid"] for p in known_posts}

    # 병렬로 새 boardId 범위 스캔
    start_id = max_scanned + 1
    end_id = start_id + SCAN_RANGE
    print(f"  → 병렬 스캔: {start_id} ~ {end_id - 1} ({SCAN_RANGE}개, {MAX_WORKERS}개 동시)")

    found = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_board, bid): bid for bid in range(start_id, end_id)}
        for future in as_completed(futures):
            board_id, post = future.result()
            if post:
                found[board_id] = post
                print(f"  → 발견: {post['title']}")

    # boardId 순서대로 정렬
    new_posts = []
    for bid in sorted(found.keys()):
        post = found[bid]
        if post["uid"] not in known_uids:
            new_posts.append(post)
            known_posts.append(post)
            known_uids.add(post["uid"])

    state["max_scanned"] = end_id - 1
    state["known_posts"] = known_posts

    # 새 공고 알림
    if new_posts:
        for p in new_posts:
            post_date_line = f"📅 공고게시일: {p['post_date']}" if p['post_date'] else ""
            apply_date_line = f"📝 청약신청일: {p['apply_date']}" if p['apply_date'] else ""
            date_info = "\n".join(filter(None, [post_date_line, apply_date_line]))
            msg = (
                f"🏠 청년안심주택 민간임대 새 공고!\n\n"
                f"📌 {p['title']}\n{date_info}\n🔗 {p['url']}"
            )
            send_telegram(msg)
    else:
        send_telegram("새로운 공고가 없습니다😭 내일 다시 확인해보겠습니다!")

    # 최근 5개 (캐시에서 바로)
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
