import asyncio
import logging
import os
import random
import json
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
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
USER_RUNTIMES = {}

logging.basicConfig(level=logging.INFO)

# --- FLASK WEB SERVER FOR RENDER HEALTH CHECK ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"subscriptions": {}, "users_data": {}, "global_messages": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "global_messages" not in data:
                data["global_messages"] = []
            return data
    except Exception as e:
        print(f"Error loading data: {e}")
        return {"subscriptions": {}, "users_data": {}, "global_messages": []}

def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving data: {e}")

def get_user_runtime(user_id):
    if user_id not in USER_RUNTIMES:
        data = load_data()
        users_meta = data.get("users_data", {})
        u_meta = users_meta.get(str(user_id), {})
        
        USER_RUNTIMES[user_id] = {
            'accounts': [],
            'is_running': u_meta.get('is_running', False),
            'total_sent': 0
        }
    return USER_RUNTIMES[user_id]

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

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return False
    
    if user.id == ADMIN_USER_ID:
        return True
        
    sub = get_user_subscription(user.id)
    if not sub["active"]:
        msg_text = (
            "❌ <b>Access Denied!</b>\n\n"
            "Aapke paas is bot ko use karne ka access nahi hai ya aapka time khatam ho chuka hai.\n"
            "Access lene ke liye Admin (@G_KlNG) se contact karein."
        )
        if update.callback_query:
            try:
                await update.callback_query.message.edit_text(msg_text, parse_mode="HTML")
            except:
                await update.callback_query.message.reply_text(msg_text, parse_mode="HTML")
        elif update.message:
            await update.message.reply_text(msg_text, parse_mode="HTML")
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
    if not os.path.exists(SESSION_DIR):
        return

    data = load_data()
    users_meta = data.get("users_data", {})
    global_msgs = data.get("global_messages", [])

    for filename in os.listdir(SESSION_DIR):
        if filename.endswith(".session"):
            parts = filename[:-8].split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                owner_id = int(parts[1])
                session_path = os.path.join(SESSION_DIR, filename[:-8])
                client = TelegramClient(session_path, API_ID, API_HASH)
                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        me = await client.get_me()
                        phone = f"+{me.phone}" if me and me.phone else parts[0]
                        
                        runtime = get_user_runtime(owner_id)
                        if not any(acc['phone'] == phone for acc in runtime['accounts']):
                            asyncio.create_task(keep_client_alive(client))
                            
                            user_meta_dict = users_meta.get(str(owner_id), {})
                            accounts_meta = user_meta_dict.get('accounts_meta', {})
                            meta = accounts_meta.get(phone, {})
                            
                            acc_msgs = meta.get('messages', [])
                            if not acc_msgs and global_msgs:
                                acc_msgs = global_msgs
                            
                            acc_dict = {
                                'phone': phone,
                                'client': client,
                                'active': True,
                                'messages': acc_msgs,
                                'group_delay': meta.get('group_delay', 2),
                                'resend_interval': meta.get('resend_interval', 60),
                                'auto_reply': meta.get('auto_reply', ''),
                                'name': me.first_name if me and me.first_name else phone
                            }
                            setup_dm_handler(client, acc_dict)
                            runtime['accounts'].append(acc_dict)
                            
                            if runtime['is_running']:
                                asyncio.create_task(broadcast_for_user(owner_id))
                    else:
                        await client.disconnect()
                except Exception as e:
                    print(f"Failed to load session {filename}: {e}")

def save_user_meta_to_disk(user_id):
    data = load_data()
    if "users_data" not in data:
        data["users_data"] = {}
    
    runtime = get_user_runtime(user_id)
    acc_data = {}
    for acc in runtime['accounts']:
        acc_data[acc['phone']] = {
            'messages': acc['messages'],
            'group_delay': acc.get('group_delay', 2),
            'resend_interval': acc.get('resend_interval', 60),
            'auto_reply': acc.get('auto_reply', '')
        }
    
    if str(user_id) not in data["users_data"]:
        data["users_data"][str(user_id)] = {}
        
    data["users_data"][str(user_id)]['accounts_meta'] = acc_data
    data["users_data"][str(user_id)]['is_running'] = runtime['is_running']
    save_data(data)

