# Ranker Bot (Discord)

A Discord version of Reddit's Ranker App: interactive posts (polls,
tier lists, ratings, etc.) that aggregate community responses into
live results, right inside a message.

Five modes are fully working: Poll (with optional image), This or
That (head-to-head image voting), Tier List, Rating, and Leaderboard.

## Project structure

```
ranker-bot/
├── bot.py              # Entry point - starts the bot, loads cogs
├── config.py            # Loads your bot token from .env
├── requirements.txt      # Python dependencies
├── .env.example          # Template for your secret token file
├── database/
│   └── db.py             # SQLite tables + helper functions (shared by all modes)
└── cogs/
    ├── polls.py           # /poll - text options, optional image
    ├── this_or_that.py     # /thisorthat - head-to-head image vote
    ├── tier_list.py         # /tierlist - sort items into S/A/B/C/D/F
    ├── rating.py            # /rate - rate one item/image 1-5 stars
    └── leaderboard.py       # /leaderboard - top rated items in the server
```

A "cog" is just a Python file that packages one feature (one mode).
Each mode gets its own cog file, and `bot.py` loads all of them on
startup.

## One-time setup

### 1. Install Python
You need Python 3.10 or newer. Check with:
```
python3 --version
```
If you don't have it, get it from python.org.

### 2. Create the bot in Discord
1. Go to https://discord.com/developers/applications
2. Click **New Application**, give it a name (e.g. "Ranker Bot").
3. Go to the **Bot** tab → click **Reset Token** → copy the token
   (you'll only see it once — save it somewhere safe).
4. Still on the Bot tab, turn on any **Privileged Gateway Intents**
   you end up needing later. The starter bot doesn't need any.
5. Go to **OAuth2 → URL Generator**. Under **Scopes**, check `bot`
   and `applications.commands`. Under **Bot Permissions**, check
   `Send Messages`, `Embed Links`, and `Read Message History`.
6. Copy the generated URL, open it in your browser, and add the bot
   to your own test server.

### 3. Install dependencies
From inside the `ranker-bot` folder:
```
pip install -r requirements.txt
```

### 4. Add your token
```
cp .env.example .env
```
Open `.env` and paste your bot token after `DISCORD_TOKEN=`.
Never share this file or commit it to GitHub — it's as sensitive as
a password.

### 5. Run the bot
```
python bot.py
```
You should see `Logged in as <YourBot>` in the terminal. Slash
commands can take up to an hour to appear globally the first time,
but usually show up within a minute or two.

## Using it

**`/poll`** — question + up to 4 text options, with an optional image attached.
Button per option, live bar-chart results.

**`/thisorthat`** — head-to-head vote between two images. Upload
`image_a` and `image_b` (optionally label them); the bot composites
them side by side into one picture and posts buttons under it.

**`/tierlist`** — give it a title and a comma-separated list of items
(e.g. `Hoodie A, Hoodie B, Crewneck C`). People pick an item from the
dropdown, then privately choose a tier (S/A/B/C/D/F) for it. The
public message updates to show each item's current community tier.

**`/rate`** — post a title (optionally with an image) for people to
rate 1-5 stars. Shows the live average.

**`/leaderboard`** — shows the top 10 rated items in the server
(ranked from all `/rate` posts), medal emoji for the top 3.

## Adding your next mode

Follow whichever existing cog is closest to what you're building —
`polls.py` for simple button choices, `rating.py` for a single-item
score, `tier_list.py` for multi-item sorting with ephemeral steps:

1. Design the interaction (buttons, select menus, or a modal).
2. Store responses using `database/db.py`'s `create_post`,
   `cast_vote` / `get_vote_counts`, or the tier-specific
   `set_tier` / `get_tier_summary` functions. Add a new table only
   if a mode needs something none of those can express.
3. Write a results renderer (embed builder) for that mode's data shape.
4. Add a slash command that ties it together, and list the new cog
   in `bot.py`'s `STARTUP_COGS`.

## Phase 2: true click-on-image modes (Chart X, Put the Dot, Venn Diagrams)

Everything above works within what a Discord *message* can do:
embeds, buttons, dropdowns. Reddit's Ranker App can also do modes
where you click an exact spot *on* an image — that's not something a
message can render, because embeds have no concept of "click at this
X,Y coordinate." Discord's real equivalent is an **Activity**: a
small web app (plain HTML/JS or a framework) that runs embedded
inside a Discord voice channel or launched from a message, using
Discord's Embedded App SDK.

This is a legitimately separate project, not just "one more cog" —
it needs:
- A small web frontend (a canvas or an `<img>` with click-position
  tracking) — this is where the "put a dot on the image" interaction
  actually lives.
- A backend endpoint that receives clicks and stores them (can reuse
  this same SQLite database, or talk back to the bot).
- Public HTTPS hosting for that frontend (Discord Activities must be
  served over HTTPS — a free option like Render, Railway, or Fly.io
  works fine for a personal project).
- Registering the Activity in the Discord Developer Portal for your
  application, alongside the bot you already built.

**Suggested order once you're ready for this phase:**
1. Register an Activity for your existing application in the
   Developer Portal (Activities tab) and get the local dev flow
   working with Discord's own starter template.
2. Build the simplest version first — "Put the Dot": show one image,
   capture one click, store the (x, y) as a fraction of image width/
   height (so it works at any display size), average all clicks to
   show where the crowd pointed.
3. Extend to more images / rounds once that's solid.
4. Grid/Venn/Map modes are the same core mechanic with different
   backgrounds and click-zone logic layered on top.

This bot doesn't include an Activity scaffold yet since it needs
hosting and Developer Portal decisions specific to your app — happy
to build that starter with you whenever you're ready to start phase 2.

## Known limitations (things to harden later)

- **Buttons stop working if the bot restarts** while a poll is still
  open, because the view isn't re-registered as persistent on
  startup. To fix: on `on_ready`, query all posts from the database
  and re-attach a `PollView` for each using `bot.add_view()`.
- **Data lives in a single SQLite file** (`ranker.db`) next to the
  bot. Fine for a personal project; if you outgrow it, swap in
  Postgres later without changing the cogs (only `database/db.py`
  needs to change).
- **No moderator controls yet** (pausing posting, whitelists, karma
  gates, etc. from the original Ranker App). These map to Discord
  role/permission checks — add them as decorators on your slash
  commands when you need them.
- **Image/grid/map modes** (Chart X, Put the Dot, Venn Diagrams) need
  a different approach since Discord embeds can't be clicked at
  arbitrary coordinates. Look into Discord's Activities (embedded
  web apps) if you want to build those later.
