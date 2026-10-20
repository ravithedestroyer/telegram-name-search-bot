import os
import asyncio
import logging

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TELETHON_SESSION = os.environ["TELETHON_SESSION"]

# Comma-separated group IDs or public @usernames.
ALLOWED_GROUPS = [
    value.strip()
    for value in os.environ.get("ALLOWED_GROUPS", "").split(",")
    if value.strip()
]

# Comma-separated Telegram numeric user IDs allowed to search.
ADMIN_USER_IDS = {
    int(value.strip())
    for value in os.environ.get("ADMIN_USER_IDS", "").split(",")
    if value.strip()
}

if not ALLOWED_GROUPS:
    raise RuntimeError("Configure ALLOWED_GROUPS in GitHub Secrets.")

if not ADMIN_USER_IDS:
    raise RuntimeError("Configure ADMIN_USER_IDS in GitHub Secrets.")

tg_client = TelegramClient(
    StringSession(TELETHON_SESSION),
    API_ID,
    API_HASH,
)


async def post_init(application: Application):
    await tg_client.connect()

    if not await tg_client.is_user_authorized():
        raise RuntimeError(
            "Telethon session is not authorized. "
            "Generate a valid session string and update the secret."
        )

    log.info("Telethon connected; allowed groups: %s", len(ALLOWED_GROUPS))


async def post_shutdown(application: Application):
    if tg_client.is_connected():
        await tg_client.disconnect()


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in ADMIN_USER_IDS


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Group-only name search bot\n\n"
            "Usage: /search Rahul Sharma\n\n"
            "Searches only the configured groups for accessible "
            "participants and messages. Results may be incomplete."
        )


async def search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message:
        return

    if not is_admin(update):
        await message.reply_text("⛔ You are not authorized to use this bot.")
        return

    query = " ".join(context.args).strip()

    if len(query) < 2:
        await message.reply_text("Usage: /search Rahul Sharma")
        return

    if len(query) > 80:
        await message.reply_text("Please use a shorter search query.")
        return

    status = await message.reply_text(
        "🔎 Searching configured Telegram groups..."
    )

    output = [f"Results for: {query}", ""]
    total_hits = 0

    try:
        for group_ref in ALLOWED_GROUPS:
            try:
                entity = await tg_client.get_entity(
                    int(group_ref) if group_ref.lstrip("-").isdigit()
                    else group_ref
                )

                group_name = getattr(entity, "title", None) or group_ref
                public_name = getattr(entity, "username", None)

                output.append(f"📂 Group: {group_name}")

                # Participant search is best-effort and subject to Telegram limits.
                try:
                    members = []
                    async for user in tg_client.iter_participants(
                        entity,
                        search=query,
                        limit=10,
                    ):
                        display_name = " ".join(
                            part for part in [
                                getattr(user, "first_name", None),
                                getattr(user, "last_name", None),
                            ] if part
                        ) or "(name unavailable)"

                        username = (
                            f"@{user.username}"
                            if getattr(user, "username", None)
                            else "No public username"
                        )

                        members.append(
                            f"  • {display_name} — {username}"
                        )

                    output.append("Members:")
                    output.extend(
                        members if members else ["  • No accessible matches"]
                    )
                    total_hits += len(members)

                except (RPCError, FloodWaitError) as exc:
                    log.warning(
                        "Member search failed in %s: %s",
                        group_ref,
                        type(exc).__name__,
                    )
                    output.append(
                        "Members: unavailable (Telegram access/rate limit)"
                    )

                # Search messages available to this authorized account.
                try:
                    messages = []
                    async for item in tg_client.iter_messages(
                        entity,
                        search=query,
                        limit=10,
                    ):
                        text = (item.message or "").replace("\n", " ")
                        if len(text) > 220:
                            text = text[:217] + "..."

                        if public_name:
                            link = (
                                f"https://t.me/{public_name}/{item.id}"
                            )
                            messages.append(f"  • {text}\n    {link}")
                        else:
                            messages.append(f"  • {text}")

                    output.append("Messages:")
                    output.extend(
                        messages if messages else ["  • No indexed matches"]
                    )
                    total_hits += len(messages)

                except (RPCError, FloodWaitError) as exc:
                    log.warning(
                        "Message search failed in %s: %s",
                        group_ref,
                        type(exc).__name__,
                    )
                    output.append(
                        "Messages: unavailable (Telegram access/rate limit)"
                    )

                output.append("")

            except Exception as exc:
                log.warning(
                    "Could not access configured group %s: %s",
                    group_ref,
                    type(exc).__name__,
                )
                output.extend([
                    f"📂 Group: {group_ref}",
                    "Could not access this group. Check the group reference, "
                    "membership and permissions.",
                    "",
                ])

        output.append(
            f"Finished. Accessible matches: {total_hits}\n"
            "Results may be incomplete. A name match does not verify identity."
        )

        # Keep each Telegram message below its size limit.
        combined = "\n".join(output)
        chunks = []
        current = ""

        for line in combined.splitlines():
            if len(current) + len(line) + 1 > 3800:
                chunks.append(current)
                current = ""
            current += line + "\n"

        if current:
            chunks.append(current)

        await status.delete()

        for chunk in chunks:
            await update.effective_chat.send_message(
                chunk,
                disable_web_page_preview=True,
            )

    except Exception:
        log.exception("Search failed")
        await status.edit_text(
            "Search failed. Check GitHub Actions logs and configured secrets."
        )


def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("search", search))

    log.info("Bot starting")
    application.run_polling()


if __name__ == "__main__":
    main()
