import asyncio
import logging
import os
import random
import json
import qrcode
import io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.users import GetFullUserRequest

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")

ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "123456789")) # Apni Telegram ID yahan dalein

SESSION_DIR = "sessions"
os.makedirs(SESSION_DIR, exist_ok=True)

DATA_FILE = "bot_data.json"
USER_ACCOUNTS = []
IS_RUNNING = False
TOTAL_MESSAGES_SENT = 0

logging.basicConfig(level=logging.INFO)
LOGIN_DATA = {}

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"subscriptions": {}, "accounts_data": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading data: {e}")
        return {"subscriptions": {}, "accounts_data": {}}

def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving data: {e}")

def get_user_subscription(user_id):
    data = load_data()
    subs = data.get("subscriptions", {})
    
    if user_id == ADMIN_USER_ID:
        return {"active": True, "expires": "Lifetime", "raw_expiry": None}
        
    str_id = str(user_id)
    if str_id in subs:
        expiry_str = subs[str_id]["expires"]
        expiry_time = datetime.fromisoformat(expiry_str)
        if datetime.now() < expiry_time:
            remaining = expiry_time - datetime.now()
            days = remaining.days
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            time_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"
            return {"active": True, "expires": time_str, "raw_expiry": expiry_time}
        else:
            return {"active": False, "expires": "Expired", "raw_expiry": expiry_time}
    return {"active": False, "expires": "No Access", "raw_expiry": None}

async def check_access(update: Update):
    user = update.effective_user
    if not user:
        return False
    
    if user.id == ADMIN_USER_ID:
        return True
        
    sub = get_user_subscription(user.id)
    if not sub["active"]:
        await update.effective_message.reply_text(
            "❌ <b>Your time is up!</b>\n\n"
            "If you want to purchase more time or get access, contact the admin.",
            parse_mode="HTML"
        )
        return False
    return True

async def keep_client_alive(client):
    while True:
        try:
            if client.is_connected():
                await client(UpdateStatusRequest(offline=False))
                await client(GetFullUserRequest('me'))
        except Exception as e:
            print(f"Ping Error: {e}")
        await asyncio.sleep(20)

def setup_dm_handler(client, acc):
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def dm_auto_responder(event):
        auto_reply_text = acc.get('auto_reply', '')
        if auto_reply_text:
            try:
                await event.reply(auto_reply_text)
            except Exception as e:
                print(f"DM Reply Error for {acc['phone']}: {e}")

async def load_saved_sessions():
    global USER_ACCOUNTS
    if not os.path.exists(SESSION_DIR):
        return

    data = load_data()
    accounts_meta = data.get("accounts_data", {})

    for filename in os.listdir(SESSION_DIR):
        if filename.endswith(".session"):
            session_name = filename[:-8]  
            session_path = os.path.join(SESSION_DIR, session_name)
            client = TelegramClient(session_path, API_ID, API_HASH)
            try:
                await client.connect()
                if await client.is_user_authorized():
                    me = await client.get_me()
                    phone = f"+{me.phone}" if me and me.phone else session_name
                    
                    if not any(acc['phone'] == phone for acc in USER_ACCOUNTS):
                        asyncio.create_task(keep_client_alive(client))
                        
                        meta = accounts_meta.get(phone, {})
                        acc_dict = {
                            'phone': phone,
                            'client': client,
                            'active': True,
                            'messages': meta.get('messages', []),
                            'group_delay': meta.get('group_delay', 2),
                            'resend_interval': meta.get('resend_interval', 60),
                            'auto_reply': meta.get('auto_reply', '')
                        }
                        setup_dm_handler(client, acc_dict)
                        USER_ACCOUNTS.append(acc_dict)
                else:
                    await client.disconnect()
            except Exception as e:
                print(f"Failed to load session {filename}: {e}")

def save_account_meta_to_disk():
    data = load_data()
    acc_data = {}
    for acc in USER_ACCOUNTS:
        acc_data[acc['phone']] = {
            'messages': acc['messages'],
            'group_delay': acc.get('group_delay', 2),
            'resend_interval': acc.get('resend_interval', 60),
            'auto_reply': acc.get('auto_reply', '')
        }
    data["accounts_data"] = acc_data
    save_data(data)

