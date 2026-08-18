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

    # 2. Tìm Tên khách hàng thông minh từ payload webhook HaraSocial
    customer_name = "Khách hàng Lead"
    if isinstance(data, dict):
        if "first_name" in data or "last_name" in data:
            fn_raw = data.get("first_name", "") or ""
            ln_raw = data.get("last_name", "") or ""
            customer_name = f"{ln_raw} {fn_raw}".strip() or f"{fn_raw} {ln_raw}".strip() or "Khách hàng Lead"
        elif "sender" in data and isinstance(data["sender"], dict):
            customer_name = data["sender"].get("name", "Khách hàng Lead")
        elif "customer" in data and isinstance(data["customer"], dict):
            customer_name = data["customer"].get("name", "Khách hàng Lead")
        elif "sender_name" in data and isinstance(data["sender_name"], str):
            customer_name = data["sender_name"]
        elif "name" in data and isinstance(data["name"], str):
            customer_name = data["name"]

    headers = {
        "Authorization": f"Bearer {HARAVAN_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # 3. KIỂM TRA THÔNG MINH: Tra cứu xem Khách hàng này đã từng có đơn chưa trên Haravan
    customer_id = None
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
                customer_id = c.get("id")
                orders_count = c.get("orders_count", 0)
                last_order_id = c.get("last_order_id")
                
                if orders_count > 0 or last_order_id is not None:
                    print(f"--> Skipped: Customer {phone_number} already has orders (count={orders_count}, last_order={last_order_id})")
                    return {
                        "status": "skipped",
                        "reason": f"Khách hàng {phone_number} đã từng có {orders_count} đơn hàng.",
                        "phone": phone_number,
                        "orders_count": orders_count
                    }
    except Exception as e:
        print(f"--> Search Customer Error: {e}")

    # 4. Nếu là Khách MỚI (chưa có đơn nào) -> Gọi API Haravan tạo đơn 0đ
    # Chuẩn hóa tên tiếng Việt theo cơ chế Haravan:
    # - first_name = Tên gọi (từ cuối cùng, VD: "Thuận")
    # - last_name = Họ & Tên đệm (các từ đầu, VD: "Nguyễn Thành")
    # Khi Haravan render danh sách đơn hàng sẽ hiển thị đúng chuẩn: "Nguyễn Thành Thuận"
    name_parts = customer_name.strip().split()
    if len(name_parts) >= 2:
        fn = name_parts[-1]
        ln = " ".join(name_parts[:-1])
    elif len(name_parts) == 1:
        fn = name_parts[0]
        ln = "Khách"
    else:
        fn = "Lead"
        ln = "Khách Hàng"

    customer_payload = {
        "first_name": fn,
        "last_name": ln,
        "phone": phone_number
    }
    if customer_id:
        customer_payload["id"] = customer_id

    address_payload = {
        "first_name": fn,
        "last_name": ln,
        "phone": phone_number,
        "address1": "Vietnam",
        "country": "Vietnam",
        "country_code": "VN"
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
            "line_items": [
                {
                    "title": "Mua Hàng Shopee Indo",
                    "price": 0,
                    "quantity": 1
                }
            ],
            "customer": customer_payload,
            "billing_address": address_payload,
            "shipping_address": address_payload
        }
    }
    
    response = requests.post(HARAVAN_API_URL, json=payload, headers=headers, timeout=10)
    print(f"--> Haravan Create Order Status: {response.status_code}, Res: {response.text[:200]}")
    return {"status": "success", "haravan_res": response.json()}

