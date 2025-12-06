import os
import sqlite3
from datetime import datetime
import base64
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import pytesseract
from PIL import Image
import io
import re

app = FastAPI()

DB_NAME = "bank_receipts.db"
RECEIPTS_DIR = "receipts"
os.makedirs(RECEIPTS_DIR, exist_ok=True)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount(f"/{RECEIPTS_DIR}", StaticFiles(directory=RECEIPTS_DIR), name="receipts")

# ------------------------
# قاعدة البيانات
# ------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT,
                pin TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                image_path TEXT,
                amount REAL,
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

init_db()

# ------------------------
# صفحة البداية
# ------------------------
@app.get("/")
def start_page(request: Request):
    return templates.TemplateResponse("start_page.html", {"request": request})

# ------------------------
# تسجيل مستخدم جديد
# ------------------------
@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
def register_user(request: Request, bank_account: str = Form(...)):
    import random, string
    # توليد user_id و PIN
    user_id = "USR-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    pin = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                  (user_id, bank_account, pin))
        conn.commit()

    # إعادة التوجيه لعرض الـ PIN
    response = RedirectResponse("/show_pin", status_code=303)
    response.set_cookie(key="new_user_id", value=user_id)
    response.set_cookie(key="new_user_pin", value=pin)
    return response

# ------------------------
# عرض PIN بعد التسجيل
# ------------------------
@app.get("/show_pin")
def show_pin(request: Request):
    user_id = request.cookies.get("new_user_id")
    pin = request.cookies.get("new_user_pin")
    return templates.TemplateResponse("show_pin.html", {"request": request, "user_id": user_id, "pin": pin})

# ------------------------
# تسجيل الدخول
# ------------------------
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_user(request: Request, bank_account: str = Form(...), pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE bank_account=? AND pin=?", (bank_account, pin))
        row = c.fetchone()
        if row:
            user_id = str(row[0])
            response = RedirectResponse("/index", status_code=303)
            response.set_cookie(key="current_user", value=user_id)
            return response
        else:
            return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات غير صحيحة"})

# ------------------------
# صفحة التقاط الإشعارات
# ------------------------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})

# ------------------------
# حفظ صورة الإشعار + OCR لاستخراج المبلغ
# ------------------------
@app.post("/capture_image")
async def capture_image(request: Request):
    data = await request.json()
    img_data = data.get("image_data")
    user_id_str = request.cookies.get("current_user")
    if not img_data or not user_id_str:
        return JSONResponse({"success": False, "error": "No image or user"})

    user_id = int(user_id_str)
    header, encoded = img_data.split(",", 1)
    image_bytes = base64.b64decode(encoded)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)

    # حفظ الصورة
    with open(filepath, "wb") as f:
        f.write(image_bytes)

    # OCR لاستخراج المبلغ
    try:
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang="eng+ara")
        # البحث عن كلمة المبلغ و الرقم بعدها
        amount_match = re.search(r"(?i)المبلغ[:\s]*([\d.,]+)", text)
        amount = float(amount_match.group(1).replace(",", ".")) if amount_match else 0.0
    except Exception as e:
        amount = 0.0

    # حفظ المسار والمبلغ في قاعدة البيانات
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
                  (user_id, filepath, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()

    return JSONResponse({"success": True, "amount": amount})

# ------------------------
# عرض الإشعارات وDashboard
# ------------------------
@app.get("/view")
def view_transactions(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse("/login", status_code=303)
    user_id = int(user_id_str)

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC", (user_id,))
        trs = c.fetchall()

    total_amount = sum([float(t["amount"]) for t in trs]) if trs else 0

    return templates.TemplateResponse("view.html", {
        "request": request,
        "transactions": trs,
        "total_amount": total_amount
    })

# ------------------------
# حذف إشعار
# ------------------------
@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse("/login", status_code=303)
    user_id = int(user_id_str)

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT image_path FROM transactions WHERE id=? AND user_id=?", (id, user_id))
        row = c.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            os.remove(row[0])
        c.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (id, user_id))
        conn.commit()

    return RedirectResponse("/view", status_code=303)
