import os, sys, re, json, random, asyncio, datetime, logging
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
VC_TEXT_CHANNEL_ID = int(os.getenv("VC_TEXT_CHANNEL_ID", "0"))
LOG_CHANNEL_ID     = int(os.getenv("LOG_CHANNEL_ID", "0"))
MOD_ROLE_ID        = int(os.getenv("MOD_ROLE_ID", "0"))
COMMAND_CHANNEL_ID = int(os.getenv("COMMAND_CHANNEL_ID", "0"))
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))

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

SPAM_MAX_MSGS        = 5
SPAM_WINDOW_SEC      = 5
SPAM_DUP_COUNT       = 3
NEW_MEMBER_DAYS      = 7
NEW_MEMBER_MAX_MSGS  = 3
SPAM_TIMEOUT_MIN     = 10

_spam_tracker: dict = {}
_spam_punished: set = set()

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

NEZUKO_PINK   = discord.Color.from_rgb(255, 105, 180)
NEZUKO_PURPLE = discord.Color.from_rgb(160,  82, 200)
NEZUKO_RED    = discord.Color.from_rgb(200,  30,  60)
NEZUKO_DARK   = discord.Color.from_rgb( 40,  10,  30)
NEZUKO_LEAVE  = discord.Color.from_rgb( 80,  40,  90)

JOIN_COLORS = [NEZUKO_PINK, NEZUKO_PURPLE, NEZUKO_RED,
               discord.Color.from_rgb(220, 60, 100),
               discord.Color.from_rgb(180, 50, 150)]

LEAVE_COLORS = [NEZUKO_LEAVE, NEZUKO_DARK,
                discord.Color.from_rgb(60, 30, 70),
                discord.Color.from_rgb(50, 20, 50)]

# ══════════════════════════════════════════════════════════════════════
#  TIER SYSTEM
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

    data["streaks"][uid]          = streak
    data["last_visit_dates"][uid] = today
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
    if mm == 10 and 25 <= dd <= 31:   return "halloween"
    if mm == 12 and 24 <= dd <= 26:   return "christmas"
    if mm == 1  and dd == 1:          return "newyear"
    if mm == 2  and dd == 14:         return "valentine"
    if mm in (3, 4) and dd <= 15:     return "sakura"
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
#  VC GREETING POOLS
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
#  SERVER WELCOME / GOODBYE
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
#  BOT STATUS POOL
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
    ("watching",  "for suspicious activity  🛡️"),
    ("listening", "heartbeats  💮"),
    ("playing",   "hide inside bamboo box  🎋"),
    ("watching",  "the pink moon rise  🌸"),
    ("listening", "for threats  🛡️"),
]

# ══════════════════════════════════════════════════════════════════════
#  COMMAND CHANNEL DISPLAY  (neko bot commands)
# ══════════════════════════════════════════════════════════════════════

_NEKO_COMMANDS_DISPLAY = (
    "📋 **Available Commands:**\n"
    "```\n"
    "69          aibooru     aihentai    anal        bfuck\n"
    "boobjob     boobs       butt        cum         danbooru\n"
    "dickride    doujin      e621        fap         footjob\n"
    "fuck        futafuck    gelbooru    grabboobs   grabbutts\n"
    "handjob     happyend    hentai      hentaigif   hentaijk\n"
    "hvideo      irl         konachan    kuni        lewdere\n"
    "lewdkitsune lewdneko    paizuri     pussy       realbooru\n"
    "rule34      safebooru   suck        suckboobs   threesome\n"
    "trap        vtuber      yaoifuck    yurifuck\n"
    "```\n"
    "✅ **Example:** `neko 69 @username`"
)

# ══════════════════════════════════════════════════════════════════════
#  🛡️  SECURITY OFFICER  —  Autonomous Threat Response
#      Bot is server Administrator — it acts immediately, logs everything.
# ══════════════════════════════════════════════════════════════════════

# ── Tuning ────────────────────────────────────────────────────────────
RAID_WINDOW_SEC     = 10   # seconds to detect mass joins
RAID_THRESHOLD      = 5    # joins in window → lockdown + ban wave
NEW_ACCOUNT_DAYS    = 7    # account age (days) → auto-kick on join
MASS_MENTION_LIMIT  = 5    # @mentions in one msg → ban
CHANNEL_NUKE_WINDOW = 30   # seconds to detect mass channel deletes
CHANNEL_NUKE_THRESH = 3    # channel deletes → lockdown + ban attacker
INVITE_BAN_STRIKES  = 1    # invite links → instant kick (no second chance)

