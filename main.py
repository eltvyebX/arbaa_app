import os
import sqlite3
from datetime import datetime
import base64
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import pytesseract
from PIL import Image
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
    return templates.TemplateResponse("register.html", {"request": request, "user_id": "", "pin": ""})

@app.post("/register")
def register_user(request: Request, bank_account: str = Form(...), user_id: str = Form(...), pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                  (user_id, bank_account, pin))
        conn.commit()
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
        c.execute("SELECT id FROM users WHERE bank_account = ? AND pin = ?", (bank_account, pin))
        row = c.fetchone()
        if row:
            user_id = str(row[0])
            response = RedirectResponse(url="/index", status_code=303)
            response.set_cookie(key="current_user", value=user_id)
            return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات الدخول غير صحيحة"})

# ------------------------
# صفحة التقاط الإشعار
# ------------------------
@app.get("/index") 
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})

# ------------------------
# رفع صورة الكاميرا وحفظها + OCR المبلغ باستخدام pytesseract
# ------------------------
@app.post("/upload_capture")
def upload_capture(request: Request, captured_image: str = Form(...)):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/login", status_code=303)

    # حفظ الصورة
    header, encoded = captured_image.split(",", 1)
    image_data = base64.b64decode(encoded)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_data)

    # OCR لاستخراج المبلغ
    try:
        img = Image.open(filepath)
        text = pytesseract.image_to_string(img, lang='ara+eng')
        amount = 0.0
        for line in text.splitlines():
            if "المبلغ" in line or "Amount" in line:
                numbers = re.findall(r"\d+[.,]?\d*", line.replace(",", "."))
                if numbers:
                    try:
                        amount = float(numbers[0])
                        break
                    except:
                        pass
    except Exception as e:
        amount = 0.0

    # حفظ مسار الصورة والمبلغ في قاعدة البيانات
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
            (user_id, filepath, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()

    return RedirectResponse(url="/view", status_code=303)

# ------------------------
# عرض الإشعارات وداشبورد الإجمالي
# ------------------------
@app.get("/view")
def view_transactions(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id_int = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/login", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC", (user_id_int,))
        trs = c.fetchall()

    total_amount = sum([float(t["amount"]) for t in trs]) if trs else 0
    return templates.TemplateResponse(
        "view.html",
        {"request": request, "transactions": trs, "total_amount": total_amount}
    )

# ------------------------
# حذف إشعار
# ------------------------
@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id_int = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/login", status_code=303)

    # حذف الصورة من المجلد
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT image_path FROM transactions WHERE id = ? AND user_id = ?", (id, user_id_int))
        row = c.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            os.remove(row[0])
        c.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (id, user_id_int))
        conn.commit()

    return RedirectResponse(url="/view", status_code=303)
