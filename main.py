import re
import json
import time
import requests
from threading import Lock
from fastapi import FastAPI, Request, Response

app = FastAPI(title="Haravan Auto Order Bot (Strict Carrier Filter & Deduplication)")

HARAVAN_ACCESS_TOKEN = "9E46B03CCB4575943B4B59AD159C6566E70A16F76423E8D6281CD1ADFC9348E9"
HARAVAN_API_URL = "https://apis.haravan.com/com/orders.json"
HARAVAN_SEARCH_CUSTOMER_URL = "https://apis.haravan.com/com/customers/search.json"

# Regex siết chặt chỉ bắt đúng đầu số Nhà mạng Viettel, Vina, Mobi, Vietnamobile, Gmobile
STRICT_VN_PHONE_REGEX = r"(?:\+?84|0)(?:3[2-9]|5[25689]|7[06-9]|8[1-9]|9[0-9])\d{7}\b"

# Bộ nhớ đệm chống trùng lặp theo thời gian (Deduplication Lock)
PROCESSED_PHONES = {}  # phone_number -> timestamp
CACHE_LOCK = Lock()
CACHE_TTL_SECONDS = 600  # Khóa chống lặp 10 phút

# Sản phẩm 0đ ĐÃ TẠO SẴN trên Haravan (bắt buộc điền để Haravan ghi nhận đúng đơn)
# Lấy ID tại: Haravan Admin -> Sản phẩm -> mở sản phẩm 0đ -> đọc số trên thanh URL
ZERO_VND_PRODUCT_ID = 1065265386   # product_id của sản phẩm "Mua Hàng Shopee Indo" (0đ)
ZERO_VND_VARIANT_ID = 1148799234   # variant_id (phiên bản) của sản phẩm "Mua Hàng Shopee Indo" (0đ)
ZERO_VND_FALLBACK_TITLE = "Mua Hàng Shopee Indo"  # chỉ dùng khi chưa điền ID ở trên

# ---- Bước 3: bóc tên khách đúng như hiển thị trên Facebook/Zalo ----
# Điểm ưu tiên theo tên key: key chứa nguyên văn tên hiển thị được ưu tiên cao nhất,
# first_name/last_name xếp cuối vì phải tự ghép nên dễ sai thứ tự.
NAME_KEY_SCORES = {
    "customer_name": 100,
    "sender_name": 98,
    "from_name": 96,
    "full_name": 94,
    "fullname": 94,
    "display_name": 92,
    "displayname": 92,
    "profile_name": 90,
    "contact_name": 88,
    "name": 80,
    "username": 62,
    "user_name": 62,
    "nickname": 55,
    "nick_name": 55,
    "first_name": 40,
    "last_name": 38,
}

# Tên đang nằm trong khối dữ liệu của người gửi thì đáng tin hơn (vd sender.name)
NAME_CONTAINER_HINTS = (
    "sender", "from", "customer", "contact", "user", "profile",
    "client", "author", "participant", "lead", "guest_info"
)

# Giá trị rác hay gặp, không phải tên thật
JUNK_NAMES = {
    "", "null", "none", "n/a", "na", "unknown", "undefined", "guest", "user",
    "anonymous", "facebook user", "zalo user", "khach", "khách", "khách hàng",
    "khách hàng lead", "no name", "test",
}

DEFAULT_CUSTOMER_NAME = "Khách hàng Lead"


