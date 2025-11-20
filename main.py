import os
import re
import shutil
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image
import requests

app = FastAPI()

# ----- إعداد القوالب -----
if not os.path.exists("templates"):
    os.makedirs("templates")

templates = Jinja2Templates(directory="templates")

# ----- قاعدة البيانات -----
DB_NAME = "bank_receipts.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trx_last4 TEXT,
                trx_date TEXT,
                amount REAL
            )
        """)
        conn.commit()

init_db()

# ----- مفتاح OCR.Space API -----
OCR_API_KEY = "K83202383788957"  # ضع مفتاحك الصحيح هنا

# ----- دالة حساب الإجمالي -----
def calculate_total_amount():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(amount) FROM transactions")
        total = cursor.fetchone()[0]
        return total if total is not None else 0.0

# ----- دالة استخراج البيانات من الصورة -----
def extract_data_from_image(image_path):
    data = {"trx_last4": "", "date_time": "", "amount": 0.0}
    clean_text = ""
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # استدعاء OCR.Space API
        response = requests.post(
            "https://api.ocr.space/parse/image",
            files={"filename": (os.path.basename(image_path), image_bytes)},
            data={"apikey": OCR_API_KEY, "language": "ara"}  # للغة العربية
        )
        result = response.json()
        if result.get("ParsedResults"):
            clean_text = result["ParsedResults"][0].get("ParsedText", "")
        else:
            clean_text = ""

        clean_text = clean_text.replace('|', '/').replace('\\', '/').replace('—', '-').replace('–', '-')
        print("--- OCR TEXT ---")
        print(clean_text)
        print("--- END OCR TEXT ---")

        # --- استخراج المبلغ ---
        amount_keywords = r'(?:المبلغ|المبلع|الإجمالي|إجمالي|رصيد|Amount|Total|SAR|AED|USD|Balance|Value)'
        amount_regex = fr'{amount_keywords}[\s:\.]*(\d{{1,3}}(?:[,\s]?[0-9]{{3}})*[\.,]?[0-9]{{0,3}})'
        amount_match = re.search(amount_regex, clean_text, re.IGNORECASE)
        raw_amount = amount_match.group(1) if amount_match else None

        if not raw_amount:
            generic_amount_match = re.search(r'\b(\d{1,6}(?:[,\s]\d{3})*[\.,]\d{2})\b', clean_text)
            if generic_amount_match:
                raw_amount = generic_amount_match.group(1)

        if not raw_amount:
            generic_amount_match = re.search(r'\b(\d{2,})\b', clean_text)
            if generic_amount_match:
                raw_amount = generic_amount_match.group(1)

        if raw_amount:
            clean_amount = raw_amount.replace(' ', '')
            if ',' in clean_amount and '.' not in clean_amount:
                if re.search(r',(\d{2})$', clean_amount):
                    clean_amount = clean_amount.replace('.', '').replace(',', '.') 
                else:
                    clean_amount = clean_amount.replace(',', '') 
            try:
                data["amount"] = float(clean_amount)
            except ValueError:
                pass

        # --- استخراج رقم العملية ---
        trx_keywords_ar = r'(?:رقم\s*العملية)'
        trx_match_ar = re.search(fr'{trx_keywords_ar}[\W_]*([0-9]+)', clean_text, re.IGNORECASE)
        trx_keywords_all = r'(?:Trx\.|ID|Ref|No|Operation|Sequence|Number|رقم|عملية)'
        trx_match_all = re.search(fr'{trx_keywords_all}[\W_]*([0-9]+)', clean_text, re.IGNORECASE)

        full_id = None
        if trx_match_ar:
            full_id = trx_match_ar.group(1)
        elif trx_match_all:
            full_id = trx_match_all.group(1)
        else:
            long_number_match = re.search(r'(\d{8,})', clean_text)
            if long_number_match:
                full_id = long_number_match.group(1)

        if full_id:
            data["trx_last4"] = full_id[-4:]

        # --- استخراج التاريخ والوقت ---
        numeric_date = re.search(r'\b(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}|\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\b', clean_text)
        if numeric_date:
            data["date_time"] = numeric_date.group(0)

        # --- تنسيق التاريخ النهائي ---
        raw_date_time = data["date_time"]
        if raw_date_time:
            input_formats = [
                '%d-%b-%Y %H:%M:%S',
                '%d-%b-%Y%H:%M:%S',
                '%d-%b-%Y',
                '%d/%m/%Y',
                '%Y-%m-%d %H:%M:%S',
                '%d-%m-%Y %H:%M:%S'
            ]
            output_format = '%H:%M:%S %d-%m-%Y'  # HH:MM:SS DD-MM-YYYY
            for fmt in input_formats:
                try:
                    dt_object = datetime.strptime(raw_date_time, fmt)
                    data["date_time"] = dt_object.strftime(output_format)
                    break
                except ValueError:
                    continue

        return data, clean_text

    except Exception as e:
        print(f"Error inside OCR: {e}")
        return {"trx_last4": "", "date_time": "", "amount": 0.0}, ""

# ----- Routes -----
@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/scan")
async def scan_receipt(request: Request, file: UploadFile = File(...)):
    temp_filename = f"temp_{file.filename}"
    with open(temp_filename, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    extracted_data, raw_text = extract_data_from_image(temp_filename)

    if os.path.exists(temp_filename):
        os.remove(temp_filename)

    return templates.TemplateResponse("review.html", {
        "request": request,
        "data": extracted_data,
        "raw_text": raw_text
    })

@app.post("/confirm")
def confirm_data(
    request: Request,
    trx_last4: str = Form(...),
    date_time: str = Form(...),
    amount: float = Form(...)
):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (trx_last4, trx_date, amount) VALUES (?, ?, ?)",
            (trx_last4, date_time, amount)
        )
        conn.commit()
    return RedirectResponse(url="/transactions", status_code=303)

@app.get("/transactions")
def view_transactions(request: Request):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id DESC")
        transactions = cursor.fetchall()

    total_amount = calculate_total_amount()
    return templates.TemplateResponse("view.html", {
        "request": request,
        "transactions": transactions,
        "total_amount": total_amount
    })

@app.post("/delete/{id}")
def delete_transaction(id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (id,))
        conn.commit()
    return RedirectResponse(url="/transactions", status_code=303)
