"""
Shared database layer.

Two kinds of tables here:
- posts / votes: generic, used by single-choice modes (poll, this-or-
  that, rating). One vote per user per post.
- tier_assignments: used by tier list mode, since a user assigns a
  tier to *multiple* items within one post (one vote per user per
  post doesn't fit that shape).

Uses SQLite via aiosqlite - no external database server needed.
The file ranker.db is created automatically on first run, and
init_db() migrates older copies of it forward automatically.
"""

import json
import time
import aiosqlite

DB_PATH = "ranker.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER UNIQUE,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    creator_id  INTEGER NOT NULL,
    mode        TEXT NOT NULL,
    question    TEXT NOT NULL,
    options     TEXT NOT NULL,   -- JSON list of {label, image_urls: [...]} (or category dicts for team builder)
    image_url   TEXT,            -- unused; kept for backward compatibility with older rows
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS votes (
    post_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    choice      TEXT NOT NULL,   -- option label, or a rating "1".."5"
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS tier_assignments (
    post_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    item        TEXT NOT NULL,
    tier        TEXT NOT NULL,   -- S / A / B / C / D / F, or "selected" for team builder
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (post_id, user_id, item)
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id          INTEGER PRIMARY KEY,
    posting_paused    INTEGER NOT NULL DEFAULT 0,
    required_role_id  INTEGER
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        # Migration: older copies of ranker.db won't have image_url yet.
        try:
            await db.execute("ALTER TABLE posts ADD COLUMN image_url TEXT")
        except aiosqlite.OperationalError:
            pass  # column already exists
        await db.commit()


async def create_post(message_id, guild_id, channel_id, creator_id, mode, question,
                       options, image_url=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO posts (message_id, guild_id, channel_id, creator_id,
                                   mode, question, options, image_url, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, guild_id, channel_id, creator_id, mode, question,
             json.dumps(options), image_url, int(time.time())),
        )
        await db.commit()
        return cursor.lastrowid


async def get_post_by_message(message_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM posts WHERE message_id = ?", (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_post(post_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM posts WHERE post_id = ?", (post_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# ---- single-choice modes (poll, this-or-that, rating) ----

async def cast_vote(post_id, user_id, choice):
    """Insert a user's vote, or overwrite it if they already voted (change of mind)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO votes (post_id, user_id, choice, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(post_id, user_id) DO UPDATE SET
                   choice = excluded.choice,
                   created_at = excluded.created_at""",
            (post_id, user_id, choice, int(time.time())),
        )
        await db.commit()


async def get_vote_counts(post_id):
    """Returns {choice: count} for a post."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT choice, COUNT(*) FROM votes WHERE post_id = ? GROUP BY choice",
            (post_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return {choice: count for choice, count in rows}


async def get_user_vote(post_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT choice FROM votes WHERE post_id = ? AND user_id = ?",
            (post_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_average_rating(post_id):
    """Returns (average, vote_count) for a rating-mode post. choice values are '1'..'5'."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT AVG(CAST(choice AS FLOAT)), COUNT(*) FROM votes WHERE post_id = ?",
            (post_id,),
        ) as cursor:
            avg, count = await cursor.fetchone()
            return (avg or 0.0), (count or 0)


# ---- tier list mode ----

async def set_tier(post_id, user_id, item, tier):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tier_assignments (post_id, user_id, item, tier, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(post_id, user_id, item) DO UPDATE SET
                   tier = excluded.tier,
                   created_at = excluded.created_at""",
            (post_id, user_id, item, tier, int(time.time())),
        )
        await db.commit()


async def get_tier_summary(post_id):
    """Returns {item: {tier: count}} for every item that has at least one assignment."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT item, tier, COUNT(*) FROM tier_assignments
               WHERE post_id = ? GROUP BY item, tier""",
            (post_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    summary = {}
    for item, tier, count in rows:
        summary.setdefault(item, {})[tier] = count
    return summary


async def clear_tier_selection(post_id, user_id):
    """Removes all of one user's tier_assignments rows for a post. Used by
    team builder to reset a user's picks before saving new ones."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM tier_assignments WHERE post_id = ? AND user_id = ?",
            (post_id, user_id),
        )
        await db.commit()


async def count_distinct_tier_users(post_id):
    """How many distinct users have submitted anything for this post (tier list
    or team builder)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM tier_assignments WHERE post_id = ?",
            (post_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


# ---- guild settings (moderator controls) ----

async def get_guild_settings(guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {"guild_id": guild_id, "posting_paused": 0, "required_role_id": None}


async def set_posting_paused(guild_id, paused: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO guild_settings (guild_id, posting_paused) VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET posting_paused = excluded.posting_paused""",
            (guild_id, int(paused)),
        )
        await db.commit()


async def set_required_role(guild_id, role_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO guild_settings (guild_id, required_role_id) VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET required_role_id = excluded.required_role_id""",
            (guild_id, role_id),
        )
        await db.commit()


# ---- leaderboard ----

async def get_rating_leaderboard(guild_id, limit=10):
    """Top rated items (rating-mode posts) in a guild, by average score."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT p.question, AVG(CAST(v.choice AS FLOAT)) AS avg_score, COUNT(*) AS votes
               FROM posts p JOIN votes v ON p.post_id = v.post_id
               WHERE p.guild_id = ? AND p.mode = 'rating'
               GROUP BY p.post_id
               ORDER BY avg_score DESC
               LIMIT ?""",
            (guild_id, limit),
        ) as cursor:
            return await cursor.fetchall()


async def get_vote_leaderboard(guild_id, limit=10):
    """Most-voted-for options across all poll and this-or-that posts in a guild.
    Options with the same label (e.g. repeated 'Hoodie A' polls) accumulate."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT v.choice, COUNT(*) AS total_votes
               FROM posts p JOIN votes v ON p.post_id = v.post_id
               WHERE p.guild_id = ? AND p.mode IN ('poll', 'this_or_that')
               GROUP BY v.choice
               ORDER BY total_votes DESC
               LIMIT ?""",
            (guild_id, limit),
        ) as cursor:
            return await cursor.fetchall()
