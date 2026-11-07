import asyncio
import logging
import os
import random
import json
import qrcode
import io
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

SESSION_DIR = "sessions"
os.makedirs(SESSION_DIR, exist_ok=True)

MESSAGES_FILE = "messages_data.json"
USER_ACCOUNTS = []
IS_RUNNING = False
TOTAL_MESSAGES_SENT = 0

logging.basicConfig(level=logging.INFO)
LOGIN_DATA = {}

def load_persisted_data():
    if not os.path.exists(MESSAGES_FILE):
        return {}
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading messages file: {e}")
        return {}

def save_persisted_data():
    data = {}
    for acc in USER_ACCOUNTS:
        data[acc['phone']] = {
            'messages': acc['messages'],
            'group_delay': acc.get('group_delay', 2),
            'resend_interval': acc.get('resend_interval', 60),
            'auto_reply': acc.get('auto_reply', '')
        }
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving messages file: {e}")

async def keep_client_alive(client):
    while True:
        try:
            if client.is_connected():
                await client(UpdateStatusRequest(offline=False))
                await client(GetFullUserRequest('me'))
        except Exception as e:
            print(f"Ping Error: {e}")
        await asyncio.sleep(20)

# --- DM AUTO-RESPONDER HANDLER FOR TELETHON CLIENTS ---
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

    saved_data = load_persisted_data()

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
                        
                        acc_info = saved_data.get(phone, {})
                        acc_messages = acc_info.get('messages', [])
                        group_delay = acc_info.get('group_delay', 2)
                        resend_interval = acc_info.get('resend_interval', 60)
                        auto_reply = acc_info.get('auto_reply', '')

                        acc_dict = {
                            'phone': phone,
                            'client': client,
                            'active': True,
                            'messages': acc_messages,
                            'group_delay': group_delay,
                            'resend_interval': resend_interval,
                            'auto_reply': auto_reply
                        }
                        setup_dm_handler(client, acc_dict)
                        USER_ACCOUNTS.append(acc_dict)
                        print(f"Successfully loaded session: {phone}")
                else:
                    await client.disconnect()
            except Exception as e:
                print(f"Failed to load session {filename}: {e}")