# --- MAIN MENU INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(update):
        return

    sub = get_user_subscription(user.id)
    time_left = sub["expires"]
    
    status_str = "▶️ RUNNING" if IS_RUNNING else "⏹ STOPPED"
    active_count = sum(1 for acc in USER_ACCOUNTS if acc['active'])
    
    speed_str = "2s"
    cycle_str = "60s"
    if USER_ACCOUNTS:
        speed_str = f"{USER_ACCOUNTS[0].get('group_delay', 2)}s"
        cycle_str = f"{USER_ACCOUNTS[0].get('resend_interval', 60)}s"

    text = (
        "✨ <b>Master Control Bot</b> ✨\n"
        "👤 <b>User Panel</b>\n"
        f"⏳ <b>Time:</b> {time_left}\n\n"
        f"📊 <b>Accounts:</b> {len(USER_ACCOUNTS)} (Running: {active_count})\n"
        f"📨 <b>Messages Sent:</b> {TOTAL_MESSAGES_SENT}\n"
        f"⚡ <b>Speed:</b> {speed_str} | 🔄 <b>Cycle:</b> {cycle_str}"
    )

    # EXACT BUTTON LAYOUT REQUESTED
    keyboard = [
        [
            InlineKeyboardButton("▶️ Start All", callback_data="start_all"),
            InlineKeyboardButton("⏹ Stop All", callback_data="stop_all")
        ],
        [
            InlineKeyboardButton("⚙️ Settings Menu", callback_data="settings_menu")
        ],
        [
            InlineKeyboardButton("🔑 Session Login", callback_data="login_session"),
            InlineKeyboardButton("📱 Phone Login", callback_data="login_phone")
        ],
        [
            InlineKeyboardButton("🗑 Delete Account", callback_data="delete_account_menu")
        ]
    ]

    markup = InlineKeyboardMarkup(keyboard)
     
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
    elif update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

