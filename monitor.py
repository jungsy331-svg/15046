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
LIST_URL = f"{BASE_URL}/youth/bbs/BMSR00015/list.do"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/youth/bbs/BMSR00015/list.do?menuNo=400008",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

# ── 목록 페이지에서 민간임대 상위 5개 파싱 ────────────
def fetch_top5():
    """
    여러 파라미터 조합으로 시도해서 목록 데이터 획득
    성공 시 상위 5개 민간임대 공고 반환
    """
    param_variants = [
        {"menuNo": "400008", "pageIndex": "1", "searchCondition": "", "searchKeyword": "", "bbsId": "BMSR00015"},
        {"menuNo": "400008", "pageIndex": "1"},
        {"menuNo": "400008"},
    ]

    for params in param_variants:
        try:
            res = requests.get(LIST_URL, params=params, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.select("table tbody tr")
            if not rows:
                continue

            results = []
            for row in rows:
                if len(results) >= 5:
                    break
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue
                # 구분 컬럼에서 민간 필터
                category = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                if "민간" not in category:
                    continue
                title_tag = cols[2].find("a") if len(cols) > 2 else None
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")
                post_date = cols[3].get_text(strip=True) if len(cols) > 3 else ""
                apply_date = cols[4].get_text(strip=True) if len(cols) > 4 else ""
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href

                # boardId 추출
                board_id = ""
                m = re.search(r"boardId=(\d+)", href)
                if m:
                    board_id = m.group(1)

                results.append({
                    "uid": board_id or title,
                    "title": title,
                    "post_date": post_date,
                    "apply_date": apply_date,
                    "url": full_url,
                })

            if results:
                print(f"  → 목록 파싱 성공: {len(results)}건")
                return results

        except Exception as e:
            print(f"  → 파싱 실패: {e}")
            continue

    print("  → 목록 파싱 전부 실패")
    return []


# ── 상태 로드/저장 ─────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "seen_uids" not in data:
            return {"seen_uids": []}
        return data
    return {"seen_uids": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

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

# ── 공고 메시지 포맷 ───────────────────────────────────
def format_post(p):
    post_date_line = f"📅 공고게시일: {p['post_date']}" if p['post_date'] else ""
    apply_date_line = f"📝 청약신청일: {p['apply_date']}" if p['apply_date'] else ""
    date_info = "\n".join(filter(None, [post_date_line, apply_date_line]))
    return f"📌 {p['title']}\n{date_info}\n🔗 {p['url']}"

# ── 메인 ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] 모니터링 시작...")

    state = load_state()
    seen_uids = set(state["seen_uids"])

    # 목록 상위 5개 (민간임대) 가져오기
    top5 = fetch_top5()

    if not top5:
        send_telegram("⚠️ 공고 목록을 불러오지 못했습니다. 사이트를 직접 확인해주세요.\n🔗 https://soco.seoul.go.kr/youth/bbs/BMSR00015/list.do?menuNo=400008")
        return

    # 새 공고 감지
    new_posts = [p for p in top5 if p["uid"] not in seen_uids]

    if new_posts:
        for p in new_posts:
            msg = f"🏠 청년안심주택 민간임대 새 공고!\n\n{format_post(p)}"
            send_telegram(msg)
            seen_uids.add(p["uid"])
    else:
        send_telegram("새로운 공고가 없습니다😭 내일 다시 확인해보겠습니다!")

    # 항상: 최신 5개 공고 전송
    lines = ["📋 최근 민간임대 공고 Newest 5\n"]
    for i, p in enumerate(top5, 1):
        post_date_line = f"📅 공고게시일: {p['post_date']}" if p['post_date'] else ""
        apply_date_line = f"📝 청약신청일: {p['apply_date']}" if p['apply_date'] else ""
        date_info = "  |  ".join(filter(None, [post_date_line, apply_date_line]))
        lines.append(f"{i}. {p['title']}\n{date_info}\n🔗 {p['url']}\n")
    send_telegram("\n".join(lines))

    state["seen_uids"] = list(seen_uids)
    save_state(state)
    print(f"[{datetime.now()}] 완료")

if __name__ == "__main__":
    main()
