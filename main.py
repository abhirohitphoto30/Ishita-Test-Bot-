import os
import re
import json
import logging
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not set")

# Global application instance (initialized once)
application = Application.builder().token(TOKEN).build()

# ------------------- PARSING LOGIC (unchanged, but added persistence) -------------------
def parse_quiz_from_text(text: str) -> list:
    pattern = r'(Q\d+\.\s.*?)(?=Q\d+\.|\Z)'
    raw_blocks = re.findall(pattern, text, re.DOTALL)
    if not raw_blocks:
        raw_blocks = re.split(r'\n(?=Q\d+\.)', text)
        raw_blocks = [b.strip() for b in raw_blocks if b.strip()]

    quiz = []
    for block in raw_blocks:
        sep_match = re.search(r'[😂😄😊]', block)
        if not sep_match:
            continue
        question_text = block[:sep_match.start()].strip()
        after_sep = block[sep_match.end():].strip()
        lines = [line.strip() for line in after_sep.split('\n') if line.strip()]
        options = []
        correct_index = None
        for i, line in enumerate(lines[:4]):
            if '✅' in line:
                correct_index = i
                clean_opt = line.replace('✅', '').strip()
            else:
                clean_opt = line
            options.append(clean_opt)
        if correct_index is None:
            for i, opt in enumerate(options):
                if '✅' in opt:
                    correct_index = i
                    options[i] = opt.replace('✅', '').strip()
                    break
        expl_match = re.search(r'Ex:\s*(.*?)(?=\nQ\d+\.|\Z)', block, re.DOTALL | re.IGNORECASE)
        explanation = expl_match.group(1).strip() if expl_match else "No explanation provided."
        explanation = re.sub(r'\s+', ' ', explanation)
        if len(options) >= 2 and correct_index is not None:
            quiz.append({
                'question': question_text,
                'options': options[:4],
                'correct_index': correct_index,
                'explanation': explanation
            })
    return quiz

# ------------------- PERSISTENCE (using /tmp/quiz_data.json) -------------------
# Since Vercel may reuse the same instance, we store user data in a JSON file.
# This is not perfect but works for a personal bot.
DATA_FILE = "/tmp/quiz_data.json"

def load_user_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_user_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def get_user_state(user_id):
    data = load_user_data()
    return data.get(str(user_id), {})

def set_user_state(user_id, state):
    data = load_user_data()
    data[str(user_id)] = state
    save_user_data(data)

def clear_user_state(user_id):
    data = load_user_data()
    if str(user_id) in data:
        del data[str(user_id)]
        save_user_data(data)

# ------------------- HANDLERS -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Quiz Bot*\n\n"
        "1. Send me a `.txt` file with your quiz.\n"
        "2. Use `/start_quiz` to begin.\n"
        "3. Tap an answer → explanation appears → press Next.\n"
        "4. `/skip` to skip current question.\n"
        "5. `/reset` to clear current quiz.",
        parse_mode='Markdown'
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
        await update.message.reply_text("Please send a .txt file.")
        return
    file = await doc.get_file()
    content = await file.download_as_bytearray()
    text = content.decode('utf-8', errors='ignore')
    quiz = parse_quiz_from_text(text)
    if not quiz:
        await update.message.reply_text("❌ No valid questions found. Check format (✅ and Ex:).")
        return
    if len(quiz) > 300:
        quiz = quiz[:300]
    # Store in persistent state
    state = {
        'quiz': quiz,
        'current': 0,
        'score': 0,
        'answered': False
    }
    set_user_state(user_id, state)
    await update.message.reply_text(f"✅ Loaded {len(quiz)} questions. Use /start_quiz to begin.")