# --- SETTINGS MENU (SEPARATED SPEED & CYCLE) ---
async def settings_menu(query):
    keyboard = [
        [InlineKeyboardButton("📝 Set Broadcast Messages", callback_data="set_broadcast_msg")],
        [InlineKeyboardButton("🤖 Set DM Auto-Reply", callback_data="set_dm_msg")],
        [InlineKeyboardButton("⚡ Set Speed (Group Delay)", callback_data="set_speed_timer")],
        [InlineKeyboardButton("🔄 Set Cycle Interval", callback_data="set_cycle_timer")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.message.edit_text(
            "⚙️ <b>Settings Menu</b>\n\nYahan se aap broadcast messages, DM auto-reply, speed aur cycle timers alag-alag configure kar sakte hain:",
            reply_markup=markup, parse_mode="HTML"
        )
    except:
        await query.message.reply_text(
            "⚙️ <b>Settings Menu</b>\n\nYahan se aap broadcast messages, DM auto-reply, speed aur cycle timers alag-alag configure kar sakte hain:",
            reply_markup=markup, parse_mode="HTML"
        )

# --- ADMIN COMMAND & PANEL ---
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to use this command!")
        return

    data = load_data()
    subs = data.get("subscriptions", {})
    
    text = "👑 <b>Admin Control Panel</b>\n\n<b>Active Users & Subscriptions:</b>\n"
    keyboard = []
    
    if not subs:
        text += "<i>No active paid users found.</i>\n"
    else:
        for uid, info in subs.items():
            exp = datetime.fromisoformat(info["expires"])
            if datetime.now() < exp:
                rem = exp - datetime.now()
                time_rem = f"{rem.days}d {rem.seconds//3600}h"
                text += f"• ID: <code>{uid}</code> | Time Left: <b>{time_rem}</b>\n"
                keyboard.append([
                    InlineKeyboardButton(f"❌ Remove {uid}", callback_data=f"admin_rem_{uid}"),
                    InlineKeyboardButton(f"➕ Add 7 Days", callback_data=f"admin_add_{uid}")
                ])

    keyboard.append([InlineKeyboardButton("➕ Grant Access to New User", callback_data="admin_grant_prompt")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# --- DELETE ACCOUNT MENU ---
async def delete_account_menu(query, page=0):
    if not USER_ACCOUNTS:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]])
        try:
            await query.message.edit_text("❌ Koi bhi account login nahi hai!", reply_markup=markup)
        except:
            await query.message.reply_text("❌ Koi bhi account login nahi hai!", reply_markup=markup)
        return

    per_page = 5
    total_pages = (len(USER_ACCOUNTS) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    current_accs = USER_ACCOUNTS[page * per_page : (page + 1) * per_page]

    keyboard = []
    for idx, acc in enumerate(current_accs):
        actual_idx = (page * per_page) + idx
        keyboard.append([InlineKeyboardButton(f"🗑 Remove {acc['phone']}", callback_data=f"del_acc_{actual_idx}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"del_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"del_page_{page+1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")])
    
    markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.message.edit_text(
            "🗑 <b>Delete Account Management</b>\nJis account ko remove karna ho us par click karein:",
            reply_markup=markup, parse_mode="HTML"
        )
    except:
        await query.message.reply_text(
            "🗑 <b>Delete Account Management</b>\nJis account ko remove karna ho us par click karein:",
            reply_markup=markup, parse_mode="HTML"
        )

# --- BROADCAST LOOP ---
async def broadcast_for_account(acc):
    global TOTAL_MESSAGES_SENT
    client = acc['client']
    while IS_RUNNING:
        account_messages = acc['messages']
        group_delay = acc.get('group_delay', 2)
        resend_interval = acc.get('resend_interval', 60)
    
        if not account_messages:
            await asyncio.sleep(5)
            continue
             
        if not acc['active']:
            break
             
        try:
            await client(UpdateStatusRequest(offline=False))
            async for dialog in client.iter_dialogs():
                if not IS_RUNNING or not acc['active']:
                    break
                if dialog.is_group:
                    try:
                        random_msg = random.choice(account_messages)
                        await client.send_message(dialog.id, random_msg)
                        TOTAL_MESSAGES_SENT += 1
                        await asyncio.sleep(group_delay)
                    except Exception as e:
                        print(f"Send Error for {acc['phone']}: {e}")
        except Exception as e:
            print(f"Iter Error for {acc['phone']}: {e}")

        if IS_RUNNING:
            await asyncio.sleep(resend_interval)

async def run_broadcast_loop():
    global IS_RUNNING
    while IS_RUNNING:
        active_clients = [acc for acc in USER_ACCOUNTS if acc['active'] and acc['messages']]
        if not active_clients:
            await asyncio.sleep(2)
            continue
        await asyncio.gather(*(broadcast_for_account(acc) for acc in active_clients))

# --- BUTTON HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_RUNNING
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    
    if not await check_access(update):
        return

    data = query.data

    if data == "start_all":
        if not USER_ACCOUNTS:
            await query.message.reply_text("❌ Pehle kam se kam 1 account login karein!")
            return
        if not any(acc['messages'] for acc in USER_ACCOUNTS):
            await query.message.reply_text("❌ Kisi bhi account me broadcast messages set nahi hain! Settings me jaakar set karein.")
            return
         
        if not IS_RUNNING:
            IS_RUNNING = True
            asyncio.create_task(run_broadcast_loop())
            await query.message.reply_text("✅ Broadcast start kar diya gaya hai!")
        else:
            await query.message.reply_text("⚠️ Broadcast pehle se running hai!")

    elif data == "stop_all":
        IS_RUNNING = False
        await query.message.reply_text("⏹ Broadcasts stop kar diye gaye hain.")

    elif data == "settings_menu":
        await settings_menu(query)

    elif data == "set_broadcast_msg":
        if not USER_ACCOUNTS:
            await query.message.reply_text("❌ Pehle account login karein!")
            return
        context.user_data['state'] = 'WAITING_GLOBAL_MSG'
        await query.message.reply_text("📝 Sabhi active accounts ke liye **Broadcast Messages** bhejayein.\n(Har nayi line ek naya message ban jayegi):", parse_mode="HTML")

    elif data == "set_dm_msg":
        if not USER_ACCOUNTS:
            await query.message.reply_text("❌ Pehle account login karein!")
            return
        context.user_data['state'] = 'WAITING_GLOBAL_DM'
        await query.message.reply_text("🤖 Sabhi accounts ke liye **DM Auto-Reply** message bhejayein:", parse_mode="HTML")

    elif data == "set_speed_timer":
        context.user_data['state'] = 'WAITING_SPEED'
        await query.message.reply_text(
            "⚡ **Speed (Group Delay) Set Karein**\n"
            "Seconds me value bhejayein (e.g., <code>3</code> matlab har message ke beech 3 seconds ka gap):",
            parse_mode="HTML"
        )

    elif data == "set_cycle_timer":
        context.user_data['state'] = 'WAITING_CYCLE'
        await query.message.reply_text(
            "🔄 **Cycle Interval Set Karein**\n"
            "Seconds me value bhejayein (e.g., <code>60</code> matlab har 60 seconds baad pura cycle repeat hoga):",
            parse_mode="HTML"
        )

    elif data == "login_phone":
        context.user_data['state'] = 'WAITING_PHONE'
        await query.message.reply_text("📱 Apna Phone Number country code ke sath bhejayein (jaise +919876543210):")

    elif data == "login_session":
        context.user_data['state'] = 'WAITING_STRING_SESSION'
        await query.message.reply_text("🔑 Apni Telethon String Session yahan bhejayein:")

    elif data == "delete_account_menu":
        await delete_account_menu(query, page=0)

    elif data.startswith("del_page_"):
        page_num = int(data.split("_")[2])
        await delete_account_menu(query, page=page_num)

    elif data.startswith("del_acc_"):
        idx = int(data.split("_")[2])
        if idx < len(USER_ACCOUNTS):
            acc = USER_ACCOUNTS.pop(idx)
            try:
                await acc['client'].disconnect()
                s_file = f"{acc['client'].session.filename}.session"
                if os.path.exists(s_file):
                    os.remove(s_file)
            except:
                pass
            save_account_meta_to_disk()
            await query.message.reply_text(f"🗑 Account {acc['phone']} successfully remove kar diya gaya.")
            await delete_account_menu(query, page=0)

    elif data == "admin_grant_prompt":
        if query.from_user.id != ADMIN_USER_ID:
            return
        context.user_data['state'] = 'WAITING_ADMIN_GRANT'
        await query.message.reply_text(
            "👑 **Grant Access**\nUser ki Telegram Numeric ID aur Days bhejayein:\n"
            "Format: <code>[User_ID] [Days]</code> (e.g. <code>123456789 30</code>)",
            parse_mode="HTML"
        )

    elif data.startswith("admin_rem_"):
        if query.from_user.id != ADMIN_USER_ID:
            return
        uid = data.split("_")[2]
        data_json = load_data()
        if uid in data_json["subscriptions"]:
            del data_json["subscriptions"][uid]
            save_data(data_json)
            await query.message.reply_text(f"✅ User <code>{uid}</code> ka access hata diya gaya hai.", parse_mode="HTML")

    elif data.startswith("admin_add_"):
        if query.from_user.id != ADMIN_USER_ID:
            return
        uid = data.split("_")[2]
        data_json = load_data()
        if uid in data_json["subscriptions"]:
            cur_exp = datetime.fromisoformat(data_json["subscriptions"][uid]["expires"])
            new_exp = max(datetime.now(), cur_exp) + timedelta(days=7)
            data_json["subscriptions"][uid]["expires"] = new_exp.isoformat()
            save_data(data_json)
            await query.message.reply_text(f"✅ User <code>{uid}</code> me 7 days aur add kar diye gaye hain.", parse_mode="HTML")

    elif data == "back_home":
        await start(update, context)

# --- TEXT HANDLER ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not context.user_data:
        return
        
    user = update.effective_user
    if user.id != ADMIN_USER_ID and not await check_access(update):
        return

    state = context.user_data.get('state')
    user_text = update.message.text.strip() if update.message.text else ""

    if state == 'WAITING_ADMIN_GRANT' and user.id == ADMIN_USER_ID:
        try:
            parts = user_text.split()
            target_id = parts[0]
            days = int(parts[1])
            
            data = load_data()
            expiry = datetime.now() + timedelta(days=days)
            data["subscriptions"][target_id] = {"expires": expiry.isoformat()}
            save_data(data)
            
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Success! User ID <code>{target_id}</code> ko {days} din ka access de diya gaya hai.", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text("❌ Sahi format me dalein. Example: <code>123456789 30</code>", parse_mode="HTML")
        return

    if state == 'WAITING_PHONE':
        phone_num = user_text
        context.user_data['phone'] = phone_num
        s_path = os.path.join(SESSION_DIR, f"phone_{user.id}_{int(asyncio.get_event_loop().time())}")
        client = TelegramClient(s_path, API_ID, API_HASH)
        await client.connect()
        try:
            sent = await client.send_code_request(phone_num)
            context.user_data['phone_client'] = client
            context.user_data['phone_hash'] = sent.phone_code_hash
            context.user_data['state'] = 'WAITING_PHONE_CODE'
            await update.message.reply_text("📨 Telegram OTP code bhej diya gaya hai. Code yahan enter karein:")
        except Exception as e:
            await client.disconnect()
            await update.message.reply_text(f"❌ Error: {e}")
            context.user_data['state'] = None

    elif state == 'WAITING_PHONE_CODE':
        code = user_text
        client = context.user_data.get('phone_client')
        phone_num = context.user_data.get('phone')
        phone_hash = context.user_data.get('phone_hash')
        try:
            await client.sign_in(phone_num, code, phone_code_hash=phone_hash)
            me = await client.get_me()
            phone = f"+{me.phone}" if me and me.phone else phone_num
            
            await client(UpdateStatusRequest(offline=False))
            asyncio.create_task(keep_client_alive(client))
            
            acc_dict = {'phone': phone, 'client': client, 'active': True, 'messages': [], 'group_delay': 2, 'resend_interval': 60, 'auto_reply': ''}
            setup_dm_handler(client, acc_dict)
            USER_ACCOUNTS.append(acc_dict)
            save_account_meta_to_disk()
            await update.message.reply_text(f"✅ Account <b>{phone}</b> successfully login ho gaya!", parse_mode="HTML")
            context.user_data['state'] = None
        except SessionPasswordNeededError:
            context.user_data['state'] = 'WAITING_PHONE_PASSWORD'
            await update.message.reply_text("🔒 2-Step Verification password enter karein:")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            await client.disconnect()
            context.user_data['state'] = None

    elif state == 'WAITING_PHONE_PASSWORD':
        password = user_text
        client = context.user_data.get('phone_client')
        phone_num = context.user_data.get('phone')
        try:
            await client.sign_in(password=password)
            me = await client.get_me()
            phone = f"+{me.phone}" if me and me.phone else phone_num
            
            await client(UpdateStatusRequest(offline=False))
            asyncio.create_task(keep_client_alive(client))
            
            acc_dict = {'phone': phone, 'client': client, 'active': True, 'messages': [], 'group_delay': 2, 'resend_interval': 60, 'auto_reply': ''}
            setup_dm_handler(client, acc_dict)
            USER_ACCOUNTS.append(acc_dict)
            save_account_meta_to_disk()
            await update.message.reply_text(f"✅ Password verified! Account <b>{phone}</b> login ho gaya.", parse_mode="HTML")
            context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            await client.disconnect()
            context.user_data['state'] = None

    elif state == 'WAITING_STRING_SESSION':
        string_session = user_text
        s_path = os.path.join(SESSION_DIR, f"str_{user.id}_{int(asyncio.get_event_loop().time())}")
        client = TelegramClient(StringSession(string_session), API_ID, API_HASH)
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                phone = f"+{me.phone}" if me and me.phone else f"Session_{user.id}"
                
                with open(f"{s_path}.session", "w") as f:
                    f.write(client.session.save())
                
                asyncio.create_task(keep_client_alive(client))
                acc_dict = {'phone': phone, 'client': client, 'active': True, 'messages': [], 'group_delay': 2, 'resend_interval': 60, 'auto_reply': ''}
                setup_dm_handler(client, acc_dict)
                USER_ACCOUNTS.append(acc_dict)
                save_account_meta_to_disk()
                await update.message.reply_text(f"✅ String Session Account <b>{phone}</b> added!", parse_mode="HTML")
            else:
                await update.message.reply_text("❌ Invalid Session String!")
            context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            context.user_data['state'] = None

    elif state == 'WAITING_GLOBAL_MSG':
        lines = [l.strip() for l in user_text.split('\n') if l.strip()]
        for acc in USER_ACCOUNTS:
            acc['messages'] = lines
        save_account_meta_to_disk()
        context.user_data['state'] = None
        await update.message.reply_text(f"✅ Sabhi accounts ke liye {len(lines)} Broadcast Messages set kar diye gaye hain!")

    elif state == 'WAITING_GLOBAL_DM':
        for acc in USER_ACCOUNTS:
            acc['auto_reply'] = user_text
        save_account_meta_to_disk()
        context.user_data['state'] = None
        await update.message.reply_text(f"✅ Sabhi accounts ke liye DM Auto-Reply set ho gaya:\n<code>{user_text}</code>", parse_mode="HTML")

    elif state == 'WAITING_SPEED':
        try:
            val = int(user_text)
            for acc in USER_ACCOUNTS:
                acc['group_delay'] = val
            save_account_meta_to_disk()
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Speed (Group Delay) successfully updated to <b>{val}s</b>!", parse_mode="HTML")
        except:
            await update.message.reply_text("❌ Sahi numeric value dalein (e.g., 3)")

    elif state == 'WAITING_CYCLE':
        try:
            val = int(user_text)
            for acc in USER_ACCOUNTS:
                acc['resend_interval'] = val
            save_account_meta_to_disk()
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Cycle Interval successfully updated to <b>{val}s</b>!", parse_mode="HTML")
        except:
            await update.message.reply_text("❌ Sahi numeric value dalein (e.g., 60)")

async def post_init(application):
    print("Loading sessions...")
    await load_saved_sessions()
    print(f"Loaded {len(USER_ACCOUNTS)} accounts.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))  # Admin command handler
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Master Paid Bot with /admin is running...")
    app.run_polling()
