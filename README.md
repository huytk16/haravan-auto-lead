# 🚀 HƯỚNG DẪN DEPLOY HARAVAN AUTO LEAD BOT (PYTHON FASTAPI)

Thư mục này chứa đầy đủ mã nguồn và file cấu hình để chạy tự động tạo đơn 0đ trên Haravan.

---

## 📁 Cấu trúc thư mục:
- `main.py`: File mã nguồn Python chứa logic FastAPI, bóc tách SĐT qua Regex và gọi Haravan Admin API.
- `requirements.txt`: Khai báo thư viện cần thiết (`fastapi`, `uvicorn`, `requests`).
- `Procfile`: File cấu hình lệnh chạy server cho Render.com / Heroku.

---

## 🛠️ Hướng dẫn Deploy 3 bước lên Render.com (Miễn phí 24/7):

1. **Upload folder này lên GitHub:**
   * Tạo 1 Repository mới trên GitHub (Ví dụ: `haravan-auto-lead`).
   * Push tất cả các file trong thư mục `haravan_auto_lead` lên Repository đó.

2. **Tạo Web Service trên Render.com:**
   * Đăng nhập [Render.com](https://render.com).
   * Bấm **New +** -> Chọn **Web Service**.
   * Kết nối với Repository GitHub vừa tạo.
   * Tại mục **Start Command**, điền: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   * Bấm **Create Web Service**.

3. **Lấy Webhook URL kết nối HaraSocial:**
   * Render sẽ cấp URL dạng: `https://haravan-auto-lead.onrender.com`
   * Đường dẫn Webhook chính thức của bạn là:  
     `https://haravan-auto-lead.onrender.com/webhook/harasocial`
   * Copy URL này dán vào mục Cấu hình Webhook của **HaraSocial / Chatbot**.