def _looks_like_real_name(value) -> bool:
    """Loại bỏ ID, SĐT, email, URL, chuỗi hash... chỉ giữ lại thứ trông giống tên người."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > 60:
        return False
    if v.lower() in JUNK_NAMES:
        return False
    if "@" in v or "://" in v:
        return False
    if re.fullmatch(r"[\d\s+()\-.]+", v):        # toàn số: SĐT hoặc ID
        return False
    if re.fullmatch(r"[0-9a-fA-F\-_]{16,}", v):  # uuid / hash / token
        return False
    if not re.search(r"[A-Za-zÀ-ỹ]", v):         # phải có ít nhất một chữ cái
        return False
    return True


def extract_customer_name(data) -> str:
    """Duyệt đệ quy toàn bộ payload, chấm điểm mọi ứng viên tên rồi chọn tên điểm cao nhất.

    Nhờ vậy handler chạy đúng với mọi format payload của HaraSocial mà không cần
    biết trước cấu trúc JSON.
    """
    candidates = []  # (score, thứ tự gặp, tên)

    def add(score: int, value: str):
        candidates.append((score, len(candidates), value.strip()))

    def score_of(key: str, container_key: str) -> int:
        base = NAME_KEY_SCORES.get(key, 0)
        if any(hint in container_key for hint in NAME_CONTAINER_HINTS):
            base += 20
        return base

    def walk(node, container_key: str = ""):
        if isinstance(node, dict):
            # Ghép Họ Đệm + Tên theo đúng thứ tự người Việt (Facebook tách sẵn kiểu này)
            fn = node.get("first_name")
            ln = node.get("last_name")
            if isinstance(fn, str) or isinstance(ln, str):
                joined = f"{(ln or '').strip()} {(fn or '').strip()}".strip()
                if _looks_like_real_name(joined):
                    add(score_of("full_name", container_key) - 5, joined)

            for k, v in node.items():
                kl = str(k).lower()
                if isinstance(v, str):
                    if kl in NAME_KEY_SCORES and _looks_like_real_name(v):
                        add(score_of(kl, container_key), v)
                else:
                    walk(v, kl)
        elif isinstance(node, list):
            for item in node:
                walk(item, container_key)

    walk(data)
    if not candidates:
        return DEFAULT_CUSTOMER_NAME

    # Điểm cao nhất thắng; cùng điểm thì lấy cái gặp trước
    best = max(candidates, key=lambda c: (c[0], -c[1]))
    return best[2]

def is_duplicate_and_lock(phone: str) -> bool:
    now = time.time()
    with CACHE_LOCK:
        # Xóa các số hết hạn TTL
        expired = [p for p, t in PROCESSED_PHONES.items() if now - t > CACHE_TTL_SECONDS]
        for p in expired:
            del PROCESSED_PHONES[p]

        if phone in PROCESSED_PHONES:
            return True
        PROCESSED_PHONES[phone] = now
        return False

@app.get("/")
def read_root():
    return {"status": "online", "message": "Haravan Auto Lead Bot with Strict Carrier Validation & Deduplication is running!"}

@app.get("/webhook/harasocial")
async def verify_webhook(request: Request):
    params = request.query_params
    challenge = params.get("hub.challenge")
    if challenge:
        return Response(content=challenge, media_type="text/plain")
    return {"status": "webhook_endpoint_ready"}

@app.post("/webhook/harasocial")
async def handle_chat_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    
    print(">>> Webhook Payload Received:", json.dumps(data, ensure_ascii=False))
    
    # 1. Trích xuất SĐT chuẩn nhà mạng Việt Nam bằng Regex
    raw_str = json.dumps(data, ensure_ascii=False)
    matches = re.findall(STRICT_VN_PHONE_REGEX, raw_str)
    
    if not matches:
        print("--> No valid VN mobile phone number detected in payload")
        return {"status": "no_valid_vn_phone_detected"}
        
    phone_number = matches[0]
    print(f"--> Valid VN Mobile Phone Detected: {phone_number}")

    # Chống lặp song song (Race Condition / Concurrency Lock)
    if is_duplicate_and_lock(phone_number):
        print(f"--> Skipped: Phone {phone_number} was recently processed in deduplication cache")
        return {
            "status": "skipped",
            "reason": f"SĐT {phone_number} vừa được xử lý tạo đơn trong vòng {CACHE_TTL_SECONDS // 60} phút qua.",
            "phone": phone_number
        }

    # 2. Bóc tên khách đúng như hiển thị trên Facebook/Zalo (quét sâu toàn payload)
    customer_name = extract_customer_name(data)
    print(f"--> Customer Name Detected: {customer_name}")

    headers = {
        "Authorization": f"Bearer {HARAVAN_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # 3. KIỂM TRA KHÁCH HÀNG: Chỉ tạo đơn cho KHÁCH MỚI 100% (chưa từng có SĐT trong Haravan)
    # Bỏ qua toàn bộ khách cũ đã tồn tại SĐT trong danh bạ Haravan (kể cả chưa từng tạo đơn hay đã có đơn)
    try:
        search_res = requests.get(
            f"{HARAVAN_SEARCH_CUSTOMER_URL}?query={phone_number}",
            headers=headers,
            timeout=5
        )
        if search_res.status_code == 200:
            customers = search_res.json().get("customers", [])
            if customers and len(customers) > 0:
                c = customers[0]
                cust_id = c.get("id")
                cust_name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
                orders_cnt = c.get("orders_count", 0)
                print(f"--> Skipped: Customer {phone_number} already exists in Haravan (ID={cust_id}, Name='{cust_name}', Orders={orders_cnt})")
                return {
                    "status": "skipped",
                    "reason": f"Khách hàng {phone_number} ({cust_name}) đã tồn tại trong hệ thống Haravan từ trước (Khách cũ).",
                    "phone": phone_number,
                    "customer_id": cust_id,
                    "orders_count": orders_cnt
                }
    except Exception as e:
        print(f"--> Search Customer Error: {e}")

    # 4. Nếu là Khách MỚI 100% (chưa từng có trong Haravan) -> Gọi API Haravan tạo đơn 0đ
    # Lấy thẳng tên người gửi từ Facebook/Zalo dán trực tiếp qua Haravan (nguyên văn 100%)
    customer_payload = {
        "first_name": customer_name,
        "last_name": "",
        "phone": phone_number
    }

    address_payload = {
        "first_name": customer_name,
        "last_name": "",
        "phone": phone_number,
        "address1": "Vietnam",
        "country": "Vietnam",
        "country_code": "VN"
    }

    # Dòng sản phẩm: trỏ thẳng vào sản phẩm 0đ có sẵn trên Haravan để đơn được ghi nhận đúng
    if ZERO_VND_VARIANT_ID:
        line_item = {
            "variant_id": ZERO_VND_VARIANT_ID,
            "quantity": 1,
            "price": 0
        }
        if ZERO_VND_PRODUCT_ID:
            line_item["product_id"] = ZERO_VND_PRODUCT_ID
    else:
        print("--> WARNING: Chưa điền ZERO_VND_VARIANT_ID, đang tạo dòng hàng tự do (Haravan sẽ KHÔNG ghi nhận đúng sản phẩm)")
        line_item = {
            "title": ZERO_VND_FALLBACK_TITLE,
            "price": 0,
            "quantity": 1
        }

    # Đơn hàng chuẩn với Kênh (Channel) là 'harasocial'
    payload = {
        "order": {
            "source_name": "harasocial",
            "source": "harasocial",
            "phone": phone_number,
            "financial_status": "pending",
            "fulfillment_status": "unfulfilled",
            "send_receipt": False,
            "send_fulfillment_receipt": False,
            "tags": "DonAo_0d, Auto_Bot",
            "note": f"Đơn ảo tự động tạo khi khách MỚI ({customer_name}) nhả SĐT hợp lệ từ HaraSocial",
            "line_items": [line_item],
            "customer": customer_payload,
            "billing_address": address_payload,
            "shipping_address": address_payload
        }
    }
    
    response = requests.post(HARAVAN_API_URL, json=payload, headers=headers, timeout=10)
    print(f"--> Haravan Create Order Status: {response.status_code}, Res: {response.text[:200]}")
    return {"status": "success", "haravan_res": response.json()}


