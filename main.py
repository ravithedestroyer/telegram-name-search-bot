
import os
import asyncio
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from ddgs import DDGS
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]


def normalize_url(url: str) -> str:
    """Remove tracking parameters and URL fragments."""
    try:
        parsed = urlparse(url.strip())
        clean_query = urlencode([
            (key, value)
            for key, value in parse_qsl(parsed.query)
            if not key.lower().startswith("utm_")
        ])
        return urlunparse((
            parsed.scheme or "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            "",
            clean_query,
            "",
        ))
    except Exception:
        return url.strip()


def search_public_web(name: str) -> list:
    results = []
    seen = set()
    errors = 0

    queries = [
        f'site:t.me "{name}"',
        f'site:telegram.me "{name}"',
        f'site:t.me "{name}" Telegram',
        f'"{name}" Telegram',
        f'"{name}" site:t.me',
        f'{name} Telegram public channel',
    ]

    try:
        with DDGS() as ddgs:
            for query in queries:
                try:
                    hits = ddgs.text(
                        query,
                        max_results=10,
                        safesearch="moderate",
                    )

                    for hit in hits:
                        raw_url = hit.get("href") or hit.get("url") or ""
                        title = (hit.get("title") or "Untitled").strip()
                        snippet = (
                            hit.get("body")
                            or hit.get("snippet")
                            or ""
                        ).strip()

                        if not raw_url:
                            continue

                        url = normalize_url(raw_url)
                        parsed = urlparse(url)
                        host = (parsed.hostname or "").lower()

                        is_tg = host in {
                            "t.me",
                            "www.t.me",
                            "telegram.me",
                            "www.telegram.me",
                        }

                        if not is_tg:
                            # Keep other pages only when they clearly
                            # mention Telegram; label them as web sources.
                            text = f"{title} {snippet}".lower()
                            if "telegram" not in text:
                                continue

                        if url in seen:
                            continue
                        seen.add(url)

                        handle = ""
                        if is_tg:
                            parts = parsed.path.strip("/").split("/")
                            if parts:
                                candidate = parts[0]
                                if re.fullmatch(
                                    r"[A-Za-z][A-Za-z0-9_]{3,}",
                                    candidate,
                                ):
                                    handle = "@" + candidate

                        searchable_text = f"{title} {snippet}".lower()
                        target = name.lower()

                        title_score = SequenceMatcher(
                            None, target, title.lower()
                        ).ratio()

                        snippet_score = SequenceMatcher(
                            None, target, snippet.lower()[:250]
                        ).ratio()

                        word_matches = sum(
                            1 for word in target.split()
                            if len(word) > 1 and word in searchable_text
                        )
                        words = max(1, len(target.split()))
                        word_score = word_matches / words

                        score = max(
                            title_score,
                            snippet_score * 0.85,
                            word_score,
                        )

                        # Prioritize actual public Telegram pages.
                        if is_tg:
                            score = min(1.0, score + 0.08)

                        results.append({
                            "title": title[:150],
                            "url": url,
                            "handle": handle,
                            "score": round(score * 100),
                            "snippet": snippet[:250],
                            "is_tg": is_tg,
                        })

                except Exception as exc:
                    errors += 1
                    print(
                        f"Query failed ({type(exc).__name__}): "
                        f"{query}"
                    )

    except Exception as exc:
        print("Search provider unavailable:", type(exc).__name__)
        raise RuntimeError("Public search provider unavailable") from exc

    results.sort(
        key=lambda item: (
            item["is_tg"],
            item["score"],
        ),
        reverse=True,
    )

    # Keep up to 8 unique public results.
    return results[:8]


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message:
        await update.message.reply_text(
            "🔎 Public Telegram Name Search\n\n"
            "Usage:\n"
            "/search Rahul Sharma\n\n"
            "Searches public web indexes for pages mentioning "
            "a name. It cannot search every Telegram account, "
            "and results do not verify someone's identity."
        )


async def search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    name = " ".join(context.args).strip()

    if len(name) < 2:
        await update.message.reply_text(
            "Enter a name, e.g. /search Rahul Sharma"
        )
        return

    if len(name) > 100:
        await update.message.reply_text(
            "Please enter a name shorter than 100 characters."
        )
        return

    status = await update.message.reply_text(
        "🔍 Searching public web indexes. Please wait..."
    )

    try:
        results = await asyncio.to_thread(
            search_public_web,
            name,
        )

        if not results:
            await status.edit_text(
                "No indexed public results found for this search.\n\n"
                "Try another spelling or a shorter name. "
                "This does not mean the Telegram account does not exist."
            )
            return

        lines = [
            f"🔎 Public search results for: {name}",
            "",
        ]

        for i, item in enumerate(results, 1):
            source_type = (
                "Public Telegram page"
                if item["is_tg"]
                else "External web page"
            )

            lines.extend([
                f"{i}. {item['title']}",
                f"Type: {source_type}",
                f"Username: {item['handle'] or 'Not found'}",
                f"Text similarity: {item['score']}% "
                "(not identity confidence)",
                f"Link: {item['url']}",
                f"Details: {item['snippet'] or 'No description indexed'}",
                "",
            ])

        lines.append(
            "⚠️ Results come from public search indexes. "
            "They may be incomplete or unrelated. Verify each source yourself."
        )

        message = "\n".join(lines)

        # Split at line boundaries to avoid cutting links/text.
        chunks = []
        current = ""

        for line in message.splitlines():
            candidate = current + line + "\n"
            if len(candidate) > 3800 and current:
                chunks.append(current)
                current = line + "\n"
            else:
                current = candidate

        if current:
            chunks.append(current)

        for chunk in chunks:
            await update.effective_chat.send_message(
                chunk,
                disable_web_page_preview=True,
            )

        await status.delete()

    except Exception as exc:
        print("Search failed:", type(exc).__name__)

        await status.edit_text(
            "⚠️ Public search is temporarily unavailable.\n"
            "Please try again later."
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search))

    print("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
