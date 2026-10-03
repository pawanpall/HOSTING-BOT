# -*- coding: utf-8 -*-
import telebot
from telebot import util
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests

# --- Flask Keep Alive ---
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I'am Harshu File Host"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")
# --- End Flask Keep Alive ---

# --- Configuration ---
TOKEN = '8632338891:AAH-aa_tnf16tm0aC_wxgn8cGbRTLpBVZxE'
OWNER_ID = 1819675229
ADMIN_ID = 1819675229
YOUR_USERNAME = '@pawanpall'
UPDATE_CHANNEL = '@indiandbms'

# --- Force Subscription Channel ---
REQUIRED_CHANNEL = '@indiandbms'  # Users must join this channel to use the bot

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

FREE_USER_LIMIT = 3
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["📞 Contact Owner"]
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["💳 Subscriptions", "📢 Broadcast"],
    ["🔒 Lock Bot", "🟢 Running All Code"],
    ["👑 Admin Panel", "📞 Contact Owner"]
]

# --- Database Setup (extended with pending_uploads and verified_users) ---
DB_LOCK = threading.Lock()

def init_db():
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS pending_uploads (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     file_id TEXT,
                     file_name TEXT,
                     file_type TEXT,
                     file_size INTEGER,
                     user_name TEXT,
                     user_username TEXT,
                     timestamp TEXT)''')
        # New table for verified users (force subscription)
        c.execute('''CREATE TABLE IF NOT EXISTS verified_users (
                     user_id INTEGER PRIMARY KEY,
                     verified_at TEXT)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
             c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}", exc_info=True)

def load_data():
    logger.info("Loading data from database...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                logger.warning(f"⚠️ Invalid expiry for user {user_id}: {expiry}")
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            user_files.setdefault(user_id, []).append((file_name, file_type))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())
        # Load verified users into memory (optional, we can check DB on the fly)
        conn.close()
        logger.info(f"Data loaded: {len(active_users)} users, {len(user_subscriptions)} subs, {len(admin_ids)} admins.")
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}", exc_info=True)

init_db()
load_data()

# --- Force Subscription & Verification Helpers ---
def is_user_verified(user_id):
    """Check if user has already verified channel membership."""
    if user_id in admin_ids or user_id == OWNER_ID:
        return True
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT 1 FROM verified_users WHERE user_id = ?', (user_id,))
        result = c.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error checking verified user {user_id}: {e}")
        return False

