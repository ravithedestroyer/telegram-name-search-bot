import os
import asyncio
from difflib import SequenceMatcher
from urllib.parse import urlparse

from ddgs import DDGS
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]


def search_public_web(name: str) -> list:
    results = []
    seen = set()

    queries = [
        f'site:t.me "{name}"',
        f'site:t.me "{name}" Telegram',
        f'"{name}" Telegram username',
    ]

    with DDGS() as ddgs:
        for query in queries:
            try:
                for hit in ddgs.text(
                    query,
                    max_results=8,
                    safesearch="moderate",
                ):
                    url = hit.get("href", "")
                    title = hit.get("title", "Untitled")
                    snippet = hit.get("body", "")

                    if not url or url in seen:
                        continue

                    parsed = urlparse(url)
                    host = (parsed.hostname or "").lower()
                    is_tg = host == "t.me" or host.endswith(".t.me")

                    if not is_tg and "telegram" not in (
                        title + " " + snippet
                    ).lower():
                        continue

                    seen.add(url)
                    handle = ""

                    if is_tg:
                        path = parsed.path.strip("/").split("/")
                        if path and path[0]:
                            candidate = path[0]
                            if candidate.replace("_", "").isalnum():
                                handle = "@" + candidate

                    score = max(
                        SequenceMatcher(
                            None, name.lower(), title.lower()
                        ).ratio(),
                        SequenceMatcher(
                            None, name.lower(), snippet[:200].lower()
                        ).ratio(),
                    )

                    results.append({
                        "title": title,
                        "url": url,
                        "handle": handle,
                        "score": round(score * 100),
                        "snippet": snippet[:180],
                    })
            except Exception as exc:
                print("Search error:", type(exc).__name__)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:8]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 Public Telegram Name Search\n\n"
        "Use:\n/search Rahul Sharma\n\n"
        "Searches publicly indexed pages only. "
        "It cannot search every Telegram account."
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()

    if len(name) < 2:
        await update.message.reply_text(
            "Enter a name, e.g. /search Rahul Sharma"
        )
        return

    if len(name) > 100:
        await update.message.reply_text("Please enter a shorter name.")
        return

    status = await update.message.reply_text("🔍 Searching public indexes...")

    try:
        results = await asyncio.to_thread(search_public_web, name)

        if not results:
            await status.edit_text(
                "No matching public pages found. Try another spelling."
            )
            return

        lines = [f"Results for: {name}\n"]

        for i, item in enumerate(results, 1):
            lines.append(
                f"{i}. {item['title'][:120]}\n"
                f"Username: {item['handle'] or 'Not found'}\n"
                f"Text similarity: {item['score']}% (not identity confidence)\n"
                f"Source: {item['url']}\n"
                f"{item['snippet']}\n"
            )

        lines.append("Matches are unverified; check the source yourself.")
        message = "\n".join(lines)

        for i in range(0, len(message), 3800):
            await update.effective_chat.send_message(
                message[i:i + 3800],
                disable_web_page_preview=True,
            )

        await status.delete()

    except Exception:
        print("Search failed; check hosting logs.")
        await status.edit_text(
            "Search temporarily unavailable. Please try again."
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search))
    print("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
