import os
import sqlite3
import traceback
from datetime import datetime

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import base64

# ---------- إعداد التطبيق ----------
app = FastAPI()
DB_NAME = "bank_receipts.db"
RECEIPTS_DIR = os.path.join("static", "receipts")
os.makedirs(RECEIPTS_DIR, exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------- تهيئة قاعدة البيانات ----------
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


# ---------- صفحات المستخدم ----------
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
            c.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                      (user_id, bank_account, pin))
            conn.commit()
    except sqlite3.IntegrityError:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "رقم الحساب مستخدم مسبقًا.",
            "user_id": ""
        })
    except Exception:
        traceback.print_exc()
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "حدث خطأ أثناء التسجيل.",
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
                user_db_id = str(row["id"])
                response = RedirectResponse(url="/view", status_code=303)
                response.set_cookie(key="current_user", value=user_db_id)
                return response
            else:
                return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات الدخول غير صحيحة."})
    except Exception:
        traceback.print_exc()
        return templates.TemplateResponse("login.html", {"request": request, "error": "حدث خطأ أثناء تسجيل الدخول."})

# ------------------------------
#   ROUTE: INDEX (DO NOT TOUCH)
# ------------------------------
@app.get("/index", response_class=HTMLResponse)
def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ---------- API لاستقبال الصور + المبلغ من الهاتف ----------

@app.post("/upload_from_phone")
async def upload_from_phone(request: Request):
    """
    JSON المتوقع من الهاتف:
    {
        "image_data": "data:image/png;base64,...",
        "amount": 123.45
    }
    """
    try:
        data = await request.json()
        image_data = data.get("image_data")
        amount = data.get("amount", 0.0)
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON"}, status_code=400)

    user_id = request.cookies.get("current_user")
    if not user_id:
        return JSONResponse({"success": False, "error": "Not logged in"}, status_code=401)

    try:
        user_id_int = int(user_id)
    except ValueError:
        return JSONResponse({"success": False, "error": "Invalid user id"}, status_code=400)

    if not image_data:
        return JSONResponse({"success": False, "error": "No image data"}, status_code=400)

    if "," in image_data:
        _, b64 = image_data.split(",", 1)
    else:
        b64 = image_data

    try:
        img_bytes = base64.b64decode(b64)
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid Base64"}, status_code=400)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id_int}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(img_bytes)

    # حفظ المعاملة في قاعدة البيانات
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
                (user_id_int, filepath, float(amount), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
    except Exception:
        traceback.print_exc()
        return JSONResponse({"success": False, "error": "Failed to save transaction"}, status_code=500)

    return {"success": True, "filename": filename, "amount": amount}


# ---------- صفحة عرض الإشعارات ----------
@app.get("/view")
def view_receipts(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    try:
        user_id_int = int(user_id)
    except ValueError:
        return RedirectResponse("/login", status_code=303)

    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC", (user_id_int,))
            rows = c.fetchall()
    except Exception:
        traceback.print_exc()
        rows = []

    images = [os.path.basename(r["image_path"]) for r in rows if r["image_path"]]
    total_images = len(rows)
    total_amount = sum([float(r["amount"] or 0) for r in rows])

    return templates.TemplateResponse("view.html", {
        "request": request,
        "images": images,
        "total_images": total_images,
        "total_amount": "%.2f" % total_amount
    })


# ---------- حذف إشعار واحد ----------
@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT image_path FROM transactions WHERE id = ?", (id,))
            row = c.fetchone()
            if row and row[0] and os.path.exists(row[0]):
                os.remove(row[0])
            c.execute("DELETE FROM transactions WHERE id = ?", (id,))
            conn.commit()
    except Exception:
        traceback.print_exc()
    return RedirectResponse("/view", status_code=303)


# ---------- حذف كل الإشعارات ----------
@app.post("/delete_all")
def delete_all(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    try:
        user_id_int = int(user_id)
    except ValueError:
        return RedirectResponse("/login", status_code=303)

    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT image_path FROM transactions WHERE user_id = ?", (user_id_int,))
            rows = c.fetchall()
            for r in rows:
                path = r[0]
                if path and os.path.exists(path):
                    os.remove(path)
            c.execute("DELETE FROM transactions WHERE user_id = ?", (user_id_int,))
            conn.commit()
    except Exception:
        traceback.print_exc()
    return RedirectResponse("/view", status_code=303)


# ---------- تشغيل محلي ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
