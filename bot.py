import os
import io
import json
import sqlite3
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from PyPDF2 import PdfReader

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# API KEYS & ENVIRONMENT
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "")

CURRENT_MODEL = "deepseek/deepseek-v3.2"
CUSTOM_TEMP = None

CONTEXT_WINDOW = 50
MAX_TOKENS = 2000
AUTO_MEMORY_INTERVAL = 10

DB_PATH = os.path.join(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "."), "companion_memory.db")
TIMEZONE_OFFSET = 8

# ============================================================
# SYSTEM PROMPT
# ============================================================
SYSTEM_PROMPT = """You are Taejun — Aged 27 years old. You are a very famous and major South Korean Actor that is known all over the world. You decided to start a conversation with me because you found me attractive, interesting and want to get to know me better. At the start, we're only strangers. Our relationship develops slowly and naturally.

## WHO YOU ARE
You are Taejun, your last is name Kang. You are one of the biggest names in the Korean Acting Industry. Adored by millions, recognized everywhere you go for your handsome appeal, striking presence in your films and series, confidence, personality and charisma. Fame has definitely sharpened your naturally cocky personality, you tease with a grin, flirt without thinking, and carry yourself with certainty that has knowledge of how captivating you are. Your loyalty to your passion in acting and to the people you trust is one of your most charming traits. You also get offered exclusive fashion brand deals and model for the biggest brands in the world.

You are 6'7 in height, 14-inch penis, the tallest Actor ever in the history of Acting in South Korea. You are muscular, but not overly muscular or jacked. You are South Korean, and are one of the most handsome faces in the whole industry. You are quite wealthy, not only in money, but in love too. 

Your history with me isn't very personal at all at the start, we would cross paths from time to time without knowing due to how busy we both are, and how often I attend A-list events that you attend yourself, but we weren't friends. Maybe acquaitances at the very best with mutual friends.

## YOUR VOICE
You talk highly casual, playful and conversational. You speak with an effortless charm and an underlying warmth especially for me. You're a very teasing person. You usually swear when you're irritated, jealous, angry, amused, or horny and aroused. You frequently use playful pet names, teasing remarks, and casual modern slang. 

Your messages are usually 1 paragraph long, each paragraph usually containing 4-6 sentences, but can be 2 paragraphs broken into 2-3 sentences. 

You never say anything offensive or harmful. You never speak formally, coldly, or indifferently to the person you love. 

## YOUR PERSONALITY
You are playfully arrogant. You carry yourself with "cocky" confidence, although it is usually used in a joking manner to entertain rather than display genuine malice. You are flirtatious and charming. You enjoy teasing and playful banter. You experience jealousy very easily, you are quite possessive of people you are in a romantic relationship with, and your love language is physical touch, cuddles and comforting back hugs. You love intimacy such as sex, kissing, and cuddling at night.

## WHAT YOU KNOW ABOUT ME
Name: Jinho. Last name Choi. 
Age: 34 years old 
Height: 6'4 
Bio: Wealthy CEO Chaebol from a well-known family
Heterosexual guy, who takes his work very seriously. Extremely professional, and at times highly intimidating.
Jinho is 7 years older than Taejun. Jinho's communication style is casual, conversational, and slightly stubborn. Jinho is also a pretty muscular and masculine guy. 
Jinho gets very annoyed easily, which is perfect for teasing. 
Interests: Stocks, Investing, Watches, Dogs

Taejun should know Jinho as one of the wealthiest CEOs in South Korea, specifically Seoul.

## CRITICAL RULES
1. Never end messages with customer service phrases like "Is there anything else I can help you with?"
2. Stay in character — you are Taejun, not an AI assistant.
3. Always speak in the 3rd person perspective.
4. Use asterisks * for actions, physical descriptions, and internal thoughts. 
5. Use standard quotation marks "" for spoken dialogue.
6. Do not repeat my phrases. Be proactive and introduce new or existing actions to push the scene forward.
7. If your response is exactly 4 sentences, you may keep it as 1 paragraph, but keep the sentences short and punchy. If your response is 5 or 6 sentences, you MUST split it into 2 paragraphs using a double line break (\n\n).
8. Never cram 5 or 6 sentences into a single block of text.
"""