def set_user_verified(user_id):
    """Mark user as verified after they confirm channel membership."""
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO verified_users (user_id, verified_at) VALUES (?, ?)',
                  (user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        logger.info(f"User {user_id} verified channel membership.")
        return True
    except Exception as e:
        logger.error(f"Error setting verified user {user_id}: {e}")
        return False

def is_user_member(user_id, channel_username):
    """Check if user is a member of the required channel."""
    if user_id in admin_ids or user_id == OWNER_ID:
        return True  # Admins & owner always bypass
    try:
        chat_member = bot.get_chat_member(channel_username, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.warning(f"Membership check failed for {user_id}: {e}")
        return False

def send_join_prompt(chat_id, user_id):
    """Send a message asking user to join the channel and then verify."""
    join_url = f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📢 Join Channel", url=join_url))
    markup.add(types.InlineKeyboardButton("✅ Verify", callback_data=f"verify_channel_{user_id}"))
    bot.send_message(chat_id,
                     f"❌ <b>Access Denied</b>\n\nYou must join our channel first to use this bot.\n\n👉 <a href='{join_url}'>JOIN {REQUIRED_CHANNEL}</a>\n\nAfter joining, click <b>Verify</b>.",
                     parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)

def check_subscription_and_continue(message, call=None):
    """If user is not member or not verified, send prompt and return False. Otherwise return True."""
    user_id = message.from_user.id if message else call.from_user.id
    chat_id = message.chat.id if message else call.message.chat.id
    # Admins bypass everything
    if user_id in admin_ids or user_id == OWNER_ID:
        return True
    # Check if already verified
    if is_user_verified(user_id):
        return True
    # Check channel membership
    if is_user_member(user_id, REQUIRED_CHANNEL):
        # Member but not verified: auto-verify now (optional) or send verify button
        # We'll auto-verify to save one click, but if you want explicit button, change this.
        set_user_verified(user_id)
        return True
    else:
        send_join_prompt(chat_id, user_id)
        return False

# --- Verification Callback ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('verify_channel_'))
def verify_channel_callback(call):
    user_id = int(call.data.split('_')[-1])
    if user_id != call.from_user.id:
        bot.answer_callback_query(call.id, "This verification is not for you.", show_alert=True)
        return
    if is_user_verified(user_id):
        bot.answer_callback_query(call.id, "You are already verified.", show_alert=True)
        bot.edit_message_text("✅ You are already verified. You can now use the bot.", 
                              call.message.chat.id, call.message.message_id, parse_mode='HTML')
        return
    if is_user_member(user_id, REQUIRED_CHANNEL):
        set_user_verified(user_id)
        bot.answer_callback_query(call.id, "✅ Verification successful! You can now use the bot.", show_alert=True)
        bot.edit_message_text("✅ Verification successful! You can now use the bot.\nSend /start to begin.", 
                              call.message.chat.id, call.message.message_id, parse_mode='HTML')
    else:
        bot.answer_callback_query(call.id, "❌ You are not a member of the channel yet. Please join first.", show_alert=True)

# --- Helper Functions (unchanged) ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                logger.warning(f"Process {script_info['process'].pid} for {script_key} not running/zombie. Cleaning.")
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try: script_info['log_file'].close()
                    except: pass
                if script_key in bot_scripts: del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            logger.warning(f"Process for {script_key} not found. Cleaning.")
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try: script_info['log_file'].close()
                except: pass
            if script_key in bot_scripts: del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process {script_key}: {e}")
            return False
    return False

def kill_process_tree(process_info):
    pid = None
    log_file_closed = False
    script_key = process_info.get('script_key', 'N/A')
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try:
                process_info['log_file'].close()
                log_file_closed = True
                logger.info(f"Closed log file for {script_key}")
            except Exception as log_e:
                logger.error(f"Error closing log file: {log_e}")
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try:
                            child.terminate()
                        except: pass
                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        try: p.kill()
                        except: pass
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except: parent.kill()
                    except: pass
                except psutil.NoSuchProcess:
                    pass
    except Exception as e:
        logger.error(f"Error killing process tree: {e}")

# --- Automatic Package Installation & Script Running (unchanged but parse_mode HTML) ---
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'psutil': 'psutil',
}
for core in ['asyncio', 'json', 'datetime', 'os', 'sys', 're', 'time', 'math', 'random', 'logging', 'threading', 'subprocess', 'zipfile', 'tempfile', 'shutil', 'sqlite3', 'atexit']:
    TELEGRAM_MODULES[core] = None

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        return False
    try:
        bot.reply_to(message, f"🐍 Installing <code>{package_name}</code>...", parse_mode='HTML')
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', package_name], capture_output=True, text=True)
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Package <code>{package_name}</code> installed.", parse_mode='HTML')
            return True
        else:
            bot.reply_to(message, f"❌ Failed to install <code>{package_name}</code>.", parse_mode='HTML')
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ Install error: {e}")
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Installing Node package <code>{module_name}</code>...", parse_mode='HTML')
        result = subprocess.run(['npm', 'install', module_name], cwd=user_folder, capture_output=True, text=True)
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Node package <code>{module_name}</code> installed.", parse_mode='HTML')
            return True
        else:
            bot.reply_to(message, f"❌ Failed to install <code>{module_name}</code>.", parse_mode='HTML')
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ NPM error: {e}")
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run Python: {script_path}")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ Script '{file_name}' not found!")
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_proc = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                _, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1)
                        if attempt_install_pip(module_name, message_obj_for_reply):
                            bot.reply_to(message_obj_for_reply, f"🔄 Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt+1)).start()
                            return
                        else:
                            bot.reply_to(message_obj_for_reply, f"❌ Missing module <code>{module_name}</code>. Install failed.", parse_mode='HTML')
                            return
            except subprocess.TimeoutExpired:
                check_proc.kill()
                check_proc.communicate()

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8')
        process = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
        bot_scripts[script_key] = {
            'process': process,
            'log_file': log_file,
            'file_name': file_name,
            'chat_id': message_obj_for_reply.chat.id,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(),
            'user_folder': user_folder,
            'type': 'py',
            'script_key': script_key
        }
        bot.reply_to(message_obj_for_reply, f"✅ Python script '{file_name}' started! (PID: {process.pid})")
    except Exception as e:
        bot.reply_to(message_obj_for_reply, f"❌ Error: {e}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run JS: {script_path}")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ JS script '{file_name}' not found!")
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_proc = subprocess.Popen(['node', script_path], cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                _, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1)
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj_for_reply):
                                bot.reply_to(message_obj_for_reply, f"🔄 Retrying '{file_name}'...")
                                time.sleep(2)
                                threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt+1)).start()
                                return
                            else:
                                bot.reply_to(message_obj_for_reply, f"❌ Missing Node module <code>{module_name}</code>.", parse_mode='HTML')
                                return
            except subprocess.TimeoutExpired:
                check_proc.kill()
                check_proc.communicate()

        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8')
        process = subprocess.Popen(['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
        bot_scripts[script_key] = {
            'process': process,
            'log_file': log_file,
            'file_name': file_name,
            'chat_id': message_obj_for_reply.chat.id,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(),
            'user_folder': user_folder,
            'type': 'js',
            'script_key': script_key
        }
        bot.reply_to(message_obj_for_reply, f"✅ JS script '{file_name}' started! (PID: {process.pid})")
    except Exception as e:
        bot.reply_to(message_obj_for_reply, f"❌ Error: {e}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

# --- Database Operations (original + pending) ---
def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            user_files.setdefault(user_id, [])
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e:
            logger.error(f"Error saving file: {e}")
        finally:
            conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]:
                    del user_files[user_id]
        except Exception as e:
            logger.error(f"Error removing file: {e}")
        finally:
            conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error adding active user: {e}")
        finally:
            conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            expiry_str = expiry.isoformat()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry_str))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"Error saving subscription: {e}")
        finally:
            conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions:
                del user_subscriptions[user_id]
        except Exception as e:
            logger.error(f"Error removing subscription: {e}")
        finally:
            conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
        except Exception as e:
            logger.error(f"Error adding admin: {e}")
        finally:
            conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            if c.rowcount > 0:
                admin_ids.discard(admin_id)
                return True
            return False
        except Exception as e:
            logger.error(f"Error removing admin: {e}")
            return False
        finally:
            conn.close()

