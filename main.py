import os
import re
import shutil
import sqlite3
import tempfile 
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageFilter 
import requests  # استخدام requests بدلاً من pytesseract

app = FastAPI()

if not os.path.exists("templates"):
    os.makedirs("templates")

templates = Jinja2Templates(directory="templates")

DB_NAME = "bank_receipts.db"

OCR_API_KEY = "YOUR_OCR_SPACE_API_KEY"  # ضع مفتاحك هنا

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

# --- دالة جديدة لحساب الإجمالي ---
def calculate_total_amount():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(amount) FROM transactions")
        total = cursor.fetchone()[0]
        return total if total is not None else 0.0

# --- دالة استخراج البيانات من الصورة باستخدام OCR.Space API ---
def extract_data_from_image(image_path):
    """
    استخدام OCR.Space API بدلاً من pytesseract
    """
    data = {"trx_last4": "", "date_time": "", "amount": 0.0}
    clean_text = ""

    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # استدعاء OCR.Space API
        response = requests.post(
            "https://api.ocr.space/parse/image",
            files={"filename": (os.path.basename(image_path), image_bytes)},
            data={"apikey": OCR_API_KEY, "language": "eng"}  # يمكنك تغيير اللغة إلى "ara" للغة العربية
        )

        result = response.json()
        if result.get("ParsedResults"):
            clean_text = result["ParsedResults"][0].get("ParsedText", "")
        else:
            clean_text = ""

        # تنظيف النص
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
        months = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
        combined_pattern = fr'\b(\d{{1,2}}[\s\-\/]+{months}[\s\-\/]+\d{{4}}[\sT,]*\d{{1,2}}:\d{{2}}(?::\d{{2}})?)\b'
        combined_match = re.search(combined_pattern, clean_text, re.IGNORECASE)
        if combined_match:
            data["date_time"] = combined_match.group(1).strip()
        else:
            # ابسط طريقة لاستخراج أي رقم تاريخ/وقت
            numeric_date = re.search(r'\b(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}|\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\b', clean_text)
            if numeric_date:
                data["date_time"] = numeric_date.group(0)

        return data, clean_text

    except Exception as e:
        print(f"Error inside OCR: {e}")
        return {"trx_last4": "", "date_time": "", "amount": 0.0}, ""
