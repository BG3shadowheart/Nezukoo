import os, sys, json, random, asyncio, datetime, difflib, logging
from collections import deque

import discord
from discord.ext import commands, tasks

try:
    import nacl  # type: ignore
    VOICE_SUPPORT_AVAILABLE = True
except Exception:
    VOICE_SUPPORT_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════
#  KEEP-ALIVE SERVER  (Render free tier — ping this URL with UptimeRobot)
# ══════════════════════════════════════════════════════════════════════

async def _keep_alive_server():
    async def _handle(reader, writer):
        try:
            await reader.read(2048)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 21\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"Nezuko is protecting!"
            )
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    port = int(os.environ.get("PORT", 10000))
    server = await asyncio.start_server(_handle, "0.0.0.0", port)
    logger.info(f"[Keep-alive] Listening on port {port}")
    async with server:
        await server.serve_forever()

# ══════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("nezuko-bot")

# ══════════════════════════════════════════════════════════════════════
#  ENVIRONMENT VARIABLES  (set these in Render dashboard)
# ══════════════════════════════════════════════════════════════════════

TOKEN              = os.getenv("TOKEN", "")
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))
VC_TEXT_CHANNEL_ID = int(os.getenv("VC_TEXT_CHANNEL_ID", "0"))   # text channel for VC greetings
LOG_CHANNEL_ID     = int(os.getenv("LOG_CHANNEL_ID", "0"))
MOD_ROLE_ID        = int(os.getenv("MOD_ROLE_ID", "0"))
COMMAND_CHANNEL_ID = int(os.getenv("COMMAND_CHANNEL_ID", "0"))   # channel where nezuko commands live
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))   # for birthday/milestone announcements

_VC_IDS_RAW = os.getenv("VC_IDS", "")
VC_IDS = [int(x.strip()) for x in _VC_IDS_RAW.split(",") if x.strip().isdigit()] if _VC_IDS_RAW.strip() else []

if not VC_IDS:
    logger.warning("[VC] VC_IDS not set — voice channel features disabled.")
if not VC_TEXT_CHANNEL_ID:
    logger.warning("[VC] VC_TEXT_CHANNEL_ID not set — VC text greetings disabled.")
if not COMMAND_CHANNEL_ID:
    logger.warning("[CMD] COMMAND_CHANNEL_ID not set — command channel filter disabled.")

# ══════════════════════════════════════════════════════════════════════
#  BAD WORDS  (add your own words to this list)
# ══════════════════════════════════════════════════════════════════════

BAD_WORDS = [
    "badword1",
    "badword2",
    "badword3",
    # add more here — all lowercase
]

# ══════════════════════════════════════════════════════════════════════
#  ANTI-SPAM CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

SPAM_MAX_MSGS        = 5    # max messages allowed in the window (normal members)
SPAM_WINDOW_SEC      = 5    # sliding window in seconds
SPAM_DUP_COUNT       = 3    # same message repeated N times = instant spam
NEW_MEMBER_DAYS      = 7    # members newer than this get stricter limits
NEW_MEMBER_MAX_MSGS  = 3    # stricter: only 3 msgs in window for new members
SPAM_TIMEOUT_MIN     = 10   # timeout duration in minutes

# Runtime state — resets on bot restart (no persistence needed)
_spam_tracker: dict  = {}   # uid → deque of (timestamp, content)
_spam_punished: set  = set()  # uids currently serving spam timeout

# ══════════════════════════════════════════════════════════════════════
#  DATA PERSISTENCE
# ══════════════════════════════════════════════════════════════════════

DATA_FILE = "nezuko_data.json"

def _blank_data():
    return {
        "warnings":        {},
        "birthdays":       {},
        "join_dates":      {},
        "vc_visit_count":  {},
        "greeted_users":   [],
        "user_tiers":      {},
        "streaks":         {},
        "last_visit_dates":{},
        "last_daily_open": "",
    }

if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump(_blank_data(), f, indent=2)

with open(DATA_FILE, "r") as f:
    data = json.load(f)

# fill any missing keys (bot upgrade safety)
for k, v in _blank_data().items():
    data.setdefault(k, v)

def _write_json():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def save_data():
    try:
        await asyncio.to_thread(_write_json)
    except Exception as e:
        logger.warning(f"[Save] Failed: {e}")

# ══════════════════════════════════════════════════════════════════════
#  COLORS
# ══════════════════════════════════════════════════════════════════════

NEZUKO_PINK    = discord.Color.from_rgb(255, 105, 180)
NEZUKO_PURPLE  = discord.Color.from_rgb(160,  82, 200)
NEZUKO_RED     = discord.Color.from_rgb(200,  30,  60)
NEZUKO_DARK    = discord.Color.from_rgb( 40,  10,  30)
NEZUKO_LEAVE   = discord.Color.from_rgb( 80,  40,  90)

JOIN_COLORS = [NEZUKO_PINK, NEZUKO_PURPLE, NEZUKO_RED,
               discord.Color.from_rgb(220, 60, 100),
               discord.Color.from_rgb(180, 50, 150)]

LEAVE_COLORS = [NEZUKO_LEAVE, NEZUKO_DARK,
                discord.Color.from_rgb(60, 30, 70),
                discord.Color.from_rgb(50, 20, 50)]

# ══════════════════════════════════════════════════════════════════════
#  TIER SYSTEM  (assigned on first VC visit)
# ══════════════════════════════════════════════════════════════════════

TIERS = ["bamboo", "blossom", "flame", "shadow", "demon"]

TIER_LABELS = {
    "bamboo":  "🎋 Bamboo",
    "blossom": "🌸 Blossom",
    "flame":   "🔥 Flame",
    "shadow":  "🌑 Shadow",
    "demon":   "😈 Demon",
}

TIER_FIRST_GREETINGS = {
    "bamboo":  "🎋 **{display_name}** — a new friend enters our bamboo grove. Stay close, stay safe~",
    "blossom": "🌸 **{display_name}** — the cherry blossoms recognized you immediately. Welcome, beautiful~",
    "flame":   "🔥 **{display_name}** — Nezuko feels warmth from you already. The flame clan welcomes its own!",
    "shadow":  "🌑 **{display_name}** — quiet and powerful. The shadow watches over you now~",
    "demon":   "😈 **{display_name}** — *sniffs cautiously* ...one of the interesting ones. Nezuko is watching.",
}

# ══════════════════════════════════════════════════════════════════════
#  STREAK TRACKING
# ══════════════════════════════════════════════════════════════════════

def _update_streak(uid: str) -> int:
    today     = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    last      = data["last_visit_dates"].get(uid)
    streak    = data["streaks"].get(uid, 0)

    if last == today:
        pass
    elif last == yesterday:
        streak += 1
    else:
        streak = 1

    data["streaks"][uid]           = streak
    data["last_visit_dates"][uid]  = today
    return streak

def _streak_badge(streak: int) -> str:
    if streak >= 30: return "👑 30-day streak!"
    if streak >= 14: return "💎 14-day streak!"
    if streak >= 7:  return "⚡ 7-day streak!"
    if streak >= 3:  return "🔥 3-day streak!"
    return ""

