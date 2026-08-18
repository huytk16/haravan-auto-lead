import re
import json
import requests
from fastapi import FastAPI, Request, Response

app = FastAPI(title="Haravan Auto Order Bot (Strict Carrier Filter)")

HARAVAN_ACCESS_TOKEN = "9E46B03CCB4575943B4B59AD159C6566E70A16F76423E8D6281CD1ADFC9348E9"
HARAVAN_API_URL = "https://apis.haravan.com/com/orders.json"
HARAVAN_SEARCH_CUSTOMER_URL = "https://apis.haravan.com/com/customers/search.json"

# Regex siết chặt chỉ bắt đúng đầu số Nhà mạng Viettel, Vina, Mobi, Vietnamobile, Gmobile
STRICT_VN_PHONE_REGEX = r"(?:\+?84|0)(?:3[2-9]|5[25689]|7[06-9]|8[1-9]|9[0-9])\d{7}\b"

@app.get("/")
def read_root():
    return {"status": "online", "message": "Haravan Auto Lead Bot with Strict Carrier Validation is running!"}

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

    # 2. Tìm Tên khách hàng thông minh từ payload
    customer_name = "Khách hàng Lead"
    if isinstance(data, dict):
        if "first_name" in data or "last_name" in data:
            fn = data.get("first_name", "") or ""
            ln = data.get("last_name", "") or ""
            customer_name = f"{fn} {ln}".strip() or "Khách hàng Lead"
        elif "sender" in data and isinstance(data["sender"], dict):
            customer_name = data["sender"].get("name", "Khách hàng Lead")
        elif "name" in data and isinstance(data["name"], str):
            customer_name = data["name"]

    headers = {
        "Authorization": f"Bearer {HARAVAN_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # 3. KIỂM TRA THÔNG MINH: Tra cứu xem Khách hàng này đã từng có đơn chưa
    try:
        search_res = requests.get(
            f"{HARAVAN_SEARCH_CUSTOMER_URL}?query={phone_number}",
            headers=headers,
            timeout=5
        )
        if search_res.status_code == 200:
            customers = search_res.json().get("customers", [])
            if customers and len(customers) > 0:
                orders_count = customers[0].get("orders_count", 0)
                if orders_count > 0:
                    print(f"--> Skipped: Customer {phone_number} already has {orders_count} orders")
                    return {
                        "status": "skipped",
                        "reason": f"Khách hàng {phone_number} đã từng có {orders_count} đơn hàng.",
                        "phone": phone_number,
                        "orders_count": orders_count
                    }
    except Exception as e:
        print(f"--> Search Customer Error: {e}")

    # 4. Nếu là Khách MỚI (chưa có đơn nào) -> Gọi API Haravan tạo đơn 0đ
    payload = {
        "order": {
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
            "customer": {
                "first_name": customer_name,
                "phone": phone_number
            }
        }
    }
    
    response = requests.post(HARAVAN_API_URL, json=payload, headers=headers, timeout=10)
    print(f"--> Haravan Create Order Status: {response.status_code}, Res: {response.text[:200]}")
    return {"status": "success", "haravan_res": response.json()}
