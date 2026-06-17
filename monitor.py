import os
import json
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

# ══════════════════════════════════════════════════════
# 설정
# ══════════════════════════════════════════════════════
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHAT_IDS = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()]
STATE_FILE = "seen_posts.json"

# 청년안심주택
SOCO_BASE   = "https://soco.seoul.go.kr"
SOCO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}
SCAN_RANGE   = 30
MAX_WORKERS  = 10
TIMEOUT      = 7

# Elyes
ELYES_BASE   = "https://www.elyes.co.kr"
ELYES_SEED   = f"{ELYES_BASE}/post/recruit/detail?i_sNtCode=BHCT&nt_idx=Zi4TSTH9NEy7/LHaBpX8xg%3D%3D"
ELYES_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": f"{ELYES_BASE}/post/recruit",
}

# ══════════════════════════════════════════════════════
# 상태 관리
# ══════════════════════════════════════════════════════
DEFAULT_STATE = {
    "max_scanned": 6560,
    "known_posts": [],   # 청년안심주택 민간임대
    "known_elyes": [],   # Elyes
}

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "max_scanned" in data:
                # 누락 키 보완
                for k, v in DEFAULT_STATE.items():
                    data.setdefault(k, v)
                return data
        except Exception:
            pass
    return dict(DEFAULT_STATE)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════════════
# 텔레그램
# ══════════════════════════════════════════════════════
def send_telegram(message: str, retries=2):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        for attempt in range(1, retries + 1):
            try:
                res = requests.post(
                    api_url,
                    json={"chat_id": chat_id, "text": message},
                    timeout=15
                )
                res.raise_for_status()
                print(f"  → 전송 완료 ({chat_id})")
                break
            except Exception as e:
                print(f"  → 전송 실패 ({attempt}/{retries}): {e}")
                if attempt < retries:
                    time.sleep(3)

# ══════════════════════════════════════════════════════
# 청년안심주택 민간임대
# ══════════════════════════════════════════════════════
def parse_dates(text):
    post_date = apply_date = ""
    m = re.search(r"공고게시일\s*(\d{4}[-./]\d{2}[-./]\d{2})", text)
    if m: post_date = m.group(1)
    m = re.search(r"청약신청일\s*(\d{4}[-./]\d{2}[-./]\d{2})", text)
    if m: apply_date = m.group(1)
    return post_date, apply_date

def check_board(board_id):
    url = f"{SOCO_BASE}/youth/bbs/BMSR00015/view.do?boardId={board_id}&menuNo=400008"
    try:
        res = requests.get(url, headers=SOCO_HEADERS, timeout=TIMEOUT)
        if res.status_code != 200:
            return board_id, None
        text = res.text
        if "민간" not in text or ("모집공고" not in text and "입주자" not in text):
            return board_id, None
        soup = BeautifulSoup(text, "html.parser")
        title_tag = soup.select_one(".bbs-view-title, .board-view-title, h3.tit, .subject, h4")
        title = title_tag.get_text(strip=True) if title_tag else f"민간임대 공고 #{board_id}"
        post_date, apply_date = parse_dates(soup.get_text())
        return board_id, {
            "uid": str(board_id),
            "title": title,
            "post_date": post_date,
            "apply_date": apply_date,
            "url": url,
        }
    except Exception:
        return board_id, None

def monitor_soco(state):
    known_posts = state["known_posts"]
    known_uids  = {p["uid"] for p in known_posts}
    start_id = state["max_scanned"] + 1
    end_id   = start_id + SCAN_RANGE
    print(f"  [청년주택] 병렬 스캔: {start_id}~{end_id-1}")

    found = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_board, bid): bid for bid in range(start_id, end_id)}
        for future in as_completed(futures):
            bid, post = future.result()
            if post:
                found[bid] = post
                print(f"  [청년주택] 발견: {post['title']}")

    new_posts = []
    for bid in sorted(found.keys()):
        post = found[bid]
        if post["uid"] not in known_uids:
            new_posts.append(post)
            known_posts.append(post)
            known_uids.add(post["uid"])

    state["max_scanned"] = end_id - 1
    state["known_posts"] = known_posts

    # 알림
    if new_posts:
        for p in new_posts:
            date_info = "\n".join(filter(None, [
                f"📅 공고게시일: {p['post_date']}" if p.get("post_date") else "",
                f"📝 청약신청일: {p['apply_date']}" if p.get("apply_date") else "",
            ]))
            send_telegram(f"🏠 청년안심주택 민간임대 새 공고!\n\n📌 {p['title']}\n{date_info}\n🔗 {p['url']}")
    else:
        send_telegram("새로운 청년안심주택 공고가 없습니다😭 내일 다시 확인해보겠습니다!")

    # Newest 5
    recent = sorted(known_posts, key=lambda p: int(p["uid"]), reverse=True)[:5]
    if recent:
        lines = ["📋 최근 민간임대 공고 Newest 5\n"]
        for i, p in enumerate(recent, 1):
            pd = f"📅 {p.get('post_date', '-')}"
            ad = f"📝 {p.get('apply_date', '-')}"
            lines.append(f"{i}. {p['title']}\n{pd}  |  {ad}\n🔗 {p['url']}\n")
        send_telegram("\n".join(lines))