# ══════════════════════════════════════════════════════════════════════
#  HOLIDAY DETECTION
# ══════════════════════════════════════════════════════════════════════

def _get_holiday() -> str:
    today = datetime.date.today()
    mm, dd = today.month, today.day
    if mm == 10 and 25 <= dd <= 31:       return "halloween"
    if mm == 12 and 24 <= dd <= 26:       return "christmas"
    if mm == 1  and dd == 1:              return "newyear"
    if mm == 2  and dd == 14:             return "valentine"
    if mm in (3, 4) and dd <= 15:         return "sakura"
    return ""

HOLIDAY_VC_JOINS = {
    "halloween": [
        "🎃 *Nezuko sniffs the air* — {display_name} arrived on Halloween night. Even demons get spooky~",
        "👻 {display_name} drifted in through the Halloween mist. *Nezuko presses against Tanjiro nervously*",
        "🕷️ {display_name} crept in on All Hallows' Eve. *squeaks in demon*",
        "🦇 {display_name} arrived with the bats! Even Nezuko is startled~ 🎃",
    ],
    "christmas": [
        "🎄 *Nezuko in a tiny Santa hat* {display_name} arrived! Best gift of the season~",
        "❄️ {display_name} stepped in from the winter cold! *Nezuko offers tiny warm hands*",
        "🎁 {display_name} is here! Nezuko thinks they're the best Christmas present yet~ 🌸",
        "⛄ {display_name} arrived! *Nezuko decorates them immediately with tinsel*",
    ],
    "newyear": [
        "🎆 {display_name} enters the new year with us! *Nezuko blows a paper horn happily*",
        "🥂 {display_name} arrived — Nezuko welcomes you into the new year! 🌸",
        "✨ {display_name} steps in as the calendar turns. Fresh year, same amazing you~",
        "🎊 {display_name} is here! *Nezuko throws confetti with tiny demon hands*",
    ],
    "valentine": [
        "💌 {display_name} arrived on Valentine's Day! *Nezuko blushes and looks away*",
        "🌹 {display_name} steps in — even Nezuko's heart does a little flutter~ 🌸",
        "💋 {display_name} joins us on the most heartfelt day! *happy demon squeaking*",
        "💘 {display_name} arrived! Nezuko has prepared a tiny valentine for you 🎋",
    ],
    "sakura": [
        "🌸 {display_name} arrives with the cherry blossoms — just as beautiful~",
        "🌺 {display_name} stepped in during sakura season! *Nezuko scatters petals*",
        "🌸 {display_name} joins as the blossoms fall. The season brought the right people~",
        "🌿 {display_name} appears like spring itself. Nezuko loves this season~ 🌸",
    ],
}

# ══════════════════════════════════════════════════════════════════════
#  VC GREETING POOLS  (Nezuko-themed)
# ══════════════════════════════════════════════════════════════════════

MILESTONE_VC_GREETINGS = [
    "🌸 **{display_name}** is back for visit **#{count}**! Nezuko kept your spot warm~ 🎋",
    "💮 **{display_name}** returns — visit **#{count}**! You're practically family now! 🌸",
    "🔥 **{display_name}** for the **#{count}th** time! Nezuko did a little spin for you~",
    "👑 **{display_name}** — **#{count}** visits! Nezuko gives you the honorary bamboo crown 🎋",
    "⚡ **{display_name}** is back again — **#{count}**! *happy squeaking intensifies*",
]

THEMED_VC_JOINS = {
    "midnight": [
        "🌑 {display_name} crept in past midnight — even Nezuko is still awake watching over~",
        "🕯️ {display_name} arrives in the dark hours. *Nezuko glows softly to guide the way*",
        "🌙 {display_name} joins at midnight. The quiet hours belong to the bravest ones~",
        "🌌 {display_name} drifted in under moonlight. *Nezuko offers a tiny lantern*",
    ],
    "morning": [
        "☀️ {display_name} is here bright and early! Even Nezuko is impressed~",
        "🍵 {display_name} arrived with the sunrise! *Nezuko has tea ready*",
        "🌅 {display_name} steps in at dawn. First ones here are the strongest! 🌸",
        "☕ {display_name} joined before the world woke. *Nezuko offers morning snacks*",
    ],
    "afternoon": [
        "🌤️ {display_name} arrived! The afternoon is more fun now~ *happy clapping*",
        "🌞 {display_name} joins midday! Nezuko saved a sunny spot just for you 🌸",
        "🍃 {display_name} steps in with the afternoon breeze. *Nezuko waves happily*",
        "☀️ {display_name} arrived! Perfect timing — Nezuko just woke from a nap~",
    ],
    "evening": [
        "🌆 {display_name} arrived at dusk! Evening sessions are Nezuko's favourite~ 🌸",
        "🌇 {display_name} joined with the golden hour! So warm, just like you~",
        "🕯️ {display_name} steps in as the lights go soft. *Nezuko lights tiny candles*",
        "🌃 {display_name} joined the evening crew! *Nezuko saved the best spot*",
    ],
}

VC_JOIN_GREETINGS = [
    "🌸 *happy squeaking* {display_name} is here!! Nezuko does a little spin~",
    "🎋 *peeks out of bamboo box* Oh! {display_name} arrived! *waves tiny hands excitedly*",
    "💮 {display_name} stepped in! Nezuko's day just got so much better~ 🌸",
    "🌸 *claps tiny hands* {display_name} is finally here! Everyone cheer!",
    "🎋 {display_name} arrived and Nezuko immediately runs over to say hi~ *squeaks happily*",
    "💮 *does a full happy spin* {display_name} joined! Nezuko is SO happy right now!",
    "🌸 {display_name} stepped through the door! *Nezuko stands at attention, very serious, then immediately smiles*",
    "🎋 Ah! {display_name} is here! *Nezuko tugs on Tanjiro's sleeve excitedly*",
    "🌸 {display_name} arrived~ *Nezuko offers a tiny bamboo greeting*",
    "💮 *very dignified bow* Welcome, {display_name}. Nezuko has been expecting you~ 🌸",
    "🌸 {display_name} joined! *eyes light up like an excited puppy*",
    "🎋 {display_name} is here! The bamboo grove celebrates your arrival~",
    "💮 *sniff sniff* Nezuko approves of {display_name}'s energy. Welcome! 🌸",
    "🌸 {display_name} arrived! Nezuko immediately goes to protect them (even if they don't need it)",
    "🎋 Oh oh oh! {display_name} is here! *Nezuko's eyes turn pink with excitement*",
    "🌸 *zooms over excitedly* {display_name}!! You came!! Nezuko is so happy!!",
    "💮 {display_name} has entered the bamboo grove. Nezuko nods approvingly~ 🎋",
    "🌸 {display_name} arrived! Even Zenitsu stopped crying for a second to wave~",
    "🎋 {display_name} stepped in! *Nezuko immediately offers bamboo tube as a gift*",
    "💮 *happy demon noises* {display_name} is here! Everyone make room!",
    "🌸 {display_name} joined! Nezuko has been sitting by the door waiting~ 🎋",
    "💮 *tilts head curiously then beams* {display_name}! Welcome welcome~",
    "🌸 {display_name} arrived and Nezuko immediately stands behind them protectively 🎋",
    "💮 The bamboo rustles — {display_name} has arrived! Nezuko felt it~ 🌸",
    "🎋 {display_name} stepped through! *Nezuko kicks the air in excitement*",
]

