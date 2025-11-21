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

DB_NAME_TRANSACTIONS = "bank_receipts.db"
DB_NAME_USERS = "users.db"

# --------------------------------------------------
# إنشاء قاعدة بيانات المعاملات
# --------------------------------------------------
def init_db_transactions():
    with sqlite3.connect(DB_NAME_TRANSACTIONS) as conn:
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

init_db_transactions()

# --------------------------------------------------
# إنشاء قاعدة بيانات المستخدمين
# --------------------------------------------------
def init_db_users():
    with sqlite3.connect(DB_NAME_USERS) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_code TEXT UNIQUE,
                bank_account TEXT UNIQUE
            )
        """)
        conn.commit()

init_db_users()

# Templates
if not os.path.exists("templates"):
    os.makedirs("templates")

templates = Jinja2Templates(directory="templates")

# --------------------------------------------------
# صفحة تسجيل المستخدم
# --------------------------------------------------
@app.get("/register")
def register_page(request: Request):
    # توليد user_code تلقائي (8 أحرف hex)
    user_code = secrets.token_hex(4)
    return templates.TemplateResponse("register.html", {"request": request, "user_code": user_code})

@app.post("/register")
def register_user(bank_account: str = Form(...), user_code: str = Form(...)):
    with sqlite3.connect(DB_NAME_USERS) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (user_code, bank_account) VALUES (?, ?)",
            (user_code, bank_account)
        )
        conn.commit()
    # بعد التسجيل، إعادة التوجيه إلى صفحة إدخال المعاملات
    return RedirectResponse(url="/", status_code=303)

# --------------------------------------------------
# صفحة إدخال معاملات جديدة
# --------------------------------------------------
@app.get("/")
def home(request: Request):
    current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    return templates.TemplateResponse("index.html", {"request": request, "data": {"trx_last4": "", "date_time": current_time, "amount": 0.0}})

@app.post("/confirm")
def confirm_data(trx_last4: str = Form(...), amount: float = Form(...)):
    date_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    with sqlite3.connect(DB_NAME_TRANSACTIONS) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (trx_last4, trx_date, amount) VALUES (?, ?, ?)",
            (trx_last4, date_time, amount)
        )
        conn.commit()
    return RedirectResponse(url="/transactions", status_code=303)

# --------------------------------------------------
# عرض المعاملات
# --------------------------------------------------
@app.get("/transactions")
def view_transactions(request: Request):
    with sqlite3.connect(DB_NAME_TRANSACTIONS) as conn:
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

@app.post("/delete/{id}")
def delete_transaction(id: int):
    with sqlite3.connect(DB_NAME_TRANSACTIONS) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (id,))
        conn.commit()
    return RedirectResponse(url="/transactions", status_code=303)

# --------------------------------------------------
# تصدير PDF
# --------------------------------------------------
@app.get("/export-pdf")
def export_pdf():
    pdf_file = "transactions_report.pdf"

    with sqlite3.connect(DB_NAME_TRANSACTIONS) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id ASC")
        transactions = cursor.fetchall()

    doc = SimpleDocTemplate(pdf_file, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("سجل العمليات البنكية", styles['Title']))
    elements.append(Spacer(1, 12))

    data = [["رقم العملية (آخر 4 أرقام)", "التاريخ والوقت", "المبلغ"]]
    total_amount = 0
    for trx in transactions:
        data.append([trx["trx_last4"], trx["trx_date"], "%.2f" % trx["amount"]])
        total_amount += trx["amount"]

    data.append(["", "الإجمالي الكلي", "%.2f" % total_amount])

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

    doc.build(elements)
    return FileResponse(pdf_file, media_type='application/pdf', filename=pdf_file)
