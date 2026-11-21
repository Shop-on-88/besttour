#!/usr/bin/env python3
"""베스트여행사 API 서버 (표준 라이브러리만 사용)."""
import hashlib, hmac, json, os, re, secrets, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BEST_DATA", "/root/besttour-data")
PORT = int(os.environ.get("BEST_PORT", "8091"))
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "best.db")

RULE = {"signup": 3000, "rate": 0.02, "minUse": 1000, "maxRatio": 0.3, "review": 1000}
SESSION_DAYS = 30
LOCK = threading.RLock()
UPLOADS = os.path.join(DATA, "uploads")
os.makedirs(UPLOADS, exist_ok=True)
PARTS = {}  # 나눠 올리는 이미지 조각

DEFAULT_SITE = {"company": "베스트여행사", "ceo": "임상수", "bizNo": "202-17-96610", "tourNo": "제2026-000002호",
                "address": "충청북도 괴산군 괴산읍 읍내로 322", "phone": "", "email": "", "kakao": "", "hours": "평일 09:00 - 18:00"}
DEFAULT_CATS = [{"name": "패키지여행", "short": "패키지", "img": "ph/1559592413-7cec4d0cae2b.jpg"},
                {"name": "해외 유명관광명소", "short": "해외명소", "img": "ph/1502602898657-3e91760cbb34.jpg"},
                {"name": "신혼여행", "short": "신혼", "img": "ph/1537996194471-e657df975ab4.jpg"},
                {"name": "효도여행", "short": "효도", "img": "ph/1493976040374-85c8e12f0c0e.jpg"},
                {"name": "국내여행", "short": "국내", "img": "ph/1507525428034-b723cf961d3e.jpg"}]
DEFAULT_BANNERS = [
    ["1514282401047-d79a71a590e8", "신혼여행", "둘만의 시간, ", "허니문", "몰디브 · 발리 · 하와이 · 칸쿤", "신혼여행"],
    ["1559592413-7cec4d0cae2b", "패키지여행", "가까운 곳부터, ", "패키지여행", "다낭 · 오사카 · 대만 · 방콕", "패키지여행"],
    ["1493976040374-85c8e12f0c0e", "효도여행", "부모님과 함께, ", "효도여행", "규슈 온천 · 교토 · 장가계 · 백두산", "효도여행"],
    ["1562680802-9cf8b15f419d", "국내여행", "이번 주말엔, ", "국내여행", "제주 · 강릉 · 부산 · 경주", "국내여행"],
    ["1570077188670-e3a8d69ac5ff", "해외 유명관광명소", "한 번쯤은, ", "세계 명소", "산토리니 · 파리 · 스위스 · 싱가포르", "해외 유명관광명소"]]


DEFAULT_HOME = {
    "logo": "베스트여행사", "logoSub": "BEST TOUR", "notice": "카카오톡 채널 추가하고 여행 혜택 알림 받기",
    "show": {"quick": True, "popular": True, "featured": True, "perks": True, "hotels": True, "countries": True, "list": True, "reviews": True},
    "popularTitle": "인기 여행지", "popularSub": "많이 찾는 지역부터 둘러보세요. 가격은 상담으로 안내해 드립니다.", "popularIds": [],
    "featuredTitle": "베스트여행사 추천 여행", "featuredId": "",
    "hotelsTitle": "전 세계 5성급 호텔", "hotelsSub": "인터넷 최저가로 비교해 드립니다. 원하는 호텔을 상담으로 바로 문의하세요.",
    "countriesTitle": "나라별 여행 정보", "countriesSub": "관광 명소부터 안전정보까지 한눈에 확인하세요.",
    "listTitle": "전체 여행 상품", "hotelsNote": "", "reviewsTitle": "고객 여행 후기", "reviewsSub": "베스트여행사와 함께 다녀온 회원님들의 이야기입니다."}
DEFAULT_LIMITS = {"gapMin": 10, "perDay": 3, "pending": 2, "minLen": 20}


def defaults(st):
    st.setdefault("home", json.loads(json.dumps(DEFAULT_HOME)))
    for k, v in DEFAULT_HOME.items():
        st["home"].setdefault(k, v)
    for k, v in DEFAULT_HOME["show"].items():
        st["home"]["show"].setdefault(k, v)
    st.setdefault("rule", {})
    for k, v in RULE.items():
        st["rule"].setdefault(k, v)
    RULE.update(st["rule"])
    # 카테고리 이름 통일: 배너가 없어진 카테고리를 가리키면 지금 카테고리로 바꿈
    if not st.get("catfix1") and st.get("categories"):
        names = [c["name"] for c in st["categories"]]
        old_map = {"패키지여행": "해외여행", "해외 유명관광명소": "해외여행", "신혼여행": "허니문", "효도여행": "해외여행"}
        for b in st.get("banners", []):
            if b.get("linkType") == "cat" and b.get("link") not in names:
                new = old_map.get(b.get("link"))
                new = new if new in names else names[0]
                if b.get("badge") == b.get("link"): b["badge"] = new
                if b.get("title2") == b.get("link"): b["title2"] = new
                b["link"] = new
        for c in st["categories"]:
            c["short"] = c["name"]
        st["catfix1"] = True
    if not st.get("catfix2") and st.get("categories"):
        names = [c["name"] for c in st["categories"]]
        old_map = {"패키지여행": "해외여행", "해외 유명관광명소": "해외여행", "신혼여행": "허니문", "효도여행": "해외여행"}
        for b in st.get("banners", []):
            bd = b.get("badge")
            new = bd if bd in names else old_map.get(bd)
            if b.get("linkType") == "cat" and new in names:
                if b.get("title2") == bd and bd not in names and bd != "효도여행": b["title2"] = new
                b["link"], b["badge"] = new, new
        st["catfix2"] = True
    if not st.get("seatfix1"):
        for b in st.get("bookings", []):
            if b.get("status") == "pending" and "seatsTaken" not in b:
                p = next((x for x in st.get("products", []) if x["id"] == b["productId"]), None)
                if p: p["seats"] = p.get("seats", 0) + b["people"]
                b["seatsTaken"] = False
        st["seatfix1"] = True
    st.setdefault("limits", dict(DEFAULT_LIMITS))
    for k, v in DEFAULT_LIMITS.items():
        st["limits"].setdefault(k, v)
    st.setdefault("reviews", [])
    st.setdefault("site", dict(DEFAULT_SITE))
    for k, v in DEFAULT_SITE.items():
        st["site"].setdefault(k, v)
    st.setdefault("categories", [dict(c) for c in DEFAULT_CATS])
    if "banners" not in st:
        st["banners"] = [{"id": "bn%d" % i, "img": "ph/bn_%s.jpg" % b[0], "badge": b[1], "title1": b[2], "title2": b[3],
                          "sub": b[4], "linkType": "cat", "link": b[5], "button": "상품 보기", "active": True}
                         for i, b in enumerate(DEFAULT_BANNERS)]
    return st
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def now_ms():
    return int(time.time() * 1000)