VC_LEAVE_GREETINGS = [
    "🌸 *sad eyes* {display_name} left... Nezuko watches the door for a while...",
    "🎋 {display_name} is gone... *Nezuko sits quietly and pats their empty spot*",
    "💮 {display_name} left... *Nezuko lets out a tiny sad squeak*",
    "🌸 Goodbye, {display_name}... *Nezuko waves slowly until they're gone*",
    "🎋 {display_name} stepped away... The bamboo grove feels quieter now~",
    "💮 *sigh* {display_name} left. Nezuko will keep their spot warm for next time 🌸",
    "🌸 {display_name} is gone... *Nezuko stares at the door with big sad eyes*",
    "🎋 {display_name} left... Nezuko puts a flower where they were sitting 🌸",
    "💮 {display_name} stepped out... Until next time! Nezuko will miss you~",
    "🌸 *tiny wave* Goodbye, {display_name}... Come back soon, okay? 🎋",
    "💮 {display_name} is gone... *Nezuko does a sad shuffle back to her corner*",
    "🌸 {display_name} left the bamboo grove... The petals fall a little slower~",
    "🎋 {display_name} stepped away... Nezuko is already counting down to their return~",
    "💮 *hugs knees* {display_name} left... but Nezuko knows they'll be back 🌸",
    "🌸 Farewell, {display_name}... *Nezuko folds a tiny origami flower and keeps it for them*",
]

VC_MOOD_MESSAGES = [
    "🌸 *Nezuko looks around contentedly at everyone in the channel~*",
    "🎋 *The bamboo rustles softly. A peaceful moment.*",
    "💮 *Nezuko does a tiny spin just because she's happy right now*",
    "🌸 *Nezuko sits quietly, watching over everyone like a tiny guardian~*",
    "🎋 *Someone in the chat smells nice. Nezuko approves.*",
    "💮 *Nezuko peeks out from her bamboo box to check on everyone~*",
    "🌸 *A pink flower petal drifts through. Nezuko catches it happily.*",
    "🎋 *Nezuko nods at everyone like the responsible demon she is~*",
    "💮 *Happy demon energy fills the channel tonight~*",
    "🌸 *Nezuko does a little protective patrol of the voice channel~*",
]

DAILY_OPENERS = [
    "🌅 *A new day in the bamboo grove begins. Nezuko stretches and looks around~* 🌸",
    "🎌 *The channel wakes up! Nezuko has been guarding all night~* 🎋",
    "🌸 *Good morning, everyone! Nezuko prepared the bamboo grove for today~*",
    "⛩️ *Another day, another adventure. Nezuko is ready!* 🌸",
    "🌸 *Day begins~ Nezuko bounces excitedly waiting for everyone to arrive~* 🎋",
]

# ══════════════════════════════════════════════════════════════════════
#  SERVER WELCOME / GOODBYE (member join/leave)
# ══════════════════════════════════════════════════════════════════════

SERVER_WELCOME_MSGS = [
    "🌸 *squeaks happily* A new friend has arrived in our family!",
    "🎋 *peeks out from bamboo box* Oh! A new person! Welcome~",
    "💮 *does a little happy spin* They're finally here!!",
    "🌸 *claps tiny hands excitedly* Yay yay yay! A new member!",
    "🎋 *sniffs curiously then beams* Nezuko approves! Welcome to the family~",
    "💮 *runs in circles excitedly* Someone new!! Someone NEW!!",
    "🌸 *stands very tall and very serious* Welcome. Nezuko will protect you. *immediately smiles*",
]

SERVER_GOODBYE_MSGS = [
    "🌸 *sad squeaking* They... they left... Nezuko will miss them...",
    "🎋 *waves tiny hand slowly* Goodbye friend... come back someday...",
    "💮 *sits quietly in corner* Another one gone... the grove feels smaller...",
    "🌸 *pats their empty spot* Until we meet again... Nezuko will remember~",
    "🎋 *puts a flower by the door* For when they return... 🌸",
    "💮 *lets out one quiet sad squeak* The bamboo misses them already...",
]

# ══════════════════════════════════════════════════════════════════════
#  BAD WORD RESPONSES
# ══════════════════════════════════════════════════════════════════════

BAD_WORD_WARN_MSGS = [
    "🌸 *tilts head and stares very disapprovingly* That's not a nice word! Nezuko says be kind~ **(Warning {warns}/3)**",
    "🎋 *blocks with bamboo tube* HMM HMMM! No bad words here!! **(Warning {warns}/3)**",
    "💮 *stomps tiny foot* Language! Nezuko is watching you VERY closely now! **(Warning {warns}/3)**",
    "🌸 *crosses arms and huffs* Nezuko did not like that word. Not one bit. **(Warning {warns}/3)**",
    "🎋 *shakes head slowly and sadly* Why would you say that... **(Warning {warns}/3)**",
]

TIMEOUT_MSGS = [
    "🌸 *uses Blood Demon Art* You've been very naughty. Even Nezuko had enough! 10 minutes to think about what you did. ⏰",
    "🎋 *disappointed squeaking intensifies* Nezuko warned you. Three times. 10 minute timeout! 🕐",
    "💮 *sighs and puts bamboo down* This is not what Nezuko wanted to do... but rules are rules. See you in 10 minutes.",
    "🌸 *turns away sadly* Nezuko doesn't like using her demon powers like this... but you left no choice. 10 minute timeout.",
    "🎋 *closes bamboo box* Even the most patient demon has limits. Time out. 🌸",
]

# ══════════════════════════════════════════════════════════════════════
#  EMBED TITLES
# ══════════════════════════════════════════════════════════════════════

VC_JOIN_TITLES = [
    "🌸 A New Friend Arrives!",
    "🎋 The Bamboo Grove Welcomes~",
    "💮 Nezuko Spotted Someone!",
    "🌸 A Presence Detected!",
    "🎋 The Grove Stirs~",
    "💮 Someone's Here!!",
    "🌸 Welcome to the Channel~",
    "🎋 Arrival Noted — Nezuko Approves",
    "💮 A Soul Enters the Grove~",
    "🌸 New Presence: Confirmed!",
]

VC_LEAVE_TITLES = [
    "🌸 Goodbye For Now...",
    "🎋 They Slipped Away~",
    "💮 The Grove Says Farewell",
    "🌸 Departure Noted...",
    "🎋 One Less in the Bamboo~",
    "💮 Until We Meet Again~",
    "🌸 Nezuko Waves Goodbye...",
]

