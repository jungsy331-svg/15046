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

# ── 날짜 파싱 ──────────────────────────────────────────
def parse_dates(soup):
    post_date = ""
    apply_date = ""
    full_text = soup.get_text("\n")
    for line in full_text.split("\n"):
        line = line.strip()
        if "공고게시일" in line or "공고일" in line:
            dates = re.findall(r"\d{4}[-./]\d{2}[-./]\d{2}", line)
            if dates:
                post_date = dates[0]
        if "청약신청일" in line or "신청일" in line:
            dates = re.findall(r"\d{4}[-./]\d{2}[-./]\d{2}", line)
            if dates:
                apply_date = dates[0]
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


# ── 새 공고 스캔 (앞으로 30칸만) ──────────────────────
def fetch_new_posts(seen):
    posts = []
    known_ids = [int(uid) for uid in seen if uid.isdigit()]
    start_id = max(known_ids) + 1 if known_ids else 6450
    end_id = start_id + 30
    print(f"  → 새 공고 스캔: {start_id} ~ {end_id}")
    for board_id in range(start_id, end_id):
        post = parse_post(board_id)
        if post:
            posts.append(post)
            print(f"  → 새 공고: {post['title']}")
    return posts


# ── 최근 5개: seen에서 캐싱된 메타 사용 ───────────────
def fetch_recent_posts(seen, new_posts, count=5):
    """
    새 공고 + 기존 known_ids 역순으로 최대 count개만 파싱
    이미 파싱한 new_posts는 재사용해서 요청 최소화
    """
    # 새 공고를 uid→post 딕셔너리로
    new_map = {p["uid"]: p for p in new_posts}

    known_ids = sorted([int(uid) for uid in seen if uid.isdigit()], reverse=True)

    recent = []
    # 새 공고 먼저 추가 (이미 파싱됨, 요청 없음)
    for p in reversed(new_posts):
        if len(recent) >= count:
            break
        recent.insert(0, p)

    # 부족하면 기존 seen에서 역순으로 파싱 (최대 10개만 시도)
    tried = 0
    for board_id in known_ids:
        if len(recent) >= count:
            break
        if tried >= 10:
            break
        if str(board_id) in new_map:
            continue
        tried += 1
        post = parse_post(board_id)
        if post:
            recent.append(post)

    # 최신순 정렬 (boardId 내림차순)
    recent.sort(key=lambda p: int(p["uid"]), reverse=True)
    return recent[:count]


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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    res = requests.post(api_url, json=payload, timeout=10)
    res.raise_for_status()
    print(f"  → 텔레그램 전송 완료")


# ── 초기 씨딩 ──────────────────────────────────────────
def seed_initial_state():
    seen = set(str(uid) for uid in range(6332, 6452))
    save_seen(seen)
    print(f"  → 초기 상태 등록 완료")
    return seen


# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    seen = load_seen()
    if not seen:
        seen = seed_initial_state()

    # 1) 새 공고 스캔
    new_posts = fetch_new_posts(seen)

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
            seen.add(p["uid"])
    else:
        send_telegram("새로운 공고가 없습니다😭 내일 다시 확인해보겠습니다!")

    # 2) 최근 5개 (새 공고 재사용 → 추가 요청 최소화)
    print("  → 최근 5개 공고 조회 중...")
    recent_posts = fetch_recent_posts(seen, new_posts, count=5)

    if recent_posts:
        lines = ["📋 최근 민간임대 공고 Newest 5\n"]
        for i, p in enumerate(recent_posts, 1):
            post_date_line = f"📅 공고게시일: {p['post_date']}" if p['post_date'] else ""
            apply_date_line = f"📝 청약신청일: {p['apply_date']}" if p['apply_date'] else ""
            date_info = "  |  ".join(filter(None, [post_date_line, apply_date_line]))
            lines.append(f"{i}. {p['title']}\n{date_info}\n🔗 {p['url']}\n")
        send_telegram("\n".join(lines))

    save_seen(seen)
    print(f"[{datetime.now()}] 완료")


if __name__ == "__main__":
    main()
