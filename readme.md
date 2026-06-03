# 📚 Quiz Bot for Telegram

This bot loads multiple-choice question banks from `.txt` files (like the ones you provided), shows questions with inline buttons, evaluates answers, and sends explanations *before* the next question.

## ✨ Features
- Accepts `.txt` files with questions marked by `✅` and explanations starting with `Ex:`.
- Parses up to 300 questions per file.
- Sends explanation as a separate message after each answer.
- Commands: `/start`, `/start_quiz`, `/skip`, `/reset`.
- Deployable on **Vercel** (serverless webhook).

## 🚀 Deployment on Vercel
1. Push this code to a GitHub repository.
2. Create a bot on Telegram via [@BotFather](https://t.me/BotFather) and get a token.
3. On Vercel → New Project → Import GitHub repo → Set environment variable `BOT_TOKEN`.
4. After deployment, set the bot webhook: