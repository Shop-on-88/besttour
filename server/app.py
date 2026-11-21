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
REVIEW_GAP = 10 * 60          # 같은 회원 후기 사이 최소 간격(초)
REVIEW_DAY_MAX = 3            # 하루 최대 후기 수
REVIEW_PENDING_MAX = 2        # 승인 대기 중 최대 후기 수
SESSION_DAYS = 30
LOCK = threading.RLock()
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
        st = json.loads(row[0])
        st.setdefault("reviews", [])
        return st
    with open(os.path.join(HERE, "products_seed.json"), encoding="utf-8") as f:
        products = json.load(f)
    admin_pw = secrets.token_urlsafe(9)
    st = {"members": [{"id": "m0", "name": "관리자", "email": "admin@besttour", "phone": "",
                       "pw": pw_hash(admin_pw), "role": "admin", "joined": now_ms(), "status": "active"}],
          "products": products, "bookings": [], "ledger": [], "audit": [], "reviews": []}
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
    earn = int(pay * RULE["rate"])
    b = {"id": uid("b"), "memberId": me["id"], "productId": p["id"], "people": n, "total": total, "used": use,
         "pay": pay, "earned": earn, "status": "confirmed", "at": now_ms(), "depart": depart}
    S["bookings"].append(b)
    p["seats"] -= n
    if use > 0: add_ledger(me["id"], "use", -use, "예약 시 사용", bookingId=b["id"])
    if earn > 0: add_ledger(me["id"], "earn", earn, "예약 적립 %g%%" % (RULE["rate"] * 100), bookingId=b["id"])
    return b, "예약이 확정되었습니다. %sP 적립" % won(earn)


def a_booking_cancel(me, d, ctx):
    need_member(me)
    b = next((x for x in S["bookings"] if x["id"] == d.get("id")), None)
    if not b or (me["role"] != "admin" and b["memberId"] != me["id"]): raise Err("예약을 찾을 수 없습니다.")
    if b["status"] != "confirmed": raise Err("이미 처리된 예약입니다.")
    if balance(b["memberId"]) + b["used"] - b["earned"] < 0:
        raise Err("적립 포인트를 이미 사용해 회수할 잔액이 부족합니다. 관리자에게 문의하세요.")
    b["status"], b["cancelledAt"] = "cancelled", now_ms()
    if b["used"] > 0: add_ledger(b["memberId"], "refund", b["used"], "예약 취소로 사용 포인트 복원", bookingId=b["id"])
    if b["earned"] > 0: add_ledger(b["memberId"], "revoke", -b["earned"], "예약 취소로 적립 포인트 회수", bookingId=b["id"])
    p = product(b["productId"])
    if p: p["seats"] += b["people"]
    if me["role"] == "admin":
        x = member(b["memberId"])
        audit("%s 회원 예약 취소 처리, 사용 %sP 복원, 적립 %sP 회수" % (x["name"] if x else "-", won(b["used"]), won(b["earned"])))
    return b, "예약을 취소했습니다"


def a_review(me, d, ctx):
    need_member(me)
    text = re.sub(r"\s+", " ", (d.get("text") or "")).strip()
    rating = to_int(d.get("rating") or 5)
    pid = d.get("productId") or None
    if pid and not product(pid): pid = None
    if not 1 <= rating <= 5: raise Err("별점을 골라 주세요.")
    if len(text) < 20: raise Err("후기는 20자 이상 써 주세요.")
    if len(text) > 1000: raise Err("후기는 1,000자까지 쓸 수 있습니다.")
    mine = [r for r in S["reviews"] if r["memberId"] == me["id"]]
    t = now_ms()
    if any(t - r["at"] < REVIEW_GAP * 1000 for r in mine): raise Err("후기는 10분에 한 번만 쓸 수 있습니다. 잠시 후 다시 써 주세요.")
    if sum(1 for r in mine if t - r["at"] < 86400000) >= REVIEW_DAY_MAX: raise Err("후기는 하루 %d개까지 쓸 수 있습니다." % REVIEW_DAY_MAX)
    if sum(1 for r in mine if r["status"] == "pending") >= REVIEW_PENDING_MAX: raise Err("승인을 기다리는 후기가 있습니다. 승인된 뒤에 다시 써 주세요.")
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
         "from": (d.get("from") or "").strip().upper(), "to": (d.get("to") or "").strip().upper(), "desc": (d.get("desc") or "").strip()}
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
        if v["cat"] not in p.get("cats", []): p["cats"] = [v["cat"]] + p.get("cats", [])
        audit("상품 수정: " + v["name"])
    else:
        v.update({"id": uid("p"), "rate": 0, "active": True, "cats": [v["cat"]]})
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


ACTIONS = {
    "signup": a_signup, "login": a_login, "logout": a_logout, "password": a_password,
    "booking": a_booking, "booking-cancel": a_booking_cancel,
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
                                       "lastReview": max([r["at"] for r in S["reviews"]] or [0])})
            if self.path.startswith("/api/health"):
                return self.send(200, {"ok": True})
        self.send(404, {"ok": False, "err": "not found"})

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
            n = min(int(self.headers.get("Content-Length") or 0), 100000)
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
            return self.send(200, {"ok": True, "msg": msg, "state": view(me)}, cookie)


def main():
    global S
    S = load()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("best api on 127.0.0.1:%d, data %s" % (PORT, DATA), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