def uid(p):
    return p + format(now_ms(), "x") + secrets.token_hex(3)


def pw_hash(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200000).hex()
    return "pbkdf2$%s$%s" % (salt, h)


def pw_ok(pw, stored):
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(pw_hash(pw, salt).split("$")[2], h)


# ---------------- 저장소 ----------------
def db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, member TEXT, exp INTEGER)")
    return c


def load():
    with db() as c:
        row = c.execute("SELECT v FROM kv WHERE k='state'").fetchone()
    if row:
        return defaults(json.loads(row[0]))
    with open(os.path.join(HERE, "products_seed.json"), encoding="utf-8") as f:
        products = json.load(f)
    admin_pw = secrets.token_urlsafe(9)
    st = {"members": [{"id": "m0", "name": "관리자", "email": "admin@besttour", "phone": "",
                       "pw": pw_hash(admin_pw), "role": "admin", "joined": now_ms(), "status": "active"}],
          "products": products, "bookings": [], "ledger": [], "audit": [], "reviews": []}
    defaults(st)
    with open(os.path.join(DATA, "admin.txt"), "w") as f:
        f.write("관리자 아이디: admin@besttour\n관리자 비밀번호: %s\n" % admin_pw)
    os.chmod(os.path.join(DATA, "admin.txt"), 0o600)
    save(st)
    return st


def save(st):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('state', ?)", (json.dumps(st, ensure_ascii=False),))


S = None


def member(mid):
    return next((m for m in S["members"] if m["id"] == mid), None)


def product(pid):
    return next((p for p in S["products"] if p["id"] == pid), None)


def balance(mid):
    return sum(l["amount"] for l in S["ledger"] if l["memberId"] == mid)


def add_ledger(mid, typ, amount, reason, **extra):
    l = {"id": uid("l"), "memberId": mid, "type": typ, "amount": amount, "reason": reason, "at": now_ms(), "by": "system"}
    l.update(extra)
    S["ledger"].append(l)
    return l


def audit(text):
    S["audit"].insert(0, {"id": uid("a"), "at": now_ms(), "text": text})
    del S["audit"][2000:]


def won(n):
    return format(int(n), ",")


def pub_member(m):
    return {k: v for k, v in m.items() if k != "pw"}


def mask(name):
    name = name or ""
    if len(name) <= 1:
        return name + "*"
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


def view(me):
    """로그인 상태에 맞는 데이터만 내려줌."""
    pub_reviews = [{"id": r["id"], "productId": r.get("productId"), "rating": r["rating"], "text": r["text"],
                    "name": mask((member(r["memberId"]) or {}).get("name", "고객")), "at": r["at"]}
                   for r in S["reviews"] if r["status"] == "approved"]
    pub_reviews.sort(key=lambda r: -r["at"])
    out = {"rule": RULE, "products": S["products"], "publicReviews": pub_reviews[:60], "meId": None,
           "site": S["site"], "categories": S["categories"], "banners": S["banners"], "home": S["home"], "limits": S["limits"],
           "hotels": S.get("hotels"), "countries": S.get("countries"), "quick": S.get("quick"),
           "members": [], "bookings": [], "ledger": [], "audit": [], "reviews": []}
    if not me:
        return out
    out["meId"] = me["id"]
    if me["role"] == "admin":
        out["members"] = [pub_member(m) for m in S["members"]]
        out["bookings"] = S["bookings"]
        out["ledger"] = S["ledger"]
        out["audit"] = S["audit"][:500]
        out["reviews"] = S["reviews"]
    else:
        out["members"] = [pub_member(me)]
        out["bookings"] = [b for b in S["bookings"] if b["memberId"] == me["id"]]
        out["ledger"] = [l for l in S["ledger"] if l["memberId"] == me["id"]]
        out["reviews"] = [r for r in S["reviews"] if r["memberId"] == me["id"]]
    return out


class Err(Exception):
    def __init__(self, msg, field="err"):
        super().__init__(msg)
        self.msg, self.field = msg, field


def to_int(v):
    s = str(v if v is not None else "").replace(",", "").strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    raise Err("숫자로 입력해 주세요.")


# ---------------- 동작 ----------------
def need_admin(me):
    if not me or me["role"] != "admin":
        raise Err("관리자만 할 수 있습니다.")


def need_member(me):
    if not me:
        raise Err("로그인이 필요합니다.")


def a_signup(me, d, ctx):
    name, email, phone = (d.get("name") or "").strip(), (d.get("email") or "").strip(), (d.get("phone") or "").strip()
    pw, pw2 = d.get("pw") or "", d.get("pw2") or ""
    if len(name) < 2 or len(name) > 30: raise Err("이름을 2자 이상 입력해 주세요.")
    if not re.fullmatch(r"[0-9\-+ ]{9,20}", phone): raise Err("연락처를 숫자로 입력해 주세요. 예: 010-0000-0000")
    if not EMAIL_RE.match(email) or len(email) > 100: raise Err("이메일 형식이 올바르지 않습니다.")
    if any(m["email"].lower() == email.lower() for m in S["members"]): raise Err("이미 가입된 이메일입니다. 로그인해 주세요.")
    if len(pw) < 8: raise Err("비밀번호는 8자 이상이어야 합니다.")
    if pw != pw2: raise Err("비밀번호 확인이 일치하지 않습니다.")
    if not d.get("agree"): raise Err("약관에 동의해야 가입할 수 있습니다.")
    m = {"id": uid("m"), "name": name, "email": email, "phone": phone, "pw": pw_hash(pw),
         "role": "member", "joined": now_ms(), "status": "active"}
    S["members"].append(m)
    add_ledger(m["id"], "signup", RULE["signup"], "가입 축하 포인트")
    ctx["login"] = m["id"]
    return m, "가입 완료. %sP가 지급되었습니다" % won(RULE["signup"])


