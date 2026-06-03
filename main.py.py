import os
import re
import logging
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app for Vercel webhook
app = Flask(__name__)

# Bot token from environment variable
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set")

# Initialize bot application
bot_app = Application.builder().token(TOKEN).build()

# ==================== PARSING LOGIC ====================
def parse_quiz_from_text(text: str) -> list:
    """
    Extract questions, options, correct answer, explanation from .txt file.
    Returns list of dicts: {question, options, correct_index, explanation}
    """
    # Split into question blocks (starting with Q followed by number and dot)
    pattern = r'(Q\d+\.\s.*?)(?=Q\d+\.|\Z)'
    raw_blocks = re.findall(pattern, text, re.DOTALL)
    if not raw_blocks:
        # Fallback: try splitting by newline patterns
        raw_blocks = re.split(r'\n(?=Q\d+\.)', text)
        raw_blocks = [b.strip() for b in raw_blocks if b.strip()]

    quiz = []
    for block in raw_blocks:
        # Find the separator line containing "😂" or similar emoji
        sep_match = re.search(r'[😂😄😊]', block)
        if not sep_match:
            continue  # skip if no separator found

        # Question text is everything before the separator
        question_text = block[:sep_match.start()].strip()
        # Options are after separator, up to 'Ex:' or end of block
        after_sep = block[sep_match.end():].strip()
        # Split into lines and take first 4 non-empty lines as options
        lines = [line.strip() for line in after_sep.split('\n') if line.strip()]
        options = []
        correct_index = None
        for i, line in enumerate(lines[:4]):  # consider first 4 lines
            if '✅' in line:
                correct_index = i
                clean_opt = line.replace('✅', '').strip()
            else:
                clean_opt = line
            options.append(clean_opt)
        if correct_index is None:
            # fallback: search for ✅ anywhere in options
            for i, opt in enumerate(options):
                if '✅' in opt:
                    correct_index = i
                    options[i] = opt.replace('✅', '').strip()
                    break

        # Extract explanation (after 'Ex:' or 'Explanation:')
        expl_match = re.search(r'Ex:\s*(.*?)(?=\nQ\d+\.|\Z)', block, re.DOTALL | re.IGNORECASE)
        explanation = expl_match.group(1).strip() if expl_match else "No explanation provided."
        # Clean extra newlines/spaces
        explanation = re.sub(r'\s+', ' ', explanation)

        if len(options) >= 2 and correct_index is not None:
            quiz.append({
                'question': question_text,
                'options': options[:4],
                'correct_index': correct_index,
                'explanation': explanation
            })
    return quiz

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Quiz Bot*\n\n"
        "1. Send me a `.txt` file with your quiz (questions in the format shown in examples).\n"
        "2. Use `/start_quiz` to begin.\n"
        "3. Each question has 4 options. Tap the correct one.\n"
        "4. Explanation will appear, then press 'Next ➡️'.\n"
        "5. `/skip` to skip current question.\n"
        "6. `/reset` to clear current quiz.",
        parse_mode='Markdown'
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
        await update.message.reply_text("Please send a .txt file.")
        return

    file = await doc.get_file()
    content = await file.download_as_bytearray()
    text = content.decode('utf-8', errors='ignore')
    quiz = parse_quiz_from_text(text)

    if not quiz:
        await update.message.reply_text("❌ No valid questions found. Check file format.")
        return

    if len(quiz) > 300:
        await update.message.reply_text(f"⚠️ File has {len(quiz)} questions. Limiting to first 300.")
        quiz = quiz[:300]

    context.user_data['quiz'] = quiz
    context.user_data['current'] = 0
    context.user_data['score'] = 0
    context.user_data['answered'] = False
    await update.message.reply_text(f"✅ Loaded {len(quiz)} questions. Use /start_quiz to begin.")

