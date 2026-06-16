import os
import json
import requests
import time
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

SCAN_RANGE = 50        # 앞으로 탐색할 최대 boardId 수
EARLY_STOP = 10        # 연속으로 공고 없는 boardId가 이 수 이상이면 조기 종료
REQUEST_TIMEOUT = 5    # 요청 타임아웃 (초)
SEED_START = 6481      # 캐시 없을 때 초기 max_scanned

# ── 상태 로드/저장 ─────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) or "max_scanned" not in data:
            print("  → 상태 파일 초기화")
            return {"max_scanned": SEED_START, "known_posts": []}
        return data
    print("  → 상태 파일 없음, 초기화")
    return {"max_scanned": SEED_START, "known_posts": []}

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

# ── 단일 boardId 체크 ──────────────────────────────────
def check_board(board_id):
    url = f"{BASE_URL}/youth/bbs/BMSR00015/view.do?boardId={board_id}&menuNo=400008"
    try:
        res = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if res.status_code != 200:
            return None
        # BeautifulSoup 전에 raw text로 빠르게 필터
        if "민간" not in res.text or (
            "모집공고" not in res.text and "입주자" not in res.text
        ):
            return None
        soup = BeautifulSoup(res.text, "html.parser")
        title_tag = soup.select_one(
            ".bbs-view-title, .board-view-title, h3.tit, .subject, h4"
        )
        title = title_tag.get_text(strip=True) if title_tag else f"민간임대 공고 #{board_id}"
        post_date, apply_date = parse_dates(soup)
        return {
            "uid": str(board_id),
            "title": title,
            "post_date": post_date,
            "apply_date": apply_date,
            "url": url,
        }
    except requests.exceptions.Timeout:
        print(f"  [boardId {board_id}] 타임아웃 → 스킵")
        return None
    except Exception as e:
        print(f"  [boardId {board_id}] 오류: {e}")
        return None

# ── known_posts 없을 때 역방향으로 5개 빠르게 복원 ────
def recover_recent_posts(max_scanned, count=5):
    print(f"  → known_posts 비어있음, 최근 {count}개 복원 중...")
    recovered = []
    empty_streak = 0
    for board_id in range(max_scanned, max_scanned - 200, -1):
        if len(recovered) >= count:
            break
        if empty_streak >= 30:
            break
        post = check_board(board_id)
        if post:
            recovered.append(post)
            empty_streak = 0
        else:
            empty_streak += 1
    recovered.reverse()
    print(f"  → {len(recovered)}개 복원 완료")
    return recovered

# ── 텔레그램 전송 ──────────────────────────────────────
def send_telegram(message: str, retries=2):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    for attempt in range(1, retries + 1):
        try:
            res = requests.post(api_url, json=payload, timeout=15)
            res.raise_for_status()
            print(f"  → 텔레그램 전송 완료")
            return
        except Exception as e:
            print(f"  → 전송 실패 ({attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(3)
    print("  → 텔레그램 전송 최종 실패")

# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    state = load_state()
    max_scanned = state["max_scanned"]
    known_posts = state["known_posts"]
    known_uids = {p["uid"] for p in known_posts}

    # 새 공고 스캔 (조기 종료 포함)
    start_id = max_scanned + 1
    end_id = start_id + SCAN_RANGE
    print(f"  → 스캔 범위: {start_id} ~ {end_id - 1} (최대 {SCAN_RANGE}개)")

    new_posts = []
    empty_streak = 0
    last_scanned = max_scanned

    for board_id in range(start_id, end_id):
        post = check_board(board_id)
        last_scanned = board_id
        if post:
            empty_streak = 0
            if post["uid"] not in known_uids:
                new_posts.append(post)
                known_posts.append(post)
                known_uids.add(post["uid"])
                print(f"  → 새 공고: {post['title']}")
        else:
            empty_streak += 1
            if empty_streak >= EARLY_STOP:
                print(f"  → {EARLY_STOP}개 연속 없음, 조기 종료 (마지막: {board_id})")
                break

    state["max_scanned"] = last_scanned
    state["known_posts"] = known_posts

    # 새 공고 알림
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

    # 최근 5개: known_posts 비었으면 역방향 복원
    if not known_posts:
        known_posts = recover_recent_posts(last_scanned)
        state["known_posts"] = known_posts

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
