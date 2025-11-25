import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

from drive_utils import (
    ShareFailure,
    folder_url_for_team,
    list_files_for_team,
    share_folder_with_user,
)
from storage import (
    all_teams_with_counts,
    all_users,
    ensure_user,
    get_user,
    init_db,
    record_share,
    team_emails,
    update_email,
    update_team,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

ADMIN_IDS: List[int] = []
raw_admins = os.environ.get("ADMIN_IDS", "")
if raw_admins:
    for part in re.split(r"[;,\s]+", raw_admins.strip()):
        if part.isdigit():
            ADMIN_IDS.append(int(part))

raw_team_choices = os.environ.get("TEAM_CHOICES", "")
TEAM_CHOICES: List[str] = []
if raw_team_choices:
    try:
        data = json.loads(raw_team_choices)
        if isinstance(data, list) and data:
            TEAM_CHOICES = [str(choice) for choice in data]
    except json.JSONDecodeError:
        TEAM_CHOICES = [choice.strip() for choice in raw_team_choices.split(";") if choice.strip()]
if not TEAM_CHOICES:
    TEAM_CHOICES = ["الفرقة الأولى", "الفرقة الثانية", "الفرقة الثالثة"]

EMAIL_REGEX = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

ACCESS_INSTRUCTIONS = (
    "للوصول للملفات بعد ما أضيفك:\n"
    "1. افتح تطبيق Google Drive أو ادخل على drive.google.com بنفس البريد اللي أرسلته.\n"
    "2. من القائمة الجانبية اختار \"الملفات المشتركة\" أو \"Shared with me\".\n"
    "3. هتلاقي المجلد اللي شاركته معاك، افتحه وتشوف المحتوى."
)

START_NOTIFY_TEXT = (
    "البوت شغّال ✨\n"
    "لو حبيت تجدد الوصول، اكتب /start أو اختار فرقتك وأرسل بريدك.\n"
    f"{ACCESS_INSTRUCTIONS}"
)

AUTO_NOTIFY_ON_START = os.environ.get("AUTO_NOTIFY_ON_START", "true").lower() in (
    "1",
    "true",
    "yes",
)

FILE_PANEL_LIMIT = int(os.environ.get("FILE_PANEL_LIMIT", "5"))
FILE_LABEL_MAX = 40
FILE_PANEL_PROMPT = "تقدر تفتح المجلد أو تبص على الملفات من هنا:"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

contact_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="مشاركة رقم الهاتف", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


def build_team_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=team, callback_data=f"team|{team}")] for team in TEAM_CHOICES]
    )


def build_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"📁 {team}", callback_data=f"admin_team|{team}")] for team in TEAM_CHOICES]
    )


def _trim_file_label(name: str) -> str:
    return name if len(name) <= FILE_LABEL_MAX else f"{name[: FILE_LABEL_MAX - 3]}..."


def build_folder_action_keyboard(team: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="افتح المجلد", url=folder_url_for_team(team)),
            ],
            [
                InlineKeyboardButton(text="لوحة الملفات", callback_data=f"files|{team}"),
            ],
        ]
    )


class RegistrationStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_team = State()
    waiting_for_email = State()


@dp.message(CommandStart())
async def start_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    profile = await get_user(message.from_user.id)
    if profile and profile.get("phone"):
        await message.answer(
            "أهلاً مرة تانية! 😊 رقم تليفونك محفوظ، تقدر تختار فرقتك وتبعت إيميل جديد في أي وقت.\n\n"
            f"{ACCESS_INSTRUCTIONS}",
            reply_markup=build_team_keyboard(),
        )
        await state.set_state(RegistrationStates.waiting_for_team)
        return
    await message.answer(
        "مرحباً! ✨ أحتاج رقم هاتفك فقط عشان نبدأ، وتقدر تعيد العملية في أي وقت من جديد.",
        reply_markup=contact_keyboard,
    )
    await state.set_state(RegistrationStates.waiting_for_phone)