def a_login(me, d, ctx):
    email = (d.get("email") or "").strip().lower()
    m = next((x for x in S["members"] if x["email"].lower() == email), None)
    if not m or not pw_ok(d.get("pw") or "", m["pw"]): raise Err("이메일 또는 비밀번호가 맞지 않습니다. 다시 확인해 주세요.")
    if m["status"] != "active": raise Err("이용이 정지된 계정입니다. 고객센터로 문의해 주세요.")
    ctx["login"] = m["id"]
    return m, m["name"] + " 님, 환영합니다"


def a_logout(me, d, ctx):
    ctx["logout"] = True
    return None, "로그아웃했습니다"


def a_password(me, d, ctx):
    need_member(me)
    if not pw_ok(d.get("old") or "", me["pw"]): raise Err("현재 비밀번호가 맞지 않습니다.")
    if len(d.get("pw") or "") < 8: raise Err("새 비밀번호는 8자 이상이어야 합니다.")
    if d.get("pw") != d.get("pw2"): raise Err("새 비밀번호 확인이 일치하지 않습니다.")
    me["pw"] = pw_hash(d["pw"])
    return me, "비밀번호를 바꿨습니다"


def p_earn(p, pay, people):
    t, v = p.get("earnType") or "default", int(p.get("earnValue") or 0)
    if t == "none": return 0
    if t == "fixed": return v * people
    if t == "rate": return int(pay * v / 100)
    return int(pay * RULE["rate"])


def a_booking(me, d, ctx):
    need_member(me)
    p = product(d.get("productId"))
    if not p or not p.get("active") or not p.get("price"): raise Err("예약할 수 없는 상품입니다.")
    n, use, depart, total = to_int(d.get("people")), to_int(d.get("use") or 0), to_int(d.get("depart") or 0), to_int(d.get("total"))
    bal = balance(me["id"])
    if not depart: raise Err("출발 날짜를 먼저 선택해 주세요.")
    if n < 1 or n > 20: raise Err("인원을 확인해 주세요.")
    if n > p["seats"]: raise Err("잔여석이 %d석뿐입니다. 인원을 줄여 주세요." % p["seats"])
    if total < p["price"] * n * 0.5 or total > p["price"] * n * 2: raise Err("상품 금액을 다시 확인해 주세요.")
    if use < 0: raise Err("사용할 포인트를 숫자로 입력해 주세요.")
    if use > bal: raise Err("보유 포인트(%sP)보다 많이 쓸 수 없습니다." % won(bal))
    if 0 < use < RULE["minUse"]: raise Err("포인트는 %sP 이상부터 쓸 수 있습니다." % won(RULE["minUse"]))
    cap = int(total * RULE["maxRatio"])
    if use > cap: raise Err("이 예약에서는 최대 %sP까지 쓸 수 있습니다." % won(cap))
    pay = total - use
    earn = p_earn(p, pay, n)
    b = {"id": uid("b"), "memberId": me["id"], "productId": p["id"], "people": n, "total": total, "used": use,
         "pay": pay, "earned": earn, "status": "pending", "at": now_ms(), "depart": depart}
    b["seatsTaken"] = False
    S["bookings"].append(b)
    if use > 0: add_ledger(me["id"], "use", -use, "예약 시 사용", bookingId=b["id"])
    return b, "예약 신청이 접수되었습니다. 담당자 확인 후 확정되면 %sP가 적립됩니다" % won(earn)


def a_booking_confirm(me, d, ctx):
    need_admin(me)
    b = next((x for x in S["bookings"] if x["id"] == d.get("id")), None)
    if not b: raise Err("예약을 찾을 수 없습니다.")
    if b["status"] != "pending": raise Err("확정 대기 중인 예약만 확정할 수 있습니다.")
    p = product(b["productId"])
    if p and b.get("seatsTaken") is False:
        if p["seats"] < b["people"]: raise Err("잔여석이 %d석뿐이라 %d명 예약을 확정할 수 없습니다. 상품의 잔여석을 늘리거나 예약을 취소하세요." % (p["seats"], b["people"]))
        p["seats"] -= b["people"]
        b["seatsTaken"] = True
    b["status"], b["confirmedAt"] = "confirmed", now_ms()
    if b["earned"] > 0 and not b.get("earnPaid"):
        p = product(b["productId"])
        add_ledger(b["memberId"], "earn", b["earned"], "예약 확정 적립" + (" (" + p["name"] + ")" if p else ""), bookingId=b["id"])
        b["earnPaid"] = True
    x = member(b["memberId"])
    audit("%s 회원 예약 확정, %sP 적립" % (x["name"] if x else "-", won(b["earned"])))
    return b, "예약을 확정했습니다. %sP 적립" % won(b["earned"])


def a_booking_cancel(me, d, ctx):
    need_member(me)
    b = next((x for x in S["bookings"] if x["id"] == d.get("id")), None)
    if not b or (me["role"] != "admin" and b["memberId"] != me["id"]): raise Err("예약을 찾을 수 없습니다.")
    if b["status"] not in ("confirmed", "pending"): raise Err("이미 처리된 예약입니다.")
    revoke = b["earned"] if (b["status"] == "confirmed" and b.get("earnPaid", True)) else 0
    if balance(b["memberId"]) + b["used"] - revoke < 0:
        raise Err("적립 포인트를 이미 사용해 회수할 잔액이 부족합니다. 관리자에게 문의하세요.")
    b["status"], b["cancelledAt"] = "cancelled", now_ms()
    if b["used"] > 0: add_ledger(b["memberId"], "refund", b["used"], "예약 취소로 사용 포인트 복원", bookingId=b["id"])
    if revoke > 0: add_ledger(b["memberId"], "revoke", -revoke, "예약 취소로 적립 포인트 회수", bookingId=b["id"])
    p = product(b["productId"])
    if p and b.get("seatsTaken", True):
        p["seats"] += b["people"]
    b["seatsTaken"] = False
    if me["role"] == "admin":
        x = member(b["memberId"])
        audit("%s 회원 예약 취소 처리, 사용 %sP 복원, 적립 %sP 회수" % (x["name"] if x else "-", won(b["used"]), won(b["earned"])))
    return b, "예약을 취소했습니다"


