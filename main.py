# main.py
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

# سنخزن الصور داخل static/receipts حتى تكون سهلة العرض عبر القالب
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
    """
    تحسين بسيط للصورة لجعل OCR أكثر دقة على خلفية بيضاء:
    - تحويل للـ grayscale
    - تعزيز التباين (autocontrast)
    - مرشحات للتوضيح
    - تطبيق threshold (عتبة) بسيطة
    """
    try:
        img = pil_img.convert("L")  # to grayscale
        img = ImageOps.autocontrast(img, cutoff=1)  # remove extreme pixels
        img = img.filter(ImageFilter.SHARPEN)
        # تطبيق عتبة بسيطة: يجعل الخلفية بيضاء والنص أسود
        # قيمة العتبة 200 تعمل غالبًا جيدة مع خلفية بيضاء واضحة
        threshold = 200
        fn = lambda x: 255 if x > threshold else 0
        img = img.point(fn, "L")
        # إعادة تحسين بسيط
        img = ImageOps.autocontrast(img, cutoff=0)
        return img
    except Exception:
        return pil_img


def extract_amount_from_text(text: str) -> float:
    """
    بحث مرن عن الرقم المقابل لكلمة 'المبلغ' أو 'Amount' داخل النص.
    يعيد 0.0 إذا لم يعثر على قيمة صالحة.
    """
    if not text:
        return 0.0

    # تنظيف النص
    txt = text.replace('\n', ' ').replace('\r', ' ')
    # أولاً: البحث عن كلمة المبلغ باللغة العربية أو الإنجليزية ثم رقم
    patterns = [
        r'(?:المبلغ|المبلع|الإجمالي|إجمالي|رصيد)\s*[:\-]?\s*([\d{1,3}][\d\.,\s]{0,20}\d)',  # عربي
        r'(?:Amount|Total|Balance|Value)\s*[:\-]?\s*([\d{1,3}][\d\.,\s]{0,20}\d)',        # إنجليزي
        r'([\d{1,3}](?:[,\s]\d{3})*(?:[.,]\d{1,3})?)'                                   # رقم عام
    ]

    for pat in patterns:
        m = re.search(pat, txt, flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            # تنظيف الرمز
            cleaned = raw.replace(' ', '').replace(',', '.')
            # إزالة أي حروف غير رقمية أو نقاط
            cleaned = re.sub(r'[^\d\.]', '', cleaned)
            # التأكد من وجود رقم صالح
            try:
                return float(cleaned)
            except Exception:
                continue

    return 0.0


# ---------- صفحات المستخدم (Start / Register / Show PIN / Login) ----------
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
        # حساب/مستخدم مكرر
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

    # عرض صفحة الـ PIN (تُعرض مرة واحدة)
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
                response = RedirectResponse(url="/index", status_code=303)
                response.set_cookie(key="current_user", value=user_db_id)
                return response
            else:
                return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات الدخول غير صحيحة."})
    except Exception:
        traceback.print_exc()
        return templates.TemplateResponse("login.html", {"request": request, "error": "حدث خطأ أثناء محاولة تسجيل الدخول."})


# ---------- صفحة التقاط الاشعار (index) ----------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    # تمرير التاريخ الحالي كي يتم عرضه إذا رغبت
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return templates.TemplateResponse("index.html", {"request": request, "data": {"date_time": current_time}})


# ---------- حفظ صورة ملتقطة من الكاميرا (capture) - يتم استدعاؤها بالـ fetch من الـ frontend ----------
@app.post("/capture_image")
async def capture_image(request: Request):
    """
    يتوقع JSON { "image_data": "data:image/png;base64,...." }
    يقوم بحفظ الصورة، تطبيق معالجة، تشغيل OCR لاستخراج المبلغ، ثم حفظ السجل في DB.
    """
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

    # تفكيك البادئة base64
    if "," in image_data:
        header, b64 = image_data.split(",", 1)
    else:
        b64 = image_data

    try:
        img_bytes = base64.b64decode(b64)
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid base64 image"}, status_code=400)

    # حفظ الملف باسم فريد
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id_int}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(img_bytes)
    except Exception:
        traceback.print_exc()
        return JSONResponse({"success": False, "error": "Failed saving file"}, status_code=500)

    # قراءة الصورة من البايت وتشغيل المعالجة ثم OCR
    try:
        img = Image.open(filepath)
        processed = preprocess_for_ocr(img)
        # استخدم tesseract لاستخراج النص (دعم العربية + إنجليزي إن أمكن)
        try:
            txt = pytesseract.image_to_string(processed, lang="ara+eng")
        except Exception:
            # fallback بدون تحديد لغة
            txt = pytesseract.image_to_string(processed)
        amount = extract_amount_from_text(txt)
    except Exception:
        traceback.print_exc()
        amount = 0.0

    # حفظ السجل في قاعدة البيانات (image_path نحفظ المسار داخل static/receipts)
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
        # رغم الخطأ في الحفظ في DB الصورة محفوظة على الأقل؛ نبلغ العميل بفشل DB
        return JSONResponse({"success": False, "error": "Saved image but failed to record transaction"}, status_code=500)

    return JSONResponse({"success": True, "amount": float(amount), "filename": filename})


# ---------- رفع ملف صورة (بديل لمسح الكاميرا) - endpoint /scan ----------
@app.post("/scan")
async def scan_receipt(file: UploadFile = File(...)):
    # يحفظ الملف مؤقتًا ثم يستدعي نفس المنطق لاستخراج المبلغ
    try:
        contents = await file.read()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"upload_{timestamp}_{file.filename}"
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

        # هنا لا نربط بالمستخدم — إذا أردت الربط بالمستخدم ضع cookie current_user أو مرره
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
                      (None, filepath, float(amount), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()

        return {"success": True, "amount": float(amount), "filename": filename}
    except Exception:
        traceback.print_exc()
        return {"success": False, "error": "Failed to process upload"}


# ---------- صفحة العرض view (صور الإيصالات + إجمالي المبالغ وعدد الصور) ----------
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


# ---------- حذف كل الإشعارات للمستخدم ----------
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


# ---------- export pdf (اختياري) ----------
@app.get("/export_pdf")
def export_pdf(request: Request):
    # يمكنك تخصيص هذا لاحقًا - هنا مجرد placeholder يعيد Redirect إلى /view
    return RedirectResponse("/view", status_code=303)


# ---------- تشغيل محلي ----------
if __name__ == "__main__":
    import uvicorn
    # إذا كنت على Windows وقد ثبتت Tesseract في مسار افتراضي قم بتعيينه هنا:
    # pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
