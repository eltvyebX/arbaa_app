import os
import sqlite3
import traceback
from datetime import datetime

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import base64

# إعداد التطبيق
app = FastAPI()
DB_NAME = "bank_receipts.db"
RECEIPTS_DIR = os.path.join("static", "receipts")
os.makedirs(RECEIPTS_DIR, exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# قاعدة البيانات
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
                amount REAL DEFAULT 0,
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

init_db()

# صفحات المستخدم
@app.get("/")
def start_page(request: Request):
    return templates.TemplateResponse("start_page.html", {"request": request})

@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "user_id": ""})

@app.post("/register")
def register_user(request: Request, bank_account: str = Form(...)):
    import random, string
    user_id = "USR-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    pin = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                (user_id, bank_account, pin)
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "رقم الحساب مستخدم مسبقًا.",
            "user_id": ""
        })

    return templates.TemplateResponse("show_pin.html", {"request": request, "user_id": user_id, "pin": pin})

@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_user(request: Request, bank_account: str = Form(...), pin: str = Form(...)):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE bank_account = ? AND pin = ?", (bank_account, pin))
            row = c.fetchone()
            if row:
                response = RedirectResponse(url="/index", status_code=303)
                response.set_cookie(key="current_user", value=str(row["id"]))
                return response
            else:
                return templates.TemplateResponse("login.html", {
                    "request": request,
                    "error": "بيانات الدخول غير صحيحة."
                })
    except:
        traceback.print_exc()
        return templates.TemplateResponse("login.html", {"request": request, "error": "خطأ أثناء تسجيل الدخول."})

# صفحة index
@app.get("/index", response_class=HTMLResponse)
def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# استقبال الصورة + المبلغ
@app.post("/upload_from_phone")
async def upload_from_phone(request: Request):
    try:
        data = await request.json()
        image_data = data.get("image_data")
        amount = float(data.get("amount", 0))
    except:
        return JSONResponse({"success": False, "error": "Invalid JSON"}, status_code=400)

    user_id = request.cookies.get("current_user")
    if not user_id:
        return JSONResponse({"success": False, "error": "Not logged in"}, status_code=401)

    # فك Base64
    if "," in image_data:
        _, b64 = image_data.split(",", 1)
    else:
        b64 = image_data

    img_bytes = base64.b64decode(b64)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)

    # حفظ الصورة
    with open(filepath, "wb") as f:
        f.write(img_bytes)

    # حفظ العملية
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
            (int(user_id), filepath, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()

    return {"success": True}

# صفحة عرض الإشعارات
@app.get("/view")
def view_receipts(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login")

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC", (user_id,))
        rows = c.fetchall()

    images = [{
        "file": os.path.basename(r["image_path"]),
        "amount": r["amount"]
    } for r in rows]

    total_amount = sum([r["amount"] for r in rows])

    return templates.TemplateResponse("view.html", {
        "request": request,
        "images": images,
        "total_amount": total_amount,
        "total_images": len(rows)
    })

# حذف كل شيء
@app.post("/delete_all")
def delete_all(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login")

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT image_path FROM transactions WHERE user_id=?", (user_id,))
        rows = c.fetchall()

        for r in rows:
            if os.path.exists(r[0]):
                os.remove(r[0])

        c.execute("DELETE FROM transactions WHERE user_id=?", (user_id,))
        conn.commit()

    return RedirectResponse("/view", status_code=303)

# تشغيل
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