def a_review(me, d, ctx):
    need_member(me)
    text = re.sub(r"\s+", " ", (d.get("text") or "")).strip()
    rating = to_int(d.get("rating") or 5)
    pid = d.get("productId") or None
    bought = {b["productId"] for b in S["bookings"] if b["memberId"] == me["id"] and b["status"] == "confirmed"}
    if not bought: raise Err("예약이 확정된 상품이 있어야 후기를 쓸 수 있습니다.")
    if not pid or pid not in bought or not product(pid): raise Err("후기를 쓸 상품을 골라 주세요. 예약이 확정된 상품만 후기를 쓸 수 있습니다.")
    if any(r["memberId"] == me["id"] and r.get("productId") == pid and r["status"] != "rejected" for r in S["reviews"]):
        raise Err("이 상품은 이미 후기를 쓰셨습니다.")
    if not 1 <= rating <= 5: raise Err("별점을 골라 주세요.")
    LM = S["limits"]
    if len(text) < LM["minLen"]: raise Err("후기는 %d자 이상 써 주세요." % LM["minLen"])
    if len(text) > 1000: raise Err("후기는 1,000자까지 쓸 수 있습니다.")
    mine = [r for r in S["reviews"] if r["memberId"] == me["id"]]
    t = now_ms()
    if LM["gapMin"] and any(t - r["at"] < LM["gapMin"] * 60000 for r in mine): raise Err("후기는 %d분에 한 번만 쓸 수 있습니다. 잠시 후 다시 써 주세요." % LM["gapMin"])
    if LM["perDay"] and sum(1 for r in mine if t - r["at"] < 86400000) >= LM["perDay"]: raise Err("후기는 하루 %d개까지 쓸 수 있습니다." % LM["perDay"])
    if LM["pending"] and sum(1 for r in mine if r["status"] == "pending") >= LM["pending"]: raise Err("승인을 기다리는 후기가 있습니다. 승인된 뒤에 다시 써 주세요.")
    norm = re.sub(r"[\s\W_]+", "", text)
    if any(re.sub(r"[\s\W_]+", "", r["text"]) == norm for r in S["reviews"]): raise Err("이미 같은 내용의 후기가 있습니다.")
    if re.search(r"(.)\1{9,}", text): raise Err("같은 글자를 반복한 후기는 등록할 수 없습니다.")
    r = {"id": uid("r"), "memberId": me["id"], "productId": pid, "rating": rating, "text": text,
         "status": "pending", "at": t, "points": 0}
    S["reviews"].append(r)
    return r, "후기를 보냈습니다. 관리자 승인 후 게시되고 포인트가 지급됩니다"


def a_review_approve(me, d, ctx):
    need_admin(me)
    r = next((x for x in S["reviews"] if x["id"] == d.get("id")), None)
    if not r: raise Err("후기를 찾을 수 없습니다.")
    if r["status"] == "approved": raise Err("이미 승인된 후기입니다.")
    pts = to_int(d.get("points") or 0)
    if pts < 0 or pts > 1000000: raise Err("지급 포인트를 확인해 주세요.")
    x = member(r["memberId"])
    r["status"], r["decidedAt"], r["points"] = "approved", now_ms(), pts
    if pts > 0 and x and not r.get("paid"):
        add_ledger(x["id"], "review", pts, "후기 작성 포인트", reviewId=r["id"], by=me["id"])
        r["paid"] = True
    audit("%s 회원 후기 승인%s" % (x["name"] if x else "-", ", %sP 지급" % won(pts) if pts > 0 else ""))
    return r, "후기를 승인했습니다" + (" (%sP 지급)" % won(pts) if pts > 0 else "")


def a_review_reject(me, d, ctx):
    need_admin(me)
    r = next((x for x in S["reviews"] if x["id"] == d.get("id")), None)
    if not r: raise Err("후기를 찾을 수 없습니다.")
    x = member(r["memberId"])
    r["status"], r["decidedAt"] = "rejected", now_ms()
    audit("%s 회원 후기 %s" % (x["name"] if x else "-", "게시 중단" if r.get("paid") else "반려"))
    return r, "후기를 반려했습니다"


def a_review_delete(me, d, ctx):
    need_admin(me)
    r = next((x for x in S["reviews"] if x["id"] == d.get("id")), None)
    if not r: raise Err("후기를 찾을 수 없습니다.")
    S["reviews"].remove(r)
    x = member(r["memberId"])
    audit("%s 회원 후기 삭제" % (x["name"] if x else "-"))
    return None, "후기를 삭제했습니다"


def a_adjust(me, d, ctx):
    need_admin(me)
    x = member(d.get("memberId"))
    if not x: raise Err("회원을 찾을 수 없습니다.")
    mode, why, bal = d.get("mode"), (d.get("reason") or "").strip(), balance(x["id"])
    try:
        amt = to_int(d.get("amount"))
    except Err:
        raise Err("포인트를 0보다 큰 숫자로 입력해 주세요.")
    if amt < 0 or (mode != "set" and amt == 0): raise Err("포인트를 0보다 큰 숫자로 입력해 주세요.")
    if not why: raise Err("사유를 입력해 주세요. 회원 포인트 내역에 그대로 표시됩니다.")
    delta = amt if mode == "add" else -amt if mode == "sub" else amt - bal
    if delta == 0: raise Err("현재 잔액과 같아서 바뀌는 것이 없습니다.")
    if bal + delta < 0: raise Err("차감 후 잔액이 0보다 작아집니다. 현재 잔액은 %sP입니다." % won(bal))
    add_ledger(x["id"], "admin", delta, why, by=me["id"])
    audit("%s 회원 포인트 %s: %s%sP, 잔액 %sP → %sP (%s)" % (
        x["name"], {"add": "추가", "sub": "차감"}.get(mode, "잔액 변경"), "+" if delta > 0 else "", won(delta), won(bal), won(bal + delta), why))
    return None, "적용했습니다. 잔액 %sP" % won(bal + delta)


def a_ledger_edit(me, d, ctx):
    need_admin(me)
    l = next((x for x in S["ledger"] if x["id"] == d.get("id")), None)
    if not l: raise Err("내역을 찾을 수 없습니다.")
    try:
        amt = to_int(d.get("amount"))
    except Err:
        raise Err("변동 포인트를 0이 아닌 숫자로 입력해 주세요.")
    why, bal, x = (d.get("reason") or "").strip(), balance(l["memberId"]), member(l["memberId"])
    if amt == 0: raise Err("변동 포인트를 0이 아닌 숫자로 입력해 주세요.")
    if not why: raise Err("사유를 입력해 주세요.")
    if bal - l["amount"] + amt < 0: raise Err("이렇게 바꾸면 잔액이 0보다 작아집니다. 현재 잔액은 %sP입니다." % won(bal))
    audit("%s 회원 포인트 내역 변경: 「%s」 %sP → 「%s」 %sP" % (x["name"] if x else "-", l["reason"], won(l["amount"]), why, won(amt)))
    l["amount"], l["reason"], l["editedAt"] = amt, why, now_ms()
    return None, "내역을 변경했습니다"