VC_FOOTER_LINES = [
    "Nezuko noticed you  🌸",
    "the bamboo grove welcomes you  🎋",
    "protected by Nezuko  💮",
    "𝑤𝑎𝑡𝑐ℎ𝑒𝑑 𝑜𝑣𝑒𝑟 𝑏𝑦 𝑁𝑒𝑧𝑢𝑘𝑜  🌸",
    "the grove remembers  🎋",
    "stay kind. Nezuko is watching~  💮",
    "you were always welcome here  🌸",
    "Nezuko has been waiting  🎋",
]

VC_LEAVE_FOOTER_LINES = [
    "Nezuko watched them leave  🌸",
    "𝑡ℎ𝑒𝑦'𝑙𝑙 𝑏𝑒 𝑏𝑎𝑐𝑘  🎋",
    "the door stays open  💮",
    "absence noted — warmth remains  🌸",
    "𝑡ℎ𝑒 𝑔𝑟𝑜𝑣𝑒 𝑟𝑒𝑚𝑒𝑚𝑏𝑒𝑟𝑠  🎋",
    "Nezuko saved a petal for their return  💮",
]

VC_JOIN_REACTIONS  = ["🌸", "🎋", "💮", "🔥", "✨", "💫", "🌙", "👑", "🌺", "⚡"]
VC_LEAVE_REACTIONS = ["🌸", "🎋", "💮", "💫", "🌑", "🌺", "🎶"]

# ══════════════════════════════════════════════════════════════════════
#  BOT STATUS POOL  (rotates every 2 hours)
# ══════════════════════════════════════════════════════════════════════

BOT_STATUS_POOL = [
    ("listening", "bamboo sounds  🎋"),
    ("watching",  "over the server  🌸"),
    ("playing",   "in her bamboo box  📦"),
    ("watching",  "Tanjiro train  🗡️"),
    ("listening", "demon slayer OST  🎵"),
    ("watching",  "for rule breakers  👀"),
    ("playing",   "with pink flowers  💮"),
    ("watching",  "everyone in VC  🌸"),
    ("listening", "the bamboo rustle  🎋"),
    ("watching",  "over the bamboo grove  💮"),
    ("playing",   "Blood Demon Art  🔥"),
    ("watching",  "the sunrise with Tanjiro  🌅"),
    ("listening", "Zenitsu's crying  😅"),
    ("watching",  "Inosuke cause chaos  🐗"),
    ("playing",   "being a very good demon  😈"),
    ("watching",  "for demons  🌸"),
    ("listening", "heartbeats  💮"),
    ("playing",   "hide inside bamboo box  🎋"),
    ("watching",  "the pink moon rise  🌸"),
    ("listening", "for anyone who needs help  💮"),
]

# ══════════════════════════════════════════════════════════════════════
#  COMMAND CHANNEL SETUP
# ══════════════════════════════════════════════════════════════════════

NEZUKO_COMMANDS = [
    "birthday", "warnings", "server", "user", "help", "antispam",
]

_COMMANDS_DISPLAY = (
    "📋 **Nezuko Commands:**\n"
    "```\n"
    "nezuko birthday YYYY-MM-DD  — set your birthday\n"
    "nezuko warnings             — check your warnings\n"
    "nezuko warnings @user       — check someone's warnings\n"
    "nezuko server               — server info\n"
    "nezuko user                 — your info\n"
    "nezuko user @user           — someone's info\n"
    "nezuko help                 — show this list\n"
    "nezuko antispam @user       — [MOD] clear spam timeout for a user\n"
    "```\n"
    "*All commands must start with* `nezuko`"
)

def suggest_commands(text: str):
    return difflib.get_close_matches(text, NEZUKO_COMMANDS, n=3, cutoff=0.3)

# ══════════════════════════════════════════════════════════════════════
#  ANTI-SPAM ENGINE
# ══════════════════════════════════════════════════════════════════════

SPAM_WARN_MSGS = [
    "🔴 *Nezuko's eyes go red* — spamming is NOT okay in this grove!",
    "😤 *Nezuko stamps her foot* — slow down! This isn't a spam contest!",
    "🌸 *Nezuko blocks the path* — too many messages! The grove needs quiet~",
    "🎋 *Nezuko shakes bamboo aggressively* — STOP the spam! Timeout activated!",
    "👹 *Nezuko's demon form activates* — spammers don't belong in the grove!",
]

async def _check_spam(message: discord.Message) -> bool:
    """
    Returns True if the message was identified as spam and handled.
    Tracks every user across ALL channels (catches hacked accounts spamming everywhere).
    New members (joined < NEW_MEMBER_DAYS days) get stricter limits.
    """
    member = message.author
    uid    = str(member.id)

    # ── Mods are immune ─────────────────────────────────────────────
    if MOD_ROLE_ID:
        mod_role = message.guild.get_role(MOD_ROLE_ID)
        if mod_role and mod_role in member.roles:
            return False

    # ── Init per-user deque ─────────────────────────────────────────
    if uid not in _spam_tracker:
        _spam_tracker[uid] = deque()

    history = _spam_tracker[uid]
    now     = asyncio.get_event_loop().time()
    history.append((now, message.content))

    # Prune entries older than the window
    while history and now - history[0][0] > SPAM_WINDOW_SEC:
        history.popleft()

    # ── Already being punished → silently delete extra messages ─────
    if uid in _spam_punished:
        try:
            await message.delete()
        except Exception:
            pass
        return True

    # ── Determine if member is "new" ────────────────────────────────
    is_new = False
    if member.joined_at:
        days_in = (discord.utils.utcnow() - member.joined_at).days
        is_new  = days_in < NEW_MEMBER_DAYS

    limit     = NEW_MEMBER_MAX_MSGS if is_new else SPAM_MAX_MSGS
    msg_count = len(history)
    dup_count = sum(
        1 for _, c in history
        if c.lower().strip() == message.content.lower().strip()
    )

    is_spam = (msg_count > limit) or (dup_count >= SPAM_DUP_COUNT)

    if not is_spam:
        return False

    # ── Spam confirmed — take action ────────────────────────────────
    _spam_punished.add(uid)

    # Bulk-delete up to the last 10 messages from this user in this channel
    try:
        await message.channel.purge(
            limit=20,
            check=lambda m: m.author.id == member.id,
            bulk=True,
        )
    except discord.Forbidden:
        try:
            await message.delete()
        except Exception:
            pass
    except Exception:
        pass

    # Timeout the member
    reason = f"Spam ({msg_count} msgs / {SPAM_WINDOW_SEC}s)" + (" — new member" if is_new else "")
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=SPAM_TIMEOUT_MIN)
        await member.timeout(until, reason=f"Nezuko Anti-Spam: {reason}")
    except discord.Forbidden:
        pass
    except Exception as e:
        logger.warning(f"[AntiSpam] Timeout failed for {member}: {e}")

    # Public warning embed
    embed = discord.Embed(
        title="🚫 Spam Detected!",
        description=(
            f"{random.choice(SPAM_WARN_MSGS)}\n\n"
            f"{member.mention} has been timed out for **{SPAM_TIMEOUT_MIN} minutes**.\n\n"
            + (
                f"⚠️ *New members must be extra patient in the grove~* 🌸"
                if is_new else
                f"🌸 *Please keep the bamboo grove peaceful for everyone~*"
            )
        ),
        color=discord.Color.red(),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text=f"Nezuko Anti-Spam  •  {reason}")
    try:
        await message.channel.send(embed=embed, delete_after=20)
    except Exception:
        pass

    await _log(
        f"🚫 **[Anti-Spam]** **{member}** (`{member.id}`) timed out for "
        f"**{SPAM_TIMEOUT_MIN} min** — {reason}."
    )

    # Clear punish state after timeout expires so they get a fresh start
    async def _lift_spam_block():
        await asyncio.sleep(SPAM_TIMEOUT_MIN * 60)
        _spam_punished.discard(uid)
        _spam_tracker.pop(uid, None)

    asyncio.create_task(_lift_spam_block())
    return True

