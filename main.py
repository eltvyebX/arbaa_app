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

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # جدول المستخدمين
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_code TEXT UNIQUE,
                bank_account TEXT
            )
        """)

        # جدول العمليات
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                trx_last4 TEXT,
                trx_date TEXT,
                amount REAL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        conn.commit()

init_db()

# Templates
templates = Jinja2Templates(directory="templates")

# --------------------------------------------------
# Start Page
# --------------------------------------------------
@app.get("/")
def start_page(request: Request):
    return templates.TemplateResponse("start.html", {"request": request})


# --------------------------------------------------
# تسجيل مستخدم جديد
# --------------------------------------------------
@app.get("/register")
def show_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
def register_user(
    request: Request,
    user_id: str = Form(...),
    bank_account: str = Form(...)
):

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # تخزين المستخدم
        c.execute("INSERT INTO users (user_code, bank_account) VALUES (?, ?)",
                  (user_id, bank_account))
        conn.commit()

        # استرجاع ID
        c.execute("SELECT id FROM users WHERE user_code = ?", (user_id,))
        db_user = c.fetchone()

    # حفظ user_id داخل Cookie
    response = RedirectResponse(url="/index", status_code=303)
    response.set_cookie("current_user", str(db_user[0]))

    return response


# --------------------------------------------------
# الصفحة الرئيسية لإدخال عملية
# --------------------------------------------------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")

    if not user_id:
        return RedirectResponse(url="/", status_code=303)

    current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "data": {"trx_last4": "", "date_time": current_time, "amount": 0.0},
        }
    )


# --------------------------------------------------
# حفظ عملية جديدة
# --------------------------------------------------
@app.post("/confirm")
def confirm_data(
    request: Request,
    trx_last4: str = Form(...),
    amount: float = Form(...)
):

    user_id = request.cookies.get("current_user")

    if not user_id:
        return RedirectResponse(url="/", status_code=303)

    # التاريخ والوقت
    date_time = datetime.now().strftime("%H:%M:%S %d-%m-%Y")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (user_id, trx_last4, trx_date, amount) VALUES (?, ?, ?, ?)",
            (user_id, trx_last4, date_time, amount)
        )
        conn.commit()

    return RedirectResponse(url="/transactions", status_code=303)


# --------------------------------------------------
# صفحة عرض معاملات المستخدم
# --------------------------------------------------
@app.get("/transactions")
def view_transactions(request: Request):

    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        )
        trs = cursor.fetchall()

    total = sum([t["amount"] for t in trs]) if trs else 0

    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "transactions": trs,
            "total_amount": total
        }
    )


# --------------------------------------------------
# حذف عملية
# --------------------------------------------------
@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (id, user_id))
        conn.commit()

    return RedirectResponse(url="/transactions", status_code=303)


# --------------------------------------------------
# ملف PDF
# --------------------------------------------------
@app.get("/export-pdf")
def export_pdf(request: Request):

    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/", status_code=303)

    pdf_file = "transactions_report.pdf"

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        )
        transactions = cursor.fetchall()

    # إنشاء PDF
    doc = SimpleDocTemplate(pdf_file, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("سجل العمليات البنكية", styles['Title']))
    elements.append(Spacer(1, 12))

    data = [["آخر 4 أرقام", "التاريخ", "المبلغ"]]
    total_amount = 0

    for trx in transactions:
        data.append([trx["trx_last4"], trx["trx_date"], "%.2f" % trx["amount"]])
        total_amount += trx["amount"]

    data.append(["", "الإجمالي", "%.2f" % total_amount])

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
