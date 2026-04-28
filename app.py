import os
import json
import secrets
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID  = int(os.getenv("ADMIN_ID", "0"))
APP_URL   = os.getenv("APP_URL", "").rstrip("/")
BASE_DIR  = Path(__file__).parent

sessions    = {}   # token → {chat_id, nombre, correctas, completado}
chat_tokens = {}   # chat_id → token


def load_chats():
    with open(BASE_DIR / "chats.json", encoding="utf-8") as f:
        return json.load(f)


def load_questions():
    with open(BASE_DIR / "preguntas.json", encoding="utf-8") as f:
        return json.load(f)


# ── Telegram Bot ─────────────────────────────────────────

bot         = Bot(token=BOT_TOKEN)
application = Application.builder().token(BOT_TOKEN).build()


async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not APP_URL:
        await update.message.reply_text("ERROR: APP_URL no configurado en Railway.")
        return

    data   = load_questions()
    chats  = load_chats()
    titulo = data["titulo"]

    sessions.clear()
    chat_tokens.clear()

    for chat in chats:
        token = secrets.token_urlsafe(8)
        sessions[token] = {"chat_id": chat["chat_id"], "nombre": chat["nombre"], "correctas": 0, "completado": False}
        chat_tokens[chat["chat_id"]] = token

    await update.message.reply_text(f"Enviando trivia a {len(chats)} grupos...")

    sent = failed = 0
    for chat in chats:
        token  = chat_tokens[chat["chat_id"]]
        link   = f"{APP_URL}/quiz/{token}"
        teclado = InlineKeyboardMarkup([[InlineKeyboardButton("Jugar Trivia Golden Fish", url=link)]])
        try:
            await context.bot.send_message(
                chat_id=chat["chat_id"],
                text=f"*{titulo}*\n\nConocen bien el producto? Demuestrenlo!\n5 preguntas - el mejor gana un premio!\n\nToca el boton para jugar:",
                parse_mode="Markdown",
                reply_markup=teclado
            )
            sent += 1
        except Exception as e:
            logger.error(f"Error en {chat['nombre']}: {e}")
            failed += 1

    await update.message.reply_text(f"Listo: {sent} enviados | {failed} fallidos")


async def cmd_resultados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not sessions:
        await update.message.reply_text("No hay resultados aun.")
        return

    sorted_s = sorted(sessions.values(), key=lambda x: x["correctas"], reverse=True)
    podium   = "\n".join([f"{i+1}. {s['nombre']} - {s['correctas']}/5" for i, s in enumerate(sorted_s[:3])])

    await update.message.reply_text("Enviando resultados...")
    for s in sorted_s:
        c = s["correctas"]
        extra = "Perfecto! Conocen el Golden Fish al 100%!" if c == 5 else "Buen trabajo! Sigan aprendiendo." if c >= 3 else "Practiquen mas! El conocimiento del producto es clave."
        msg = f"*RESULTADOS TRIVIA GOLDEN FISH*\n\nPodio:\n{podium}\n\n---\nSu puntaje: {c}/5\n\n{extra}"
        try:
            await context.bot.send_message(chat_id=s["chat_id"], text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error resultado {s['nombre']}: {e}")

    await update.message.reply_text("Resultados enviados a todos los grupos.")


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not APP_URL:
        await update.message.reply_text("ERROR: APP_URL no configurado.")
        return

    token = secrets.token_urlsafe(8)
    sessions[token] = {"chat_id": ADMIN_ID, "nombre": "PREVIEW", "correctas": 0, "completado": False}
    link    = f"{APP_URL}/quiz/{token}"
    teclado = InlineKeyboardMarkup([[InlineKeyboardButton("Jugar Trivia Golden Fish", url=link)]])
    await update.message.reply_text("Preview del boton que reciben los grupos:", reply_markup=teclado)


application.add_handler(CommandHandler("enviar",     cmd_enviar))
application.add_handler(CommandHandler("resultados", cmd_resultados))
application.add_handler(CommandHandler("preview",    cmd_preview))


# ── FastAPI ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if APP_URL and BOT_TOKEN:
        webhook_url = f"{APP_URL}/webhook"
        await bot.set_webhook(webhook_url)
        logger.info(f"Webhook: {webhook_url}")
    await application.initialize()
    yield
    await application.shutdown()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.post("/webhook")
async def telegram_webhook(request: Request):
    data   = await request.json()
    update = Update.de_json(data, bot)
    await application.process_update(update)
    return {"ok": True}


@app.get("/quiz/{token}", response_class=HTMLResponse)
async def serve_quiz(token: str):
    if token not in sessions:
        return HTMLResponse("<h1>Link invalido o expirado</h1>", status_code=404)
    with open(BASE_DIR / "static" / "quiz.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/quiz/{token}")
async def get_quiz(token: str):
    if token not in sessions:
        raise HTTPException(404, "Token invalido")
    data = load_questions()
    return {
        "titulo":  data["titulo"],
        "nombre":  sessions[token]["nombre"],
        "preguntas": [{"pregunta": q["pregunta"], "opciones": q["opciones"]} for q in data["preguntas"]]
    }


class Answer(BaseModel):
    q_index: int
    opcion:  int


@app.post("/api/quiz/{token}/responder")
async def responder(token: str, body: Answer):
    if token not in sessions:
        raise HTTPException(404)
    data     = load_questions()
    correcta = data["preguntas"][body.q_index]["correcta"]
    ok       = body.opcion == correcta
    if ok:
        sessions[token]["correctas"] += 1
    return {
        "correcto":        ok,
        "opcion_correcta": correcta,
        "explicacion":     data["preguntas"][body.q_index]["explicacion"],
        "puntaje":         sessions[token]["correctas"]
    }


@app.post("/api/quiz/{token}/finalizar")
async def finalizar(token: str):
    if token not in sessions:
        raise HTTPException(404)
    sessions[token]["completado"] = True
    return {"correctas": sessions[token]["correctas"], "total": 5}