# ══════════════════════════════════════════════════════════════════════
#  GREETING HELPERS
# ══════════════════════════════════════════════════════════════════════

def _get_time_theme() -> str:
    hour = datetime.datetime.utcnow().hour
    if   0 <= hour <  6:  return "midnight"
    elif 6 <= hour < 12:  return "morning"
    elif 12 <= hour < 18: return "afternoon"
    else:                  return "evening"

def _is_late_night() -> bool:
    return 0 <= datetime.datetime.utcnow().hour < 5

def get_vc_join_greeting(member) -> str:
    uid   = str(member.id)
    name  = member.display_name
    count = data["vc_visit_count"].get(uid, 0) + 1
    data["vc_visit_count"][uid] = count

    _update_streak(uid)

    is_first = uid not in data["greeted_users"]
    if is_first:
        data["greeted_users"].append(uid)
        tier = random.choice(TIERS)
        data["user_tiers"][uid] = tier
        return TIER_FIRST_GREETINGS[tier].format(display_name=name)

    holiday = _get_holiday()
    if holiday and holiday in HOLIDAY_VC_JOINS and random.random() < 0.40:
        return random.choice(HOLIDAY_VC_JOINS[holiday]).format(display_name=name)

    if count % 10 == 0 or count % 5 == 0:
        return random.choice(MILESTONE_VC_GREETINGS).format(display_name=name, count=count)

    theme = _get_time_theme()
    pool  = THEMED_VC_JOINS.get(theme, []) + VC_JOIN_GREETINGS
    return random.choice(pool).format(display_name=name)

def get_vc_leave_greeting(display_name: str) -> str:
    return random.choice(VC_LEAVE_GREETINGS).format(display_name=display_name)

async def maybe_send_daily_open(channel: discord.TextChannel):
    today = datetime.date.today().isoformat()
    if data["last_daily_open"] == today:
        return
    data["last_daily_open"] = today
    try:
        await channel.send(random.choice(DAILY_OPENERS))
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════
#  VC MOVE LOGIC  (ported from original bot — exact same behaviour)
# ══════════════════════════════════════════════════════════════════════

def _vc_has_users(vc: discord.VoiceChannel) -> bool:
    return any(m for m in vc.members if not m.bot)

async def _move_bot(guild: discord.Guild, go_to: discord.VoiceChannel = None):
    """
    Priority order:
      1. go_to given → follow that user immediately.
      2. Already in the correct VC → do nothing.
      3. Find any monitored VC with users → move there.
      4. Nobody anywhere → stay put (never disconnect).
      5. Not connected at all → join first valid VC_IDS channel.
    """
    if not VC_IDS:
        return

    if not VOICE_SUPPORT_AVAILABLE:
        logger.warning("[VC] Voice support is unavailable. Install discord.py[voice] and PyNaCl, then redeploy.")
        return

    vc_client = guild.voice_client

    # ── Step 1: hard follow ──────────────────────────────────────────
    if go_to and go_to.id in VC_IDS:
        if vc_client and vc_client.is_connected():
            if vc_client.channel.id == go_to.id:
                return
            try:
                await vc_client.move_to(go_to)
            except Exception as e:
                logger.warning(f"[VC] move_to {go_to.name} failed: {e}")
        else:
            try:
                await go_to.connect()
            except Exception as e:
                logger.warning(f"[VC] connect to {go_to.name} failed: {e}")
        return

    # ── Step 2: find best populated VC ──────────────────────────────
    best = None
    for vc_id in VC_IDS:
        vc = guild.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel) and _vc_has_users(vc):
            best = vc
            break

    if best:
        if vc_client and vc_client.is_connected():
            if vc_client.channel.id == best.id:
                return
            try:
                await vc_client.move_to(best)
            except Exception as e:
                logger.warning(f"[VC] move_to {best.name} failed: {e}")
        else:
            try:
                await best.connect()
            except Exception as e:
                logger.warning(f"[VC] connect to {best.name} failed: {e}")
        return

    # ── Step 3: nobody anywhere ──────────────────────────────────────
    if vc_client and vc_client.is_connected():
        return  # stay wherever we are

    for vc_id in VC_IDS:
        vc = guild.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel):
            try:
                await vc.connect()
                return
            except Exception as e:
                logger.warning(f"[VC] startup connect to {vc.name} failed: {e}")
                return

# ══════════════════════════════════════════════════════════════════════
#  DISCORD LOG HELPER
# ══════════════════════════════════════════════════════════════════════

async def _log(msg: str):
    if not LOG_CHANNEL_ID:
        return
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(msg)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════
#  SAFE SEND  (with rate-limit retry)
# ══════════════════════════════════════════════════════════════════════

async def _safe_send(channel: discord.TextChannel, **kwargs):
    for attempt in range(2):
        try:
            return await channel.send(**kwargs)
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(getattr(e, "retry_after", 2) + 0.5)
            elif attempt == 0:
                await asyncio.sleep(1)
            else:
                raise
    return None

# ══════════════════════════════════════════════════════════════════════
#  BOT SETUP
# ══════════════════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.voice_states    = True
intents.message_content = True
intents.members         = True

bot = commands.Bot(command_prefix="nezuko ", intents=intents, help_command=None)

# ══════════════════════════════════════════════════════════════════════
#  BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════

@tasks.loop(seconds=120)
async def autosave_task():
    try:
        await save_data()
    except Exception:
        pass

@tasks.loop(seconds=300)
async def vc_reconnect_heartbeat():
    """Every 5 min: if bot got kicked / disconnected, rejoin."""
    for guild in bot.guilds:
        try:
            vc_client = guild.voice_client
            if vc_client and vc_client.is_connected():
                continue
            await _move_bot(guild)
        except Exception as e:
            logger.warning(f"[VC heartbeat] {e}")