# ══════════════════════════════════════════════════════
# Elyes
# ══════════════════════════════════════════════════════
def fetch_elyes_page(url):
    """단일 페이지 요청 → (제목, 이전글url, 다음글url) 반환"""
    try:
        res = requests.get(url, headers=ELYES_HEADERS, timeout=TIMEOUT)
        if res.status_code != 200:
            return None, None, None
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text("\n")

        # 제목: 날짜 직전 줄
        title = ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if re.match(r"\d{4}\.\d{2}\.\d{2}", line) and i > 0:
                title = lines[i - 1]
                break

        # 이전글(더 최신) / 다음글(더 오래된) 링크
        prev_url = next_url = None
        for a in soup.select("a[href*='recruit/detail']"):
            label = a.get_text(strip=True)
            href  = a["href"]
            full  = f"{ELYES_BASE}{href}" if href.startswith("/") else href
            if "이전글" in label and not prev_url:
                prev_url = full
            if "다음글" in label and not next_url:
                next_url = full

        return title, prev_url, next_url
    except Exception as e:
        print(f"  [Elyes] 요청 오류 ({url[:60]}): {e}")
        return None, None, None

def make_elyes_uid(url):
    m = re.search(r"nt_idx=([^&]+)", url)
    return f"elyes_{m.group(1)[:20]}" if m else f"elyes_{url[-20:]}"

def monitor_elyes(state):
    known_elyes = state.get("known_elyes", [])
    seen_uids   = {p["uid"] for p in known_elyes}

    # 시작점: 저장된 최신 공고 URL 또는 seed
    start_url = known_elyes[0]["url"] if known_elyes else ELYES_SEED

    # 1) 이전글 방향으로 올라가며 최신 공고 찾기 (최대 5번)
    latest_url = start_url
    visited = {start_url}
    for _ in range(5):
        _, prev_url, _ = fetch_elyes_page(latest_url)
        if prev_url and prev_url not in visited:
            latest_url = prev_url
            visited.add(prev_url)
        else:
            break

    # 2) 최신 URL부터 다음글 방향으로 5개 수집 (페이지당 1회 요청)
    posts = []
    current_url = latest_url
    visited2 = set()
    while len(posts) < 5 and current_url and current_url not in visited2:
        visited2.add(current_url)
        title, _, next_url = fetch_elyes_page(current_url)
        if title:
            posts.append({
                "uid":   make_elyes_uid(current_url),
                "title": title,
                "url":   current_url,
            })
        current_url = next_url

    if not posts:
        print("  [Elyes] 공고 파싱 실패")
        send_telegram("새로운 Elyes 공고가 없습니다😭 내일 다시 확인해보겠습니다!")
        return

    new_posts = [p for p in posts if p["uid"] not in seen_uids]

    if new_posts:
        for p in new_posts:
            send_telegram(f"🏢 Elyes 새 모집공고!\n\n📌 {p['title']}\n🔗 {p['url']}")
            print(f"  [Elyes] 알림: {p['title']}")
        state["known_elyes"] = (new_posts + known_elyes)[:20]
    else:
        send_telegram("새로운 Elyes 공고가 없습니다😭 내일 다시 확인해보겠습니다!")
        print("  [Elyes] 새 공고 없음")
        state["known_elyes"] = known_elyes  # 변경 없어도 명시적 저장

    # Newest 5
    recent = (new_posts + known_elyes)[:5]
    lines  = ["📋 최근 Elyes 공고 Newest 5\n"]
    for i, p in enumerate(recent, 1):
        lines.append(f"{i}. {p['title']}\n🔗 {p['url']}\n")
    send_telegram("\n".join(lines))

# ══════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════
def main():
    print(f"[{datetime.now()}] 모니터링 시작")

    state = load_state()

    print("\n[1/2] 청년안심주택 민간임대")
    monitor_soco(state)

    print("\n[2/2] Elyes 모집공고")
    monitor_elyes(state)

    save_state(state)
    print(f"\n[{datetime.now()}] 완료")

if __name__ == "__main__":
    main()
