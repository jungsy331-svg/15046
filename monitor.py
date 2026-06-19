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
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHAT_IDS  = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()]
STATE_FILE = "seen_posts.json"

# 청년안심주택
SOCO_BASE    = "https://soco.seoul.go.kr"
SOCO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
SCAN_RANGE   = 30
MAX_WORKERS  = 10
TIMEOUT      = 7

# Elyes - 최신 공고 URL로 업데이트 (2026.04.03 공고)
ELYES_BASE    = "https://www.elyes.co.kr"
# 최신 알려진 공고: [문래 롯데캐슬] 재임대 모집공고(공고일 26.04.03)
ELYES_SEED    = f"{ELYES_BASE}/post/recruit/detail?i_sNtCode=BHCT&nt_idx=Zi4TSTH9NEy7%2FLHaBpX8xg%3D%3D"
ELYES_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": f"{ELYES_BASE}/post/recruit",
}

# ══════════════════════════════════════════════════════
# 상태 관리
# ══════════════════════════════════════════════════════
DEFAULT_STATE = {
    "max_scanned": 6560,
    "known_posts": [],   # 청년안심주택
    "known_elyes": [],   # Elyes (최신순)
}

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "max_scanned" in data:
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
                res = requests.post(api_url, json={"chat_id": chat_id, "text": message}, timeout=15)
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
        return board_id, {"uid": str(board_id), "title": title,
                          "post_date": post_date, "apply_date": apply_date, "url": url}
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

    if new_posts:
        for p in new_posts:
            date_info = "\n".join(filter(None, [
                f"📅 공고게시일: {p['post_date']}" if p.get("post_date") else "",
                f"📝 청약신청일: {p['apply_date']}" if p.get("apply_date") else "",
            ]))
            send_telegram(f"🏠 청년안심주택 민간임대 새 공고!\n\n📌 {p['title']}\n{date_info}\n🔗 {p['url']}")
    else:
        send_telegram("새로운 청년안심주택 공고가 없습니다😭 내일 다시 확인해보겠습니다!")

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

# ▶ 초기 seed: 현재 알려진 최신 5개 공고 (2026.06 기준)
# known_elyes가 비어있을 때만 사용. 이후엔 저장된 값 사용.
ELYES_INITIAL_POSTS = [
    {
        "uid": "elyes_init_1",
        "title": "[문래 롯데캐슬] '26.06.12 재임대 모집공고 접수 현황",
        "url": "https://www.elyes.co.kr/post/recruit/detail?i_sNtCode=BHCT&nt_idx=b%2FxAiqz2C4UJNuFB7vQolQ%3D%3D",
    },
    {
        "uid": "elyes_init_2",
        "title": "[어바니엘 충정로] 공실세대 모집공고 (공고일:'26.06.12)",
        "url": "https://www.elyes.co.kr/post/recruit/detail?i_sNtCode=BHCT&nt_idx=ihUmvn8bxNDfDtFoe71FLw%3D%3D",
    },
    {
        "uid": "elyes_init_3",
        "title": "[문래 롯데캐슬] 재임대 모집공고(공고일 26.06.12)",
        "url": "https://www.elyes.co.kr/post/recruit/detail?i_sNtCode=BHCT&nt_idx=Cty4c%2BDn2blzO5r7d%2BFvTQ%3D%3D",
    },
    {
        "uid": "elyes_init_4",
        "title": "[어바니엘 한강] 공실세대 모집공고 (접수기간: 6/12~6/14)",
        "url": "https://www.elyes.co.kr/post/recruit/detail?i_sNtCode=BHCT&nt_idx=1QJmyPj7ghzcp3ohDJkWDg%3D%3D",
    },
    {
        "uid": "elyes_init_5",
        "title": "[어바니엘 가산] 공실세대 모집공고 (접수기간: 6/12~6/14)",
        "url": "https://www.elyes.co.kr/post/recruit/detail?i_sNtCode=BHCT&nt_idx=pITb9ZqnOyVGKWtLY2Py7w%3D%3D",
    },
]

def make_elyes_uid(url):
    m = re.search(r"nt_idx=([^&]+)", url)
    return f"elyes_{m.group(1)[:25]}" if m else f"elyes_{url[-25:]}"

def fetch_elyes_page(url):
    """
    단일 페이지 요청 → (실제URL, 제목, 이전글URL)
    이전글 = 더 최신 공고
    """
    try:
        res = requests.get(url, headers=ELYES_HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if res.status_code != 200:
            return url, None, None
        actual_url = res.url
        soup = BeautifulSoup(res.text, "html.parser")

        # 제목: YYYY.MM.DD 날짜 직전 줄
        title = None
        lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if re.match(r"\d{4}\.\d{2}\.\d{2}$", line) and i > 0:
                candidate = lines[i - 1]
                if len(candidate) > 5 and candidate not in ["모집공고", "공지사항", "이용안내", "홈"]:
                    title = candidate
                    break

        # 이전글 링크 (더 최신)
        prev_url = None
        page_text = soup.get_text("\n")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "nt_idx" not in href:
                continue
            full = href if href.startswith("http") else f"{ELYES_BASE}{href}"
            link_text = a.get_text(strip=True)
            if len(link_text) < 3:
                continue
            # 페이지 텍스트에서 링크 제목 위치 앞에 "이전글" 있는지 확인
            pos = page_text.find(link_text[:20])
            if pos > 50:
                context = page_text[max(0, pos - 100):pos]
                if "이전글" in context:
                    prev_url = full
                    break

        return actual_url, title, prev_url

    except Exception as e:
        print(f"  [Elyes] 파싱 오류: {e}")
        return url, None, None

def monitor_elyes(state):
    known_elyes = state.get("known_elyes", [])

    # 초기 실행: seed 주입
    if not known_elyes:
        print("  [Elyes] 초기 실행: seed 공고 주입")
        known_elyes = ELYES_INITIAL_POSTS
        state["known_elyes"] = known_elyes

    seen_uids = {p["uid"] for p in known_elyes}

    # 최신 공고에서 이전글(더 최신) 방향으로 최대 10개 탐색
    # 매일 공고가 수개씩 올라오므로 10개면 충분
    start_url = known_elyes[0]["url"]
    print(f"  [Elyes] 최신 탐색 시작: {start_url[:60]}")

    new_posts = []
    current = start_url
    visited = set()

    for _ in range(10):
        actual_url, title, prev_url = fetch_elyes_page(current)
        uid = make_elyes_uid(actual_url)

        if uid in seen_uids:
            # 이미 아는 공고 → 여기서 중단
            break

        if title:
            new_posts.append({"uid": uid, "title": title, "url": actual_url})
            seen_uids.add(uid)
            print(f"  [Elyes] 새 공고: {title}")

        if not prev_url or prev_url in visited:
            break
        visited.add(current)
        current = prev_url

    # 알림 전송
    if new_posts:
        for p in reversed(new_posts):
            send_telegram(f"🏢 Elyes 새 모집공고!\n\n📌 {p['title']}\n🔗 {p['url']}")
        state["known_elyes"] = new_posts + known_elyes
    else:
        send_telegram("새로운 Elyes 공고가 없습니다😭 내일 다시 확인해보겠습니다!")
        print("  [Elyes] 새 공고 없음")
        state["known_elyes"] = known_elyes  # ← 버그 수정: 항상 저장

    # Newest 5 항상 전송 (known_elyes 기준)
    recent = state["known_elyes"][:5]
    if recent:
        lines = ["📋 최근 Elyes 공고 Newest 5\n"]
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
