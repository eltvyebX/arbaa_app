import os
import sqlite3
from datetime import datetime
import base64
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import traceback

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
# تسجيل مستخدم جديد
# ------------------------
@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "user_id": ""})

@app.post("/register")
def register_user(request: Request, bank_account: str = Form(...)):
    try:
        # توليد user_id و pin
        import random, string
        user_id = "USR-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        pin = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                (user_id, bank_account, pin)
            )
            conn.commit()

        # بعد الحفظ نعرض صفحة show_pin
        return templates.TemplateResponse("show_pin.html", {
            "request": request,
            "user_id": user_id,
            "pin": pin
        })

    except sqlite3.IntegrityError:
        # في حالة تكرار رقم الحساب أو user_id
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "رقم الحساب موجود مسبقًا. حاول استخدام حساب آخر.",
            "user_id": ""
        })
    except Exception:
        print(traceback.format_exc())
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "حدث خطأ غير متوقع. حاول مرة أخرى.",
            "user_id": ""
        })

# ------------------------
# تسجيل الدخول
# ------------------------
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_user(request: Request, bank_account: str = Form(...), pin: str = Form(...)):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                "SELECT * FROM users WHERE bank_account=? AND pin=?",
                (bank_account, pin)
            )
            user = c.fetchone()
            if user:
                response = RedirectResponse(url="/index", status_code=303)
                response.set_cookie(key="current_user", value=str(user["id"]))
                return response
            else:
                return templates.TemplateResponse("login.html", {
                    "request": request,
                    "error": "رقم الحساب أو PIN غير صحيح."
                })
    except Exception:
        print(traceback.format_exc())
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "حدث خطأ أثناء تسجيل الدخول."
        })

# ------------------------
# الصفحة الرئيسية بعد تسجيل الدخول
# ------------------------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return templates.TemplateResponse("index.html", {"request": request, "data": {"date_time": current_time}})

# ------------------------
# تشغيل السيرفر
# ------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