@dp.message(RegistrationStates.waiting_for_phone, F.contact)
async def collect_contact(message: Message, state: FSMContext) -> None:
    contact = message.contact
    phone = contact.phone_number
    await ensure_user(
        telegram_id=message.from_user.id,
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
        username=message.from_user.username or "",
        phone=phone,
    )
    await message.answer(
        "رائع، الآن يمكنك اختيار فرقتك:",
        reply_markup=build_team_keyboard(),
    )
    await state.set_state(RegistrationStates.waiting_for_team)


@dp.message(RegistrationStates.waiting_for_phone)
async def force_contact(message: Message) -> None:
    await message.answer(
        "⚠️ نحتاج رقم هاتفك من الزر عشان نكمل التسجيل، يرجى إرساله من هناك.",
        reply_markup=contact_keyboard,
    )


@dp.callback_query(StateFilter(RegistrationStates.waiting_for_team), lambda c: c.data and c.data.startswith("team|"))
async def select_team(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, team = callback.data.split("|", 1)
    if team not in TEAM_CHOICES:
        await callback.message.answer("⚠️ الفرقة غير معروفة، الرجاء المحاولة مرة أخرى.")
        return
    await update_team(callback.from_user.id, team)
    await callback.message.answer(
        "✅ ممتاز! الآن أرسل بريدك الإلكتروني، ولو احتجت تعيدها ممكن ترسل إيميل جديد.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(RegistrationStates.waiting_for_email)


@dp.message(StateFilter(RegistrationStates.waiting_for_email))
async def collect_email(message: Message, state: FSMContext) -> None:
    email = (message.text or "").strip()
    if not email or not EMAIL_REGEX.fullmatch(email):
        await message.answer("⚠️ البريد الإلكتروني غير صالح، تأكد إنك كتبت الشكل name@example.com.")
        return
    await update_email(message.from_user.id, email)
    profile = await get_user(message.from_user.id)
    if not profile:
        await message.answer(
            "❌ لم يتم تحميل ملف التعريف الخاص بك، حاول إرسال /start من جديد."
        )
        await state.clear()
        return
    team: Optional[str] = profile.get("team")
    if not team:
        await message.answer("⚠️ لم يتم تحديد الفرقة بعد، اخترها مجددًا.")
        await state.clear()
        return
    await message.answer("⏳ جارٍ إضافة بريدك إلى مجلد الفرقة... لحظات.")
    try:
        await share_folder_with_user(team, email)
        await record_share(message.from_user.id)
        await message.answer(
            f"✅ تمت إضافتك إلى مجلد {team}! تقدر تبعت بريد تاني لو حبيت تعيد الصلاحية.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer(ACCESS_INSTRUCTIONS)
        await message.answer(FILE_PANEL_PROMPT, reply_markup=build_folder_action_keyboard(team))
    except ShareFailure as exc:
        logger.warning("Share failure for %s (%s): %s", email, team, exc.original)
        await message.answer(exc.user_message)
    except Exception as exc:
        logger.exception("Failed to share folder: %s", exc)
        await message.answer(
            "❌ حصل خطأ غير متوقع أثناء مشاركة المجلد، جرب مرة تانية بعد شوية أو بلغ الأدمن."
        )
    finally:
        await state.clear()

@dp.message(Command(commands=["admin"]))
async def admin_dashboard(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ غير مصرح لك باستخدام هذا الأمر.")
        return
    rows = await all_teams_with_counts()
    stats = "\n".join(
        f"• {row['team']}: إجمالي أعضاء {row['total']}, أعضاء تمت إضافتهم {row['added']}"
        for row in rows
    ) or "لم تصل أي بيانات بعد."
    await message.answer(
        f"إحصائيات الفرق:\n{stats}\n\nاختر فرقة لعرض البريد الإلكتروني:",
        reply_markup=build_admin_keyboard(),
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("admin_team|"))
async def show_emails(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ غير مصرح", show_alert=True)
        return
    await callback.answer()
    _, team = callback.data.split("|", 1)
    emails = await team_emails(team)
    if not emails:
        await callback.message.answer(f"لا توجد رسائل بريد مسجلة للفرقة {team}.")
        return
    joined_emails = "\n".join(emails)
    await callback.message.answer(
        f"بريد الفرقة {team}:\n{joined_emails}\n\nاستخدم تحديد الكل ونسخ إذا رغبت في إضافتها دفعة واحدة.")


@dp.callback_query(lambda c: c.data and c.data.startswith("files|"))
async def show_file_panel(callback: CallbackQuery) -> None:
    await callback.answer()
    _, team = callback.data.split("|", 1)
    try:
        files = await list_files_for_team(team, FILE_PANEL_LIMIT)
    except Exception as exc:
        logger.warning("Failed to list files for %s: %s", team, exc)
        await callback.message.answer("⚠️ تعذر جلب الملفات دلوقتي، جرب بعد شوية.")
        return
    if not files:
        await callback.message.answer("📁 مفيش ملفات حالياً في المجلد.", reply_markup=build_folder_action_keyboard(team))
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_trim_file_label(file["name"]), url=file["webViewLink"])]
            for file in files
        ]
    )
    await callback.message.answer("📂 أحدث الملفات:", reply_markup=keyboard)


async def _send_lines_in_chunks(message: Message, lines: List[str]) -> None:
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 4000:
            await message.answer(chunk.rstrip())
            chunk = ""
        chunk += f"{line}\n"
    if chunk:
        await message.answer(chunk.rstrip())


def _format_user_record(user: dict) -> str:
    name_parts = [user.get("first_name") or "", user.get("last_name") or ""]
    name = " ".join(part for part in name_parts if part).strip() or user.get("username") or "بدون اسم"
    email = user.get("email") or "لم يُدخل"
    phone = user.get("phone") or "غير متوفر"
    team = user.get("team") or "غير محددة"
    shared = "🌟 تمت المشاركة" if user.get("shared_at") else "⚠️ لم تتم المشاركة"
    return f"• {name} ({user['telegram_id']}) | فريق {team} | {email} | {phone} | {shared}"


@dp.message(Command(commands=["admin_users"]))
async def admin_users(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ غير مصرح لك باستخدام هذا الأمر.")
        return
    users = await all_users()
    if not users:
        await message.answer("لا توجد بيانات مستخدمين بعد.")
        return
    await message.answer("📋 بيانات المستخدمين:")
    lines = [_format_user_record(user) for user in users]
    await _send_lines_in_chunks(message, lines)


@dp.message(Command(commands=["broadcast_start"]))
async def broadcast_start(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ غير مصرح لك باستخدام هذا الأمر.")
        return
    users = await all_users()
    if not users:
        await message.answer("لا توجد بيانات مستخدمين للإرسال.")
        return
    sent = 0
    for user in users:
        try:
            await bot.send_message(user["telegram_id"], START_NOTIFY_TEXT)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as exc:
            logger.warning("Broadcast start failed for %s: %s", user["telegram_id"], exc)
    await message.answer(f"تم إرسال إشعار البداية لـ {sent}/{len(users)} مستخدم.")


async def notify_users_on_start() -> None:
    if not AUTO_NOTIFY_ON_START:
        return
    users = await all_users()
    if not users:
        return
    for user in users:
        try:
            await bot.send_message(user["telegram_id"], START_NOTIFY_TEXT)
            await asyncio.sleep(0.05)
        except Exception as exc:
            logger.warning("Auto start notify failed for %s: %s", user["telegram_id"], exc)


async def main() -> None:
    init_db()
    await notify_users_on_start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        if hasattr(bot, "session") and bot.session:
            asyncio.run(bot.session.close())

from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is running")

app = web.Application()
app.router.add_get("/", handle)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=10000)
