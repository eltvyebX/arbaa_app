import os
import re
import sqlite3
import traceback
import base64
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from PIL import Image, ImageFilter, ImageOps
import pytesseract

# ---------- إعداد التطبيق و المسارات ----------
app = FastAPI()
DB_NAME = "bank_receipts.db"

# مسار تخزين الصور
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
        # جدول المستخدمين
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT UNIQUE,
                pin TEXT
            )
        """)
        # جدول المعاملات/الإيصالات
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


# ---------- دوال مساعدة ----------
def calculate_total_amount_for_user(user_id: int) -> float:
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT SUM(amount) FROM transactions WHERE user_id = ?", (user_id,))
        total = c.fetchone()[0]
        return float(total) if total is not None else 0.0


def preprocess_for_ocr(pil_img: Image.Image) -> Image.Image:
    try:
        img = pil_img.convert("L")  # grayscale
        img = ImageOps.autocontrast(img, cutoff=1)
        img = img.filter(ImageFilter.SHARPEN)
        threshold = 200
        fn = lambda x: 255 if x > threshold else 0
        img = img.point(fn, "L")
        img = ImageOps.autocontrast(img, cutoff=0)
        return img
    except Exception:
        return pil_img


def extract_amount_from_text(text: str) -> float:
    if not text:
        return 0.0

    txt = text.replace('\n', ' ').replace('\r', ' ')
    patterns = [
        r'(?:المبلغ|المبلع|الإجمالي|إجمالي|رصيد)\s*[:\-]?\s*([\d{1,3}][\d\.,\s]{0,20}\d)',
        r'(?:Amount|Total|Balance|Value)\s*[:\-]?\s*([\d{1,3}][\d\.,\s]{0,20}\d)',
        r'([\d{1,3}](?:[,\s]\d{3})*(?:[.,]\d{1,3})?)'
    ]

    for pat in patterns:
        m = re.search(pat, txt, flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            cleaned = raw.replace(' ', '').replace(',', '.')
            cleaned = re.sub(r'[^\d\.]', '', cleaned)
            try:
                return float(cleaned)
            except Exception:
                continue

    return 0.0


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
            last_id = c.lastrowid
    except sqlite3.IntegrityError:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "رقم الحساب مستخدم مسبقًا. استخدم حسابًا آخر أو تواصل معي.",
            "user_id": ""
        })
    except Exception:
        traceback.print_exc()
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "حدث خطأ أثناء التسجيل. حاول لاحقًا.",
            "user_id": ""
        })

    # تعيين cookie تلقائي بعد التسجيل
    response = templates.TemplateResponse("show_pin.html", {"request": request, "user_id": user_id, "pin": pin})
    response.set_cookie(key="current_user", value=str(last_id), httponly=True, samesite="lax", max_age=3600*24)
    return response


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
                response = RedirectResponse(url="/index", status_code=303)
                response.set_cookie(key="current_user", value=user_db_id, httponly=True, samesite="lax", max_age=3600*24)
                return response
            else:
                return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات الدخول غير صحيحة."})
    except Exception:
        traceback.print_exc()
        return templates.TemplateResponse("login.html", {"request": request, "error": "حدث خطأ أثناء محاولة تسجيل الدخول."})


# ---------- صفحة التقاط الاشعار ----------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    try:
        user_id_int = int(user_id)
    except ValueError:
        return RedirectResponse("/login", status_code=303)
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return templates.TemplateResponse("index.html", {"request": request, "data": {"date_time": current_time}})


# ---------- حفظ صورة ملتقطة من الكاميرا ----------
@app.post("/capture_image")
async def capture_image(request: Request):
    try:
        payload = await request.json()
        image_data = payload.get("image_data")
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
        return JSONResponse({"success": False, "error": "No image_data provided"}, status_code=400)

    if "," in image_data:
        _, b64 = image_data.split(",", 1)
    else:
        b64 = image_data

    try:
        img_bytes = base64.b64decode(b64)
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid base64 image"}, status_code=400)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id_int}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(img_bytes)
    except Exception:
        traceback.print_exc()
        return JSONResponse({"success": False, "error": "Failed saving file"}, status_code=500)

    try:
        img = Image.open(filepath)
        processed = preprocess_for_ocr(img)
        try:
            txt = pytesseract.image_to_string(processed, lang="ara+eng")
        except Exception:
            txt = pytesseract.image_to_string(processed)
        amount = extract_amount_from_text(txt)
    except Exception:
        traceback.print_exc()
        amount = 0.0

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
                (user_id_int, filepath, float(amount), created_at)
            )
            conn.commit()
    except Exception:
        traceback.print_exc()
        return JSONResponse({"success": False, "error": "Saved image but failed to record transaction"}, status_code=500)

    return JSONResponse({"success": True, "amount": float(amount), "filename": filename})


# ---------- رفع ملف صورة (بديل لمسح الكاميرا) ----------
@app.post("/scan")
async def scan_receipt(request: Request, file: UploadFile = File(...)):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return JSONResponse({"success": False, "error": "Not logged in"}, status_code=401)
    try:
        user_id_int = int(user_id)
    except ValueError:
        return JSONResponse({"success": False, "error": "Invalid user id"}, status_code=400)

    try:
        contents = await file.read()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{user_id_int}_{timestamp}_{file.filename}"
        filepath = os.path.join(RECEIPTS_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(contents)

        img = Image.open(filepath)
        processed = preprocess_for_ocr(img)
        try:
            txt = pytesseract.image_to_string(processed, lang="ara+eng")
        except Exception:
            txt = pytesseract.image_to_string(processed)
        amount = extract_amount_from_text(txt)

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
                      (user_id_int, filepath, float(amount), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()

        return {"success": True, "amount": float(amount), "filename": filename}
    except Exception:
        traceback.print_exc()
        return {"success": False, "error": "Failed to process upload"}


# ---------- صفحة العرض view ----------
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
    total_images = len(images)
    total_amount = sum([float(r["amount"] or 0) for r in rows]) if rows else 0.0

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
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            c.execute("DELETE FROM transactions WHERE user_id = ?", (user_id_int,))
            conn.commit()
    except Exception:
        traceback.print_exc()
    return RedirectResponse("/view", status_code=303)


# ---------- export pdf ----------
@app.get("/export_pdf")
def export_pdf(request: Request):
    return RedirectResponse("/view", status_code=303)


# ---------- تشغيل محلي ----------
if __name__ == "__main__":
    import uvicorn
    # pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