@tasks.loop(hours=2)
async def rotate_status():
    """Rotate presence every 2 hours."""
    try:
        atype_str, name = random.choice(BOT_STATUS_POOL)
        atype_map = {
            "listening": discord.ActivityType.listening,
            "watching":  discord.ActivityType.watching,
            "playing":   discord.ActivityType.playing,
        }
        await bot.change_presence(
            activity=discord.Activity(type=atype_map[atype_str], name=name),
            status=discord.Status.online,
        )
    except Exception:
        pass

@tasks.loop(minutes=35)
async def mood_drop():
    """Periodic mood message in VC text channel when someone is in VC."""
    if not VC_TEXT_CHANNEL_ID:
        return
    channel = bot.get_channel(VC_TEXT_CHANNEL_ID)
    if not channel:
        return
    for vc_id in VC_IDS:
        vc = bot.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel):
            if [m for m in vc.members if not m.bot]:
                try:
                    await channel.send(random.choice(VC_MOOD_MESSAGES))
                except Exception:
                    pass
                break

@tasks.loop(hours=24)
async def check_birthdays():
    """Fire once a day — wish birthday members."""
    await bot.wait_until_ready()
    if not GENERAL_CHANNEL_ID:
        return
    today = datetime.date.today()
    channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if not channel:
        return
    for uid, bday_str in data.get("birthdays", {}).items():
        try:
            bday = datetime.date.fromisoformat(bday_str)
            if bday.month == today.month and bday.day == today.day:
                member = channel.guild.get_member(int(uid))
                if member:
                    embed = discord.Embed(
                        title="🎂 Happy Birthday!!",
                        description=(
                            f"🌸 *Nezuko starts spinning and clapping furiously!* 🌸\n\n"
                            f"Today is **{member.mention}'s** birthday!!! 🎉🎊\n"
                            f"Nezuko wishes you so much happiness and bamboo~ 🎋\n\n"
                            f"*(Everyone go wish them a happy birthday!!)*"
                        ),
                        color=NEZUKO_PINK,
                        timestamp=datetime.datetime.utcnow(),
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.set_footer(text="Nezuko Bot  •  🎂 Many happy returns!")
                    await channel.send(content="🎉 @everyone", embed=embed)
        except Exception:
            pass

@tasks.loop(hours=24)
async def check_milestones():
    """Fire once a day — server anniversary milestones."""
    await bot.wait_until_ready()
    if not GENERAL_CHANNEL_ID:
        return
    today = datetime.date.today()
    channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if not channel:
        return

    milestones = {
        7:   ("🌱", "**1 week** in the grove!", "You're part of the family now!"),
        30:  ("🌿", "**1 month** in the family!", "Nezuko is SO happy you stayed~"),
        100: ("🌳", "**100 days** in the bamboo grove!", "An absolute legend. Nezuko bows."),
        365: ("🎊", "**1 full year** with us!", "Nezuko throws the biggest party ever!! 🎋"),
    }

    for uid, join_str in data.get("join_dates", {}).items():
        try:
            join_date = datetime.date.fromisoformat(join_str)
            days = (today - join_date).days
            if days in milestones:
                member = channel.guild.get_member(int(uid))
                if member:
                    emoji, milestone, comment = milestones[days]
                    embed = discord.Embed(
                        title=f"{emoji} Member Milestone!",
                        description=(
                            f"🌸 **{member.mention}** has been in the server for {milestone}\n\n"
                            f"{comment} 💮\n"
                            f"Thank you for being part of our Demon Slayer family!"
                        ),
                        color=NEZUKO_PINK,
                        timestamp=datetime.datetime.utcnow(),
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.set_footer(text="Nezuko Bot  •  🌸 Every day counts!")
                    await channel.send(embed=embed)
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

    # Set initial presence
    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the server  🌸"
            ),
            status=discord.Status.online,
        )
    except Exception:
        pass

    # Connect to voice channels on startup
    for guild in bot.guilds:
        try:
            await _move_bot(guild)
        except Exception:
            pass

    # Start all background tasks
    for task in (autosave_task, vc_reconnect_heartbeat, rotate_status,
                 mood_drop, check_birthdays, check_milestones):
        if not task.is_running():
            task.start()

    # Start keep-alive HTTP server
    asyncio.create_task(_keep_alive_server())

    await _log(f"🌸 **Nezuko Bot online** — `{bot.user}` | `{len(bot.guilds)}` guild(s)")
    logger.info("🌸 Nezuko Bot is ready!")

@bot.event
async def on_close():
    await save_data()

# ──────────────────────────────────────────────────────────────────────
#  SERVER MEMBER JOIN / LEAVE
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_member_join(member: discord.Member):
    # Save join date for milestones
    data["join_dates"][str(member.id)] = datetime.date.today().isoformat()
    await save_data()

    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="🌸 A New Member Has Arrived!",
        description=(
            f"{random.choice(SERVER_WELCOME_MSGS)}\n\n"
            f"Welcome to the server, {member.mention}! 🎋\n"
            f"You are member **#{member.guild.member_count}**!\n\n"
            f"📜 Please read the rules and enjoy your stay~\n"
            f"🎂 Set your birthday: `nezuko birthday YYYY-MM-DD`"
        ),
        color=NEZUKO_PINK,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Nezuko Bot  •  🌸 Stay kind, have fun!")
    await channel.send(embed=embed)

    # Welcome DM
    try:
        dm_embed = discord.Embed(
            title=f"🌸 Welcome to {member.guild.name}!",
            description=(
                "Hi! I'm **Nezuko Bot**, your friendly server guardian~ 🎋\n\n"
                "📜 Read the rules and be kind!\n"
                "🎂 Set your birthday: `nezuko birthday YYYY-MM-DD`\n"
                "💬 Have fun and Nezuko will watch over you!\n\n"
                "*Nezuko squeaks happily and waves~* 🌸"
            ),
            color=NEZUKO_PINK,
        )
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    await _log(f"✅ **{member}** joined the server.")