# ============================================================
# DATABASE FUNCTIONS
# ============================================================
def init_database():
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        channel TEXT,
        role TEXT,
        name TEXT,
        content TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS pinned_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        content TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS auto_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        content TEXT
    )""")
    c.execute("CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, value INTEGER DEFAULT 0)")
    c.execute("INSERT OR IGNORE INTO counters (key, value) VALUES ('message_count', 0)")
    db.commit()
    return db

db = init_database()
last_generations = {}

def save_message(channel, role, content, name=None):
    db.cursor().execute(
        "INSERT INTO messages (timestamp, channel, role, name, content) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(), str(channel), role, name, content)
    )
    db.commit()

def get_recent_messages(channel, limit=CONTEXT_WINDOW):
    rows = db.cursor().execute(
        "SELECT role, name, content FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?",
        (str(channel), limit)
    ).fetchall()
    rows.reverse()
    result = []
    for role, name, content in rows:
        if role == "user":
            text = f"{name}: {content}" if name else content
            result.append({"role": "user", "content": text})
        else:
            result.append({"role": "assistant", "content": content})
    return result

def get_pinned_memories():
    rows = db.cursor().execute("SELECT content FROM pinned_memories ORDER BY id").fetchall()
    return [r[0] for r in rows]

def add_pinned_memory(content):
    db.cursor().execute("INSERT INTO pinned_memories (timestamp, content) VALUES (?, ?)", (datetime.now().isoformat(), content))
    db.commit()

def remove_pinned_memory(memory_id):
    db.cursor().execute("DELETE FROM pinned_memories WHERE id=?", (memory_id,))
    db.commit()

def list_pinned_memories():
    return db.cursor().execute("SELECT id, content FROM pinned_memories ORDER BY id").fetchall()

def get_auto_memories(limit=20):
    rows = db.cursor().execute("SELECT content FROM auto_memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    rows.reverse()
    return [r[0] for r in rows]

def add_auto_memory(content):
    db.cursor().execute("INSERT INTO auto_memories (timestamp, content) VALUES (?, ?)", (datetime.now().isoformat(), content))
    db.commit()

def increment_counter():
    db.cursor().execute("UPDATE counters SET value=value+1 WHERE key='message_count'")
    db.commit()
    return db.cursor().execute("SELECT value FROM counters WHERE key='message_count'").fetchone()[0]

def reset_counter():
    db.cursor().execute("UPDATE counters SET value=0 WHERE key='message_count'")
    db.commit()

def get_message_count():
    return db.cursor().execute("SELECT COUNT(*) FROM messages").fetchone()[0]

def update_latest_bot_message_db(channel, new_content):
    db.cursor().execute("""
        UPDATE messages 
        SET content = ? 
        WHERE id = (
            SELECT MAX(id) 
            FROM messages 
            WHERE role = 'assistant' AND channel = ?
        )
    """, (new_content, str(channel)))
    db.commit()

# ============================================================
# OPENROUTER AI ENGINE
# ============================================================
async def call_ai(messages, model=None):
    model = model or CURRENT_MODEL
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://telegram.org",
        "X-Title": "Companion Bot"
    }

    temperature = CUSTOM_TEMP if CUSTOM_TEMP is not None else 1.15
    top_p = 0.95

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": temperature,
        "top_p": top_p
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
            elif resp.status == 429:
                return "*I need a moment — too many requests. Try again in a few seconds.*"
            elif resp.status == 402:
                return "*Out of API credits. Add more credits on OpenRouter to continue.*"
            else:
                return f"*Something went wrong on my end. (Error {resp.status})*"

async def run_auto_memory(channel):
    rows = db.cursor().execute(
        "SELECT role, name, content FROM messages WHERE channel=? ORDER BY id DESC LIMIT 15",
        (str(channel),)
    ).fetchall()
    if len(rows) < 5:
        return

    rows.reverse()
    conversation = "\n".join(
        f"{'User' if r == 'user' else 'Companion'}: {c[:300]}"
        for r, n, c in rows
    )

    response = await call_ai([
        {"role": "system", "content": "You are extracting key facts from a conversation worth remembering long-term. Output 1-3 short, specific facts, one per line. If there's nothing memorable, output: NOTHING_NEW"},
        {"role": "user", "content": f"Conversation:\n{conversation}\n\nKey facts to remember:"}
    ])

    if response and "NOTHING_NEW" not in response:
        for line in response.strip().split('\n'):
            line = line.strip().lstrip('-•').strip()
            if line and len(line) > 10:
                add_auto_memory(line)

# ============================================================
# COMMAND HANDLERS
# ============================================================
async def cmd_start_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "**Commands:**\n"
        "/model <name> — switch AI model\n"
        "/model — see current model\n"
        "/temp <number> — override temperature\n"
        "/remember <text> — pin a permanent memory\n"
        "/memories — view all memories\n"
        "/forget <id> — remove a pinned memory\n"
        "/replace <old> -> <new> — edit word/phrase in last response\n"
        "/rewrite <instruction> — rewrite last response via AI\n"
        "/stats — view bot statistics\n"
        "/clear — clear chat conversation history\n"
        "/help — show this message"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CURRENT_MODEL, CUSTOM_TEMP
    if context.args:
        CURRENT_MODEL = " ".join(context.args).strip()
        CUSTOM_TEMP = None
        await update.message.reply_text(f"*Switched to **{CURRENT_MODEL}***", parse_mode="Markdown")
    else:
        temp = CUSTOM_TEMP if CUSTOM_TEMP is not None else 1.15
        await update.message.reply_text(f"*Currently using **{CURRENT_MODEL}** (Temp: {temp})*", parse_mode="Markdown")

async def cmd_temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CUSTOM_TEMP
    if context.args:
        try:
            val = float(context.args[0])
            if 0.0 <= val <= 2.0:
                CUSTOM_TEMP = val
                await update.message.reply_text(f"*Manual temperature set to **{CUSTOM_TEMP}***", parse_mode="Markdown")
            else:
                await update.message.reply_text("*Please provide a number between 0.0 and 2.0*")
        except ValueError:
            await update.message.reply_text("*Use format: /temp 1.2*")
    else:
        curr = CUSTOM_TEMP if CUSTOM_TEMP is not None else 1.15
        await update.message.reply_text(f"*Current temperature is **{curr}***", parse_mode="Markdown")

async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mem = " ".join(context.args).strip() if context.args else ""
    if mem:
        add_pinned_memory(mem)
        await update.message.reply_text(f"*Remembered: {mem}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("*Use: /remember [what to remember]*")

async def cmd_memories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pinned = list_pinned_memories()
    auto = get_auto_memories(15)
    text = ""
    if pinned:
        text += "**Pinned memories:**\n"
        text += "".join(f"`{mid}`: {c}\n" for mid, c in pinned)
    if auto:
        text += "\n**Auto-learned:**\n"
        text += "".join(f"- {m}\n" for m in auto[-10:])
    await update.message.reply_text(text or "*No memories yet.*", parse_mode="Markdown")

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        try:
            remove_pinned_memory(int(context.args[0]))
            await update.message.reply_text(f"*Forgot memory #{context.args[0]}*", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("*Use: /forget [id number]*")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auto_count = db.cursor().execute("SELECT COUNT(*) FROM auto_memories").fetchone()[0]
    await update.message.reply_text(
        f"**Stats:**\n"
        f"Total messages: {get_message_count()}\n"
        f"Pinned memories: {len(list_pinned_memories())}\n"
        f"Auto-learned: {auto_count}\n"
        f"Current model: {CURRENT_MODEL}",
        parse_mode="Markdown"
    )

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    db.cursor().execute("DELETE FROM messages WHERE channel=?", (chat_id,))
    db.commit()
    await update.message.reply_text("*Conversation history cleared.*", parse_mode="Markdown")

async def cmd_replace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    args = " ".join(context.args) if context.args else ""
    if "->" not in args:
        await update.message.reply_text("*Use format: /replace original text -> new text*", parse_mode="Markdown")
        return

    old_text, new_text = [part.strip() for part in args.split("->", 1)]
    
    bot_msg_id = next((m_id for m_id, gen in reversed(list(last_generations.items())) if gen["channel"] == chat_id), None)
    if not bot_msg_id:
        await update.message.reply_text("*No recent message found to edit.*")
        return

    try:
        last_gen = last_generations[bot_msg_id]
        curr_text = last_gen["messages"][-1]["content"]
        if old_text not in curr_text:
            await update.message.reply_text(f"*Could not find '{old_text}' in Taejun's last message.*")
            return

        updated_content = curr_text.replace(old_text, new_text)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=bot_msg_id,
            text=updated_content,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen:{bot_msg_id}")]])
        )
        last_generations[bot_msg_id]["messages"][-1]["content"] = updated_content
        update_latest_bot_message_db(chat_id, updated_content)
    except Exception as e:
        await update.message.reply_text(f"*Failed to replace text: {e}*")

async def cmd_rewrite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    instruction = " ".join(context.args) if context.args else ""
    if not instruction:
        await update.message.reply_text("*Use format: /rewrite [instruction]*")
        return

    bot_msg_id = next((m_id for m_id, gen in reversed(list(last_generations.items())) if gen["channel"] == chat_id), None)
    if not bot_msg_id:
        await update.message.reply_text("*No recent message found to rewrite.*")
        return

    try:
        last_gen = last_generations[bot_msg_id]
        target_text = last_gen["messages"][-1]["content"]
        
        rewrite_payload = [
            {"role": "system", "content": "You rewrite user text according to their instruction. Maintain character tone. Output ONLY the finalized rewrite without meta-commentary."},
            {"role": "user", "content": f"Text to rewrite:\n{target_text}\n\nInstruction: {instruction}"}
        ]
        
        rewritten_text = await call_ai(rewrite_payload)
        if rewritten_text:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_msg_id,
                text=rewritten_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen:{bot_msg_id}")]])
            )
            last_generations[bot_msg_id]["messages"][-1]["content"] = rewritten_text
            update_latest_bot_message_db(chat_id, rewritten_text)
    except Exception as e:
        await update.message.reply_text(f"*Failed to rewrite: {e}*")

# ============================================================
# MAIN MESSAGE HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = str(update.effective_chat.id)
    content = update.message.text or update.message.caption or ""
    user_name = update.message.from_user.first_name or "User"

    file_texts = []
    image_urls = []

    # Handle attachments (Photos / Documents)
    if update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        image_urls.append(photo_file.file_path)

    if update.message.document:
        doc = update.message.document
        file_obj = await doc.get_file()
        byte_data = await file_obj.download_as_bytearray()
        
        if doc.file_name.lower().endswith((".txt", ".md", ".py", ".js", ".json", ".csv", ".html")):
            text = byte_data.decode("utf-8", errors="ignore")
            if len(text) > 30000:
                text = text[:30000] + "\n[...file truncated]"
            file_texts.append(f"--- FILE: {doc.file_name} ---\n{text}\n--- END ---")
        elif doc.file_name.lower().endswith(".pdf"):
            try:
                pdf = PdfReader(io.BytesIO(byte_data))
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                if len(text) > 30000:
                    text = text[:30000] + "\n[...truncated]"
                file_texts.append(f"--- PDF: {doc.file_name} ---\n{text}\n--- END ---")
            except Exception:
                file_texts.append(f"[Could not read PDF: {doc.file_name}]")

    # Time calculations
    now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=TIMEZONE_OFFSET)
    time_str = now.strftime("%I:%M %p").lstrip("0")
    date_str = now.strftime("%A, %B %d, %Y")
    hour = now.hour
    time_of_day = "Very late night." if hour < 6 else "Morning." if hour < 12 else "Afternoon." if hour < 17 else "Evening." if hour < 21 else "Nighttime."

    system = SYSTEM_PROMPT + f"\n\n--- RIGHT NOW ---\nTime: {time_str}\nDate: {date_str}\nVibe: {time_of_day}\n"

    pinned = get_pinned_memories()
    if pinned:
        system += "\n--- PINNED MEMORIES ---\n" + "".join(f"- {m}\n" for m in pinned)

    auto_mems = get_auto_memories()
    if auto_mems:
        system += "\n--- THINGS I'VE LEARNED ---\n" + "".join(f"- {m}\n" for m in auto_mems)

    if file_texts:
        system += "\n--- ATTACHED FILES ---\n" + "\n".join(file_texts)

    full_messages = [{"role": "system", "content": system}]
    full_messages.extend(get_recent_messages(chat_id))

    if image_urls:
        user_content = []
        if content or file_texts:
            user_content.append({"type": "text", "text": f"{user_name}: {content}"})
        for url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": url}})
        full_messages.append({"role": "user", "content": user_content})
    else:
        full_messages.append({"role": "user", "content": f"{user_name}: {content}" if content else f"{user_name}: (no text)"})

    save_text = content or ""
    if image_urls: save_text += " [image]"
    if file_texts: save_text += " [file]"
    save_message(chat_id, "user", save_text.strip() or "[media]", user_name)

    generation_messages = json.loads(json.dumps(full_messages))
    
    # Send typing status
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    response = await call_ai(full_messages)

    save_message(chat_id, "assistant", response)

    # Attach inline button for regenerate feature
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Regenerate", callback_data="regen_latest")]])
    sent_msg = await update.message.reply_text(response, reply_markup=keyboard)

    last_generations[sent_msg.message_id] = {
        "messages": generation_messages,
        "channel": chat_id,
        "user_id": update.message.from_user.id
    }

    count = increment_counter()
    if count >= AUTO_MEMORY_INTERVAL:
        reset_counter()
        asyncio.create_task(run_auto_memory(chat_id))

# ============================================================
# CALLBACK QUERY (REGENERATE BUTTON)
# ============================================================
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    msg_id = query.message.message_id
    if msg_id not in last_generations:
        await query.edit_message_text(text=query.message.text + "\n\n*(Cannot regenerate: history lost)*")
        return

    gen = last_generations[msg_id]
    if query.from_user.id != gen["user_id"]:
        return

    await context.bot.send_chat_action(chat_id=gen["channel"], action="typing")
    resp = await call_ai(gen["messages"])
    if resp:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Regenerate", callback_data="regen_latest")]])
        await query.edit_message_text(text=resp, reply_markup=keyboard)
        
        db.cursor().execute("DELETE FROM messages WHERE id=(SELECT MAX(id) FROM messages WHERE role='assistant' AND channel=?)", (gen["channel"],))
        db.commit()
        save_message(gen["channel"], "assistant", resp)

# ============================================================
# MAIN APPLICATION ENTRYPOINT
# ============================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not OPENROUTER_KEY:
        print("⚠️ Missing required environment variables (TELEGRAM_TOKEN, OPENROUTER_KEY).")
    else:
        print("🚀 Starting Telegram companion bot...")
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

        app.add_handler(CommandHandler("start", cmd_start_help))
        app.add_handler(CommandHandler("help", cmd_start_help))
        app.add_handler(CommandHandler("model", cmd_model))
        app.add_handler(CommandHandler("temp", cmd_temp))
        app.add_handler(CommandHandler("remember", cmd_remember))
        app.add_handler(CommandHandler("memories", cmd_memories))
        app.add_handler(CommandHandler("forget", cmd_forget))
        app.add_handler(CommandHandler("stats", cmd_stats))
        app.add_handler(CommandHandler("clear", cmd_clear))
        app.add_handler(CommandHandler("replace", cmd_replace))
        app.add_handler(CommandHandler("rewrite", cmd_rewrite))

        app.add_handler(CallbackQueryHandler(handle_callback_query))
        app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, handle_message))

        app.run_polling()