def a_ledger_delete(me, d, ctx):
    need_admin(me)
    l = next((x for x in S["ledger"] if x["id"] == d.get("id")), None)
    if not l: raise Err("내역을 찾을 수 없습니다.")
    bal, x = balance(l["memberId"]), member(l["memberId"])
    if bal - l["amount"] < 0: raise Err("이 내역을 지우면 잔액이 0보다 작아집니다. 현재 잔액은 %sP입니다." % won(bal))
    S["ledger"].remove(l)
    audit("%s 회원 포인트 내역 삭제: 「%s」 %s%sP, 잔액 %sP → %sP" % (
        x["name"] if x else "-", l["reason"], "+" if l["amount"] > 0 else "", won(l["amount"]), won(bal), won(bal - l["amount"])))
    return None, "내역을 삭제했습니다"


def a_member_status(me, d, ctx):
    need_admin(me)
    x = member(d.get("id"))
    if not x or x["role"] == "admin": raise Err("회원을 찾을 수 없습니다.")
    x["status"] = "suspended" if x["status"] == "active" else "active"
    if x["status"] != "active":
        with db() as c:
            c.execute("DELETE FROM sessions WHERE member=?", (x["id"],))
    audit("%s 회원 %s" % (x["name"], "정지 해제" if x["status"] == "active" else "이용 정지"))
    return None, "변경했습니다"


def a_member_delete(me, d, ctx):
    need_admin(me)
    x = member(d.get("id"))
    if not x or x["role"] == "admin": raise Err("회원을 찾을 수 없습니다.")
    S["members"].remove(x)
    S["ledger"] = [l for l in S["ledger"] if l["memberId"] != x["id"]]
    S["reviews"] = [r for r in S["reviews"] if r["memberId"] != x["id"]]
    with db() as c:
        c.execute("DELETE FROM sessions WHERE member=?", (x["id"],))
    audit("%s 회원 삭제 (%s)" % (x["name"], x["email"]))
    return None, "회원을 삭제했습니다"


def a_member_seen(me, d, ctx):
    need_admin(me)
    me["seenAt"] = now_ms()
    return None, ""


def a_product_save(me, d, ctx):
    need_admin(me)
    v = {"name": (d.get("name") or "").strip(), "cat": d.get("cat") or "패키지여행", "scene": d.get("scene") or "beach",
         "from": (d.get("from") or "").strip().upper(), "to": (d.get("to") or "").strip().upper(), "desc": (d.get("desc") or "").strip(),
         "img": clean_img(d.get("img")),
         "tags": [t for t in (d.get("tags") or []) if isinstance(t, str) and re.fullmatch(r"[\w\-]{1,30}", t)][:20],
         "earnType": d.get("earnType") if d.get("earnType") in ("default", "rate", "fixed", "none") else "default"}
    try:
        v["earnValue"] = max(0, to_int(d.get("earnValue") or 0))
    except Err:
        raise Err("적립 값을 숫자로 입력해 주세요.")
    if v["earnType"] == "rate" and v["earnValue"] > 50: raise Err("적립률은 50% 이하로 입력해 주세요.")
    if v["earnType"] in ("rate", "fixed") and v["earnValue"] == 0: raise Err("적립 값을 입력해 주세요.")
    names = [c["name"] for c in S["categories"]]
    cats = [c for c in (d.get("cats") or []) if c in names]
    if v["cat"] not in names: v["cat"] = cats[0] if cats else (names[0] if names else "")
    if v["cat"] not in cats: cats = [v["cat"]] + cats
    v["cats"] = cats
    for k in ("nights", "days", "price", "seats"):
        try:
            v[k] = max(0, to_int(d.get(k) or 0))
        except Err:
            v[k] = 0
    if len(v["name"]) < 2: raise Err("상품명을 입력해 주세요.")
    if (v["from"] and not re.fullmatch(r"[A-Z]{3}", v["from"])) or (v["to"] and not re.fullmatch(r"[A-Z]{3}", v["to"])):
        raise Err("공항 코드는 영문 3자로 입력해 주세요. 예: ICN")
    pid = d.get("id")
    if pid:
        p = product(pid)
        if not p: raise Err("상품을 찾을 수 없습니다.")
        p.update(v)
        audit("상품 수정: " + v["name"])
    else:
        v.update({"id": uid("p"), "rate": 0, "active": True})
        S["products"].append(v)
        audit("상품 추가: " + v["name"])
    return None, "상품을 저장했습니다"


def a_product_toggle(me, d, ctx):
    need_admin(me)
    p = product(d.get("id"))
    if not p: raise Err("상품을 찾을 수 없습니다.")
    p["active"] = not p.get("active")
    audit("상품 %s: %s" % ("판매 재개" if p["active"] else "판매 중지", p["name"]))
    return None, "변경했습니다"


def a_product_delete(me, d, ctx):
    need_admin(me)
    p = product(d.get("id"))
    if not p: raise Err("상품을 찾을 수 없습니다.")
    S["products"].remove(p)
    audit("상품 삭제: " + p["name"])
    return None, "상품을 삭제했습니다"


def clean_img(v):
    v = (v or "").strip()
    if re.fullmatch(r"(ph/[\w\-]+\.jpg|/api/img/[\w\-]+\.(jpg|png|webp))", v):
        return v
    return ""


