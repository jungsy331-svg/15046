import os
import json
import hashlib
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "seen_posts.json"

URL = "https://soco.seoul.go.kr/youth/bbs/BMSR00015/list.do?menuNo=400008"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── 공고 목록 파싱 ─────────────────────────────────────
def fetch_posts():
    res = requests.get(URL, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    posts = []
    rows = soup.select("table tbody tr")

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        # 구분 컬럼 (민간 / 공공 등)
        category = cols[1].get_text(strip=True)
        if "민간" not in category:
            continue

        title_tag = cols[2].find("a")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = title_tag.get("href", "")
        post_date = cols[3].get_text(strip=True) if len(cols) > 3 else ""

        # 고유 ID: 제목+날짜 해시
        uid = hashlib.md5(f"{title}{post_date}".encode()).hexdigest()

        posts.append({
            "uid": uid,
            "title": title,
            "date": post_date,
            "url": f"https://soco.seoul.go.kr{href}" if href.startswith("/") else href,
        })

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


# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    posts = fetch_posts()
    print(f"  → 민간임대 공고 {len(posts)}건 조회")

    seen = load_seen()
    new_posts = [p for p in posts if p["uid"] not in seen]

    if not new_posts:
        print("  → 새 공고 없음")
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
