import os
import sqlite3
from datetime import datetime
import uuid
import hashlib

from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates

app = FastAPI()
DB_NAME = "bank_receipts.db"

# Templates
templates = Jinja2Templates(directory="templates")

# تهيئة جدول المستخدمين
def init_users_table():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_account TEXT UNIQUE NOT NULL,
                user_token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()

init_users_table()

# ----------------------
# Routes
# ----------------------
@app.get("/register")
def show_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "user_token": None})

@app.post("/register")
def register_user(request: Request, bank_account: str = Form(...)):
    bank_account = bank_account.strip()
    user_token = None

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_token FROM users WHERE bank_account = ?", (bank_account,))
        row = cursor.fetchone()

        if row:
            # المستخدم موجود، أرجع token الحالي
            user_token = row[0]
        else:
            # توليد user_token قوي (UUID + hash)
            raw_uuid = str(uuid.uuid4())
            user_token = hashlib.sha256(raw_uuid.encode()).hexdigest()[:16]  # token 16 حرف
            cursor.execute(
                "INSERT INTO users (bank_account, user_token, created_at) VALUES (?, ?, ?)",
                (bank_account, user_token, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()

    return templates.TemplateResponse("register.html", {"request": request, "user_token": user_token})