def a_upload(me, d, ctx):
    need_admin(me)
    key, part, total, data = str(d.get("key") or ""), to_int(d.get("part")), to_int(d.get("total")), d.get("data") or ""
    if not re.fullmatch(r"[a-z0-9]{8,32}", key) or not 0 <= part < total <= 60 or not re.fullmatch(r"[A-Za-z0-9+/=]*", data):
        raise Err("이미지 업로드 형식이 올바르지 않습니다.")
    PARTS.setdefault(key, {})[part] = data
    if len(PARTS[key]) < total:
        return None, ""
    import base64
    raw = base64.b64decode("".join(PARTS.pop(key)[i] for i in range(total)))
    if raw[:3] == b"\xff\xd8\xff": ext = "jpg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n": ext = "png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP": ext = "webp"
    else: raise Err("JPG, PNG, WEBP 이미지만 올릴 수 있습니다.")
    if len(raw) > 6 * 1024 * 1024: raise Err("이미지가 너무 큽니다.")
    name = "%s.%s" % (uid("i"), ext)
    with open(os.path.join(UPLOADS, name), "wb") as f:
        f.write(raw)
    ctx["img"] = "/api/img/" + name
    return None, ""


def a_settings(me, d, ctx):
    need_admin(me)
    for k in DEFAULT_SITE:
        S["site"][k] = (str(d.get(k) or "")).strip()[:200]
    if not S["site"]["company"]: raise Err("상호를 입력해 주세요.")
    if S["site"]["kakao"] and not re.match(r"^https://", S["site"]["kakao"]): raise Err("카카오톡 상담 주소는 https:// 로 시작해야 합니다.")
    audit("사이트 정보 수정")
    return None, "사이트 정보를 저장했습니다"


def find_cat(name):
    return next((c for c in S["categories"] if c["name"] == name), None)


def a_cat_save(me, d, ctx):
    need_admin(me)
    old, name = (d.get("old") or "").strip(), re.sub(r"\s+", " ", (d.get("name") or "")).strip()
    short = name
    img = clean_img(d.get("img"))
    if not 1 <= len(name) <= 20: raise Err("카테고리 이름을 1~20자로 입력해 주세요.")
    if name == "전체": raise Err("'전체'는 카테고리 이름으로 쓸 수 없습니다.")
    other = find_cat(name)
    if old:
        c = find_cat(old)
        if not c: raise Err("카테고리를 찾을 수 없습니다.")
        if other and other is not c: raise Err("같은 이름의 카테고리가 이미 있습니다.")
        c.update({"name": name, "short": short, "img": img or c.get("img", "")})
        if old != name:
            for p in S["products"]:
                p["cats"] = [name if x == old else x for x in p.get("cats", [p.get("cat")])]
                if p.get("cat") == old: p["cat"] = name
            for b in S["banners"]:
                if b.get("linkType") == "cat" and b.get("link") == old: b["link"] = name
            audit("카테고리 이름 변경: %s → %s" % (old, name))
        else:
            audit("카테고리 수정: " + name)
        return None, "카테고리를 저장했습니다"
    if other: raise Err("같은 이름의 카테고리가 이미 있습니다.")
    if len(S["categories"]) >= 20: raise Err("카테고리는 20개까지 만들 수 있습니다.")
    S["categories"].append({"name": name, "short": short, "img": img})
    audit("카테고리 추가: " + name)
    return None, "카테고리를 추가했습니다"


def a_cat_move(me, d, ctx):
    need_admin(me)
    c = find_cat(d.get("name"))
    if not c: raise Err("카테고리를 찾을 수 없습니다.")
    L = S["categories"]; i = L.index(c); j = i + (1 if d.get("dir") == "down" else -1)
    if 0 <= j < len(L): L[i], L[j] = L[j], L[i]
    return None, ""


def a_cat_delete(me, d, ctx):
    need_admin(me)
    c = find_cat(d.get("name"))
    if not c: raise Err("카테고리를 찾을 수 없습니다.")
    if len(S["categories"]) <= 1: raise Err("카테고리는 최소 1개 있어야 합니다.")
    S["categories"].remove(c)
    first = S["categories"][0]["name"]
    for p in S["products"]:
        cs = [x for x in p.get("cats", [p.get("cat")]) if x != c["name"]]
        p["cats"] = cs or [first]
        if p.get("cat") == c["name"]: p["cat"] = p["cats"][0]
    for b in S["banners"]:
        if b.get("linkType") == "cat" and b.get("link") == c["name"]: b["link"] = first
    audit("카테고리 삭제: %s (상품은 다른 카테고리로 옮김)" % c["name"])
    return None, "카테고리를 삭제했습니다"


def find_banner(bid):
    return next((b for b in S["banners"] if b["id"] == bid), None)


def a_banner_save(me, d, ctx):
    need_admin(me)
    v = {k: (str(d.get(k) or "")).strip()[:80] for k in ("badge", "title1", "title2", "sub", "button")}
    v["img"] = clean_img(d.get("img"))
    v["linkType"] = d.get("linkType") if d.get("linkType") in ("cat", "product", "none") else "none"
    v["link"] = (d.get("link") or "").strip()
    if not v["img"]: raise Err("배너 이미지를 올려 주세요.")
    if not (v["title1"] or v["title2"]): raise Err("배너 제목을 입력해 주세요.")
    if v["linkType"] == "cat" and not find_cat(v["link"]): raise Err("연결할 카테고리를 골라 주세요.")
    if v["linkType"] == "product" and not product(v["link"]): raise Err("연결할 상품을 골라 주세요.")
    v["button"] = v["button"] or "상품 보기"
    bid = d.get("id")
    if bid:
        b = find_banner(bid)
        if not b: raise Err("배너를 찾을 수 없습니다.")
        b.update(v); audit("메인 배너 수정: %s%s" % (v["title1"], v["title2"]))
    else:
        if len(S["banners"]) >= 15: raise Err("배너는 15개까지 넣을 수 있습니다.")
        v.update({"id": uid("bn"), "active": True}); S["banners"].append(v)
        audit("메인 배너 추가: %s%s" % (v["title1"], v["title2"]))
    return None, "배너를 저장했습니다"


def a_banner_move(me, d, ctx):
    need_admin(me)
    b = find_banner(d.get("id"))
    if not b: raise Err("배너를 찾을 수 없습니다.")
    L = S["banners"]; i = L.index(b); j = i + (1 if d.get("dir") == "down" else -1)
    if 0 <= j < len(L): L[i], L[j] = L[j], L[i]
    return None, ""


def a_banner_toggle(me, d, ctx):
    need_admin(me)
    b = find_banner(d.get("id"))
    if not b: raise Err("배너를 찾을 수 없습니다.")
    b["active"] = not b.get("active")
    return None, "배너를 %s" % ("다시 보이게 했습니다" if b["active"] else "숨겼습니다")


