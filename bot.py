import asyncio
import logging
import os
import re
from datetime import datetime
from io import BytesIO
from typing import Final, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Document, InlineKeyboardButton, InlineKeyboardMarkup, Message

from docx import Document as DocxDocument
from dotenv import load_dotenv
from pdfminer.high_level import extract_text as pdfminer_extract_text
from PyPDF2 import PdfReader


load_dotenv()

token = os.getenv("BOT_TOKEN")

if token is None:
    raise RuntimeError("Переменная окружения BOT_TOKEN не установлена")

API_TOKEN: Final[str] = token


class ResumeUpload(StatesGroup):
    waiting_for_file = State()


def normalize_text(text: str) -> str:
    if not text:
        return ""

    cleaned = text.replace("\u00ad", "").replace("\u200b", "").replace("\xa0", " ")
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = re.sub(r"[\t ]+", " ", cleaned)
    cleaned = re.sub(r"\n +", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_text_from_pdf(file_buffer: BytesIO) -> str:
    file_buffer.seek(0)
    text = pdfminer_extract_text(file_buffer)
    if text:
        return normalize_text(text)

    file_buffer.seek(0)
    reader = PdfReader(file_buffer)
    pages = [page.extract_text() or "" for page in reader.pages]
    return normalize_text("\n".join(pages))


def extract_text_from_docx(file_buffer: BytesIO) -> str:
    file_buffer.seek(0)
    document = DocxDocument(file_buffer)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    return normalize_text("\n".join(filter(None, paragraphs)))


def get_file_extension(document: Document) -> Optional[str]:
    if not document.file_name:
        return None
    if "." not in document.file_name:
        return None
    return document.file_name.rsplit(".", 1)[1].lower()


def build_jobs_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Откликнуться", url="https://example.com/job1")],
        [InlineKeyboardButton(text="Откликнуться", url="https://example.com/job2")],
        [InlineKeyboardButton(text="Откликнуться", url="https://example.com/job3")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def handle_resume_command(message: Message, state: FSMContext) -> None:
    await state.set_state(ResumeUpload.waiting_for_file)
    await message.answer(
        "Пришлите файл резюме в формате PDF или DOCX, и я пришлю его текст.",
    )


async def handle_resume_file(message: Message, state: FSMContext, bot: Bot) -> None:
    document = message.document

    if document is None:
        await message.answer("Это должен быть документ. Пришлите PDF или DOCX файл.")
        return

    extension = get_file_extension(document)
    if extension not in {"pdf", "docx"}:
        await message.answer("Поддерживаются только PDF и DOCX файлы резюме.")
        return

    file = await bot.get_file(document.file_id)
    buffer = BytesIO()
    await bot.download(file, destination=buffer)
    buffer.seek(0)

    try:
        if extension == "pdf":
            text = extract_text_from_pdf(buffer)
        else:
            text = extract_text_from_docx(buffer)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to parse resume: %s", exc)
        await message.answer("Не удалось обработать файл. Попробуйте другой формат или файл.")
        return
    finally:
        await state.clear()

    if not text:
        await message.answer("Полученный файл не содержит читаемого текста.")
        return

    date_label = datetime.now().strftime("%Y-%m-%d")
    max_payload_length = 3500
    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_payload_length:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, max_payload_length)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, max_payload_length)
        if split_at == -1:
            split_at = max_payload_length

        chunk = remaining[:split_at].rstrip()
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()

    for index, chunk in enumerate(chunks, start=1):
        header = f"{date_label} • Страница {index}\n\n"
        max_body_length = 4096 - len(header)
        body = chunk[:max_body_length]
        await message.answer(header + body)


async def handle_search_command(message: Message) -> None:
    vacancies = [
        "Вакансия 1: Middle Python Developer",
        "Вакансия 2: Data Analyst",
        "Вакансия 3: Backend Engineer",
    ]

    jobs_text = "\n\n".join(vacancies)
    await message.answer(
        f"Вот несколько предложений:\n\n{jobs_text}",
        reply_markup=build_jobs_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def handle_start_command(message: Message) -> None:
    await message.answer(
        "Привет! Я бот для проверки резюме. Используйте команды:\n"
        "/resume — отправить резюме (PDF или DOCX)\n"
        "/search — показать вакансии"
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=API_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(handle_start_command, Command("start"))
    dp.message.register(handle_resume_command, Command("resume"))
    dp.message.register(handle_resume_file, ResumeUpload.waiting_for_file, F.document)
    dp.message.register(handle_search_command, Command("search"))

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
