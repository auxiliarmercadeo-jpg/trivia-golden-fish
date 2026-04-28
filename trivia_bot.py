import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, PollAnswerHandler, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID  = int(os.getenv("ADMIN_ID", "0"))
BASE_DIR  = Path(__file__).parent

# poll_id → {chat_id, q_index, correct_option_id}
poll_map = {}
# chat_id → {nombre, correctas}
scores = {}
# chat_id → set of q_index answered correctly by any member
correct_questions = {}


def load_chats():
    with open(BASE_DIR / "chats.json", encoding="utf-8") as f:
        return json.load(f)


def load_questions():
    with open(BASE_DIR / "preguntas.json", encoding="utf-8") as f:
        return json.load(f)


async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("No autorizado.")
        return

    data      = load_questions()
    chats     = load_chats()
    preguntas = data["preguntas"]
    titulo    = data["titulo"]

    poll_map.clear()
    scores.clear()
    correct_questions.clear()

    await update.message.reply_text(f"Enviando trivia a {len(chats)} grupos...")

    sent = failed = 0
    for chat in chats:
        chat_id = chat["chat_id"]
        nombre  = chat["nombre"]
        scores[chat_id]            = {"nombre": nombre, "correctas": 0}
        correct_questions[chat_id] = set()

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"*{titulo}* \n\n"
                    f"Conocen bien el producto? Demuestrenlo!\n"
                    f"5 preguntas - el mejor gana un premio!"
                ),
                parse_mode="Markdown"
            )
            for i, q in enumerate(preguntas):
                msg = await context.bot.send_poll(
                    chat_id=chat_id,
                    question=f"Pregunta {i+1} de {len(preguntas)} - {q['pregunta']}",
                    options=q["opciones"],
                    type="quiz",
                    correct_option_id=q["correcta"],
                    explanation=q["explicacion"],
                    is_anonymous=False,
                    open_period=86400
                )
                poll_map[msg.poll.id] = {
                    "chat_id": chat_id,
                    "q_index": i,
                    "correct": q["correcta"]
                }
            sent += 1
        except Exception as e:
            logger.error(f"Error en {nombre}: {e}")
            failed += 1

    await update.message.reply_text(f"Listo: {sent} enviados | {failed} fallidos")


async def cmd_resultados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("No autorizado.")
        return

    if not scores:
        await update.message.reply_text("No hay resultados aun.")
        return

    sorted_scores = sorted(scores.items(), key=lambda x: x[1]["correctas"], reverse=True)
    medals = ["1", "2", "3"]

    podium_lines = []
    for i, (_, data) in enumerate(sorted_scores[:3]):
        podium_lines.append(f"{medals[i]}. {data['nombre']} - {data['correctas']}/5")
    podium = "\n".join(podium_lines)

    await update.message.reply_text(f"Enviando resultados a {len(scores)} grupos...")

    for _, (chat_id, data) in enumerate(sorted_scores):
        correctas = data["correctas"]

        if correctas == 5:
            extra = "Perfecto! Conocen el Golden Fish al 100%! Premio merecido!"
        elif correctas >= 3:
            extra = "Buen trabajo! Sigan aprendiendo el producto."
        else:
            extra = "Practiquen mas! El conocimiento del producto es clave para vender mejor."

        msg = (
            f"*RESULTADOS TRIVIA GOLDEN FISH*\n\n"
            f"Podio de esta trivia:\n{podium}\n\n"
            f"---\n"
            f"Su puntaje: {correctas}/5 respuestas correctas\n\n"
            f"{extra}"
        )

        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error resultado a {data['nombre']}: {e}")

    await update.message.reply_text("Resultados enviados a todos los grupos.")


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send trivia only to admin chat for preview before sending to all groups."""
    if update.effective_user.id != ADMIN_ID:
        return

    data      = load_questions()
    preguntas = data["preguntas"]
    titulo    = data["titulo"]

    await update.message.reply_text(
        f"PREVIEW - {titulo}\n\nAsí verán los grupos las preguntas:"
    )
    for i, q in enumerate(preguntas):
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=f"Pregunta {i+1} de {len(preguntas)} - {q['pregunta']}",
            options=q["opciones"],
            type="quiz",
            correct_option_id=q["correcta"],
            explanation=q["explicacion"],
            is_anonymous=False
        )


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer  = update.poll_answer
    poll_id = answer.poll_id

    if poll_id not in poll_map:
        return

    info    = poll_map[poll_id]
    chat_id = info["chat_id"]
    q_index = info["q_index"]

    if chat_id not in scores:
        return

    if answer.option_ids and answer.option_ids[0] == info["correct"]:
        correct_questions[chat_id].add(q_index)
        scores[chat_id]["correctas"] = len(correct_questions[chat_id])


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("enviar",     cmd_enviar))
    app.add_handler(CommandHandler("resultados", cmd_resultados))
    app.add_handler(CommandHandler("preview",    cmd_preview))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