@bot.event
async def on_member_remove(member: discord.Member):
    channel = bot.get_channel(GOODBYE_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="💮 A Member Has Left...",
        description=(
            f"{random.choice(SERVER_GOODBYE_MSGS)}\n\n"
            f"**{member.name}** has left the server.\n"
            f"Nezuko will miss you... the door stays open~ 🌸"
        ),
        color=NEZUKO_LEAVE,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Nezuko Bot  •  Come back soon! 🎋")
    await channel.send(embed=embed)
    await _log(f"👋 **{member}** left the server.")

# ──────────────────────────────────────────────────────────────────────
#  VOICE STATE UPDATE  (VC following + VC greetings)
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id == bot.user.id:
        return

    guild   = member.guild
    channel = bot.get_channel(VC_TEXT_CHANNEL_ID) if VC_TEXT_CHANNEL_ID else None

    was_monitored = before.channel is not None and before.channel.id in VC_IDS
    now_monitored = after.channel  is not None and after.channel.id  in VC_IDS

    # ── VC following logic (copied from original) ────────────────────
    if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
        await _move_bot(guild, go_to=after.channel)
    elif was_monitored and not now_monitored:
        await _move_bot(guild)
    elif was_monitored and now_monitored and before.channel.id != after.channel.id:
        await _move_bot(guild, go_to=after.channel)

    if not channel:
        return

    # ── JOIN greeting ────────────────────────────────────────────────
    if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
        await maybe_send_daily_open(channel)

        greeting = get_vc_join_greeting(member)
        others   = [m for m in after.channel.members if not m.bot and m.id != member.id]
        if not others:
            greeting += "\n*— the grove is all yours! Nezuko will keep you company~ 🌸*"
        elif len(others) >= 4:
            greeting += f"\n*— {len(others)} others are already here! So lively~ 💮*"

        uid   = str(member.id)
        tier  = data["user_tiers"].get(uid)
        badge = _streak_badge(data["streaks"].get(uid, 0))

        footer = random.choice(VC_FOOTER_LINES)
        if tier:
            footer = f"{footer}  ·  {TIER_LABELS[tier]}"
        if badge:
            footer = f"{footer}  ·  {badge}"

        color = NEZUKO_DARK if _is_late_night() else random.choice(JOIN_COLORS)

        embed = discord.Embed(
            title=random.choice(VC_JOIN_TITLES),
            description=greeting,
            color=color,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=footer, icon_url=member.display_avatar.url)

        msg = await _safe_send(channel, embed=embed)
        if msg:
            try:
                await msg.add_reaction(random.choice(VC_JOIN_REACTIONS))
            except Exception:
                pass

    # ── LEAVE greeting ───────────────────────────────────────────────
    elif was_monitored and not now_monitored:
        leave_msg = get_vc_leave_greeting(member.display_name)
        embed = discord.Embed(
            title=random.choice(VC_LEAVE_TITLES),
            description=leave_msg,
            color=random.choice(LEAVE_COLORS),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(
            text=random.choice(VC_LEAVE_FOOTER_LINES),
            icon_url=member.display_avatar.url,
        )
        msg = await _safe_send(channel, embed=embed)
        if msg:
            try:
                await msg.add_reaction(random.choice(VC_LEAVE_REACTIONS))
            except Exception:
                pass

# ──────────────────────────────────────────────────────────────────────
#  BAD WORD FILTER + COMMAND CHANNEL FILTER  (on_message)
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ── Anti-spam check (runs first, catches hacked accounts / raiders) ──
    if isinstance(message.channel, discord.TextChannel):
        if await _check_spam(message):
            return  # message was spam — stop processing

    # ── Command channel filter (ported from original) ─────────────────
    if COMMAND_CHANNEL_ID and message.channel.id == COMMAND_CHANNEL_ID:
        content = message.content.strip()

        if not content.lower().startswith("nezuko"):
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(
                f"🌸 *Nezuko tilts her head* This channel is for `nezuko` commands only!\n"
                f"{_COMMANDS_DISPLAY}\n"
                f"✅ **Example:** `nezuko help` {message.author.mention}"
            )
            return

        parts = content.split()
        if len(parts) < 2:
            await message.channel.send(
                f"{_COMMANDS_DISPLAY}\n✅ **Example:** `nezuko help` {message.author.mention}"
            )
            return

        cmd = parts[1].lower()
        if cmd not in NEZUKO_COMMANDS:
            suggestions = suggest_commands(cmd)
            if suggestions:
                suggestion_text = "\n".join([f"`nezuko {s}`" for s in suggestions])
                await message.channel.send(
                    f"❓ *Nezuko tilts her head* Unknown command **`{cmd}`**\n\n"
                    f"Did you mean:\n{suggestion_text}\n\n"
                    f"✅ **Example:** `nezuko help` {message.author.mention}"
                )
            else:
                await message.channel.send(
                    f"{_COMMANDS_DISPLAY}\n"
                    f"❌ Unknown command **`{cmd}`** {message.author.mention}\n"
                    f"✅ **Example:** `nezuko help`"
                )
            return

    # ── Bad word filter ───────────────────────────────────────────────
    if isinstance(message.channel, discord.TextChannel) and message.channel.is_nsfw():
        await bot.process_commands(message)
        return

    content_lower = message.content.lower()
    if any(word in content_lower for word in BAD_WORDS):
        # Skip if mod
        if MOD_ROLE_ID:
            mod_role = message.guild.get_role(MOD_ROLE_ID)
            if mod_role and mod_role in message.author.roles:
                await bot.process_commands(message)
                return

        try:
            await message.delete()
        except discord.Forbidden:
            pass

        uid = str(message.author.id)
        data["warnings"][uid] = data["warnings"].get(uid, 0) + 1
        warns = data["warnings"][uid]

        if warns >= 3:
            data["warnings"][uid] = 0  # reset after timeout

            try:
                until = discord.utils.utcnow() + datetime.timedelta(minutes=10)
                await message.author.timeout(until, reason="Repeated bad language (Nezuko Bot)")
            except discord.Forbidden:
                pass

            embed = discord.Embed(
                title="⏰ Timed Out!",
                description=(
                    f"{random.choice(TIMEOUT_MSGS)}\n\n"
                    f"{message.author.mention} has been timed out for **10 minutes**.\n"
                    f"Warnings reset. Please be kind when you return~ 🌸"
                ),
                color=discord.Color.red(),
                timestamp=datetime.datetime.utcnow(),
            )
            embed.set_footer(text="3 warnings = 10 min timeout  •  Nezuko Bot 🌸")
            await message.channel.send(embed=embed)
            await _log(f"⏰ **{message.author}** timed out for 10 min (3 bad language warnings).")
        else:
            warn_text = random.choice(BAD_WORD_WARN_MSGS).format(warns=warns)
            embed = discord.Embed(
                title="🌸 Language Warning!",
                description=f"{message.author.mention} {warn_text}",
                color=NEZUKO_PINK,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.set_footer(text="3 warnings = 10 min timeout!  •  Nezuko Bot 🌸")
            await message.channel.send(embed=embed, delete_after=15)

    await bot.process_commands(message)

# ══════════════════════════════════════════════════════════════════════
#  NEZUKO COMMANDS
# ══════════════════════════════════════════════════════════════════════

@bot.command(name="birthday")
async def cmd_birthday(ctx: commands.Context, date: str = None):
    """nezuko birthday YYYY-MM-DD"""
    if not date:
        await ctx.reply("🎂 Usage: `nezuko birthday YYYY-MM-DD`\nExample: `nezuko birthday 2000-03-15`")
        return
    try:
        bday = datetime.date.fromisoformat(date)
        if bday.year < 1900 or bday >= datetime.date.today():
            await ctx.reply("❌ That date doesn't look right! Use a date in the past~ 🌸")
            return
        # store month+day only (year fixed to 2000 so it repeats every year)
        data["birthdays"][str(ctx.author.id)] = bday.replace(year=2000).isoformat()
        await ctx.reply(
            f"🎂 Nezuko has noted your birthday as **{bday.strftime('%B %d')}**!\n"
            f"She'll make sure everyone celebrates~ 🌸🎋"
        )
    except ValueError:
        await ctx.reply("❌ Invalid format! Use: `nezuko birthday YYYY-MM-DD`\nExample: `nezuko birthday 2000-03-15`")

@bot.command(name="warnings")
async def cmd_warnings(ctx: commands.Context, member: discord.Member = None):
    """nezuko warnings [@user]"""
    member = member or ctx.author
    warns = data["warnings"].get(str(member.id), 0)
    color = discord.Color.red() if warns >= 2 else (discord.Color.orange() if warns == 1 else NEZUKO_PINK)
    embed = discord.Embed(
        title="🌸 Warning Check",
        description=(
            f"**{member.display_name}** has **{warns}/3** warnings.\n"
            + (f"\n*Nezuko is watching very closely...* 👀" if warns >= 2 else
               f"\n*Nezuko nods approvingly~ 🌸*" if warns == 0 else
               f"\n*Nezuko shakes her head a little...* 🎋")
        ),
        color=color,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Nezuko Bot  •  🌸")
    await ctx.send(embed=embed)

@bot.command(name="server")
async def cmd_server(ctx: commands.Context):
    """nezuko server"""
    guild = ctx.guild
    embed = discord.Embed(title=f"📋 {guild.name}", color=NEZUKO_PINK)
    embed.add_field(name="👥 Members",    value=guild.member_count,                     inline=True)
    embed.add_field(name="💬 Channels",   value=len(guild.text_channels),               inline=True)
    embed.add_field(name="🔊 Voice",      value=len(guild.voice_channels),              inline=True)
    embed.add_field(name="🛡️ Roles",      value=len(guild.roles),                       inline=True)
    embed.add_field(name="📅 Created",    value=guild.created_at.strftime("%B %d, %Y"), inline=True)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Nezuko Bot  •  🌸")
    await ctx.send(embed=embed)

@bot.command(name="user")
async def cmd_user(ctx: commands.Context, member: discord.Member = None):
    """nezuko user [@user]"""
    member = member or ctx.author
    uid    = str(member.id)
    warns  = data["warnings"].get(uid, 0)
    tier   = TIER_LABELS.get(data["user_tiers"].get(uid, ""), "Not yet assigned")
    streak = data["streaks"].get(uid, 0)
    badge  = _streak_badge(streak) or "None yet"
    visits = data["vc_visit_count"].get(uid, 0)

    embed = discord.Embed(title=f"👤 {member.display_name}", color=NEZUKO_PINK)
    embed.add_field(name="🏷️ Username",  value=str(member),                              inline=True)
    embed.add_field(name="📅 Joined",    value=member.joined_at.strftime("%B %d, %Y"),   inline=True)
    embed.add_field(name="🎭 Tier",      value=tier,                                     inline=True)
    embed.add_field(name="🔥 Streak",    value=f"{streak} day(s)  {badge}",              inline=True)
    embed.add_field(name="🎙️ VC Visits", value=str(visits),                              inline=True)
    embed.add_field(name="⚠️ Warnings",  value=f"{warns}/3",                             inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Nezuko Bot  •  🌸")
    await ctx.send(embed=embed)

@bot.command(name="help")
async def cmd_help(ctx: commands.Context):
    """nezuko help"""
    embed = discord.Embed(
        title="🌸 Nezuko Bot — Help",
        description=_COMMANDS_DISPLAY,
        color=NEZUKO_PINK,
    )
    embed.set_footer(text="Nezuko Bot  •  Protecting the server~ 🎋")
    await ctx.send(embed=embed)

# ── Mod-only commands ──────────────────────────────────────────────────

@bot.command(name="clearwarnings")
@commands.has_permissions(manage_messages=True)
async def cmd_clearwarnings(ctx: commands.Context, member: discord.Member):
    """[MOD] nezuko clearwarnings @user"""
    data["warnings"][str(member.id)] = 0
    await ctx.reply(f"✅ Cleared all warnings for **{member.display_name}**. Nezuko forgives you~ 🌸")

@cmd_clearwarnings.error
async def clearwarnings_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Only moderators can clear warnings!")

@bot.command(name="antispam")
@commands.has_permissions(manage_messages=True)
async def cmd_antispam(ctx: commands.Context, member: discord.Member = None):
    """[MOD] nezuko antispam @user — clears a user from spam timeout so they can chat again."""
    if not member:
        # Show current spam state
        punished_list = list(_spam_punished)
        if not punished_list:
            embed = discord.Embed(
                title="🌸 Anti-Spam Status",
                description="✅ No members are currently in spam timeout! The grove is peaceful~ 🎋",
                color=NEZUKO_PINK,
            )
        else:
            names = []
            for uid in punished_list:
                m = ctx.guild.get_member(int(uid))
                names.append(m.mention if m else f"<@{uid}>")
            embed = discord.Embed(
                title="🚫 Anti-Spam Status",
                description=f"Currently spam-timed-out members:\n" + "\n".join(names),
                color=discord.Color.orange(),
            )
        embed.set_footer(text="Nezuko Anti-Spam  •  Use `nezuko antispam @user` to lift a ban")
        await ctx.send(embed=embed)
        return

    uid = str(member.id)
    _spam_punished.discard(uid)
    _spam_tracker.pop(uid, None)
    embed = discord.Embed(
        title="✅ Spam Block Lifted",
        description=(
            f"🌸 Nezuko reluctantly unblocks **{member.display_name}**...\n\n"
            f"{member.mention} can chat again. Nezuko will be watching~ 👀"
        ),
        color=NEZUKO_PINK,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text="Nezuko Anti-Spam  •  Cleared by mod")
    await ctx.send(embed=embed)
    await _log(f"✅ **[Anti-Spam]** {ctx.author} cleared spam block for **{member}**.")

@cmd_antispam.error
async def antispam_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Only moderators can manage the anti-spam system!")

@bot.command(name="timeout")
@commands.has_permissions(moderate_members=True)
async def cmd_timeout(ctx: commands.Context, member: discord.Member, minutes: int = 10):
    """[MOD] nezuko timeout @user [minutes]"""
    if minutes < 1 or minutes > 40320:
        await ctx.reply("❌ Minutes must be between 1 and 40320.")
        return
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await member.timeout(until, reason=f"Manual timeout by {ctx.author}")
        embed = discord.Embed(
            title="⏰ Member Timed Out",
            description=(
                f"{member.mention} timed out for **{minutes} minutes**.\n"
                f"🌸 *Nezuko shakes her head sadly...*"
            ),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.reply("❌ I don't have permission to timeout this user!")

@cmd_timeout.error
async def timeout_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Only moderators can use this command!")

# ══════════════════════════════════════════════════════════════════════
#  GLOBAL ERROR HANDLER
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        pass  # handled by on_message channel filter
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Missing argument! Try `nezuko help` for usage~ 🌸")
    elif isinstance(error, commands.BadArgument):
        await ctx.reply("❌ Invalid argument! Make sure you tagged a valid user or used the right format.")

# ══════════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════════

if not TOKEN:
    logger.error("TOKEN env var is not set!")
    sys.exit(1)

bot.run(TOKEN)