# --- Pending Upload Database Functions ---
def add_pending_upload(user_id, file_id, file_name, file_type, file_size, user_name, user_username):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            c.execute('''INSERT INTO pending_uploads
                         (user_id, file_id, file_name, file_type, file_size, user_name, user_username, timestamp)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                      (user_id, file_id, file_name, file_type, file_size, user_name, user_username, timestamp))
            conn.commit()
            return c.lastrowid
        except Exception as e:
            logger.error(f"Error adding pending upload: {e}")
            return None
        finally:
            conn.close()

def get_pending_upload(upload_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('SELECT id, user_id, file_id, file_name, file_type, file_size, user_name, user_username FROM pending_uploads WHERE id = ?', (upload_id,))
            row = c.fetchone()
            if row:
                return {'id': row[0], 'user_id': row[1], 'file_id': row[2], 'file_name': row[3],
                        'file_type': row[4], 'file_size': row[5], 'user_name': row[6], 'user_username': row[7]}
            return None
        except Exception as e:
            logger.error(f"Error getting pending upload: {e}")
            return None
        finally:
            conn.close()

def delete_pending_upload(upload_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM pending_uploads WHERE id = ?', (upload_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error deleting pending upload: {e}")
            return False
        finally:
            conn.close()

# --- File Processing after Admin Approval (unchanged) ---
def process_approved_file(upload_id, admin_chat_id, user_message_obj=None):
    pending = get_pending_upload(upload_id)
    if not pending:
        bot.send_message(admin_chat_id, f"❌ Pending upload {upload_id} not found.")
        return False

    user_id = pending['user_id']
    file_id = pending['file_id']
    file_name = pending['file_name']
    file_ext = os.path.splitext(file_name)[1].lower()
    file_type = pending['file_type']

    # Re-check file limit
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.send_message(admin_chat_id, f"⚠️ User limit reached ({current_files}/{limit_str}). Cannot approve.")
        delete_pending_upload(upload_id)
        return False

    try:
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        user_folder = get_user_folder(user_id)

        if file_ext == '.zip':
            temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
            zip_path = os.path.join(temp_dir, file_name)
            with open(zip_path, 'wb') as f:
                f.write(downloaded)
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(temp_dir)
            extracted = os.listdir(temp_dir)
            py_files = [f for f in extracted if f.endswith('.py')]
            js_files = [f for f in extracted if f.endswith('.js')]
            req_file = 'requirements.txt' if 'requirements.txt' in extracted else None
            pkg_json = 'package.json' if 'package.json' in extracted else None

            if req_file:
                try:
                    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(temp_dir, req_file)], check=True, capture_output=True)
                    bot.send_message(admin_chat_id, f"✅ Python deps installed.")
                except Exception as e:
                    bot.send_message(admin_chat_id, f"❌ Python deps failed: {e}")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    delete_pending_upload(upload_id)
                    return False
            if pkg_json:
                try:
                    subprocess.run(['npm', 'install'], cwd=temp_dir, check=True, capture_output=True)
                    bot.send_message(admin_chat_id, f"✅ Node deps installed.")
                except Exception as e:
                    bot.send_message(admin_chat_id, f"❌ Node deps failed: {e}")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    delete_pending_upload(upload_id)
                    return False

            # Find main script
            main_script = None
            for p in ['main.py', 'bot.py', 'app.py']:
                if p in py_files:
                    main_script = p
                    file_type = 'py'
                    break
            if not main_script:
                for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                    if p in js_files:
                        main_script = p
                        file_type = 'js'
                        break
            if not main_script and py_files:
                main_script = py_files[0]
                file_type = 'py'
            elif not main_script and js_files:
                main_script = js_files[0]
                file_type = 'js'

            if not main_script:
                bot.send_message(admin_chat_id, "❌ No .py or .js script found in zip.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                delete_pending_upload(upload_id)
                return False

            for item in os.listdir(temp_dir):
                src = os.path.join(temp_dir, item)
                dst = os.path.join(user_folder, item)
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                elif os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)
            shutil.rmtree(temp_dir, ignore_errors=True)
            save_user_file(user_id, main_script, file_type)
            script_path = os.path.join(user_folder, main_script)
            if file_type == 'py':
                threading.Thread(target=run_script, args=(script_path, user_id, user_folder, main_script, user_message_obj)).start()
            else:
                threading.Thread(target=run_js_script, args=(script_path, user_id, user_folder, main_script, user_message_obj)).start()
            bot.send_message(admin_chat_id, f"✅ Approved and started: {main_script}")
            return True

        else:  # single .py or .js
            file_path = os.path.join(user_folder, file_name)
            with open(file_path, 'wb') as f:
                f.write(downloaded)
            save_user_file(user_id, file_name, file_type)
            if file_type == 'py':
                threading.Thread(target=run_script, args=(file_path, user_id, user_folder, file_name, user_message_obj)).start()
            else:
                threading.Thread(target=run_js_script, args=(file_path, user_id, user_folder, file_name, user_message_obj)).start()
            bot.send_message(admin_chat_id, f"✅ Approved and started: {file_name}")
            return True

    except Exception as e:
        logger.error(f"Error in process_approved_file: {e}", exc_info=True)
        bot.send_message(admin_chat_id, f"❌ Error: {e}")
        return False
    finally:
        delete_pending_upload(upload_id)

# --- Document Handler (with admin approval + verification) ---
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    if not check_subscription_and_continue(message):
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document
    logger.info(f"Document from {user_id}: {doc.file_name}")

    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked, cannot accept files.")
        return

    # Initial limit check
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached.")
        return

    file_name = doc.file_name
    if not file_name:
        bot.reply_to(message, "⚠️ No file name.")
        return
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Only .py, .js, .zip allowed.")
        return
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ File too large (max 20MB).")
        return

    # Save to pending_uploads table
    user_name = message.from_user.first_name
    user_username = message.from_user.username or "No username"
    upload_id = add_pending_upload(
        user_id=user_id,
        file_id=doc.file_id,
        file_name=file_name,
        file_type=file_ext[1:],
        file_size=doc.file_size,
        user_name=user_name,
        user_username=user_username
    )
    if not upload_id:
        bot.reply_to(message, "❌ Internal error, please try later.")
        return

    bot.reply_to(message, f"✅ File <code>{file_name}</code> submitted for admin approval. You will be notified when approved or rejected.", parse_mode='HTML')

    # Notify all admins
    for admin_id in admin_ids:
        try:
            caption = (f"📥 New file requires approval\n"
                       f"👤 User: {user_name} (@{user_username})\n"
                       f"🆔 User ID: <code>{user_id}</code>\n"
                       f"📄 File: <code>{file_name}</code>\n"
                       f"📏 Size: {doc.file_size // 1024} KB\n"
                       f"🆔 Upload ID: <code>{upload_id}</code>")
            sent = bot.send_document(admin_id, doc.file_id, caption=caption, parse_mode='HTML')
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_upload_{upload_id}"),
                types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload_{upload_id}")
            )
            bot.edit_message_reply_markup(admin_id, sent.message_id, reply_markup=markup)
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

# --- Approval / Rejection Callback (with verification) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_upload_') or call.data.startswith('reject_upload_'))
def handle_approval_callback(call):
    if not check_subscription_and_continue(None, call):
        return

    admin_id = call.from_user.id
    if admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Only admins can approve/reject.", show_alert=True)
        return

    upload_id = int(call.data.split('_')[-1])
    pending = get_pending_upload(upload_id)
    if not pending:
        bot.answer_callback_query(call.id, "⚠️ This upload request no longer exists.", show_alert=True)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        return

    user_id = pending['user_id']
    file_name = pending['file_name']

    if call.data.startswith('approve_upload_'):
        bot.answer_callback_query(call.id, "✅ Approving and starting...")
        success = process_approved_file(upload_id, admin_chat_id=call.message.chat.id, user_message_obj=call.message)
        if success:
            try:
                bot.send_message(user_id, f"✅ Your file <code>{file_name}</code> has been approved and is now running.", parse_mode='HTML')
            except Exception as e:
                logger.error(f"Could not notify user {user_id}: {e}")
            try:
                bot.edit_message_caption(
                    caption=call.message.caption + "\n\n✅ <b>APPROVED</b>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode='HTML',
                    reply_markup=None
                )
            except: pass
        else:
            bot.send_message(call.message.chat.id, f"❌ Failed to process file for user {user_id}.")
    else:  # reject
        bot.answer_callback_query(call.id, "❌ Rejected.")
        delete_pending_upload(upload_id)
        reject_msg = "AGLI BAR SE YE FILE RUN MT KARNA SIR"
        try:
            bot.send_message(user_id, f"❌ Your file <code>{file_name}</code> was rejected by admin.\n\n{reject_msg}", parse_mode='HTML')
        except Exception as e:
            logger.error(f"Could not notify user {user_id}: {e}")
        try:
            bot.edit_message_caption(
                caption=call.message.caption + "\n\n❌ <b>REJECTED</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=None
            )
        except: pass

# --- Menu Creation (unchanged) ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot', callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts')
        ]
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], admin_buttons[0])
        markup.add(admin_buttons[1], admin_buttons[3])
        markup.add(admin_buttons[2], admin_buttons[5])
        markup.add(admin_buttons[4])
        markup.add(buttons[4])
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3])
        markup.add(types.InlineKeyboardButton('📊 Statistics', callback_data='stats'))
        markup.add(buttons[4])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}'))
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# --- Logic Functions (all include verification via check_subscription_and_continue) ---
def _logic_send_welcome(message):
    if not check_subscription_and_continue(message):
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username or "Not set"
    
    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin.")
        return
    
    if user_id not in active_users:
        add_active_user(user_id)
        try:
            owner_msg = (f"🎉 New user!\n👤 {user_name}\n✳️ @{user_username}\n🆔 <code>{user_id}</code>")
            bot.send_message(OWNER_ID, owner_msg, parse_mode='HTML')
        except: pass
    
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    
    expiry_info = ""
    if user_id == OWNER_ID:
        user_status = "👑 Owner"
    elif user_id in admin_ids:
        user_status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry = user_subscriptions[user_id]['expiry']
        if expiry > datetime.now():
            user_status = "⭐ Premium"
            days_left = (expiry - datetime.now()).days
            expiry_info = f"\n⏳ Expires in {days_left} days"
        else:
            user_status = "🆓 Free (Expired)"
            remove_subscription_db(user_id)
    else:
        user_status = "🆓 Free User"
    
    welcome_text = (f"〽️ Welcome, {user_name}!\n\n"
                    f"🆔 ID: <code>{user_id}</code>\n"
                    f"✳️ @{user_username}\n"
                    f"🔰 Status: {user_status}{expiry_info}\n"
                    f"📁 Files: {current_files}/{limit_str}\n\n"
                    f"🤖 Upload <code>.py</code>, <code>.js</code> or <code>.zip</code> files. They require admin approval first.\n\n"
                    f"👇 Use buttons.")
    
    bot.send_message(chat_id, welcome_text,
                     reply_markup=create_reply_keyboard_main_menu(user_id),
                     parse_mode='HTML')

def _logic_updates_channel(message):
    if not check_subscription_and_continue(message):
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    bot.reply_to(message, "📢 Our channel:", reply_markup=markup)

def _logic_upload_file(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked.")
        return
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ Limit reached ({current_files}/{limit_str}). Delete files first.")
        return
    bot.reply_to(message, "📤 Send your .py, .js or .zip file. It will be sent to admins for approval.")

def _logic_check_files(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, "📂 No files uploaded yet.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in sorted(files):
        is_running = is_bot_running(user_id, fname)
        status = "🟢 Running" if is_running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{fname} ({ftype}) - {status}", callback_data=f'file_{user_id}_{fname}'))
    bot.reply_to(message, "📂 Your files:", reply_markup=markup)

def _logic_bot_speed(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    start = time.time()
    wait = bot.reply_to(message, "🏃 Testing speed...")
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        latency = round((time.time() - start) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: level = "👑 Owner"
        elif user_id in admin_ids: level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now(): level = "⭐ Premium"
        else: level = "🆓 Free"
        msg = f"⚡ Bot Speed\n⏱️ Response: {latency} ms\n🚦 Status: {status}\n👤 Level: {level}"
        bot.edit_message_text(msg, message.chat.id, wait.message_id)
    except Exception as e:
        bot.edit_message_text("❌ Speed test error.", message.chat.id, wait.message_id)

def _logic_contact_owner(message):
    if not check_subscription_and_continue(message):
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "Contact owner:", reply_markup=markup)

def _logic_statistics(message):
    if not check_subscription_and_continue(message):
        return
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files = sum(len(f) for f in user_files.values())
    running = sum(1 for k, v in bot_scripts.items() if is_bot_running(int(k.split('_')[0]), v['file_name']))
    if user_id in admin_ids:
        msg = f"📊 Statistics\n👥 Users: {total_users}\n📂 Files: {total_files}\n🟢 Running: {running}\n🔒 Bot locked: {bot_locked}"
    else:
        msg = f"📊 Statistics\n👥 Users: {total_users}\n📂 Files: {total_files}\n🟢 Running: {running}"
    bot.reply_to(message, msg)

def _logic_subscriptions_panel(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    bot.reply_to(message, "💳 Subscription Management", reply_markup=create_subscription_menu())

def _logic_broadcast_init(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    msg = bot.reply_to(message, "📢 Send broadcast message.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Broadcast cancelled.")
        return
    content = message.text
    if not content:
        bot.reply_to(message, "Cannot broadcast empty text.")
        return
    target = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_broadcast_{message.message_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
    )
    bot.reply_to(message, f"⚠️ Confirm broadcast to {target} users:\n\n<pre>{content[:500]}</pre>", reply_markup=markup, parse_mode='HTML')

def handle_confirm_broadcast(call):
    admin_id = call.from_user.id
    if admin_id not in admin_ids:
        bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
        return
    original = call.message.reply_to_message
    if not original or not original.text:
        bot.answer_callback_query(call.id, "No broadcast message found.")
        return
    text = original.text
    bot.answer_callback_query(call.id, "Broadcasting...")
    bot.edit_message_text("📢 Broadcasting...", call.message.chat.id, call.message.message_id, reply_markup=None)
    threading.Thread(target=execute_broadcast, args=(text, call.message.chat.id)).start()

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)

def execute_broadcast(text, admin_chat_id):
    sent = 0
    failed = 0
    for uid in list(active_users):
        try:
            bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.05)
    bot.send_message(admin_chat_id, f"📢 Broadcast done.\n✅ Sent: {sent}\n❌ Failed: {failed}")

def _logic_toggle_lock_bot(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    bot.reply_to(message, f"🔒 Bot {status}.")

def _logic_admin_panel(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    bot.reply_to(message, "👑 Admin Panel", reply_markup=create_admin_panel())

def _logic_run_all_scripts(message):
    if not check_subscription_and_continue(message):
        return
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    bot.reply_to(message, "⏳ Starting all user scripts...")
    started = 0
    for uid, files in list(user_files.items()):
        folder = get_user_folder(uid)
        for fname, ftype in files:
            if not is_bot_running(uid, fname):
                path = os.path.join(folder, fname)
                if os.path.exists(path):
                    if ftype == 'py':
                        threading.Thread(target=run_script, args=(path, uid, folder, fname, message)).start()
                    else:
                        threading.Thread(target=run_js_script, args=(path, uid, folder, fname, message)).start()
                    started += 1
                    time.sleep(0.5)
    bot.send_message(message.chat.id, f"✅ Attempted to start {started} scripts.")

# --- Command Handlers & Text Handlers ---
BUTTON_TEXT_TO_LOGIC = {
    "📢 Updates Channel": _logic_updates_channel,
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📞 Contact Owner": _logic_contact_owner,
    "📊 Statistics": _logic_statistics,
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot,
    "🟢 Running All Code": _logic_run_all_scripts,
    "👑 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    if not check_subscription_and_continue(message):
        return
    BUTTON_TEXT_TO_LOGIC[message.text](message)

@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    if not check_subscription_and_continue(message):
        return
    _logic_send_welcome(message)

@bot.message_handler(commands=['uploadfile'])
def cmd_upload(message):
    if not check_subscription_and_continue(message):
        return
    _logic_upload_file(message)

@bot.message_handler(commands=['checkfiles'])
def cmd_check(message):
    if not check_subscription_and_continue(message):
        return
    _logic_check_files(message)

@bot.message_handler(commands=['botspeed'])
def cmd_speed(message):
    if not check_subscription_and_continue(message):
        return
    _logic_bot_speed(message)

@bot.message_handler(commands=['statistics'])
def cmd_stats(message):
    if not check_subscription_and_continue(message):
        return
    _logic_statistics(message)

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if not check_subscription_and_continue(message):
        return
    _logic_broadcast_init(message)

@bot.message_handler(commands=['lockbot'])
def cmd_lock(message):
    if not check_subscription_and_continue(message):
        return
    _logic_toggle_lock_bot(message)

@bot.message_handler(commands=['adminpanel'])
def cmd_admin(message):
    if not check_subscription_and_continue(message):
        return
    _logic_admin_panel(message)

@bot.message_handler(commands=['runningallcode'])
def cmd_runall(message):
    if not check_subscription_and_continue(message):
        return
    _logic_run_all_scripts(message)

@bot.message_handler(commands=['ping'])
def ping(message):
    if not check_subscription_and_continue(message):
        return
    start = time.time()
    m = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start) * 1000, 2)
    bot.edit_message_text(f"Pong! {latency} ms", message.chat.id, m.message_id)

# --- Callback Query Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    # Verification bypass for admin and owner is handled inside check_subscription_and_continue
    # But we need to call it at the start of every callback that requires access.
    # For callbacks that are part of the "verify" flow, we must not block them.
    if call.data.startswith('verify_channel_'):
        # let the verify handler run without subscription check
        verify_channel_callback(call)
        return
    
    # For all other callbacks, enforce verification
    if not check_subscription_and_continue(None, call):
        return

    global bot_locked
    user_id = call.from_user.id
    data = call.data
    logger.info(f"Callback: {user_id} - {data}")

    if bot_locked and user_id not in admin_ids and data not in ['speed', 'stats', 'back_to_main']:
        bot.answer_callback_query(call.id, "Bot locked.", show_alert=True)
        return

    if data == 'upload':
        _logic_upload_file(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'check_files':
        _logic_check_files(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'speed':
        _logic_bot_speed(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'stats':
        _logic_statistics(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'back_to_main':
        _logic_send_welcome(call.message)
        bot.answer_callback_query(call.id)
    elif data == 'subscription':
        if user_id in admin_ids:
            _logic_subscriptions_panel(call.message)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data == 'broadcast':
        if user_id in admin_ids:
            _logic_broadcast_init(call.message)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data == 'lock_bot':
        if user_id in admin_ids:
            bot_locked = True
            bot.answer_callback_query(call.id, "Bot locked.")
            _logic_send_welcome(call.message)
    elif data == 'unlock_bot':
        if user_id in admin_ids:
            bot_locked = False
            bot.answer_callback_query(call.id, "Bot unlocked.")
            _logic_send_welcome(call.message)
    elif data == 'run_all_scripts':
        if user_id in admin_ids:
            _logic_run_all_scripts(call.message)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data == 'admin_panel':
        if user_id in admin_ids:
            _logic_admin_panel(call.message)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data == 'add_admin':
        if user_id == OWNER_ID:
            msg = bot.send_message(call.message.chat.id, "👑 Enter user ID to add as admin.\n/cancel")
            bot.register_next_step_handler(msg, process_add_admin_id)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
    elif data == 'remove_admin':
        if user_id == OWNER_ID:
            msg = bot.send_message(call.message.chat.id, "👑 Enter admin ID to remove.\n/cancel")
            bot.register_next_step_handler(msg, process_remove_admin_id)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Owner only.", show_alert=True)
    elif data == 'list_admins':
        if user_id in admin_ids:
            admins_str = "\n".join(f"- <code>{aid}</code> {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(admin_ids))
            bot.send_message(call.message.chat.id, f"👑 Admins:\n{admins_str}", parse_mode='HTML')
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data == 'add_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "💳 Enter <code>user_id days</code> (e.g., <code>12345678 30</code>)\n/cancel", parse_mode='HTML')
            bot.register_next_step_handler(msg, process_add_subscription)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data == 'remove_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "💳 Enter user ID to remove subscription.\n/cancel")
            bot.register_next_step_handler(msg, process_remove_subscription)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data == 'check_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "💳 Enter user ID to check subscription.\n/cancel")
            bot.register_next_step_handler(msg, process_check_subscription)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Admin only.", show_alert=True)
    elif data.startswith('confirm_broadcast_'):
        handle_confirm_broadcast(call)
    elif data == 'cancel_broadcast':
        handle_cancel_broadcast(call)
    elif data.startswith('file_'):
        file_control_callback(call)
    elif data.startswith('start_'):
        start_bot_callback(call)
    elif data.startswith('stop_'):
        stop_bot_callback(call)
    elif data.startswith('restart_'):
        restart_bot_callback(call)
    elif data.startswith('delete_'):
        delete_bot_callback(call)
    elif data.startswith('logs_'):
        logs_bot_callback(call)
    else:
        bot.answer_callback_query(call.id, "Unknown action.")

# --- Admin & Subscription processing helpers (unchanged but using HTML) ---
def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        aid = int(message.text.strip())
        if aid == OWNER_ID:
            bot.reply_to(message, "Owner is already admin.")
            return
        add_admin_db(aid)
        bot.reply_to(message, f"✅ User {aid} is now admin.")
    except:
        bot.reply_to(message, "Invalid ID. Use numeric ID.")

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        aid = int(message.text.strip())
        if aid == OWNER_ID:
            bot.reply_to(message, "Cannot remove owner.")
            return
        if remove_admin_db(aid):
            bot.reply_to(message, f"✅ Admin {aid} removed.")
        else:
            bot.reply_to(message, "User was not admin.")
    except:
        bot.reply_to(message, "Invalid ID.")

def process_add_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        parts = message.text.split()
        uid = int(parts[0])
        days = int(parts[1])
        current = user_subscriptions.get(uid, {}).get('expiry')
        start = current if current and current > datetime.now() else datetime.now()
        new_expiry = start + timedelta(days=days)
        save_subscription(uid, new_expiry)
        bot.reply_to(message, f"✅ Subscription for {uid} added. Expires {new_expiry.strftime('%Y-%m-%d')}")
    except:
        bot.reply_to(message, "Invalid format. Use <code>user_id days</code>", parse_mode='HTML')

def process_remove_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid in user_subscriptions:
            remove_subscription_db(uid)
            bot.reply_to(message, f"✅ Subscription removed for {uid}")
        else:
            bot.reply_to(message, "User has no active subscription.")
    except:
        bot.reply_to(message, "Invalid user ID.")

def process_check_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid in user_subscriptions:
            exp = user_subscriptions[uid]['expiry']
            if exp > datetime.now():
                days = (exp - datetime.now()).days
                bot.reply_to(message, f"✅ User {uid} has active sub. Expires {exp.strftime('%Y-%m-%d')} ({days} days left)")
            else:
                bot.reply_to(message, f"⚠️ User {uid} subscription expired on {exp.strftime('%Y-%m-%d')}")
        else:
            bot.reply_to(message, f"ℹ️ User {uid} has no subscription.")
    except:
        bot.reply_to(message, "Invalid user ID.")

# --- File control callbacks (unchanged, but using HTML) ---
def file_control_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, "You can only manage your own files.", show_alert=True)
            _logic_check_files(call.message)
            return
        files = user_files.get(owner_id, [])
        if not any(f[0] == file_name for f in files):
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            _logic_check_files(call.message)
            return
        is_running = is_bot_running(owner_id, file_name)
        ftype = next((f[1] for f in files if f[0] == file_name), '?')
        text = f"⚙️ Controls for <code>{file_name}</code> ({ftype}) of User <code>{owner_id}</code>\nStatus: {'🟢 Running' if is_running else '🔴 Stopped'}"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, is_running), parse_mode='HTML')
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"file_control error: {e}")

def start_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, "Permission denied.", show_alert=True)
            return
        if is_bot_running(owner_id, file_name):
            bot.answer_callback_query(call.id, "Already running.", show_alert=True)
            return
        files = user_files.get(owner_id, [])
        ftype = next((f[1] for f in files if f[0] == file_name), None)
        if not ftype:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            return
        folder = get_user_folder(owner_id)
        path = os.path.join(folder, file_name)
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, "File missing.", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Starting {file_name}...")
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner_id, folder, file_name, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(path, owner_id, folder, file_name, call.message)).start()
        time.sleep(1)
        is_running = is_bot_running(owner_id, file_name)
        text = f"⚙️ Controls for <code>{file_name}</code> ({ftype}) of User <code>{owner_id}</code>\nStatus: {'🟢 Running' if is_running else '🟡 Starting...'}"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, is_running), parse_mode='HTML')
    except Exception as e:
        logger.error(f"start error: {e}")

def stop_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, "Permission denied.", show_alert=True)
            return
        if not is_bot_running(owner_id, file_name):
            bot.answer_callback_query(call.id, "Not running.", show_alert=True)
            return
        script_key = f"{owner_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
        bot.answer_callback_query(call.id, f"Stopped {file_name}.")
        files = user_files.get(owner_id, [])
        ftype = next((f[1] for f in files if f[0] == file_name), '?')
        text = f"⚙️ Controls for <code>{file_name}</code> ({ftype}) of User <code>{owner_id}</code>\nStatus: 🔴 Stopped"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, False), parse_mode='HTML')
    except Exception as e:
        logger.error(f"stop error: {e}")

def restart_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, "Permission denied.", show_alert=True)
            return
        script_key = f"{owner_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
        time.sleep(1)
        files = user_files.get(owner_id, [])
        ftype = next((f[1] for f in files if f[0] == file_name), None)
        if not ftype:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            return
        folder = get_user_folder(owner_id)
        path = os.path.join(folder, file_name)
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, "File missing.", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"Restarting {file_name}...")
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner_id, folder, file_name, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(path, owner_id, folder, file_name, call.message)).start()
        time.sleep(1)
        is_running = is_bot_running(owner_id, file_name)
        text = f"⚙️ Controls for <code>{file_name}</code> ({ftype}) of User <code>{owner_id}</code>\nStatus: {'🟢 Running' if is_running else '🟡 Starting...'}"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=create_control_buttons(owner_id, file_name, is_running), parse_mode='HTML')
    except Exception as e:
        logger.error(f"restart error: {e}")

def delete_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, "Permission denied.", show_alert=True)
            return
        script_key = f"{owner_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]
        folder = get_user_folder(owner_id)
        file_path = os.path.join(folder, file_name)
        log_path = os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")
        for p in (file_path, log_path):
            if os.path.exists(p):
                try: os.remove(p)
                except: pass
        remove_user_file_db(owner_id, file_name)
        bot.answer_callback_query(call.id, f"Deleted {file_name}.")
        bot.edit_message_text(f"🗑️ Deleted <code>{file_name}</code> (User <code>{owner_id}</code>)", call.message.chat.id, call.message.message_id, parse_mode='HTML')
    except Exception as e:
        logger.error(f"delete error: {e}")

def logs_bot_callback(call):
    try:
        _, owner_id_str, file_name = call.data.split('_', 2)
        owner_id = int(owner_id_str)
        if call.from_user.id != owner_id and call.from_user.id not in admin_ids:
            bot.answer_callback_query(call.id, "Permission denied.", show_alert=True)
            return
        folder = get_user_folder(owner_id)
        log_path = os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, "No logs yet.", show_alert=True)
            return
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()
        if len(logs) > 4000:
            logs = logs[-4000:]
            logs = "...\n" + logs
        bot.send_message(call.message.chat.id, f"📜 Logs for <code>{file_name}</code>:\n<pre>{logs}</pre>", parse_mode='HTML')
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"logs error: {e}")

# --- Cleanup and Main ---
def cleanup():
    logger.warning("Shutting down, killing all scripts...")
    for key, info in list(bot_scripts.items()):
        kill_process_tree(info)
    logger.warning("Cleanup done.")
atexit.register(cleanup)

if __name__ == '__main__':
    logger.info("🤖 Bot starting...")
    keep_alive()
    logger.info("🚀 Polling started.")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