def a_banner_delete(me, d, ctx):
    need_admin(me)
    b = find_banner(d.get("id"))
    if not b: raise Err("배너를 찾을 수 없습니다.")
    S["banners"].remove(b); audit("메인 배너 삭제: %s%s" % (b.get("title1", ""), b.get("title2", "")))
    return None, "배너를 삭제했습니다"


def a_home_save(me, d, ctx):
    need_admin(me)
    H = S["home"]
    for k, v in DEFAULT_HOME.items():
        if isinstance(v, str):
            H[k] = (str(d.get(k) if d.get(k) is not None else "")).strip()[:120]
    H["logo"] = H["logo"] or S["site"].get("company") or "베스트여행사"
    sh = d.get("show") or {}
    for k in DEFAULT_HOME["show"]:
        H["show"][k] = bool(sh.get(k))
    H["popularIds"] = [x for x in (d.get("popularIds") or []) if product(x)][:12]
    H["featuredId"] = d.get("featuredId") if product(d.get("featuredId")) else ""
    audit("홈 화면 설정 수정")
    return None, "홈 화면 설정을 저장했습니다"


def a_rules_save(me, d, ctx):
    need_admin(me)
    try:
        signup, rate, minuse, maxr, review = (to_int(d.get(k)) for k in ("signup", "rate", "minUse", "maxRatio", "review"))
        gap, perday, pend, minlen = (to_int(d.get(k)) for k in ("gapMin", "perDay", "pending", "minLen"))
    except Err:
        raise Err("모든 칸을 숫자로 입력해 주세요.")
    if not (0 <= signup <= 1000000 and 0 <= rate <= 50 and 0 <= minuse <= 1000000 and 0 <= maxr <= 100 and 0 <= review <= 1000000):
        raise Err("포인트 규칙 값을 확인해 주세요. (적립률 0~50%, 사용 한도 0~100%)")
    if not (0 <= gap <= 1440 and 0 <= perday <= 100 and 0 <= pend <= 100 and 1 <= minlen <= 500):
        raise Err("후기 제한 값을 확인해 주세요.")
    RULE.update({"signup": signup, "rate": rate / 100.0, "minUse": minuse, "maxRatio": maxr / 100.0, "review": review})
    S["rule"] = dict(RULE)
    S["limits"].update({"gapMin": gap, "perDay": perday, "pending": pend, "minLen": minlen})
    audit("포인트 규칙 변경: 가입 %sP, 적립 %d%%, 최소 사용 %sP, 사용 한도 %d%%, 후기 %sP" % (won(signup), rate, won(minuse), maxr, won(review)))
    return None, "포인트 규칙을 저장했습니다"


def a_member_edit(me, d, ctx):
    need_admin(me)
    x = member(d.get("id"))
    if not x: raise Err("회원을 찾을 수 없습니다.")
    name, email, phone = (d.get("name") or "").strip(), (d.get("email") or "").strip(), (d.get("phone") or "").strip()
    if len(name) < 2 or len(name) > 30: raise Err("이름을 2자 이상 입력해 주세요.")
    if not EMAIL_RE.match(email): raise Err("이메일 형식이 올바르지 않습니다.")
    if any(m is not x and m["email"].lower() == email.lower() for m in S["members"]): raise Err("다른 회원이 쓰는 이메일입니다.")
    if phone and not re.fullmatch(r"[0-9\-+ ]{9,20}", phone): raise Err("연락처를 숫자로 입력해 주세요.")
    audit("회원 정보 수정: %s → %s (%s)" % (x["name"], name, email))
    x.update({"name": name, "email": email, "phone": phone})
    return None, "회원 정보를 저장했습니다"


def a_member_pw(me, d, ctx):
    need_admin(me)
    x = member(d.get("id"))
    if not x: raise Err("회원을 찾을 수 없습니다.")
    pw = d.get("pw") or ""
    if len(pw) < 8: raise Err("새 비밀번호는 8자 이상이어야 합니다.")
    x["pw"] = pw_hash(pw)
    if x is not me:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE member=?", (x["id"],))
    audit("%s 회원 비밀번호 재설정" % x["name"])
    return None, "비밀번호를 바꿨습니다. 회원에게 새 비밀번호를 알려 주세요"


def _txt(v, n=200):
    return re.sub(r"[\x00-\x1f]", " ", str(v if v is not None else "")).strip()[:n]


def a_content_save(me, d, ctx):
    need_admin(me)
    key, L = d.get("key"), d.get("value")
    if key not in ("hotels", "countries", "quick") or not isinstance(L, list) or len(L) > 40:
        raise Err("저장 형식이 올바르지 않습니다.")
    out = []
    for x in L:
        if not isinstance(x, dict): continue
        if key == "quick":
            to = _txt(x.get("to"), 80)
            if not re.fullmatch(r"(tag|sec:(hot|list|perks|hotels|ctry|reviews)|mypage|install|kakao|cat:.{1,20}|product:[\w\-]{1,40})", to): to = "sec:list"
            qid = _txt(x.get("id"), 30)
            it = {"id": qid if re.fullmatch(r"[\w\-]{1,30}", qid or "") else uid("q"), "label": _txt(x.get("label"), 20), "icon": _txt(x.get("icon"), 4), "img": clean_img(x.get("img")), "to": to, "hl": bool(x.get("hl"))}
            if it["label"]: out.append(it)
        elif key == "hotels":
            it = {k: _txt(x.get(k), 40) for k in ("id", "title", "area", "star", "price", "more", "country")}
            it["img"] = clean_img(x.get("img")); it["id"] = it["id"] or uid("h")
            if it["title"]: out.append(it)
        else:
            spots = [{"name": _txt(sp.get("name"), 40), "desc": _txt(sp.get("desc"), 150), "img": clean_img(sp.get("img"))}
                     for sp in (x.get("spots") or [])[:20] if isinstance(sp, dict) and _txt(sp.get("name"))]
            emg = [{"label": _txt(e.get("label"), 40), "num": _txt(e.get("num"), 40)} for e in (x.get("emg") or [])[:20] if isinstance(e, dict) and _txt(e.get("label"))]
            tips = [_txt(t, 200) for t in (x.get("tips") or [])[:20] if _txt(t)]
            it = {"id": _txt(x.get("id"), 30) or uid("c"), "name": _txt(x.get("name"), 20), "flag": _txt(x.get("flag"), 8),
                  "alert": _txt(x.get("alert"), 150), "spots": spots, "tips": tips, "emg": emg}
            if it["name"]: out.append(it)
    S[key] = out
    audit({"hotels": "호텔 카드", "countries": "나라별 여행정보", "quick": "빠른 메뉴"}[key] + " 수정")
    return None, "저장했습니다"


