import re
import requests
from fastapi import FastAPI, Request

app = FastAPI(title="Haravan Auto Order Bot")

HARAVAN_ACCESS_TOKEN = "9E46B03CCB4575943B4B59AD159C6566E70A16F76423E8D6281CD1ADFC9348E9"
HARAVAN_API_URL = "https://apis.haravan.com/com/orders.json"

# Regex nhận diện SĐT Việt Nam chuẩn
PHONE_REGEX = r"(?:\+?84|0)(?:\d){9}\b"

@app.get("/")
def read_root():
    return {"status": "online", "message": "Haravan Auto Lead Bot is running!"}

@app.post("/webhook/harasocial")
async def handle_chat_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    
    # 1. Trích xuất tin nhắn và thông tin khách
    message_obj = data.get("message", {}) if isinstance(data.get("message"), dict) else {}
    message = message_obj.get("text", "")
    
    sender_obj = data.get("sender", {}) if isinstance(data.get("sender"), dict) else {}
    customer_name = sender_obj.get("name", "Khách hàng Lead")
    
    # 2. Tìm SĐT trong tin nhắn
    match = re.search(PHONE_REGEX, message)
    if match:
        phone_number = match.group(0)
        
        # 3. Payload gửi API Haravan tạo đơn 0đ
        payload = {
            "order": {
                "phone": phone_number,
                "financial_status": "pending",
                "fulfillment_status": "unfulfilled",
                "send_receipt": False,                  # Tắt gửi ZNS/SMS rác cho khách
                "send_fulfillment_receipt": False,
                "tags": "DonAo_0d, Auto_Bot",
                "note": "Đơn ảo tự động tạo khi khách nhả SĐT từ HaraSocial",
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
        
        headers = {
            "Authorization": f"Bearer {HARAVAN_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # 4. Gọi API Haravan
        response = requests.post(HARAVAN_API_URL, json=payload, headers=headers)
        return {"status": "success", "haravan_res": response.json()}

    return {"status": "no_phone_detected"}
