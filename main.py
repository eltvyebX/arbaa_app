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
import pytesseract

app = FastAPI()

if not os.path.exists("templates"):
    os.makedirs("templates")

templates = Jinja2Templates(directory="templates")

DB_NAME = "bank_receipts.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER INTEGER PRIMARY KEY AUTOINCREMENT,
                trx_last4 TEXT,
                trx_date TEXT,
                amount REAL
            )
        """)
        conn.commit()

init_db()

# --- دالة جديدة لحساب الإجمالي ---
def calculate_total_amount():
    """يحسب مجموع حقل المبلغ (amount) في جميع سجلات المعاملات."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # استخدام دالة SUM المدمجة في SQLite
        cursor.execute("SELECT SUM(amount) FROM transactions")
        total = cursor.fetchone()[0]
        # إذا كان الجدول فارغاً، فإن SUM قد يعيد None، لذا نضمن إعادة 0.0
        return total if total is not None else 0.0

def extract_data_from_image(image_path):
    """
    دالة استخراج البيانات مع تحسين معالجة الصور ودعم الكلمات المفتاحية العربية.
    """
    data = {"trx_last4": "", "date_time": "", "amount": 0.0}
    clean_text = ""
    tmp_filename = None
    
    try:
        # 1. Image Preprocessing
        img = Image.open(image_path)
        img = img.convert('L')
        img = img.filter(ImageFilter.SHARPEN)
        width, height = img.size
        img = img.resize((int(width * 1.5), int(height * 1.5)), Image.LANCZOS)
        
        # 2. استخراج النص الخام باستخدام ملف مؤقت 
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp_file:
            tmp_filename = tmp_file.name
            img.save(tmp_filename, format='TIFF')

        text = pytesseract.image_to_string(tmp_filename)
        
        # تنظيف النص: استبدال الفواصل الغريبة والشرطات الطويلة
        clean_text = text.replace('|', '/').replace('\\', '/').replace('—', '-').replace('–', '-')
        
        # طباعة النص الخام للمساعدة في التشخيص
        print("--- RAW TEXT START ---")
        print(clean_text)
        print("--- RAW TEXT END ---")
        
        # --- 1. استخراج المبلغ (Amount) ---
        
        amount_keywords = r'(?:المبلغ|المبلع|الإجمالي|إجمالي|رصيد|Amount|Total|SAR|AED|USD|Balance|Value)'
        
        # النمط يركز على الكلمة المفتاحية ثم يلتقط الرقم (مع فواصل اختيارية)
        amount_regex = fr'{amount_keywords}[\s:\.]*(\d{{1,3}}(?:[,\s]?[0-9]{{3}})*[\.,]?[0-9]{{0,3}})'
        amount_match = re.search(amount_regex, clean_text, re.IGNORECASE)
        
        raw_amount = None
        if amount_match:
            raw_amount = amount_match.group(1)
        
        # محاولات احتياطية (تم الاحتفاظ بها لضمان التوافق)
        if not raw_amount:
            generic_amount_match = re.search(r'\b(\d{1,6}(?:[,\s]\d{3})*[\.,]\d{2})\b', clean_text)
            if generic_amount_match:
                raw_amount = generic_amount_match.group(1)
        
        if not raw_amount:
            generic_amount_match = re.search(r'\b(\d{2,})\b', clean_text)
            if generic_amount_match:
                 raw_amount = generic_amount_match.group(1)

        if raw_amount:
            # تنظيف: إزالة فواصل الآلاف أولاً (مسافة أو فاصلة)
            clean_amount = raw_amount.replace(' ', '')
            
            # إذا كانت الفاصلة هي الفاصل العشري، نوحدها إلى نقطة
            if ',' in clean_amount and '.' not in clean_amount:
                # إذا كانت آخر فاصلة تسبق رقمين، نعتبرها فاصل عشري
                if re.search(r',(\d{2})$', clean_amount):
                    # إزالة أي نقاط قد تكون فواصل آلاف ثم استبدال الفاصلة بالنقطة
                    clean_amount = clean_amount.replace('.', '').replace(',', '.') 
                else:
                    # إذا كانت فواصل آلاف فقط، يتم إزالتها
                    clean_amount = clean_amount.replace(',', '') 
            
            # إذا كان الرقم لا يحتوي على فاصل عشري بعد التنظيف، فإنه يعتبر رقم صحيح، لذا يجب إزالة أي نقاط متبقية (فواصل آلاف محتملة)
            if '.' not in clean_amount and clean_amount.count('.') > 0:
                clean_amount = clean_amount.replace('.', '')
            
            try:
                # التحويل إلى رقم عشري. إذا كان 1300، فسيتم تحويله إلى 1300.0
                data["amount"] = float(clean_amount)
            except ValueError:
                print(f"Failed to convert amount to float: {clean_amount}")
                pass

        # --- 2. استخراج رقم العملية (Transaction ID) ---
        # (بقية المنطق بدون تغيير)
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
        
        # --- 3. استخراج التاريخ والوقت (بدون تغيير) ---
        
        months = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
        
        # أ) البحث عن النمط المدمج (تاريخ + وقت)
        combined_pattern = fr'\b(\d{{1,2}}[\s\-\/]+{months}[\s\-\/]+\d{{4}}[\sT,]*\d{{1,2}}:\d{{2}}(?::\d{{2}})?)\b'
        combined_match = re.search(combined_pattern, clean_text, re.IGNORECASE)
        
        if combined_match:
            data["date_time"] = combined_match.group(1).strip()
        else:
            # ب) البحث المنفصل (إذا فشل البحث المدمج)
            found_date = ""
            found_time = ""

            # البحث عن الوقت
            time_pattern = r'\b([0-1]?[0-9]|2[0-3]):([0-5][0-9])(?::([0-5][0-9]))?(\s?(?:AM|PM|am|pm))?\b'
            time_matches = re.findall(time_pattern, clean_text)
            
            # إضافة الكلمة المفتاحية العربية: الوقت
            time_keyword = r'(?:Time|الوقت)'
            time_keyword_match = re.search(fr'{time_keyword}[\s:.]*({time_pattern})', clean_text, re.IGNORECASE)
            
            if time_keyword_match:
                found_time = time_keyword_match.group(0).replace("Time", "").replace("الوقت", "").strip(": .")
            elif time_matches:
                t = time_matches[0]
                found_time = f"{t[0]}:{t[1]}" 
                if t[2]: found_time += f":{t[2]}"
                if t[3]: found_time += f"{t[3]}"

            # البحث عن التاريخ
            # إضافة الكلمة المفتاحية العربية: التاريخ
            date_keyword = r'(?:Date|التاريخ)'
            date_keyword_match = re.search(fr'{date_keyword}[\s:.]*([0-9A-Za-z\/\-\.\, ]+)', clean_text, re.IGNORECASE)
            numeric_date = re.search(r'\b(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}|\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\b', clean_text)
            textual_date = re.search(fr'\b(\d{{1,2}}[\s\-\/]+{months}[\s\-\/]+\d{{2,4}}|{months}[\s\-\/]+\d{{1,2}}(?:th|st|nd|rd)?[\s,]+\d{{2,4}})\b', clean_text, re.IGNORECASE)

            if date_keyword_match and len(date_keyword_match.group(1)) < 20:
                found_date = date_keyword_match.group(1).strip()
            elif textual_date:
                found_date = textual_date.group(0)
            elif numeric_date:
                found_date = numeric_date.group(0)

            data["date_time"] = f"{found_date} {found_time}".strip()
        
        # --- 4. تنسيق التاريخ ---
        raw_date_time = data["date_time"]

        if raw_date_time:
            # قائمة بالتنسيقات المحتملة للتواريخ المستخرجة
            input_formats = [
                '%d-%b-%Y %H:%M:%S',  
                '%d-%b-%Y%H:%M:%S',   
                '%d-%b-%Y',           
                '%d/%m/%Y',           
                '%Y-%m-%d %H:%M:%S', 
                '%d/%m/%Y %H:%M:%S'
            ]
            output_format = '%H:%M:%S %d-%m-%Y' # التنسيق المطلوب: DD-MM-YYYY

            for fmt in input_formats:
                try:
                    # محاولة تحليل السلسلة النصية إلى كائن تاريخ
                    dt_object = datetime.strptime(raw_date_time, fmt)
                    
                    # إذا نجح التحليل، قم بالتنسيق إلى DD-MM-YYYY وقم بتعيين القيمة
                    data["date_time"] = dt_object.strftime(output_format)
                    break 
                except ValueError:
                    # إذا لم يتطابق التنسيق، حاول التنسيق التالي
                    continue
            
        # --- 5. نهاية الدالة ---

        return data, clean_text
    except Exception as e:
        print(f"Error inside OCR: {e}")
        return {"trx_last4": "", "date_time": "", "amount": 0.0}, ""
    finally:
        # 3. التأكد من حذف الملف المؤقت في جميع الأحوال
        if tmp_filename and os.path.exists(tmp_filename):
            os.remove(tmp_filename)

# --- Routes ---

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
    amount: float = Form(...),
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
        
    # جلب الإجمالي وإرساله إلى القالب
    total_amount = calculate_total_amount()
        
    return templates.TemplateResponse("view.html", {
        "request": request, 
        "transactions": transactions,
        "total_amount": total_amount # تمرير الإجمالي هنا
    })

@app.post("/delete/{id}")
def delete_transaction(id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (id,))
        conn.commit()
        
    return RedirectResponse(url="/transactions", status_code=303)