# Suspicious URL patterns — auto-delete + ban sender
INVITE_LINK_RE = re.compile(
    r"discord(?:\.gg|app\.com/invite|\.com/invite)/[a-zA-Z0-9\-_]+",
    re.IGNORECASE,
)
BOT_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_-]{24,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,38}"
)
PHISHING_RE = re.compile(
    r"(?:grabify|iplogger|blasze|iptracker|api\.incolumitas"
    r"|dlscord|discorcl|steamcommunlty|free-nitro|nitro-gift"
    r"|discord-gift|gift-discord)\.",
    re.IGNORECASE,
)

# Dangerous permissions that should never appear on non-mod roles
DANGEROUS_PERMS = [
    "administrator", "manage_guild", "ban_members", "kick_members",
    "manage_roles", "manage_channels", "mention_everyone",
    "manage_webhooks", "manage_messages",
]

# Runtime state
_recent_joins:     deque = deque()  # (timestamp, guild_id, member_id)
_channel_deletes:  dict  = {}       # guild_id → deque of timestamps
_lockdown_guilds:  set   = set()    # guilds currently in lockdown
_sec_actioned:     set   = set()    # user IDs already handled this session


# ── Log helper ────────────────────────────────────────────────────────

async def _sec_log(guild: discord.Guild, title: str, description: str,
                   color: discord.Color = None):
    """Post a security action report to the log channel."""
    if not LOG_CHANNEL_ID:
        return
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if not ch:
        return
    embed = discord.Embed(
        title=f"🛡️ {title}",
        description=description,
        color=color or discord.Color.from_rgb(180, 0, 0),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text="Nezuko Security Officer  •  Action taken automatically")
    try:
        ping = f"<@&{MOD_ROLE_ID}> " if MOD_ROLE_ID else ""
        await ch.send(content=ping or None, embed=embed)
    except Exception as e:
        logger.warning(f"[Security] Log send failed: {e}")


# ── Guard helpers ─────────────────────────────────────────────────────

def _is_mod(member: discord.Member) -> bool:
    """True if member is a mod / admin — never touch these users."""
    if member.guild_permissions.administrator:
        return True
    if MOD_ROLE_ID:
        role = member.guild.get_role(MOD_ROLE_ID)
        if role and role in member.roles:
            return True
    return False

def _safe_to_punish(member: discord.Member) -> bool:
    """True if we can safely act on this member (not bot, not mod, not server owner)."""
    if member.bot:
        return False
    if member.id == member.guild.owner_id:
        return False
    if _is_mod(member):
        return False
    return True


# ── Core actions ──────────────────────────────────────────────────────

async def _ban(member: discord.Member, reason: str, delete_days: int = 1) -> bool:
    """Ban a member and return True on success."""
    try:
        await member.guild.ban(member, reason=f"[Nezuko Security] {reason}",
                               delete_message_days=delete_days)
        return True
    except Exception as e:
        logger.warning(f"[Security] Ban failed for {member}: {e}")
        return False

async def _kick(member: discord.Member, reason: str) -> bool:
    """Kick a member and return True on success."""
    try:
        await member.kick(reason=f"[Nezuko Security] {reason}")
        return True
    except Exception as e:
        logger.warning(f"[Security] Kick failed for {member}: {e}")
        return False

async def _timeout(member: discord.Member, minutes: int, reason: str) -> bool:
    """Timeout a member and return True on success."""
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await member.timeout(until, reason=f"[Nezuko Security] {reason}")
        return True
    except Exception as e:
        logger.warning(f"[Security] Timeout failed for {member}: {e}")
        return False

async def _purge_user_messages(channel: discord.TextChannel, user: discord.Member, limit: int = 30):
    """Bulk-delete recent messages from a user in a channel."""
    try:
        await channel.purge(limit=limit, check=lambda m: m.author.id == user.id, bulk=True)
    except Exception:
        pass