async def show_manage_menu(query, page=0):
    if not USER_ACCOUNTS:
        await query.message.reply_text("❌ Koi bhi account login nahi hai!")
        return

    per_page = 4  
    total_accounts = len(USER_ACCOUNTS)
    total_pages = (total_accounts + per_page - 1) // per_page
    if total_pages == 0: 
        total_pages = 1
     
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    current_accounts = USER_ACCOUNTS[start_idx:start_idx + per_page]
     
    keyboard = []
    for offset, acc in enumerate(current_accounts):
        index = start_idx + offset
        status_icon = "🟢 ON" if acc['active'] else "🔴 OFF"
        msg_count = len(acc['messages'])
        serial_no = index + 1  
         
        keyboard.append([
            InlineKeyboardButton(f"{serial_no}. {acc['phone']} ({msg_count} msgs) : {status_icon}", callback_data=f"toggle_acc_{index}"),
        ])
        keyboard.append([
            InlineKeyboardButton("📝 Msg", callback_data=f"set_acc_msg_{index}"),
            InlineKeyboardButton(f"⚙️ Timers", callback_data=f"set_acc_timers_{index}"),
            InlineKeyboardButton("🤖 DM Reply", callback_data=f"set_dm_reply_{index}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"delete_acc_{index}")
        ])
     
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"manage_page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop_page"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"manage_page_{page + 1}"))
     
    if nav_row:
        keyboard.append(nav_row)
         
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")])
     
    markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.message.edit_text(
            f"📱 <b>Manage Accounts & Settings (Page {page + 1}/{total_pages}):</b>\n"
            "• <b>📝 Msg:</b> Bulk broadcast messages set karein.\n"
            "• <b>⚙️ Timers:</b> Group delay & Resend interval set karein.\n"
            "• <b>🤖 DM Reply:</b> Koi personal message kare toh automatic kya reply jaye.", 
            reply_markup=markup, parse_mode="HTML"
        )
    except Exception:
        pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str = "▶️ RUNNING" if IS_RUNNING else "⏹ STOPPED"
    active_count = sum(1 for acc in USER_ACCOUNTS if acc['active'])
    total_messages = sum(len(acc['messages']) for acc in USER_ACCOUNTS)

    text = (
        "✨ <b>Master Control Bot</b> ✨\n"
        "👤 <b>User Panel</b>\n"
        f"📊 <b>Status:</b> {status_str}\n"
        f"💬 <b>Total Saved Messages:</b> {total_messages}\n"
        f"📈 <b>Accounts:</b> {len(USER_ACCOUNTS)} (Running: {active_count})\n"
        f"📨 <b>Messages Sent:</b> {TOTAL_MESSAGES_SENT}\n"
        f"⚡ <b>Speed & Cycle:</b> Custom per account"
    )

    keyboard = [
        [
            InlineKeyboardButton("▶️ Start All", callback_data="start_all"),
            InlineKeyboardButton("⏹ Stop All", callback_data="stop_all")
        ],
        [
            InlineKeyboardButton("📱 Manage Accounts & Settings", callback_data="manage_accounts")
        ],
        [
            InlineKeyboardButton("🔑 Session Login", callback_data="login_session"),
            InlineKeyboardButton("📱 Phone Login", callback_data="login_phone")
        ],
        [
            InlineKeyboardButton("🗑 Delete Account", callback_data="manage_accounts")
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

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_RUNNING
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    data = query.data

    if data == "start_all":
        active_clients = [acc for acc in USER_ACCOUNTS if acc['active']]
        if not active_clients:
            await query.message.reply_text("❌ Pehle kam se kam 1 Active Account hona chahiye!")
            return
         
        accounts_with_msgs = [acc for acc in active_clients if acc['messages']]
        if not accounts_with_msgs:
            await query.message.reply_text("❌ Kisi bhi active account me messages add nahi hain!")
            return
         
        if not IS_RUNNING:
            IS_RUNNING = True
            asyncio.create_task(run_broadcast_loop())
            await query.message.reply_text("✅ Sabhi accounts simultaneous broadcast start kar chuke hain!")
        else:
            await query.message.reply_text("⚠️ Broadcast pehle se running hai!")

    elif data == "stop_all":
        IS_RUNNING = False
        await query.message.reply_text("⏹ Broadcasts stop kar diye gaye hain.")

    elif data == "login_phone":
        if context.user_data is not None:
            context.user_data['state'] = 'WAITING_PHONE'
        await query.message.reply_text("📱 Apna Phone Number country code ke sath bhejayein (jaise +919876543210):")

    elif data == "login_session":
        if context.user_data is not None:
            context.user_data['state'] = 'WAITING_STRING_SESSION'
        await query.message.reply_text("🔑 Apni Telethon String Session yahan bhejayein:")

    elif data == "manage_accounts":
        await show_manage_menu(query, page=0)
        return

    elif data.startswith("manage_page_"):
        page_num = int(data.split("_")[2])
        await show_manage_menu(query, page=page_num)
        return

    elif data.startswith("toggle_acc_"):
        idx = int(data.split("_")[2])
        if idx < len(USER_ACCOUNTS):
            USER_ACCOUNTS[idx]['active'] = not USER_ACCOUNTS[idx]['active']
        per_page = 4
        current_page = idx // per_page
        await show_manage_menu(query, page=current_page)
        return

    elif data.startswith("set_acc_msg_"):
        idx = int(data.split("_")[3])
        if idx < len(USER_ACCOUNTS):
            if context.user_data is not None:
                context.user_data['state'] = 'WAITING_ACC_MSG'
                context.user_data['target_acc_index'] = idx
            acc_phone = USER_ACCOUNTS[idx]['phone']
            await query.message.reply_text(
                f"📝 Account <b>{acc_phone}</b> ke liye bulk messages bhejayein (Har line ek naya message):",
                parse_mode="HTML"
            )
        return

    elif data.startswith("set_acc_timers_"):
        idx = int(data.split("_")[3])
        if idx < len(USER_ACCOUNTS):
            if context.user_data is not None:
                context.user_data['state'] = 'WAITING_ACC_TIMERS'
                context.user_data['target_acc_index'] = idx
            acc_phone = USER_ACCOUNTS[idx]['phone']
            await query.message.reply_text(
                f"⚙️ Account <b>{acc_phone}</b> ke liye timers is format me bhejayein:\n"
                "<b>[Group Delay] [Resend Interval]</b> (e.g., <code>3 60</code>)",
                parse_mode="HTML"
            )
        return

    elif data.startswith("set_dm_reply_"):
        idx = int(data.split("_")[3])
        if idx < len(USER_ACCOUNTS):
            if context.user_data is not None:
                context.user_data['state'] = 'WAITING_DM_REPLY'
                context.user_data['target_acc_index'] = idx
            acc_phone = USER_ACCOUNTS[idx]['phone']
            await query.message.reply_text(
                f"🤖 Account <b>{acc_phone}</b> ke liye Auto DM Reply text bhejayein:",
                parse_mode="HTML"
            )
        return

    elif data.startswith("delete_acc_"):
        idx = int(data.split("_")[2])
        if idx < len(USER_ACCOUNTS):
            acc_to_del = USER_ACCOUNTS[idx]
            try:
                await acc_to_del['client'].disconnect()
                session_file = f"{acc_to_del['client'].session.filename}.session"
                if os.path.exists(session_file):
                    os.remove(session_file)
            except Exception as e:
                print(f"Error deleting session file: {e}")
                 
            removed = USER_ACCOUNTS.pop(idx)
            save_persisted_data()
            await query.message.reply_text(f"🗑 Account {removed['phone']} remove kar diya gaya.")
        per_page = 4
        current_page = idx // per_page
        if current_page * per_page >= len(USER_ACCOUNTS) and current_page > 0:
            current_page -= 1
        await show_manage_menu(query, page=current_page)
        return

    elif data == "back_home":
        await start(update, context)
        return

    else:
        await start(update, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not context.user_data:
        return
    state = context.user_data.get('state')
    user_text = update.message.text.strip() if update.message.text else ""
    user_id = update.message.from_user.id

    if state == 'WAITING_PHONE':
        phone_num = user_text
        context.user_data['phone'] = phone_num
        session_path = os.path.join(SESSION_DIR, f"phone_acc_{user_id}_{int(asyncio.get_event_loop().time())}")
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        try:
            sent = await client.send_code_request(phone_num)
            context.user_data['phone_client'] = client
            context.user_data['phone_hash'] = sent.phone_code_hash
            context.user_data['session_path'] = session_path
            context.user_data['state'] = 'WAITING_PHONE_CODE'
            await update.message.reply_text("📨 Telegram OTP code bhej diya gaya hai. Code yahan enter karein:")
        except Exception as e:
            await client.disconnect()
            await update.message.reply_text(f"❌ Error sending code: {e}")
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
            
            acc_dict = {
                'phone': phone, 
                'client': client, 
                'active': True, 
                'messages': [], 
                'group_delay': 2, 
                'resend_interval': 60,
                'auto_reply': ''
            }
            setup_dm_handler(client, acc_dict)
            USER_ACCOUNTS.append(acc_dict)
            save_persisted_data()
            await update.message.reply_text(f"✅ Account <b>{phone}</b> successfully login ho gaya!", parse_mode="HTML")
            context.user_data['state'] = None
        except SessionPasswordNeededError:
            context.user_data['state'] = 'WAITING_PHONE_PASSWORD'
            await update.message.reply_text("🔒 2-Step Verification password enter karein:")
        except Exception as e:
            await update.message.reply_text(f"❌ Sign in error: {e}")
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
            
            acc_dict = {
                'phone': phone, 
                'client': client, 
                'active': True, 
                'messages': [], 
                'group_delay': 2, 
                'resend_interval': 60,
                'auto_reply': ''
            }
            setup_dm_handler(client, acc_dict)
            USER_ACCOUNTS.append(acc_dict)
            save_persisted_data()
            await update.message.reply_text(f"✅ Password verified! Account <b>{phone}</b> login ho gaya.", parse_mode="HTML")
            context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"❌ Password Error: {e}")
            await client.disconnect()
            context.user_data['state'] = None

    elif state == 'WAITING_STRING_SESSION':
        string_session = user_text
        session_path = os.path.join(SESSION_DIR, f"str_acc_{user_id}_{int(asyncio.get_event_loop().time())}")
        client = TelegramClient(StringSession(string_session), API_ID, API_HASH)
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                phone = f"+{me.phone}" if me and me.phone else f"Session_{user_id}"
                
                # Save string session to file properly
                with open(f"{session_path}.session", "w") as f:
                    f.write(client.session.save())
                
                asyncio.create_task(keep_client_alive(client))
                acc_dict = {
                    'phone': phone, 
                    'client': client, 
                    'active': True, 
                    'messages': [], 
                    'group_delay': 2, 
                    'resend_interval': 60,
                    'auto_reply': ''
                }
                setup_dm_handler(client, acc_dict)
                USER_ACCOUNTS.append(acc_dict)
                save_persisted_data()
                await update.message.reply_text(f"✅ String Session Account <b>{phone}</b> successfully added!", parse_mode="HTML")
            else:
                await update.message.reply_text("❌ Invalid Session String!")
            context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"❌ Session Error: {e}")
            context.user_data['state'] = None

    elif state == 'WAITING_ACC_MSG':
        idx = context.user_data.get('target_acc_index')
        if idx is not None and idx < len(USER_ACCOUNTS):
            if user_text:
                lines = [line.strip() for line in user_text.split('\n') if line.strip()]
                USER_ACCOUNTS[idx]['messages'].extend(lines)
                save_persisted_data()
                acc_phone = USER_ACCOUNTS[idx]['phone']
                context.user_data['state'] = None
                context.user_data['target_acc_index'] = None
                await update.message.reply_text(
                    f"✅ Account {acc_phone} ke liye {len(lines)} messages add ho gaye!\n"
                    f"Total messages: {len(USER_ACCOUNTS[idx]['messages'])}"
                )

    elif state == 'WAITING_ACC_TIMERS':
        idx = context.user_data.get('target_acc_index')
        if idx is not None and idx < len(USER_ACCOUNTS):
            try:
                parts = user_text.split()
                g_delay = int(parts[0])
                r_interval = int(parts[1])
                 
                USER_ACCOUNTS[idx]['group_delay'] = g_delay
                USER_ACCOUNTS[idx]['resend_interval'] = r_interval
                save_persisted_data()
                acc_phone = USER_ACCOUNTS[idx]['phone']
                 
                context.user_data['state'] = None
                context.user_data['target_acc_index'] = None
                await update.message.reply_text(
                    f"✅ Account {acc_phone} ke timers update ho gaye:\n"
                    f"• Group Delay: <b>{g_delay}s</b>\n"
                    f"• Resend Interval: <b>{r_interval}s</b>",
                    parse_mode="HTML"
                )
            except Exception:
                await update.message.reply_text("❌ Sahi format me value dalein. Example: <code>3 60</code>", parse_mode="HTML")

    elif state == 'WAITING_DM_REPLY':
        idx = context.user_data.get('target_acc_index')
        if idx is not None and idx < len(USER_ACCOUNTS):
            USER_ACCOUNTS[idx]['auto_reply'] = user_text
            save_persisted_data()
            acc_phone = USER_ACCOUNTS[idx]['phone']
            context.user_data['state'] = None
            context.user_data['target_acc_index'] = None
            await update.message.reply_text(
                f"✅ Account {acc_phone} ka DM Auto-Reply set ho gaya:\n<code>{user_text}</code>",
                parse_mode="HTML"
            )

async def post_init(application):
    print("Loading sessions and data...")
    await load_saved_sessions()
    print(f"Total accounts loaded: {len(USER_ACCOUNTS)}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Advanced Bot is running...")
    app.run_polling()
