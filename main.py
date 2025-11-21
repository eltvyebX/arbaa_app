import os
import sqlite3
from datetime import datetime
import secrets

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# --------------------------------------------------
# FastAPI + DB
# --------------------------------------------------
app = FastAPI()
DB_NAME = "bank_receipts.db"

# --------------------------------------------------
# تهيئة قاعدة البيانات للعمليات
# --------------------------------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trx_last4 TEXT,
                trx_date TEXT,
                amount REAL
            )
        """)
        conn.commit()

# --------------------------------------------------
# تهيئة قاعدة بيانات المستخدمين
# --------------------------------------------------
def init_users_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_number TEXT UNIQUE,
                user_code TEXT UNIQUE
            )
        """)
        conn.commit()

init_db()
init_users_db()

# --------------------------------------------------
# Templates
# --------------------------------------------------
if not os.path.exists("templates"):
    os.makedirs("templates")

templates = Jinja2Templates(directory="templates")

# --------------------------------------------------
# Routes
# --------------------------------------------------

# صفحة التسجيل
@app.get("/")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
def register_user(request: Request, account_number: str = Form(...)):
    # توليد user_code قوي
    user_code = secrets.token_hex(4)  # 8 أحرف hex
    
    # حفظ المستخدم في قاعدة البيانات
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (account_number, user_code) VALUES (?, ?)",
            (account_number, user_code)
        )
        conn.commit()
    
    # إعادة توجيه إلى صفحة إدخال العمليات
    return RedirectResponse(url="/index", status_code=303)

# صفحة إدخال العمليات
@app.get("/index")
def home(request: Request):
    current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    return templates.TemplateResponse("index.html", {"request": request, "data": {"trx_last4": "", "date_time": current_time, "amount": 0.0}})

# حفظ العملية
@app.post("/confirm")
def confirm_data(
    request: Request,
    trx_last4: str = Form(...),
    amount: float = Form(...),
):
    # توليد التاريخ والوقت لحظيًا عند حفظ البيانات
    date_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (trx_last4, trx_date, amount) VALUES (?, ?, ?)",
            (trx_last4, date_time, amount)
        )
        conn.commit()

    return RedirectResponse(url="/transactions", status_code=303)

# عرض سجل العمليات
@app.get("/transactions")
def view_transactions(request: Request):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id DESC")
        trs = cursor.fetchall()

    total = sum([t["amount"] for t in trs]) if trs else 0
    return templates.TemplateResponse("view.html", {
        "request": request,
        "transactions": trs,
        "total_amount": total
    })

# حذف عملية
@app.post("/delete/{id}")
def delete_transaction(id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (id,))
        conn.commit()
    return RedirectResponse(url="/transactions", status_code=303)

# تصدير PDF
@app.get("/export-pdf")
def export_pdf():
    pdf_file = "transactions_report.pdf"

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id ASC")
        transactions = cursor.fetchall()

    # إنشاء ملف PDF
    doc = SimpleDocTemplate(pdf_file, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # عنوان
    elements.append(Paragraph("سجل العمليات البنكية", styles['Title']))
    elements.append(Spacer(1, 12))

    # إعداد البيانات للجدول
    data = [["رقم العملية (آخر 4 أرقام)", "التاريخ والوقت", "المبلغ"]]
    total_amount = 0
    for trx in transactions:
        data.append([trx["trx_last4"], trx["trx_date"], "%.2f" % trx["amount"]])
        total_amount += trx["amount"]

    # صف الإجمالي
    data.append(["", "الإجمالي الكلي", "%.2f" % total_amount])

    # إعداد الجدول
    table = Table(data, colWidths=[120, 180, 100])
    style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('BACKGROUND', (0,-1), (-1,-1), colors.lightgrey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
    ])
    table.setStyle(style)
    elements.append(table)

    # حفظ PDF
    doc.build(elements)

    # إعادة الملف للتحميل
    return FileResponse(pdf_file, media_type='application/pdf', filename=pdf_file)