async def _lockdown_server(guild: discord.Guild, reason: str):
    """
    Server lockdown: raise verification level to HIGHEST and apply slowmode
    to all text channels so only verified members can send.
    Automatically lifts after 10 minutes.
    """
    if guild.id in _lockdown_guilds:
        return  # already locked

    _lockdown_guilds.add(guild.id)
    logger.warning(f"[Security] LOCKDOWN activated on {guild.name}: {reason}")

    # Raise verification level
    try:
        await guild.edit(
            verification_level=discord.VerificationLevel.highest,
            reason=f"[Nezuko Security] Lockdown: {reason}",
        )
    except Exception as e:
        logger.warning(f"[Security] Verification level raise failed: {e}")

    # Apply 30s slowmode to all text channels
    for ch in guild.text_channels:
        try:
            await ch.edit(slowmode_delay=30, reason="Nezuko Security: Lockdown")
        except Exception:
            pass

    await _sec_log(
        guild,
        "🔒 SERVER LOCKDOWN ACTIVATED",
        f"**Reason:** {reason}\n\n"
        f"• Verification level raised to **Highest**\n"
        f"• 30-second slowmode applied to all text channels\n"
        f"• Lockdown will **auto-lift in 10 minutes**\n\n"
        f"Review the situation and manually adjust if needed.",
        color=discord.Color.dark_red(),
    )

    # Auto-lift after 10 minutes
    async def _lift():
        await asyncio.sleep(600)
        if guild.id not in _lockdown_guilds:
            return
        _lockdown_guilds.discard(guild.id)
        try:
            await guild.edit(
                verification_level=discord.VerificationLevel.medium,
                reason="[Nezuko Security] Lockdown auto-lifted",
            )
        except Exception:
            pass
        for ch in guild.text_channels:
            try:
                await ch.edit(slowmode_delay=0, reason="Nezuko Security: Lockdown lifted")
            except Exception:
                pass
        await _sec_log(
            guild,
            "🔓 Lockdown Auto-Lifted",
            "10-minute lockdown period ended. Verification level reset to **Medium**, slowmode cleared.\n"
            "Monitor the server and re-lock manually if needed.",
            color=discord.Color.green(),
        )

    asyncio.create_task(_lift())


# ── Join security ─────────────────────────────────────────────────────

async def _security_check_join(member: discord.Member):
    """
    Runs on every member join.
    Actions:
      • Account < NEW_ACCOUNT_DAYS days old → kick immediately
      • RAID_THRESHOLD joins in RAID_WINDOW_SEC → lockdown + ban all new raiders
    """
    now = datetime.datetime.utcnow().timestamp()
    _recent_joins.append((now, member.guild.id, member.id))

    # Prune stale entries
    cutoff = now - RAID_WINDOW_SEC
    while _recent_joins and _recent_joins[0][0] < cutoff:
        _recent_joins.popleft()

    guild_joins = [(t, mid) for t, gid, mid in _recent_joins if gid == member.guild.id]
    join_count  = len(guild_joins)

    account_age = (discord.utils.utcnow() - member.created_at).days

    # ── Raid response ────────────────────────────────────────────────
    if join_count >= RAID_THRESHOLD and member.guild.id not in _lockdown_guilds:
        await _lockdown_server(member.guild, f"Raid detected — {join_count} joins in {RAID_WINDOW_SEC}s")

        # Ban every joining account in the raid window
        banned = []
        for _, mid in guild_joins:
            raider = member.guild.get_member(mid)
            if raider and _safe_to_punish(raider) and raider.id not in _sec_actioned:
                _sec_actioned.add(raider.id)
                ok = await _ban(raider, f"Raid participant — {join_count} simultaneous joins", delete_days=1)
                if ok:
                    banned.append(str(raider))

        await _sec_log(
            member.guild,
            "⚡ RAID DETECTED — Auto-Banned Wave",
            f"**{join_count}** accounts joined in **{RAID_WINDOW_SEC}s** — raid confirmed.\n\n"
            f"**Banned ({len(banned)}):** {', '.join(banned) or 'none (already handled)'}\n\n"
            f"Server is now in **lockdown** (auto-lifts in 10 min).",
            color=discord.Color.dark_red(),
        )
        return

    # ── New / suspicious account → kick ─────────────────────────────
    if account_age < NEW_ACCOUNT_DAYS and member.id not in _sec_actioned:
        _sec_actioned.add(member.id)

        # DM them first so they know why
        try:
            await member.send(
                f"👋 Hey! You were automatically kicked from **{member.guild.name}** "
                f"because your account is only **{account_age} day(s) old**.\n\n"
                f"This is a security measure to protect the server. "
                f"Please try joining again in a few days~ 🌸"
            )
        except Exception:
            pass

        kicked = await _kick(member, f"Account too new ({account_age} day(s) old)")
        if kicked:
            await _sec_log(
                member.guild,
                "🆕 New Account — Auto-Kicked",
                f"**{member}** (`{member.id}`) was kicked automatically.\n"
                f"Account age: **{account_age} day(s)** (threshold: {NEW_ACCOUNT_DAYS} days)\n"
                f"DM sent explaining the reason.",
                color=discord.Color.orange(),
            )