# --- MAIN MENU INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(update, context):
        return

    sub = get_user_subscription(user.id)
    time_left = sub["expires"]
    
    runtime = get_user_runtime(user.id)
    # Correctly show running count as 0 if stopped, else count active accounts
    active_count = sum(1 for acc in runtime['accounts'] if acc['active']) if runtime['is_running'] else 0
    
    speed_str = "2-5s"
    cycle_str = "60s"
    if runtime['accounts']:
        speed_str = f"{runtime['accounts'][0].get('group_delay', 2)}s"
        cycle_str = f"{runtime['accounts'][0].get('resend_interval', 60)}s"

    text = (
        "✨ <b>Master Control Bot</b> ✨\n"
        "👤 <b>User Panel</b>\n"
        f"⏳ <b>Time:</b> {time_left}\n\n"
        f"📊 <b>Accounts:</b> {len(runtime['accounts'])} (Running: {active_count})\n"
        f"📨 <b>Messages Sent:</b> {runtime['total_sent']}\n"
        f"⏱ <b>Schedule:</b> Not Set (Immediate)\n"
        f"⚡ <b>Speed:</b> {speed_str} | 🔄 <b>Cycle:</b> {cycle_str}"
    )

    keyboard = [
        [
            InlineKeyboardButton("▶️ Start All", callback_data="start_all"),
            InlineKeyboardButton("⏹ Stop All", callback_data="stop_all")
        ],
        [
            InlineKeyboardButton("⏰ Schedule Broadcast", callback_data="schedule_msg_menu")
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")
        ],
        [
            InlineKeyboardButton("🔑 Session Login", callback_data="login_session")
        ],
        [
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

# --- SETTINGS MENU ---
async def settings_menu(query):
    keyboard = [
        [InlineKeyboardButton("📊 Detailed Account Status", callback_data="acc_status_detail")],
        [InlineKeyboardButton("💬 Auto-PM Config", callback_data="set_dm_msg")],
        [InlineKeyboardButton("⏱ Speeds & Delays", callback_data="speeds_menu")],
        [InlineKeyboardButton("📝 Auto Bio", callback_data="auto_bio_menu")],
        [InlineKeyboardButton("👥 Group Joiner", callback_data="group_joiner_menu")],
        [InlineKeyboardButton("📢 Broadcast Messages", callback_data="broadcast_menu")],
        [InlineKeyboardButton("⚙️ Special Acc Config", callback_data="special_acc_menu")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="back_home")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.message.edit_text(
            "⚙️ <b>Settings Menu</b>\n\nNeeche diye gaye options me se select karein:",
            reply_markup=markup, parse_mode="HTML"
        )
    except:
        await query.message.reply_text(
            "⚙️ <b>Settings Menu</b>\n\nNeeche diye gaye options me se select karein:",
            reply_markup=markup, parse_mode="HTML"
        )

# --- BROADCAST MESSAGES MENU ---
async def show_broadcast_menu(query, user_id):
    data = load_data()
    global_msgs = data.get("global_messages", [])
    
    keyboard = []
    for idx, msg in enumerate(global_msgs):
        short_msg = msg[:25] + "..." if len(msg) > 25 else msg
        keyboard.append([InlineKeyboardButton(f"❌ Msg #{idx+1}: {short_msg}", callback_data=f"del_bmsg_{idx}")])
        
    keyboard.append([InlineKeyboardButton("➕ Add Message", callback_data="add_bmsg_prompt")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="settings_menu")])
    
    markup = InlineKeyboardMarkup(keyboard)
    text = "📢 <b>Broadcast Messages</b>\n\nAapke configured messages neeche hain:"
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except:
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

# --- ADMIN PANEL ---
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return

    keyboard = [
        [InlineKeyboardButton("➕ Grant Access to User", callback_data="admin_grant_prompt")],
        [InlineKeyboardButton("📋 Subscriptions List", callback_data="admin_sub_list")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]
    ]
    await update.message.reply_text(
        "👑 <b>Admin Control Panel</b>\n\nNeeche diye gaye options me se select karein:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def show_subscriptions_list(query):
    data = load_data()
    subs = data.get("subscriptions", {})
    
    keyboard = []
    text = "📋 <b>Active Access List:</b>\n\n"
    
    if not subs:
        text += "<i>Koi bhi user abhi active nahi hai.</i>"
    else:
        for uid, info in subs.items():
            exp = datetime.fromisoformat(info["expires"])
            if datetime.now() < exp:
                rem = exp - datetime.now()
                time_rem = f"{rem.days}d {rem.seconds//3600}h"
                keyboard.append([InlineKeyboardButton(f"👤 {uid} ({time_rem} left)", callback_data=f"admin_manage_user_{uid}")])

    keyboard.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="back_to_admin")])
    
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except:
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# --- DELETE ACCOUNT MENU ---
async def delete_account_menu(query, user_id, page=0):
    runtime = get_user_runtime(user_id)
    if not runtime['accounts']:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]])
        try:
            await query.message.edit_text("❌ Aapka koi bhi account login nahi hai!", reply_markup=markup)
        except:
            await query.message.reply_text("❌ Aapka koi bhi account login nahi hai!", reply_markup=markup)
        return

    per_page = 5
    total_pages = (len(runtime['accounts']) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    current_accs = runtime['accounts'][page * per_page : (page + 1) * per_page]

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

# --- ISOLATED BROADCAST LOOP PER USER ---
async def broadcast_for_user(user_id):
    runtime = get_user_runtime(user_id)
    while runtime['is_running']:
        active_clients = [acc for acc in runtime['accounts'] if acc['active'] and acc['messages']]
        if not active_clients:
            await asyncio.sleep(2)
            continue
            
        for acc in active_clients:
            if not runtime['is_running']:
                break
            client = acc['client']
            account_messages = acc['messages']
            group_delay = acc.get('group_delay', 2)
            resend_interval = acc.get('resend_interval', 60)
            
            try:
                await client(UpdateStatusRequest(offline=False))
                async for dialog in client.iter_dialogs():
                    if not runtime['is_running'] or not acc['active']:
                        break
                    if dialog.is_group:
                        try:
                            random_msg = random.choice(account_messages)
                            await client.send_message(dialog.id, random_msg)
                            runtime['total_sent'] += 1
                            await asyncio.sleep(group_delay)
                        except Exception as e:
                            print(f"Send Error for {acc['phone']}: {e}")
            except Exception as e:
                print(f"Iter Error for {acc['phone']}: {e}")
                
            await asyncio.sleep(resend_interval)

# --- BUTTON HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data

    if not await check_access(update, context):
        return

    runtime = get_user_runtime(user_id)

    if data == "start_all":
        if not runtime['accounts']:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
            await query.message.edit_text(
                "❌ <b>Pehle kam se kam apna 1 account login karein!</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        if not any(acc['messages'] for acc in runtime['accounts']):
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
            await query.message.edit_text(
                "❌ <b>Aapke kisi bhi account me broadcast messages set nahi hain!</b>\nSettings me jaakar set karein.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return
         
        runtime['is_running'] = True
        runtime['total_sent'] = 0  # Reset message count to zero on fresh start
        save_user_meta_to_disk(user_id)
        asyncio.create_task(broadcast_for_user(user_id))
        
        status_text = "🚀 <b>Broadcast Triggered & Active!</b>\n\n"
        for acc in runtime['accounts']:
            client = acc['client']
            try:
                if client.is_connected() and await client.is_user_authorized():
                    status_text += f"✅ Already running: {acc.get('name', acc['phone'])}\n"
                else:
                    status_text += f"❌ Failed: {acc.get('name', acc['phone'])} (Session Revoked/Disconnected)\n"
            except Exception as e:
                status_text += f"❌ Failed: {acc.get('name', acc['phone'])} ({e})\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
        await query.message.edit_text(status_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "stop_all":
        runtime['is_running'] = False
        save_user_meta_to_disk(user_id)
        
        status_text = "⏹ <b>Broadcast Stopped</b>\n\n"
        for acc in runtime['accounts']:
            status_text += f"🔴 Stopped: {acc.get('name', acc['phone'])}\n"
            
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
        await query.message.edit_text(status_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "settings_menu":
        await settings_menu(query)

    elif data == "broadcast_menu":
        await show_broadcast_menu(query, user_id)

    elif data == "add_bmsg_prompt":
        context.user_data['state'] = 'WAITING_ADD_BMSG'
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="broadcast_menu")]]
        await query.message.edit_text(
            "📝 Naya Broadcast Message yahan bhejayein (Aap multiple lines me bhi bhej sakte hain):",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    elif data.startswith("del_bmsg_"):
        idx = int(data.split("_")[2])
        db_data = load_data()
        global_msgs = db_data.get("global_messages", [])
        if idx < len(global_msgs):
            removed = global_msgs.pop(idx)
            db_data["global_messages"] = global_msgs
            save_data(db_data)
            
            for acc in runtime['accounts']:
                acc['messages'] = global_msgs
            save_user_meta_to_disk(user_id)
            
            await query.answer(f"🗑 Message #{idx+1} deleted!", show_alert=True)
        await show_broadcast_menu(query, user_id)

    elif data == "acc_status_detail":
        text = "📊 <b>Detailed Account Status</b>\n\n"
        if not runtime['accounts']:
            text += "<i>Koi account login nahi hai.</i>"
        else:
            for idx, acc in enumerate(runtime['accounts']):
                status_icon = "🟢 Active" if acc['active'] else "🔴 Inactive"
                text += f"{idx+1}. 📱 <b>{acc['phone']}</b> ({acc.get('name', 'User')}) - {status_icon}\n"
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings_menu")]]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "speeds_menu":
        context.user_data['state'] = 'WAITING_SPEED'
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings_menu")]]
        await query.message.edit_text("⏱ Speeds (Group Delay) seconds me bhejayein (jaise <code>3</code>):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "schedule_msg_menu":
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
        await query.message.edit_text("⏰ <b>Schedule Broadcast</b> feature jald hi update hoga!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data in ["auto_bio_menu", "group_joiner_menu", "special_acc_menu"]:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings_menu")]]
        await query.message.edit_text("⚙️ Is module par kaam chal raha hai, jald live hoga!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "set_dm_msg":
        context.user_data['state'] = 'WAITING_GLOBAL_DM'
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings_menu")]]
        await query.message.edit_text("🤖 Aapke accounts ke liye **DM Auto-Reply** message bhejayein:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "set_speed_timer":
        context.user_data['state'] = 'WAITING_SPEED'
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings_menu")]]
        await query.message.edit_text("⚡ Seconds me speed value bhejayein (e.g., <code>3</code>):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "set_cycle_timer":
        context.user_data['state'] = 'WAITING_CYCLE'
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings_menu")]]
        await query.message.edit_text("🔄 Cycle interval seconds me bhejayein (e.g., <code>60</code>):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "login_phone":
        context.user_data['state'] = 'WAITING_PHONE'
        await query.message.reply_text("📱 Apna Phone Number country code ke sath bhejayein (jaise +919876543210):")

    elif data == "login_session":
        context.user_data['state'] = 'WAITING_STRING_SESSION'
        await query.message.reply_text("🔑 Apni Telethon String Session yahan bhejayein:")

    elif data == "delete_account_menu":
        await delete_account_menu(query, user_id, page=0)

    elif data.startswith("del_page_"):
        page_num = int(data.split("_")[2])
        await delete_account_menu(query, user_id, page=page_num)

    elif data.startswith("del_acc_"):
        idx = int(data.split("_")[2])
        if idx < len(runtime['accounts']):
            acc = runtime['accounts'].pop(idx)
            try:
                await acc['client'].disconnect()
                s_file = f"{SESSION_DIR}/user_{user_id}_{acc['phone'].replace('+', '')}.session"
                if os.path.exists(s_file):
                    os.remove(s_file)
            except:
                pass
            save_user_meta_to_disk(user_id)
            await query.message.reply_text(f"🗑 Account {acc['phone']} successfully remove kar diya gaya.")
            await delete_account_menu(query, user_id, page=0)

    # --- ADMIN CALLBACKS ---
    elif data == "admin_grant_prompt":
        if user_id != ADMIN_USER_ID:
            return
        context.user_data['state'] = 'WAITING_ADMIN_GRANT'
        await query.message.reply_text(
            "👑 **Grant Access**\nUser ki Telegram Numeric ID aur Days bhejayein:\n"
            "Format: <code>[User_ID] [Days]</code> (e.g. <code>123456789 30</code>)",
            parse_mode="HTML"
        )

    elif data == "admin_sub_list":
        if user_id != ADMIN_USER_ID:
            return
        await show_subscriptions_list(query)

    elif data == "back_to_admin":
        if user_id != ADMIN_USER_ID:
            return
        keyboard = [
            [InlineKeyboardButton("➕ Grant Access to User", callback_data="admin_grant_prompt")],
            [InlineKeyboardButton("📋 Subscriptions List", callback_data="admin_sub_list")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]
        ]
        await query.message.edit_text(
            "👑 <b>Admin Control Panel</b>\n\nNeeche diye gaye options me se select karein:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data.startswith("admin_manage_user_"):
        if user_id != ADMIN_USER_ID:
            return
        uid = data.split("_")[3]
        data_json = load_data()
        subs = data_json.get("subscriptions", {})
        if uid in subs:
            exp_str = subs[uid]["expires"]
            exp_dt = datetime.fromisoformat(exp_str)
            rem = exp_dt - datetime.now()
            time_rem = f"{rem.days} days, {rem.seconds//3600} hours" if rem.days >= 0 else "Expired"
            
            keyboard = [
                [InlineKeyboardButton("➕ Add 7 Days", callback_data=f"admin_add7_{uid}")],
                [InlineKeyboardButton("➕ Add 30 Days", callback_data=f"admin_add30_{uid}")],
                [InlineKeyboardButton("❌ Remove Access", callback_data=f"admin_rem_{uid}")],
                [InlineKeyboardButton("🔙 Back to List", callback_data="admin_sub_list")]
            ]
            await query.message.edit_text(
                f"👤 <b>Manage User:</b> <code>{uid}</code>\n⏳ <b>Time Left:</b> {time_rem}\n\nKya karna chahte hain?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

    elif data.startswith("admin_add7_") or data.startswith("admin_add30_"):
        if user_id != ADMIN_USER_ID:
            return
        parts = data.split("_")
        days_to_add = 7 if "7" in parts[1] else 30
        uid = parts[2]
        
        data_json = load_data()
        if uid in data_json["subscriptions"]:
            cur_exp = datetime.fromisoformat(data_json["subscriptions"][uid]["expires"])
            new_exp = max(datetime.now(), cur_exp) + timedelta(days=days_to_add)
            data_json["subscriptions"][uid]["expires"] = new_exp.isoformat()
            save_data(data_json)
            await query.answer(f"✅ Successfully added {days_to_add} days!", show_alert=True)
            await show_subscriptions_list(query)

    elif data.startswith("admin_rem_"):
        if user_id != ADMIN_USER_ID:
            return
        uid = data.split("_")[2]
        data_json = load_data()
        if uid in data_json["subscriptions"]:
            del data_json["subscriptions"][uid]
            save_data(data_json)
            await query.answer("✅ Access removed!", show_alert=True)
            await show_subscriptions_list(query)

    elif data == "back_home":
        await start(update, context)

# --- TEXT HANDLER WITH MULTIPLE MESSAGES & AUTO-DELETE ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    user = update.effective_user
    user_id = user.id

    if user_id != ADMIN_USER_ID and not await check_access(update, context):
        return

    runtime = get_user_runtime(user_id)
    state = context.user_data.get('state')
    user_text = update.message.text.strip() if update.message.text else ""

    try:
        await update.message.delete()
    except Exception:
        pass

    if state == 'WAITING_ADD_BMSG':
        lines = [l.strip() for l in user_text.split('\n') if l.strip()]
        db_data = load_data()
        if "global_messages" not in db_data:
            db_data["global_messages"] = []
        db_data["global_messages"].extend(lines)
        save_data(db_data)
        
        for acc in runtime['accounts']:
            acc['messages'] = db_data["global_messages"]
        save_user_meta_to_disk(user_id)
        
        context.user_data['state'] = None
        await update.message.reply_text(f"✅ {len(lines)} naye broadcast messages successfully add ho gaye!")
        await start(update, context)
        return

    if state == 'WAITING_ADMIN_GRANT' and user_id == ADMIN_USER_ID:
        context.user_data['state'] = None
        try:
            parts = user_text.split()
            target_id = parts[0]
            days = int(parts[1])
            
            data = load_data()
            expiry = datetime.now() + timedelta(days=days)
            data["subscriptions"][target_id] = {"expires": expiry.isoformat()}
            save_data(data)
            
            await update.message.reply_text(f"✅ Success! User ID <code>{target_id}</code> ko {days} din ka access de diya gaya hai.", parse_mode="HTML")
            try:
                await context.bot.send_message(chat_id=int(target_id), text=f"🎉 Aapko {days} din ka access mil gaya hai! Ab /start bhej kar bot use karein.")
            except:
                pass
        except Exception:
            await update.message.reply_text("❌ Sahi format me dalein. Example: <code>123456789 30</code>", parse_mode="HTML")
        return

    if state == 'WAITING_PHONE':
        phone_num = user_text
        context.user_data['phone'] = phone_num
        s_path = os.path.join(SESSION_DIR, f"user_{user_id}_phone_{int(asyncio.get_event_loop().time())}")
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
        return

    elif state == 'WAITING_PHONE_CODE':
        code = user_text
        client = context.user_data.get('phone_client')
        phone_num = context.user_data.get('phone')
        phone_hash = context.user_data.get('phone_hash')
        try:
            await client.sign_in(phone_num, code, phone_code_hash=phone_hash)
            me = await client.get_me()
            phone = f"+{me.phone}" if me and me.phone else phone_num
            name = me.first_name if me and me.first_name else phone
            
            await client(UpdateStatusRequest(offline=False))
            asyncio.create_task(keep_client_alive(client))
            
            data = load_data()
            global_msgs = data.get("global_messages", [])
            
            acc_dict = {'phone': phone, 'client': client, 'active': True, 'messages': global_msgs, 'group_delay': 2, 'resend_interval': 60, 'auto_reply': '', 'name': name}
            setup_dm_handler(client, acc_dict)
            runtime['accounts'].append(acc_dict)
            save_user_meta_to_disk(user_id)
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Account <b>{name} ({phone})</b> successfully login ho gaya!", parse_mode="HTML")
            await start(update, context)
        except SessionPasswordNeededError:
            context.user_data['state'] = 'WAITING_PHONE_PASSWORD'
            await update.message.reply_text("🔒 2-Step Verification password enter karein:")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            await client.disconnect()
            context.user_data['state'] = None
        return

    elif state == 'WAITING_PHONE_PASSWORD':
        password = user_text
        client = context.user_data.get('phone_client')
        phone_num = context.user_data.get('phone')
        try:
            await client.sign_in(password=password)
            me = await client.get_me()
            phone = f"+{me.phone}" if me and me.phone else phone_num
            name = me.first_name if me and me.first_name else phone
            
            await client(UpdateStatusRequest(offline=False))
            asyncio.create_task(keep_client_alive(client))
            
            data = load_data()
            global_msgs = data.get("global_messages", [])
            
            acc_dict = {'phone': phone, 'client': client, 'active': True, 'messages': global_msgs, 'group_delay': 2, 'resend_interval': 60, 'auto_reply': '', 'name': name}
            setup_dm_handler(client, acc_dict)
            runtime['accounts'].append(acc_dict)
            save_user_meta_to_disk(user_id)
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Password verified! Account <b>{name}</b> login ho gaya.", parse_mode="HTML")
            await start(update, context)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            await client.disconnect()
            context.user_data['state'] = None
        return

    elif state == 'WAITING_STRING_SESSION':
        string_session = user_text
        s_path = os.path.join(SESSION_DIR, f"user_{user_id}_str_{int(asyncio.get_event_loop().time())}")
        client = TelegramClient(StringSession(string_session), API_ID, API_HASH)
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                phone = f"+{me.phone}" if me and me.phone else f"Session_{user_id}"
                name = me.first_name if me and me.first_name else phone
                
                with open(f"{s_path}.session", "w") as f:
                    f.write(client.session.save())
                
                asyncio.create_task(keep_client_alive(client))
                
                data = load_data()
                global_msgs = data.get("global_messages", [])
                
                acc_dict = {'phone': phone, 'client': client, 'active': True, 'messages': global_msgs, 'group_delay': 2, 'resend_interval': 60, 'auto_reply': '', 'name': name}
                setup_dm_handler(client, acc_dict)
                runtime['accounts'].append(acc_dict)
                save_user_meta_to_disk(user_id)
                context.user_data['state'] = None
                await update.message.reply_text(f"✅ String Session Account <b>{name}</b> added!", parse_mode="HTML")
                await start(update, context)
            else:
                await update.message.reply_text("❌ Invalid Session String!")
                context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            context.user_data['state'] = None
        return

    elif state == 'WAITING_GLOBAL_DM':
        for acc in runtime['accounts']:
            acc['auto_reply'] = user_text
        save_user_meta_to_disk(user_id)
        context.user_data['state'] = None
        await update.message.reply_text(f"✅ Aapke accounts ke liye DM Auto-Reply set ho gaya:\n<code>{user_text}</code>", parse_mode="HTML")
        await start(update, context)
        return

    elif state == 'WAITING_SPEED':
        try:
            val = int(user_text)
            for acc in runtime['accounts']:
                acc['group_delay'] = val
            save_user_meta_to_disk(user_id)
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Speed successfully updated to <b>{val}s</b>!", parse_mode="HTML")
            await start(update, context)
        except:
            await update.message.reply_text("❌ Sahi numeric value dalein (e.g., 3)")
        return

    elif state == 'WAITING_CYCLE':
        try:
            val = int(user_text)
            for acc in runtime['accounts']:
                acc['resend_interval'] = val
            save_user_meta_to_disk(user_id)
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Cycle Interval successfully updated to <b>{val}s</b>!", parse_mode="HTML")
            await start(update, context)
        except:
            await update.message.reply_text("❌ Sahi numeric value dalein (e.g., 60)")
        return

async def post_init(application):
    print("Loading sessions...")
    await load_saved_sessions()
    print("All user sessions loaded successfully.")

if __name__ == '__main__':
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Master Bot is running smoothly...")
    app.run_polling()
