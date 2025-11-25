import os
import sqlite3
from datetime import datetime
import secrets

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# --------------------------------------------------
# FastAPI + DB Setup
# --------------------------------------------------
app = FastAPI()
DB_NAME = "bank_receipts.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # users table 
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT,
                pin TEXT
            )
        """)

        #  transactions table
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

# static
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

# --------------------------------------------------
## 🏠 Start Page
# --------------------------------------------------
@app.get("/")
def start_page(request: Request):
    return templates.TemplateResponse("start_page.html", {"request": request})

# --------------------------------------------------
## 📝 تسجيل مستخدم جديد
# --------------------------------------------------
@app.get("/register")
def show_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
def register_user(
    request: Request,
    user_id: str = Form(...),
    bank_account: str = Form(...),
    # 🟢 تعديل: استقبال PIN من الفورم بدلاً من توليده في الخادم
    pin: str = Form(...) 
):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        c.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                  (user_id, bank_account, pin))
        conn.commit()

        c.execute("SELECT id FROM users WHERE user_id = ?", (user_id,))
        db_user = c.fetchone()

    # عرض صفحة تخبر المستخدم بالـ PIN
    return templates.TemplateResponse(
        "show_pin.html",
        {
            "request": request,
            "pin": pin,
            "user_id": db_user[0]
        }
    )

# --------------------------------------------------
## 🔐 صفحة تسجيل الدخول
# --------------------------------------------------
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login_user(
    request: Request,
    bank_account: str = Form(...),
    pin: str = Form(...)
):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM users WHERE bank_account = ? AND pin = ?",
            (bank_account, pin)
        )
        user = c.fetchone()

    if user:
        response = RedirectResponse(url="/index", status_code=303)
        response.set_cookie("current_user", str(user[0]))
        return response
    else:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "بيانات الدخول غير صحيحة"}
        )

# --------------------------------------------------
## 🧾 transactions home page (Index)
# --------------------------------------------------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")

    if not user_id:
        return RedirectResponse(url="/", status_code=303)

    # 🟢 تعديل: استخدام تنسيق آمن للتاريخ والوقت
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            # يتم تمرير بيانات مبدئية لصفحة index/إدخال عملية جديدة
            "data": {"trx_last4": "", "date_time": current_time, "amount": 0.0},
        }
    )

# --------------------------------------------------
## 💾 save new transaction
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

    # 🟢 استخدام نفس التنسيق الآمن عند حفظ التاريخ
    date_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (user_id, trx_last4, trx_date, amount) VALUES (?, ?, ?, ?)",
            (int(user_id), trx_last4, date_time, amount)
        )
        conn.commit()

    return RedirectResponse(url="/transactions", status_code=303)

# --------------------------------------------------
## 📊 transcations page view
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

    total = sum([float(t["amount"]) for t in trs]) if trs else 0

    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "transactions": trs,
            "total_amount": total
        }
    )

# --------------------------------------------------
## 🗑️ delete transaction
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
## 📄 PDF file Export
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

    doc = SimpleDocTemplate(pdf_file, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # هنا قد تحتاج إلى حزمة خطوط تدعم اللغة العربية في ReportLab
    # (ReportLab لا يدعم العربية افتراضياً بشكل جيد، لكننا نستخدمه كما هو في الكود الأصلي)
    elements.append(Paragraph("transactions log", styles['Title']))
    elements.append(Spacer(1, 12))

    data = [["Last 4 Digit", "Date", "Amount"]]
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