# ── Message security ──────────────────────────────────────────────────

async def _security_check_message(message: discord.Message) -> bool:
    """
    Runs on every non-bot message. Takes immediate action, logs everything.
    Returns True if message was handled and further processing should stop.
    """
    content     = message.content
    member      = message.author
    channel     = message.channel

    if not _safe_to_punish(member):
        return False

    # ── Bot token leak → delete + ban ────────────────────────────────
    if BOT_TOKEN_RE.search(content):
        await _purge_user_messages(channel, member)
        await _ban(member, "Posted a bot token / credential leak", delete_days=7)
        await _sec_log(
            message.guild,
            "🔑 CRITICAL: Token Leak — User Banned",
            f"**{member}** (`{member.id}`) posted what appears to be a **bot token** in {channel.mention}.\n\n"
            f"✅ Message(s) deleted\n✅ User permanently banned\n"
            f"⚠️ **If a real token was exposed, regenerate it NOW** in the Discord Developer Portal!",
            color=discord.Color.dark_red(),
        )
        return True

    # ── Phishing / IP grabber link → delete + ban ────────────────────
    if PHISHING_RE.search(content):
        await _purge_user_messages(channel, member)
        await _ban(member, "Posted phishing/IP-grabber link", delete_days=7)
        await _sec_log(
            message.guild,
            "🎣 Phishing Link — User Banned",
            f"**{member}** (`{member.id}`) posted a **phishing or IP-grabber link** in {channel.mention}.\n\n"
            f"Content: `{content[:300]}`\n\n"
            f"✅ Message(s) deleted\n✅ User permanently banned",
            color=discord.Color.dark_red(),
        )
        return True

    # ── Unauthorized invite link → delete + kick ──────────────────────
    if INVITE_LINK_RE.search(content):
        await _purge_user_messages(channel, member)
        await _kick(member, "Posted unauthorized Discord invite link")
        await _sec_log(
            message.guild,
            "🔗 Invite Link — User Kicked",
            f"**{member}** (`{member.id}`) posted an **unauthorized Discord invite** in {channel.mention}.\n\n"
            f"Content: `{content[:300]}`\n\n"
            f"✅ Message(s) deleted\n✅ User kicked (may rejoin — ban if they repeat)",
            color=discord.Color.orange(),
        )
        return True

    # ── Mass @mentions → delete + ban ───────────────────────────────
    total_mentions = len(message.mentions) + len(message.role_mentions)
    if total_mentions >= MASS_MENTION_LIMIT:
        await _purge_user_messages(channel, member)
        await _ban(member, f"Mass mention attack ({total_mentions} mentions)", delete_days=1)
        await _sec_log(
            message.guild,
            "📢 Mass Mention — User Banned",
            f"**{member}** (`{member.id}`) pinged **{total_mentions} users/roles** in {channel.mention}.\n\n"
            f"Content: `{content[:300]}`\n\n"
            f"✅ Message(s) deleted\n✅ User permanently banned",
            color=discord.Color.dark_red(),
        )
        return True

    # ── @everyone / @here abuse → delete + timeout 1 hour ───────────
    if message.mention_everyone:
        try:
            await message.delete()
        except Exception:
            pass
        await _timeout(member, 60, "@everyone/@here used without permission")
        await _sec_log(
            message.guild,
            "📣 @everyone Abuse — User Timed Out",
            f"**{member}** (`{member.id}`) used **@everyone/@here** without permission in {channel.mention}.\n\n"
            f"✅ Message deleted\n✅ User timed out **60 minutes**\n"
            f"*(If this repeats, ban manually or lower their role permissions.)*",
            color=discord.Color.orange(),
        )
        return True

    return False


