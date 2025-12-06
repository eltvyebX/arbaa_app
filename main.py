import os
import sqlite3
from datetime import datetime
import base64
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from PIL import Image
import io
import pytesseract
import re

app = FastAPI()
DB_NAME = "bank_receipts.db"

# مجلد حفظ الإشعارات
RECEIPTS_DIR = "receipts"
os.makedirs(RECEIPTS_DIR, exist_ok=True)

# Templates + Static
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount(f"/{RECEIPTS_DIR}", StaticFiles(directory=RECEIPTS_DIR), name="receipts")

# ------------------------
# تهيئة قاعدة البيانات
# ------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT UNIQUE,
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
# صفحة تسجيل مستخدم جديد
# ------------------------
@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "user_id": ""})

@app.post("/register")
def register_user(request: Request,
                  bank_account: str = Form(...)):

    # توليد user_id و pin عشوائي
    import random, string
    user_id = "USR-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    pin = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

    # إضافة المستخدم إلى قاعدة البيانات
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                  (user_id, bank_account, pin))
        conn.commit()

    return templates.TemplateResponse("show_pin.html", {"request": request, "user_id": user_id, "pin": pin})

# ------------------------
# صفحة تسجيل الدخول
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
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "رقم الحساب أو الرقم السري غير صحيح"
            })

# ------------------------
# صفحة التقاط الإشعار (index)
# ------------------------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})

# ------------------------
# استقبال الصورة وحفظها + OCR
# ------------------------
@app.post("/capture_image")
async def capture_image(request: Request):
    data = await request.json()
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return JSONResponse({"success": False, "error": "Not logged in"})

    try:
        user_id = int(user_id_str)
    except ValueError:
        return JSONResponse({"success": False, "error": "Invalid user id"})

    image_data = data.get("image_data")
    if not image_data:
        return JSONResponse({"success": False, "error": "No image data provided"})

    # حفظ الصورة
    header, encoded = image_data.split(",", 1)
    image_bytes = base64.b64decode(encoded)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_bytes)

    # OCR لاستخراج المبلغ
    try:
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang='eng+ara')
        # البحث عن الرقم بعد كلمة "المبلغ" أو "Amount"
        match = re.search(r'(?:المبلغ|Amount)[^\d]*(\d+(?:\.\d+)?)', text)
        amount = float(match.group(1)) if match else 0.0
    except Exception as e:
        amount = 0.0

    # حفظ المعاملة
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
            (user_id, filepath, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()

    return JSONResponse({"success": True})

# ------------------------
# عرض الإشعارات مع إجمالي المبالغ
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
        total_amount = sum([float(t["amount"]) for t in trs]) if trs else 0.0

    return templates.TemplateResponse("view.html", {
        "request": request,
        "transactions": trs,
        "total_amount": total_amount
    })
