import os
import sqlite3
import traceback
from datetime import datetime

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import base64

# -------------------------------------------------------
# إعداد التطبيق
# -------------------------------------------------------

app = FastAPI()

DB_NAME = "bank_receipts.db"
RECEIPTS_DIR = os.path.join("static", "receipts")

os.makedirs(RECEIPTS_DIR, exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# -------------------------------------------------------
# قاعدة البيانات
# -------------------------------------------------------

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # جدول المستخدمين
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT UNIQUE,
                pin TEXT
            )
        """)

        # جدول العمليات
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


# -------------------------------------------------------
# صفحات المستخدم
# -------------------------------------------------------

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

    return templates.TemplateResponse("show_pin.html", {
        "request": request,
        "user_id": user_id,
        "pin": pin
    })


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login_user(request: Request, bank_account: str = Form(...), pin: str = Form(...)):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            c.execute("SELECT id FROM users WHERE bank_account=? AND pin=?", (bank_account, pin))
            row = c.fetchone()

            if row:
                user_db_id = str(row["id"])

                response = RedirectResponse(url="/index", status_code=303)
                response.set_cookie(
                    key="current_user",
                    value=user_db_id,
                    max_age=60 * 60 * 24 * 7,
                    httponly=False,
                    secure=False,
                    samesite="lax"
                )
                return response

            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "بيانات الدخول غير صحيحة."
            })

    except Exception:
        traceback.print_exc()
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "خطأ أثناء تسجيل الدخول."
        })


# -------------------------------------------------------
# صفحة index
# -------------------------------------------------------

@app.get("/index", response_class=HTMLResponse)
def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# -------------------------------------------------------
# استقبال الصورة + المبلغ (Upload)
# -------------------------------------------------------

@app.post("/upload_from_phone")
async def upload_from_phone(request: Request):
    try:
        data = await request.json()
        image_data = data.get("image_data")
        amount = float(data.get("amount", 0))

    except Exception as e:
        return JSONResponse({"success": False, "error": f"Invalid JSON or amount: {e}"}, status_code=400)

    # تحقق من تسجيل الدخول
    user_id = request.cookies.get("current_user")
    if not user_id:
        return JSONResponse({"success": False, "error": "Not logged in"}, status_code=401)

    # استخراج Base64
    if "," in image_data:
        _, b64 = image_data.split(",", 1)
    else:
        b64 = image_data

    # تحويل إلى bytes
    try:
        img_bytes = base64.b64decode(b64)
    except:
        return JSONResponse({"success": False, "error": "Invalid base64"}, status_code=400)

    # حفظ الصورة
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(img_bytes)

    # حفظ العملية في DB
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO transactions (user_id, image_path, amount, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            int(user_id),
            filepath,
            amount,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()

    return {"success": True}


# -------------------------------------------------------
# عرض الإشعارات (Gallery)
# -------------------------------------------------------

@app.get("/view")
def view_receipts(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login")

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC",
            (int(user_id),)
        )
        rows = c.fetchall()

    images = [{
        "id": r["id"],
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


# -------------------------------------------------------
# حذف كل الإشعارات
# -------------------------------------------------------

@app.post("/delete_all")
def delete_all(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login")

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        c.execute("SELECT image_path FROM transactions WHERE user_id=?", (int(user_id),))
        rows = c.fetchall()

        for r in rows:
            img_path = r[0]
            if img_path and os.path.exists(img_path):
                os.remove(img_path)

        c.execute("DELETE FROM transactions WHERE user_id=?", (int(user_id),))
        conn.commit()

    return RedirectResponse("/view", status_code=303)


# -------------------------------------------------------
# حذف إشعار واحد
# -------------------------------------------------------

@app.post("/delete/{transaction_id}")
def delete_transaction(transaction_id: int, request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return JSONResponse({"success": False, "error": "Not logged in"})

    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()

            c.execute(
                "SELECT image_path FROM transactions WHERE id=? AND user_id=?",
                (transaction_id, int(user_id))
            )
            row = c.fetchone()

            if row:
                path = row[0]
                if path and os.path.exists(path):
                    os.remove(path)

            c.execute(
                "DELETE FROM transactions WHERE id=? AND user_id=?",
                (transaction_id, int(user_id))
            )

            conn.commit()

        return JSONResponse({"success": True})

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# -------------------------------------------------------
# تعديل المبلغ
# -------------------------------------------------------

@app.post("/update_amount/{transaction_id}")
def update_amount(transaction_id: int, request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return JSONResponse({"success": False, "error": "Not logged in"})

    new_amount = request.query_params.get("amount")
    if new_amount is None:
        return JSONResponse({"success": False, "error": "Amount not provided"})

    try:
        amount_val = float(new_amount)

    except:
        return JSONResponse({"success": False, "error": "Invalid amount"}, status_code=400)

    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("""
                UPDATE transactions SET amount=?
                WHERE id=? AND user_id=?
            """, (amount_val, transaction_id, int(user_id)))
            conn.commit()

        return JSONResponse({"success": True, "amount": amount_val})

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# -------------------------------------------------------
# تشغيل محلي
# -------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