# ── Role / permission security ────────────────────────────────────────

async def _strip_dangerous_perms(role: discord.Role, flagged: list[str], action: str):
    """
    Automatically remove dangerous permissions from a role and log it.
    """
    guild = role.guild
    try:
        # Build a permissions object with the flagged perms set to False
        current = role.permissions
        kwargs  = {p: False for p in flagged}
        new_perms = current.__class__(**{
            p: False if p in flagged else getattr(current, p)
            for p in dir(current) if not p.startswith("_") and isinstance(getattr(current, p), bool)
        })
        await role.edit(permissions=new_perms, reason="[Nezuko Security] Dangerous perms auto-stripped")
        stripped = True
    except Exception as e:
        logger.warning(f"[Security] Could not strip perms from role {role.name}: {e}")
        stripped = False

    await _sec_log(
        guild,
        f"⚠️ Dangerous Role Perms — {'Auto-Stripped' if stripped else 'MANUAL ACTION NEEDED'}",
        f"Role **`{role.name}`** (`{role.id}`) was **{action}** with dangerous permissions:\n"
        f"`{'`, `'.join(flagged)}`\n\n"
        + (f"✅ Permissions **automatically removed** from the role."
           if stripped else
           f"❌ **Could not auto-strip** — remove these permissions manually!"),
        color=discord.Color.orange() if stripped else discord.Color.dark_red(),
    )

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
    member = message.author
    uid    = str(member.id)

    if _is_mod(member):
        return False

    if uid not in _spam_tracker:
        _spam_tracker[uid] = deque()

    history = _spam_tracker[uid]
    now     = asyncio.get_event_loop().time()
    history.append((now, message.content))

    while history and now - history[0][0] > SPAM_WINDOW_SEC:
        history.popleft()

    if uid in _spam_punished:
        try:
            await message.delete()
        except Exception:
            pass
        return True

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

    _spam_punished.add(uid)

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

    reason = f"Spam ({msg_count} msgs / {SPAM_WINDOW_SEC}s)" + (" — new member" if is_new else "")
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=SPAM_TIMEOUT_MIN)
        await member.timeout(until, reason=f"Nezuko Anti-Spam: {reason}")
    except Exception as e:
        logger.warning(f"[AntiSpam] Timeout failed for {member}: {e}")

    embed = discord.Embed(
        title="🚫 Spam Detected!",
        description=(
            f"{random.choice(SPAM_WARN_MSGS)}\n\n"
            f"{member.mention} has been timed out for **{SPAM_TIMEOUT_MIN} minutes**.\n\n"
            + (
                "⚠️ *New members must be extra patient in the grove~* 🌸"
                if is_new else
                "🌸 *Please keep the bamboo grove peaceful for everyone~*"
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
    if   0 <= hour <  6: return "midnight"
    elif 6 <= hour < 12: return "morning"
    elif 12 <= hour < 18: return "afternoon"
    else:                 return "evening"

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
#  VC MOVE LOGIC
# ══════════════════════════════════════════════════════════════════════

def _vc_has_users(vc: discord.VoiceChannel) -> bool:
    return any(m for m in vc.members if not m.bot)

async def _move_bot(guild: discord.Guild, go_to: discord.VoiceChannel = None):
    if not VC_IDS:
        return
    if not VOICE_SUPPORT_AVAILABLE:
        logger.warning("[VC] Voice support unavailable. Install discord.py[voice] and PyNaCl, then redeploy.")
        return

    vc_client = guild.voice_client

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

    if vc_client and vc_client.is_connected():
        return

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
#  LOG + SAFE SEND HELPERS
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

bot = commands.Bot(command_prefix="neko ", intents=intents, help_command=None)

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
    await bot.wait_until_ready()
    if not GENERAL_CHANNEL_ID:
        return
    today   = datetime.date.today()
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
    await bot.wait_until_ready()
    if not GENERAL_CHANNEL_ID:
        return
    today   = datetime.date.today()
    channel = bot.get_channel(GENERAL_CHANNEL_ID)
    if not channel:
        return

    milestones = {
        7:   ("🌱", "**1 week** in the grove!",          "You're part of the family now!"),
        30:  ("🌿", "**1 month** in the family!",         "Nezuko is SO happy you stayed~"),
        100: ("🌳", "**100 days** in the bamboo grove!",  "An absolute legend. Nezuko bows."),
        365: ("🎊", "**1 full year** with us!",           "Nezuko throws the biggest party ever!! 🎋"),
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

    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the server  🛡️"
            ),
            status=discord.Status.online,
        )
    except Exception:
        pass

    for guild in bot.guilds:
        try:
            await _move_bot(guild)
        except Exception:
            pass

    for task in (autosave_task, vc_reconnect_heartbeat, rotate_status,
                 mood_drop, check_birthdays, check_milestones):
        if not task.is_running():
            task.start()

    asyncio.create_task(_keep_alive_server())

    await _log(f"🌸 **Nezuko Bot online** — `{bot.user}` | `{len(bot.guilds)}` guild(s) | 🛡️ Security Officer active")
    logger.info("🌸 Nezuko Bot is ready — Security Officer armed!")

@bot.event
async def on_close():
    await save_data()

# ──────────────────────────────────────────────────────────────────────
#  SERVER MEMBER JOIN / LEAVE
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_member_join(member: discord.Member):
    data["join_dates"][str(member.id)] = datetime.date.today().isoformat()
    await save_data()

    # 🛡️ Security check
    await _security_check_join(member)

    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="🌸 A New Member Has Arrived!",
        description=(
            f"{random.choice(SERVER_WELCOME_MSGS)}\n\n"
            f"Welcome to the server, {member.mention}! 🎋\n"
            f"You are member **#{member.guild.member_count}**!\n\n"
            f"📜 Please read the rules and enjoy your stay~ 🌸"
        ),
        color=NEZUKO_PINK,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Nezuko Bot  •  🌸 Stay kind, have fun!")
    await channel.send(embed=embed)

    try:
        dm_embed = discord.Embed(
            title=f"🌸 Welcome to {member.guild.name}!",
            description=(
                "Hi! I'm **Nezuko Bot**, your friendly server guardian~ 🎋\n\n"
                "📜 Read the rules and be kind!\n"
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
#  VOICE STATE UPDATE
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id == bot.user.id:
        return

    guild   = member.guild
    channel = bot.get_channel(VC_TEXT_CHANNEL_ID) if VC_TEXT_CHANNEL_ID else None

    was_monitored = before.channel is not None and before.channel.id in VC_IDS
    now_monitored = after.channel  is not None and after.channel.id  in VC_IDS

    if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
        await _move_bot(guild, go_to=after.channel)
    elif was_monitored and not now_monitored:
        await _move_bot(guild)
    elif was_monitored and now_monitored and before.channel.id != after.channel.id:
        await _move_bot(guild, go_to=after.channel)

    if not channel:
        return

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
#  MESSAGE HANDLER  (spam, security, bad words, command channel)
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ── Anti-spam (catches raiders / hacked accounts) ──────────────
    if isinstance(message.channel, discord.TextChannel):
        if await _check_spam(message):
            return

    # ── Security threat checks ─────────────────────────────────────
    if isinstance(message.channel, discord.TextChannel):
        if await _security_check_message(message):
            return

    # ── Command channel filter ─────────────────────────────────────
    if COMMAND_CHANNEL_ID and message.channel.id == COMMAND_CHANNEL_ID:
        content = message.content.strip().lower()
        if not content.startswith("neko"):
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(
                f"{_NEKO_COMMANDS_DISPLAY}\n{message.author.mention}"
            )
            return

    # ── Bad word filter (skip NSFW channels) ──────────────────────
    if isinstance(message.channel, discord.TextChannel) and message.channel.is_nsfw():
        await bot.process_commands(message)
        return

    content_lower = message.content.lower()
    if any(word in content_lower for word in BAD_WORDS):
        if not _is_mod(message.author):
            try:
                await message.delete()
            except discord.Forbidden:
                pass

            uid = str(message.author.id)
            data["warnings"][uid] = data["warnings"].get(uid, 0) + 1
            warns = data["warnings"][uid]

            if warns >= 3:
                data["warnings"][uid] = 0

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
#  🛡️ SECURITY EVENTS
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """
    If a non-mod member receives a role with dangerous permissions → strip it instantly.
    Mods assigning themselves extra perms is also caught here.
    """
    added_roles = set(after.roles) - set(before.roles)
    for role in added_roles:
        perms   = role.permissions
        flagged = [p for p in DANGEROUS_PERMS if getattr(perms, p, False)]
        if not flagged:
            continue

        # If the role itself is dangerous, strip the perms from the role
        await _strip_dangerous_perms(role, flagged, "assigned to member")

        # Additionally log who got it
        await _sec_log(
            after.guild,
            "⚠️ Dangerous Role Auto-Stripped",
            f"{after.mention} (`{after.id}`) was given **`{role.name}`** which had elevated perms.\n"
            f"Flagged perms: `{'`, `'.join(flagged)}`\n\n"
            f"✅ Permissions removed from the role automatically.",
            color=discord.Color.orange(),
        )


@bot.event
async def on_guild_role_create(role: discord.Role):
    """If a new role is created with dangerous perms → strip them immediately."""
    flagged = [p for p in DANGEROUS_PERMS if getattr(role.permissions, p, False)]
    if flagged:
        await _strip_dangerous_perms(role, flagged, "created")


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    """If a role is edited to gain dangerous perms it didn't have → strip them."""
    newly_flagged = [
        p for p in DANGEROUS_PERMS
        if not getattr(before.permissions, p, False) and getattr(after.permissions, p, False)
    ]
    if newly_flagged:
        await _strip_dangerous_perms(after, newly_flagged, "updated")


@bot.event
async def on_guild_channel_delete(channel):
    """
    Detect channel nuking. On threshold:
      • Immediately lock the server down
      • Find the attacker via audit log and ban them
    """
    guild_id = str(channel.guild.id)
    now      = datetime.datetime.utcnow().timestamp()

    if guild_id not in _channel_deletes:
        _channel_deletes[guild_id] = deque()
    _channel_deletes[guild_id].append(now)

    cutoff = now - CHANNEL_NUKE_WINDOW
    while _channel_deletes[guild_id] and _channel_deletes[guild_id][0] < cutoff:
        _channel_deletes[guild_id].popleft()

    count = len(_channel_deletes[guild_id])
    if count >= CHANNEL_NUKE_THRESH:
        _channel_deletes[guild_id].clear()  # reset counter to avoid repeat triggers

        await _lockdown_server(channel.guild,
                               f"Channel nuke — {count} channels deleted in {CHANNEL_NUKE_WINDOW}s")

        # Try to find the attacker via audit log and ban them
        attacker = None
        try:
            async for entry in channel.guild.audit_logs(
                limit=5, action=discord.AuditLogAction.channel_delete
            ):
                if (now - entry.created_at.timestamp()) < CHANNEL_NUKE_WINDOW + 5:
                    suspect = entry.user
                    if suspect and _safe_to_punish(suspect):
                        attacker = suspect
                        break
        except Exception:
            pass

        ban_line = ""
        if attacker and attacker.id not in _sec_actioned:
            _sec_actioned.add(attacker.id)
            ok = await _ban(attacker, f"Channel nuke — deleted {count} channels", delete_days=7)
            ban_line = f"✅ Attacker **{attacker}** (`{attacker.id}`) **permanently banned**.\n"

        await _sec_log(
            channel.guild,
            "💣 CHANNEL NUKE — Emergency Lockdown",
            f"**{count} channels deleted** in `{CHANNEL_NUKE_WINDOW}s`!\n"
            f"Last deleted: **`#{channel.name}`**\n\n"
            f"✅ Server locked down (verification maxed, slowmode applied)\n"
            f"{ban_line}"
            f"Review audit log and restore any missing channels.",
            color=discord.Color.dark_red(),
        )


@bot.event
async def on_guild_channel_create(channel):
    await _log(f"📁 Channel created: **`#{channel.name}`** (`{channel.id}`)")


@bot.event
async def on_webhooks_update(channel):
    """
    Webhooks are a common attack vector. Find the creator via audit log and remove it.
    """
    try:
        webhooks = await channel.webhooks()
        async for entry in channel.guild.audit_logs(limit=3, action=discord.AuditLogAction.webhook_create):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 30:
                creator = entry.user
                # Delete the suspicious webhook
                target_wh = discord.utils.get(webhooks, id=entry.target.id)
                if target_wh:
                    try:
                        await target_wh.delete(reason="[Nezuko Security] Suspicious webhook auto-deleted")
                    except Exception:
                        pass
                await _sec_log(
                    channel.guild,
                    "🕵️ Webhook Auto-Deleted",
                    f"A new webhook was created in {channel.mention} by **{creator}** (`{creator.id}`).\n"
                    f"Webhook: `{entry.target.id}` — **automatically deleted** as a precaution.\n\n"
                    f"If this was legitimate, recreate the webhook and whitelist the user.",
                    color=discord.Color.orange(),
                )
                return
    except Exception:
        pass

    await _sec_log(
        channel.guild,
        "🕵️ Webhook Change Detected",
        f"Webhooks in {channel.mention} were modified. Check the audit log.",
        color=discord.Color.orange(),
    )


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    """
    Automatically reverse dangerous server-setting changes.
    Specifically: if verification level is LOWERED, raise it back.
    """
    changes = []

    if before.name != after.name:
        changes.append(f"Name: `{before.name}` → `{after.name}`")

    if before.verification_level != after.verification_level:
        if after.verification_level < before.verification_level:
            # Verification was LOWERED — reverse it immediately
            try:
                await after.edit(
                    verification_level=before.verification_level,
                    reason="[Nezuko Security] Verification level lowered — auto-reversed",
                )
                changes.append(
                    f"Verification level **lowered** (`{before.verification_level}` → `{after.verification_level}`) "
                    f"— ✅ **auto-reversed back to `{before.verification_level}`**"
                )
            except Exception:
                changes.append(
                    f"Verification level **LOWERED** — ❌ could not auto-reverse, fix manually!"
                )
        else:
            changes.append(f"Verification level raised: `{before.verification_level}` → `{after.verification_level}`")

    if before.owner_id != after.owner_id:
        changes.append(
            f"🔑 **Server ownership transferred!** "
            f"`{before.owner_id}` → `{after.owner_id}` — verify this immediately!"
        )

    if changes:
        color = discord.Color.dark_red() if any("ownership" in c or "LOWERED" in c for c in changes) \
                else discord.Color.orange()
        await _sec_log(
            after,
            "Server Settings Changed",
            "Server settings were modified:\n" + "\n".join(f"• {c}" for c in changes),
            color=color,
        )


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    await _log(f"🔨 **{user}** (`{user.id}`) was **banned** from `{guild.name}`.")


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    await _log(f"✅ **{user}** (`{user.id}`) was **unbanned** from `{guild.name}`.")


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    age_sec = (discord.utils.utcnow() - message.created_at).total_seconds()
    if age_sec < 10 and not isinstance(message.channel, discord.DMChannel):
        await _log(
            f"🗑️ Recent message by **{message.author}** deleted in "
            f"{message.channel.mention} (age {age_sec:.1f}s): `{message.content[:200]}`"
        )


@bot.event
async def on_bulk_message_delete(messages):
    """
    Bulk delete: if large, find the deleter via audit log and timeout them.
    """
    if not messages:
        return
    channel = messages[0].channel
    if isinstance(channel, discord.DMChannel):
        return

    count = len(messages)

    # Find who did it
    deleter = None
    try:
        async for entry in channel.guild.audit_logs(
            limit=3, action=discord.AuditLogAction.message_bulk_delete
        ):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 30:
                deleter = entry.user
                break
    except Exception:
        pass

    action_line = ""
    if count >= 50 and deleter and _safe_to_punish(deleter):
        # Very large purge by a non-mod → suspicious, timeout 2 hours
        ok = await _timeout(deleter, 120, f"Suspicious bulk message deletion ({count} messages)")
        if ok:
            action_line = f"\n✅ Deleter **{deleter}** timed out **2 hours** (large unauthorized purge)."

    await _sec_log(
        channel.guild,
        "🗑️ Bulk Message Deletion",
        f"**{count} messages** were bulk-deleted in {channel.mention}.\n"
        f"**Deleted by:** {deleter.mention if deleter else 'unknown (check audit log)'}"
        f"{action_line}",
        color=discord.Color.orange() if count < 50 else discord.Color.dark_red(),
    )

# ══════════════════════════════════════════════════════════════════════
#  GLOBAL ERROR HANDLER
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        pass  # No commands — ignore silently
    elif isinstance(error, commands.MissingRequiredArgument):
        pass
    elif isinstance(error, commands.BadArgument):
        pass

# ══════════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════════

if not TOKEN:
    logger.error("TOKEN env var is not set!")
    sys.exit(1)

bot.run(TOKEN)
