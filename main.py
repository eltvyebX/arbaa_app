import os
import sqlite3
from datetime import datetime
import base64
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from PIL import Image
import pytesseract
import re

app = FastAPI()
DB_NAME = "bank_receipts.db"

RECEIPTS_DIR = "receipts"
os.makedirs(RECEIPTS_DIR, exist_ok=True)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount(f"/{RECEIPTS_DIR}", StaticFiles(directory=RECEIPTS_DIR), name="receipts")

# ---------------- Database Init ----------------
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

# ---------------- Index ----------------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})

# ---------------- Upload Captured Image ----------------
@app.post("/upload_capture")
def upload_capture(request: Request, captured_image: str = Form(...)):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)

    # حفظ الصورة
    header, encoded = captured_image.split(",", 1)
    image_data = base64.b64decode(encoded)
    filename = f"{user_id_str}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_data)

    # OCR لاستخراج المبلغ
    img = Image.open(filepath)
    text = pytesseract.image_to_string(img, lang='ara+eng')
    amounts = re.findall(r"\d+[\.,]?\d*", text)
    amount_value = float(amounts[0].replace(',', '.')) if amounts else 0.0

    # حفظ في قاعدة البيانات
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
            (int(user_id_str), filepath, amount_value, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()

    return RedirectResponse(url="/view", status_code=303)

# ---------------- View ----------------
@app.get("/view")
def view_transactions(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC",
            (int(user_id_str),)
        )
        trs = c.fetchall()

    total_amount = sum([float(t["amount"]) for t in trs]) if trs else 0
    return templates.TemplateResponse(
        "view.html",
        {"request": request, "transactions": trs, "total_amount": total_amount}
    )

# ---------------- Delete ----------------
@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT image_path FROM transactions WHERE id = ? AND user_id = ?", (id, int(user_id_str)))
        row = c.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            os.remove(row[0])
        c.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (id, int(user_id_str)))
        conn.commit()

    return RedirectResponse(url="/view", status_code=303)