ACTIONS = {
    "content-save": a_content_save,
    "home-save": a_home_save, "rules-save": a_rules_save, "member-edit": a_member_edit, "member-pw": a_member_pw,
    "upload": a_upload, "settings": a_settings, "cat-save": a_cat_save, "cat-move": a_cat_move, "cat-delete": a_cat_delete,
    "banner-save": a_banner_save, "banner-move": a_banner_move, "banner-toggle": a_banner_toggle, "banner-delete": a_banner_delete,
    "signup": a_signup, "login": a_login, "logout": a_logout, "password": a_password,
    "booking": a_booking, "booking-cancel": a_booking_cancel, "booking-confirm": a_booking_confirm,
    "review": a_review, "review-approve": a_review_approve, "review-reject": a_review_reject, "review-delete": a_review_delete,
    "adjust": a_adjust, "ledger-edit": a_ledger_edit, "ledger-delete": a_ledger_delete,
    "member-status": a_member_status, "member-delete": a_member_delete, "member-seen": a_member_seen,
    "product-save": a_product_save, "product-toggle": a_product_toggle, "product-delete": a_product_delete,
}

# 로그인 시도 제한 (IP별)
FAILS = {}


def throttled(ip):
    t = time.time()
    lst = [x for x in FAILS.get(ip, []) if t - x < 600]
    FAILS[ip] = lst
    return len(lst) >= 10


class H(BaseHTTPRequestHandler):
    server_version = "best"

    def log_message(self, *a):
        pass

    def ip(self):
        return (self.headers.get("X-Real-IP") or self.client_address[0] or "").strip()

    def session_member(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        tok = c["bt_s"].value if "bt_s" in c else None
        if not tok:
            return None, None
        with db() as cn:
            row = cn.execute("SELECT member, exp FROM sessions WHERE token=?", (tok,)).fetchone()
        if not row or row[1] < time.time():
            return None, tok
        m = member(row[0])
        if not m or m["status"] != "active":
            return None, tok
        return m, tok

    def send(self, code, obj, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with LOCK:
            me, _ = self.session_member()
            if self.path.startswith("/api/state"):
                return self.send(200, {"ok": True, "state": view(me)})
            if self.path.startswith("/api/poll"):
                if not me or me["role"] != "admin":
                    return self.send(200, {"ok": True})
                return self.send(200, {"ok": True, "members": sum(1 for m in S["members"] if m["role"] != "admin"),
                                       "lastJoin": max([m["joined"] for m in S["members"] if m["role"] != "admin"] or [0]),
                                       "pendingReviews": sum(1 for r in S["reviews"] if r["status"] == "pending"),
                                       "lastReview": max([r["at"] for r in S["reviews"]] or [0]),
                                       "pendingBookings": sum(1 for b in S["bookings"] if b["status"] == "pending"),
                                       "lastBooking": max([b["at"] for b in S["bookings"]] or [0])})
            mm = re.fullmatch(r"/api/img/([\w\-]+\.(jpg|png|webp))", self.path.split("?")[0])
            if mm:
                fp = os.path.join(UPLOADS, mm.group(1))
                if os.path.isfile(fp):
                    with open(fp, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[mm.group(2)])
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if self.path.startswith("/api/health"):
                return self.send(200, {"ok": True})
        self.send(404, {"ok": False, "err": "not found"})

    def do_HEAD(self):
        mm = re.fullmatch(r"/api/(health|img/[\w\-]+\.(jpg|png|webp))", self.path.split("?")[0])
        ok = bool(mm) and (mm.group(1) == "health" or os.path.isfile(os.path.join(UPLOADS, mm.group(1)[4:])))
        self.send_response(200 if ok else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        m = re.fullmatch(r"/api/([a-z\-]+)", self.path.split("?")[0])
        fn = ACTIONS.get(m.group(1)) if m else None
        if not fn:
            return self.send(404, {"ok": False, "err": "없는 기능입니다."})
        # 다른 사이트에서 보내는 요청 차단
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and re.sub(r"^https?://", "", origin) != host:
            return self.send(403, {"ok": False, "err": "허용되지 않은 요청입니다."})
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 300000)
            d = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(d, dict):
                raise ValueError
        except Exception:
            return self.send(400, {"ok": False, "err": "요청 형식이 올바르지 않습니다."})
        name = m.group(1)
        ip = self.ip()
        if name in ("login", "signup") and throttled(ip):
            return self.send(429, {"ok": False, "err": "시도가 너무 많습니다. 10분 뒤 다시 해 주세요."})
        with LOCK:
            me, tok = self.session_member()
            ctx = {}
            backup = json.dumps(S)
            try:
                _, msg = fn(me, d, ctx)
            except Err as e:
                if name == "login":
                    FAILS.setdefault(ip, []).append(time.time())
                S.clear(); S.update(json.loads(backup))
                return self.send(200, {"ok": False, "err": e.msg})
            if name == "signup":
                FAILS.setdefault(ip, []).append(time.time() - 480)  # 가입은 IP당 짧은 시간에 많이 못 하게
            save(S)
            cookie = None
            if ctx.get("login"):
                new = secrets.token_urlsafe(32)
                with db() as c:
                    if tok:
                        c.execute("DELETE FROM sessions WHERE token=?", (tok,))
                    c.execute("DELETE FROM sessions WHERE exp<?", (time.time(),))
                    c.execute("INSERT INTO sessions VALUES (?,?,?)", (new, ctx["login"], time.time() + SESSION_DAYS * 86400))
                cookie = "bt_s=%s; Path=/; Max-Age=%d; HttpOnly; Secure; SameSite=Lax" % (new, SESSION_DAYS * 86400)
                me = member(ctx["login"])
            if ctx.get("logout"):
                if tok:
                    with db() as c:
                        c.execute("DELETE FROM sessions WHERE token=?", (tok,))
                cookie = "bt_s=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
                me = None
            if name == "upload":
                return self.send(200, {"ok": True, "img": ctx.get("img")})
            return self.send(200, {"ok": True, "msg": msg, "state": view(me)}, cookie)


def main():
    global S
    S = load()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("best api on 127.0.0.1:%d, data %s" % (PORT, DATA), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