async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    if not state or 'quiz' not in state:
        await update.message.reply_text("No quiz loaded. Please send a .txt file first.")
        return
    state['current'] = 0
    state['score'] = 0
    state['answered'] = False
    set_user_state(user_id, state)
    await send_question(update, context, user_id)

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int = None):
    if chat_id is None:
        chat_id = update.effective_chat.id
    state = get_user_state(user_id)
    quiz = state.get('quiz')
    idx = state.get('current', 0)
    if not quiz or idx >= len(quiz):
        total = len(quiz) if quiz else 0
        await context.bot.send_message(chat_id, f"🏁 Quiz finished! Final score: {state.get('score',0)}/{total}")
        clear_user_state(user_id)
        return
    q = quiz[idx]
    text = f"*Q{idx+1}. {q['question']}*\n\n"
    keyboard = []
    for i, opt in enumerate(q['options']):
        label = f"{chr(65+i)}. {opt}"
        callback = f"ans|{idx}|{i}"
        keyboard.append([InlineKeyboardButton(label, callback_data=callback)])
    keyboard.append([InlineKeyboardButton("⏭️ Skip", callback_data=f"skip|{idx}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    state = get_user_state(user_id)
    if not state or 'quiz' not in state:
        await query.edit_message_text("No active quiz. Send a .txt file and /start_quiz")
        return
    quiz = state['quiz']
    current_idx = state['current']
    answered = state.get('answered', False)

    if data.startswith('ans|'):
        _, q_idx, chosen = data.split('|')
        q_idx = int(q_idx)
        chosen = int(chosen)
        if answered:
            await query.edit_message_text("You already answered this question. Press Next.")
            return
        if q_idx != current_idx:
            await query.edit_message_text("Question mismatch. Use /start_quiz again.")
            return
        correct = quiz[q_idx]['correct_index']
        if chosen == correct:
            state['score'] = state.get('score', 0) + 1
            result = "✅ Correct!"
        else:
            correct_letter = chr(65 + correct)
            correct_text = quiz[q_idx]['options'][correct]
            result = f"❌ Incorrect. Correct answer: {correct_letter}. {correct_text}"
        explanation = quiz[q_idx]['explanation']
        await query.edit_message_text(f"{result}\n\n📖 *Explanation:* {explanation}", parse_mode='Markdown')
        state['answered'] = True
        set_user_state(user_id, state)
        # Send Next button
        keyboard = [[InlineKeyboardButton("Next ➡️", callback_data=f"next|{q_idx}")]]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Click below to continue:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data.startswith('skip|'):
        _, q_idx = data.split('|')
        q_idx = int(q_idx)
        if q_idx != current_idx:
            await query.edit_message_text("Skip mismatch. Use /start_quiz.")
            return
        state['answered'] = True
        set_user_state(user_id, state)
        await query.edit_message_text("⏭️ Question skipped.")
        keyboard = [[InlineKeyboardButton("Next ➡️", callback_data=f"next|{q_idx}")]]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Press Next to continue.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data.startswith('next|'):
        _, q_idx = data.split('|')
        q_idx = int(q_idx)
        if q_idx != current_idx:
            await query.edit_message_text("Error. Use /start_quiz to reset.")
            return
        state['current'] = current_idx + 1
        state['answered'] = False
        set_user_state(user_id, state)
        await send_question(update, context, user_id, update.effective_chat.id)
        try:
            await query.delete_message()  # Delete the "Next" button message
        except:
            pass
    else:
        await query.edit_message_text("Unknown action.")

async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    if not state or 'quiz' not in state:
        await update.message.reply_text("No active quiz.")
        return
    state['answered'] = True
    set_user_state(user_id, state)
    await update.message.reply_text("⏭️ Skipped current question.")
    state['current'] = state['current'] + 1
    state['answered'] = False
    set_user_state(user_id, state)
    await send_question(update, context, user_id, update.effective_chat.id)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_user_state(user_id)
    await update.message.reply_text("🔄 Quiz data cleared. Send a new .txt file and /start_quiz.")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("start_quiz", start_quiz))
application.add_handler(CommandHandler("skip", skip_command))
application.add_handler(CommandHandler("reset", reset_command))
application.add_handler(MessageHandler(filters.Document.TXT, handle_document))
application.add_handler(CallbackQueryHandler(handle_callback))

# ------------------- FLASK WEBHOOK (CORRECTED) -------------------
@app.route("/", methods=["GET"])
def index():
    return "Quiz Bot is running."

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
async def webhook(token):
    if token != TOKEN:
        return jsonify({"status": "unauthorized"}), 403
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        # Process the update asynchronously
        await application.process_update(update)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.exception("Webhook error")
        return jsonify({"status": "error", "message": str(e)}), 500

# Initialize the application (this must be done before processing updates)
@app.before_first_request
def init_bot():
    # This runs once when the server starts
    loop = asyncio.get_event_loop()
    loop.run_until_complete(application.initialize())

import asyncio