async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data.get('quiz')
    if not quiz:
        await update.message.reply_text("No quiz loaded. Please send a .txt file first.")
        return
    context.user_data['current'] = 0
    context.user_data['score'] = 0
    context.user_data['answered'] = False
    await send_question(update, context)

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    if chat_id is None:
        chat_id = update.effective_chat.id
    quiz = context.user_data.get('quiz')
    idx = context.user_data.get('current', 0)
    if not quiz or idx >= len(quiz):
        await context.bot.send_message(chat_id, "🏁 Quiz finished! Final score: {}/{}".format(
            context.user_data.get('score', 0), len(quiz) if quiz else 0))
        return

    q = quiz[idx]
    text = f"*Q{idx+1}. {q['question']}*\n\n"
    keyboard = []
    for i, opt in enumerate(q['options']):
        label = f"{chr(65+i)}. {opt}"
        callback = f"ans|{idx}|{i}"
        keyboard.append([InlineKeyboardButton(label, callback_data=callback)])
    # Add a "Skip" button
    keyboard.append([InlineKeyboardButton("⏭️ Skip", callback_data=f"skip|{idx}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    # Retrieve user_data (store per user using context.user_data; works as long as same user)
    # Note: In serverless, might be per request but practically okay for personal bot
    quiz = context.user_data.get('quiz')
    current_idx = context.user_data.get('current', 0)

    if data.startswith('ans|'):
        _, q_idx, chosen = data.split('|')
        q_idx = int(q_idx)
        chosen = int(chosen)
        # Prevent answering same question twice
        if context.user_data.get('answered', False):
            await query.edit_message_text("You already answered this question. Press Next.")
            return
        if q_idx != current_idx:
            await query.edit_message_text("Question mismatch. Please use /start_quiz again.")
            return
        correct = quiz[q_idx]['correct_index']
        if chosen == correct:
            context.user_data['score'] = context.user_data.get('score', 0) + 1
            result = "✅ Correct!"
        else:
            correct_letter = chr(65 + correct)
            correct_text = quiz[q_idx]['options'][correct]
            result = f"❌ Incorrect. Correct answer: {correct_letter}. {correct_text}"
        # Show result and explanation in a new message
        explanation = quiz[q_idx]['explanation']
        await query.edit_message_text(f"{result}\n\n📖 *Explanation:* {explanation}", parse_mode='Markdown')
        context.user_data['answered'] = True
        # Show Next button
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
        context.user_data['answered'] = True
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
        # Move to next question
        context.user_data['current'] = current_idx + 1
        context.user_data['answered'] = False
        await send_question(update, context, chat_id=update.effective_chat.id)
        await query.delete_message()  # delete the "Next" button message
    else:
        await query.edit_message_text("Unknown action.")

async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data.get('quiz')
    current = context.user_data.get('current', 0)
    if not quiz or current >= len(quiz):
        await update.message.reply_text("No active quiz.")
        return
    context.user_data['answered'] = True
    await update.message.reply_text("⏭️ Skipped current question.")
    # Move to next
    context.user_data['current'] = current + 1
    context.user_data['answered'] = False
    await send_question(update, context)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔄 Quiz data cleared. Send a new .txt file and /start_quiz.")

# Register handlers
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("start_quiz", start_quiz))
bot_app.add_handler(CommandHandler("skip", skip_command))
bot_app.add_handler(CommandHandler("reset", reset_command))
bot_app.add_handler(MessageHandler(filters.Document.TXT, handle_document))
bot_app.add_handler(CallbackQueryHandler(handle_callback))

# ==================== WEBHOOK (Flask) ====================
@app.route("/", methods=["GET"])
def index():
    return "Quiz Bot is running."

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
async def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot_app.bot)
        await bot_app.process_update(update)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.exception("Webhook error")
        return jsonify({"status": "error", "message": str(e)}), 500

# Vercel requires this variable
application = app

# When running locally (optional)
if __name__ == "__main__":
    from telegram.ext import Updater
    import asyncio
    # Use polling locally for testing
    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher
    # Add same handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("start_quiz", start_quiz))
    dispatcher.add_handler(CommandHandler("skip", skip_command))
    dispatcher.add_handler(CommandHandler("reset", reset_command))
    dispatcher.add_handler(MessageHandler(filters.Document.TXT, handle_document))
    dispatcher.add_handler(CallbackQueryHandler(handle_callback))
    updater.start_polling()
    updater.idle()