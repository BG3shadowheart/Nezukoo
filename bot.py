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
#  KEEP-ALIVE  (Render free tier)
# ══════════════════════════════════════════════════════════════════════

async def _keep_alive_server():
    async def _handle(reader, writer):
        try:
            await reader.read(2048)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b"Content-Length: 21\r\nConnection: close\r\n\r\n"
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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nezuko-bot")

# ══════════════════════════════════════════════════════════════════════
#  ENV VARS  — set all in Render dashboard → Environment
# ══════════════════════════════════════════════════════════════════════

TOKEN              = os.getenv("TOKEN", "")
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))
VC_TEXT_CHANNEL_ID = int(os.getenv("VC_TEXT_CHANNEL_ID", "0"))
LOG_CHANNEL_ID     = int(os.getenv("LOG_CHANNEL_ID", "0"))
VERIFY_CHANNEL_ID  = int(os.getenv("VERIFY_CHANNEL_ID", "0"))  # where suspicious joins are reviewed
MOD_ROLE_ID        = int(os.getenv("MOD_ROLE_ID", "0"))
COMMAND_CHANNEL_ID = int(os.getenv("COMMAND_CHANNEL_ID", "0"))

_VC_IDS_RAW = os.getenv("VC_IDS", "")
VC_IDS = [int(x.strip()) for x in _VC_IDS_RAW.split(",") if x.strip().isdigit()] if _VC_IDS_RAW.strip() else []

# ══════════════════════════════════════════════════════════════════════
#  BAD WORDS  (add your own — all lowercase)
# ══════════════════════════════════════════════════════════════════════

BAD_WORDS: list = []

# ══════════════════════════════════════════════════════════════════════
#  ANTI-SPAM CONFIG
# ══════════════════════════════════════════════════════════════════════

SPAM_MAX_MSGS       = 5
SPAM_WINDOW_SEC     = 5
SPAM_DUP_COUNT      = 3
NEW_MEMBER_DAYS     = 14
NEW_MEMBER_MAX_MSGS = 3
SPAM_TIMEOUT_MIN    = 10

_spam_tracker: dict = {}
_spam_punished: set = set()

# ══════════════════════════════════════════════════════════════════════
#  DATA PERSISTENCE
# ══════════════════════════════════════════════════════════════════════

DATA_FILE = "nezuko_data.json"

def _blank_data():
    return {
        "warnings":         {},
        "join_dates":       {},
        "vc_visit_count":   {},
        "greeted_users":    [],
        "user_tiers":       {},
        "streaks":          {},
        "last_visit_dates": {},
        "last_daily_open":  "",
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
        logger.warning(f"[Save] {e}")

# ══════════════════════════════════════════════════════════════════════
#  COLORS
# ══════════════════════════════════════════════════════════════════════

NEZUKO_PINK   = discord.Color.from_rgb(255, 105, 180)
NEZUKO_PURPLE = discord.Color.from_rgb(160,  82, 200)
NEZUKO_RED    = discord.Color.from_rgb(200,  30,  60)
NEZUKO_DARK   = discord.Color.from_rgb( 40,  10,  30)
NEZUKO_LEAVE  = discord.Color.from_rgb( 80,  40,  90)

JOIN_COLORS  = [NEZUKO_PINK, NEZUKO_PURPLE, NEZUKO_RED,
                discord.Color.from_rgb(220, 60, 100), discord.Color.from_rgb(180, 50, 150)]
LEAVE_COLORS = [NEZUKO_LEAVE, NEZUKO_DARK,
                discord.Color.from_rgb(60, 30, 70), discord.Color.from_rgb(50, 20, 50)]

# ══════════════════════════════════════════════════════════════════════
#  TIER SYSTEM
# ══════════════════════════════════════════════════════════════════════

TIERS = ["bamboo", "blossom", "flame", "shadow", "demon"]
TIER_LABELS = {
    "bamboo":  "🎋 Bamboo",  "blossom": "🌸 Blossom",
    "flame":   "🔥 Flame",   "shadow":  "🌑 Shadow",  "demon": "😈 Demon",
}
TIER_FIRST_GREETINGS = {
    "bamboo":  "🎋 **{n}** — a new friend enters our bamboo grove. Stay close, stay safe~",
    "blossom": "🌸 **{n}** — the cherry blossoms recognized you immediately. Welcome, beautiful~",
    "flame":   "🔥 **{n}** — Nezuko feels warmth from you already. The flame clan welcomes its own!",
    "shadow":  "🌑 **{n}** — quiet and powerful. The shadow watches over you now~",
    "demon":   "😈 **{n}** — *sniffs cautiously* ...one of the interesting ones. Nezuko is watching.",
}

# ══════════════════════════════════════════════════════════════════════
#  STREAK TRACKING
# ══════════════════════════════════════════════════════════════════════

def _update_streak(uid):
    today     = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    last      = data["last_visit_dates"].get(uid)
    streak    = data["streaks"].get(uid, 0)
    if last == today:       pass
    elif last == yesterday: streak += 1
    else:                   streak = 1
    data["streaks"][uid]          = streak
    data["last_visit_dates"][uid] = today
    return streak

def _streak_badge(streak):
    if streak >= 30: return "👑 30-day streak!"
    if streak >= 14: return "💎 14-day streak!"
    if streak >= 7:  return "⚡ 7-day streak!"
    if streak >= 3:  return "🔥 3-day streak!"
    return ""

# ══════════════════════════════════════════════════════════════════════
#  HOLIDAY DETECTION
# ══════════════════════════════════════════════════════════════════════

def _get_holiday():
    today = datetime.date.today()
    mm, dd = today.month, today.day
    if mm == 10 and 25 <= dd <= 31: return "halloween"
    if mm == 12 and 24 <= dd <= 26: return "christmas"
    if mm == 1  and dd == 1:        return "newyear"
    if mm == 2  and dd == 14:       return "valentine"
    if mm in (3, 4) and dd <= 15:   return "sakura"
    return ""

HOLIDAY_VC_JOINS = {
    "halloween": [
        "🎃 *Nezuko sniffs the air* — {n} arrived on Halloween night. Even demons get spooky~",
        "👻 {n} drifted in through the Halloween mist. *Nezuko presses against Tanjiro nervously*",
    ],
    "christmas": [
        "🎄 *Nezuko in a tiny Santa hat* {n} arrived! Best gift of the season~",
        "❄️ {n} stepped in from the winter cold! *Nezuko offers tiny warm hands*",
    ],
    "newyear": [
        "🎆 {n} enters the new year with us! *Nezuko blows a paper horn happily*",
        "✨ {n} steps in as the calendar turns. Fresh year, same amazing you~",
    ],
    "valentine": [
        "💌 {n} arrived on Valentine's Day! *Nezuko blushes and looks away*",
        "🌹 {n} steps in — even Nezuko's heart does a little flutter~ 🌸",
    ],
    "sakura": [
        "🌸 {n} arrives with the cherry blossoms — just as beautiful~",
        "🌺 {n} stepped in during sakura season! *Nezuko scatters petals*",
    ],
}

# ══════════════════════════════════════════════════════════════════════
#  VC GREETING POOLS
# ══════════════════════════════════════════════════════════════════════

MILESTONE_VC = [
    "🌸 **{n}** is back for visit **#{c}**! Nezuko kept your spot warm~ 🎋",
    "💮 **{n}** returns — visit **#{c}**! You're practically family now! 🌸",
    "🔥 **{n}** for the **#{c}th** time! Nezuko did a little spin for you~",
    "👑 **{n}** — **#{c}** visits! Nezuko gives you the honorary bamboo crown 🎋",
]

THEMED_VC = {
    "midnight": [
        "🌑 {n} crept in past midnight — even Nezuko is still awake watching over~",
        "🌙 {n} joins at midnight. The quiet hours belong to the bravest ones~",
    ],
    "morning": [
        "☀️ {n} is here bright and early! Even Nezuko is impressed~",
        "🍵 {n} arrived with the sunrise! *Nezuko has tea ready*",
    ],
    "afternoon": [
        "🌤️ {n} arrived! The afternoon is more fun now~ *happy clapping*",
        "🌞 {n} joins midday! Nezuko saved a sunny spot just for you 🌸",
    ],
    "evening": [
        "🌆 {n} arrived at dusk! Evening sessions are Nezuko's favourite~ 🌸",
        "🌃 {n} joined the evening crew! *Nezuko saved the best spot*",
    ],
}

VC_JOIN_POOL = [
    "🌸 *happy squeaking* {n} is here!! Nezuko does a little spin~",
    "🎋 *peeks out of bamboo box* Oh! {n} arrived! *waves tiny hands excitedly*",
    "💮 {n} stepped in! Nezuko's day just got so much better~ 🌸",
    "🌸 *claps tiny hands* {n} is finally here! Everyone cheer!",
    "🎋 {n} arrived and Nezuko immediately runs over to say hi~ *squeaks happily*",
    "💮 *does a full happy spin* {n} joined! Nezuko is SO happy right now!",
    "🌸 {n} stepped through the door! *Nezuko stands at attention, very serious, then immediately smiles*",
    "🎋 Ah! {n} is here! *Nezuko tugs on Tanjiro's sleeve excitedly*",
    "🌸 {n} arrived~ *Nezuko offers a tiny bamboo greeting*",
    "💮 *very dignified bow* Welcome, {n}. Nezuko has been expecting you~ 🌸",
    "🌸 {n} joined! *eyes light up like an excited puppy*",
    "🎋 {n} is here! The bamboo grove celebrates your arrival~",
    "💮 *sniff sniff* Nezuko approves of {n}'s energy. Welcome! 🌸",
    "🌸 {n} arrived! Nezuko immediately goes to protect them (even if they don't need it)",
    "🎋 Oh oh oh! {n} is here! *Nezuko's eyes turn pink with excitement*",
    "🌸 *zooms over excitedly* {n}!! You came!! Nezuko is so happy!!",
    "💮 {n} has entered the bamboo grove. Nezuko nods approvingly~ 🎋",
    "🌸 {n} arrived! Even Zenitsu stopped crying for a second to wave~",
    "🎋 {n} stepped in! *Nezuko immediately offers bamboo tube as a gift*",
    "💮 *happy demon noises* {n} is here! Everyone make room!",
    "🌸 {n} joined! Nezuko has been sitting by the door waiting~ 🎋",
    "💮 *tilts head curiously then beams* {n}! Welcome welcome~",
    "🌸 {n} arrived and Nezuko immediately stands behind them protectively 🎋",
    "💮 The bamboo rustles — {n} has arrived! Nezuko felt it~ 🌸",
    "🎋 {n} stepped through! *Nezuko kicks the air in excitement*",
]

VC_LEAVE_POOL = [
    "🌸 *sad eyes* {n} left... Nezuko watches the door for a while...",
    "🎋 {n} is gone... *Nezuko sits quietly and pats their empty spot*",
    "💮 {n} left... *Nezuko lets out a tiny sad squeak*",
    "🌸 Goodbye, {n}... *Nezuko waves slowly until they're gone*",
    "🎋 {n} stepped away... The bamboo grove feels quieter now~",
    "💮 *sigh* {n} left. Nezuko will keep their spot warm for next time 🌸",
    "🌸 {n} is gone... *Nezuko stares at the door with big sad eyes*",
    "🎋 {n} left... Nezuko puts a flower where they were sitting 🌸",
    "💮 {n} stepped out... Until next time! Nezuko will miss you~",
    "🌸 *tiny wave* Goodbye, {n}... Come back soon, okay? 🎋",
    "💮 {n} is gone... *Nezuko does a sad shuffle back to her corner*",
    "🌸 Farewell, {n}... *Nezuko folds a tiny origami flower and keeps it for them*",
]

VC_MOOD_POOL = [
    "🌸 *Nezuko looks around contentedly at everyone in the channel~*",
    "🎋 *The bamboo rustles softly. A peaceful moment.*",
    "💮 *Nezuko does a tiny spin just because she's happy right now*",
    "🌸 *Nezuko sits quietly, watching over everyone like a tiny guardian~*",
    "🎋 *Someone in the chat smells nice. Nezuko approves.*",
    "💮 *Nezuko peeks out from her bamboo box to check on everyone~*",
    "🌸 *A pink flower petal drifts through. Nezuko catches it happily.*",
]

DAILY_OPENERS = [
    "🌅 *A new day in the bamboo grove begins. Nezuko stretches and looks around~* 🌸",
    "🎌 *The channel wakes up! Nezuko has been guarding all night~* 🎋",
    "🌸 *Good morning, everyone! Nezuko prepared the bamboo grove for today~*",
    "⛩️ *Another day, another adventure. Nezuko is ready!* 🌸",
]

# ══════════════════════════════════════════════════════════════════════
#  SERVER JOIN / LEAVE MESSAGES
# ══════════════════════════════════════════════════════════════════════

WELCOME_MSGS = [
    "🌸 *squeaks happily* A new friend has arrived in our family!",
    "🎋 *peeks out from bamboo box* Oh! A new person! Welcome~",
    "💮 *does a little happy spin* They're finally here!!",
    "🌸 *claps tiny hands excitedly* Yay yay yay! A new member!",
    "🎋 *sniffs curiously then beams* Nezuko approves! Welcome to the family~",
    "💮 *runs in circles excitedly* Someone new!! Someone NEW!!",
    "🌸 *stands very tall and very serious* Welcome. Nezuko will protect you. *immediately smiles*",
]

GOODBYE_MSGS = [
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

WARN_MSGS = [
    "🌸 *tilts head and stares very disapprovingly* That's not a nice word! Nezuko says be kind~ **(Warning {w}/3)**",
    "🎋 *blocks with bamboo tube* HMM HMMM! No bad words here!! **(Warning {w}/3)**",
    "💮 *stomps tiny foot* Language! Nezuko is watching you VERY closely now! **(Warning {w}/3)**",
    "🌸 *crosses arms and huffs* Nezuko did not like that word. Not one bit. **(Warning {w}/3)**",
]

TIMEOUT_MSGS = [
    "🌸 *uses Blood Demon Art* You've been very naughty. Even Nezuko had enough! 10 minutes to think about what you did. ⏰",
    "🎋 *disappointed squeaking intensifies* Nezuko warned you. Three times. 10 minute timeout! 🕐",
    "💮 *sighs and puts bamboo down* This is not what Nezuko wanted to do... but rules are rules. See you in 10 minutes.",
]

# ══════════════════════════════════════════════════════════════════════
#  VC EMBED STRINGS
# ══════════════════════════════════════════════════════════════════════

VC_JOIN_TITLES = [
    "🌸 A New Friend Arrives!", "🎋 The Bamboo Grove Welcomes~",
    "💮 Nezuko Spotted Someone!", "🌸 A Presence Detected!",
    "🎋 The Grove Stirs~", "💮 Someone's Here!!",
    "🌸 Welcome to the Channel~", "🎋 Arrival Noted — Nezuko Approves",
]
VC_LEAVE_TITLES = [
    "🌸 Goodbye For Now...", "🎋 They Slipped Away~",
    "💮 The Grove Says Farewell", "🌸 Departure Noted...",
    "🎋 One Less in the Bamboo~", "💮 Until We Meet Again~",
]
VC_FOOTER_LINES = [
    "Nezuko noticed you  🌸", "the bamboo grove welcomes you  🎋",
    "protected by Nezuko  💮", "stay kind. Nezuko is watching~  💮",
    "you were always welcome here  🌸",
]
VC_LEAVE_FOOTER_LINES = [
    "Nezuko watched them leave  🌸", "the door stays open  💮",
    "absence noted — warmth remains  🌸", "Nezuko saved a petal for their return  💮",
]
VC_JOIN_REACTIONS  = ["🌸", "🎋", "💮", "🔥", "✨", "💫", "🌙", "👑", "🌺", "⚡"]
VC_LEAVE_REACTIONS = ["🌸", "🎋", "💮", "💫", "🌑", "🌺", "🎶"]

# ══════════════════════════════════════════════════════════════════════
#  BOT STATUS POOL
# ══════════════════════════════════════════════════════════════════════

STATUS_POOL = [
    ("listening", "bamboo sounds  🎋"),   ("watching", "over the server  🌸"),
    ("playing",   "in her bamboo box  📦"), ("watching", "for rule breakers  👀"),
    ("playing",   "with pink flowers  💮"), ("watching", "everyone in VC  🌸"),
    ("listening", "the bamboo rustle  🎋"), ("playing",  "Blood Demon Art  🔥"),
    ("watching",  "for suspicious activity  🛡️"), ("listening", "heartbeats  💮"),
    ("watching",  "the pink moon rise  🌸"), ("listening", "for threats  🛡️"),
    ("watching",  "for demons  😈"),         ("playing",   "being a very good demon  😈"),
]

# ══════════════════════════════════════════════════════════════════════
#  COMMAND CHANNEL DISPLAY
# ══════════════════════════════════════════════════════════════════════

NEKO_CMD_DISPLAY = (
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
#  🛡️  SECURITY OFFICER
# ══════════════════════════════════════════════════════════════════════

RAID_WINDOW_SEC     = 10
RAID_THRESHOLD      = 5
MASS_MENTION_LIMIT  = 5
CHANNEL_NUKE_WINDOW = 30
CHANNEL_NUKE_THRESH = 3

# Suspicion score thresholds
SUSPICION_FLAG = 55   # flag for mod review
SUSPICION_BAN  = 85   # auto-ban immediately

INVITE_RE   = re.compile(r"discord(?:\.gg|app\.com/invite|\.com/invite)/[a-zA-Z0-9\-_]+", re.I)
TOKEN_RE    = re.compile(r"[A-Za-z0-9_-]{24,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,38}")
PHISH_RE    = re.compile(
    r"(?:grabify|iplogger|blasze|iptracker|api\.incolumitas"
    r"|dlscord|discorcl|steamcommunlty|free-nitro|nitro-gift"
    r"|discord-gift|gift-discord|discocrd)\.", re.I)
SUSNAME_RE  = re.compile(
    r"(?:^\d{4,}$|discord.*bot|free.*nitro|nitro.*free|[a-z]{1,2}\d{6,})", re.I)

DANGEROUS_PERMS = [
    "administrator", "manage_guild", "ban_members", "kick_members",
    "manage_roles", "manage_channels", "mention_everyone",
    "manage_webhooks", "manage_messages",
]

_recent_joins:    deque = deque()
_channel_deletes: dict  = {}
_lockdown_guilds: set   = set()
_sec_actioned:    set   = set()
_verify_pending:  dict  = {}   # msg_id -> member_id

# ── Guard helpers ─────────────────────────────────────────────────────

def _is_mod(member):
    if member.guild_permissions.administrator:
        return True
    if MOD_ROLE_ID:
        role = member.guild.get_role(MOD_ROLE_ID)
        if role and role in member.roles:
            return True
    return False

def _safe_to_punish(member):
    if getattr(member, 'bot', False):              return False
    if member.id == member.guild.owner_id:          return False
    if _is_mod(member):                             return False
    return True

async def _sec_log(guild, title, description, color=None):
    if not LOG_CHANNEL_ID:
        return None
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if not ch:
        return None
    embed = discord.Embed(
        title=f"🛡️ {title}", description=description,
        color=color or discord.Color.from_rgb(180, 0, 0),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text="Nezuko Security Officer  •  Action taken automatically")
    try:
        ping = f"<@&{MOD_ROLE_ID}> " if MOD_ROLE_ID else ""
        return await ch.send(content=ping or None, embed=embed)
    except Exception as e:
        logger.warning(f"[Security] Log failed: {e}")
        return None

# ── Core punishment actions ───────────────────────────────────────────

async def _ban(member, reason, delete_days=1):
    try:
        await member.guild.ban(member, reason=f"[Nezuko Security] {reason}",
                               delete_message_days=delete_days)
        logger.info(f"[Security] Banned {member} — {reason}")
        return True
    except Exception as e:
        logger.warning(f"[Security] Ban failed {member}: {e}")
        return False

async def _kick(member, reason):
    try:
        await member.kick(reason=f"[Nezuko Security] {reason}")
        logger.info(f"[Security] Kicked {member} — {reason}")
        return True
    except Exception as e:
        logger.warning(f"[Security] Kick failed {member}: {e}")
        return False

async def _tmout(member, minutes, reason):
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await member.timeout(until, reason=f"[Nezuko Security] {reason}")
        return True
    except Exception as e:
        logger.warning(f"[Security] Timeout failed {member}: {e}")
        return False

async def _purge_msgs(channel, user, limit=30):
    try:
        await channel.purge(limit=limit, check=lambda m: m.author.id == user.id, bulk=True)
    except Exception:
        pass

async def _strip_perms(role, flagged, action_desc):
    try:
        current = role.permissions
        updates = {}
        for perm in discord.Permissions.VALID_FLAGS:
            updates[perm] = getattr(current, perm, False)
        for p in flagged:
            updates[p] = False
        await role.edit(permissions=discord.Permissions(**updates),
                        reason="[Nezuko Security] Dangerous perms auto-stripped")
        stripped = True
    except Exception as e:
        logger.warning(f"[Security] Strip perms failed on {role.name}: {e}")
        stripped = False
    await _sec_log(
        role.guild, f"⚠️ Role Perms — {'Auto-Stripped' if stripped else 'ACTION NEEDED'}",
        f"Role **`{role.name}`** {action_desc} with dangerous perms: `{'`, `'.join(flagged)}`\n\n"
        + ("✅ Permissions removed automatically." if stripped
           else "❌ **Could not auto-strip** — remove these permissions manually!"),
        color=discord.Color.orange() if stripped else discord.Color.dark_red(),
    )

async def _lockdown(guild, reason):
    if guild.id in _lockdown_guilds:
        return
    _lockdown_guilds.add(guild.id)
    logger.warning(f"[Security] LOCKDOWN {guild.name}: {reason}")
    try:
        await guild.edit(verification_level=discord.VerificationLevel.highest,
                         reason=f"[Nezuko Security] {reason}")
    except Exception as e:
        logger.warning(f"[Security] Verification raise failed: {e}")
    for ch in guild.text_channels:
        try:
            await ch.edit(slowmode_delay=30, reason="Nezuko Security: Lockdown")
        except Exception:
            pass
    await _sec_log(guild, "🔒 SERVER LOCKDOWN ACTIVATED",
        f"**Reason:** {reason}\n\n"
        f"• Verification → **Highest**\n• 30s slowmode on all channels\n• **Auto-lifts in 10 min**",
        color=discord.Color.dark_red())
    async def _lift():
        await asyncio.sleep(600)
        if guild.id not in _lockdown_guilds:
            return
        _lockdown_guilds.discard(guild.id)
        try:
            await guild.edit(verification_level=discord.VerificationLevel.medium,
                             reason="[Nezuko Security] Lockdown auto-lifted")
        except Exception:
            pass
        for ch in guild.text_channels:
            try:
                await ch.edit(slowmode_delay=0, reason="Lockdown lifted")
            except Exception:
                pass
        await _sec_log(guild, "🔓 Lockdown Auto-Lifted",
            "Server back to normal. Verification reset to **Medium**, slowmode cleared.",
            color=discord.Color.green())
    asyncio.create_task(_lift())

# ══════════════════════════════════════════════════════════════════════
#  SUSPICION SCORING SYSTEM
#  Real humans invited by friends score low and join freely.
#  Bots / suspicious accounts get flagged for mod review or auto-banned.
# ══════════════════════════════════════════════════════════════════════

def _score_member(member, in_raid=False):
    """Return (score: int, reasons: list[str])."""
    score   = 0
    reasons = []
    age     = (discord.utils.utcnow() - member.created_at).days

    if age < 1:
        score += 40; reasons.append(f"Account created **today** ({age}d old)")
    elif age < 3:
        score += 30; reasons.append(f"Account only **{age} day(s)** old")
    elif age < 7:
        score += 20; reasons.append(f"Account only **{age} days** old")
    elif age < 14:
        score += 8;  reasons.append(f"Fairly new account ({age}d)")

    if member.default_avatar == member.display_avatar:
        score += 20; reasons.append("No profile picture (default avatar)")

    name = member.name
    if SUSNAME_RE.search(name):
        score += 20; reasons.append(f"Suspicious username pattern: `{name}`")

    digit_ratio = sum(c.isdigit() for c in name) / max(len(name), 1)
    if digit_ratio > 0.6 and len(name) >= 5:
        score += 15; reasons.append(f"Username is mostly numbers: `{name}`")

    if len(name.replace("_", "").replace(".", "")) <= 3:
        score += 10; reasons.append(f"Very short username: `{name}`")

    if not member.global_name:
        score += 5; reasons.append("No global display name set")

    if in_raid:
        score += 30; reasons.append("Joined during a **detected raid wave**")

    return score, reasons


async def _handle_suspicious(member, score, reasons):
    """Post approve/disapprove to VERIFY_CHANNEL or auto-ban if score is extreme."""
    if score >= SUSPICION_BAN:
        _sec_actioned.add(member.id)
        ok = await _ban(member, f"Auto-ban: suspicion score {score}", delete_days=1)
        if ok:
            await _sec_log(member.guild, "🚫 Suspicious Account — Auto-Banned",
                f"**{member}** (`{member.id}`) scored **{score}/100**.\n\n"
                f"**Signals:**\n" + "\n".join(f"• {r}" for r in reasons) +
                f"\n\n✅ Auto-banned (exceeded threshold {SUSPICION_BAN}).",
                color=discord.Color.dark_red())
        return

    # Post to verify channel for mod to approve or kick
    ch = bot.get_channel(VERIFY_CHANNEL_ID) or bot.get_channel(LOG_CHANNEL_ID)
    if not ch:
        return

    embed = discord.Embed(
        title="🔍 Suspicious Member — Mod Review Required",
        description=(
            f"**{member}** (`{member.id}`) joined and looks suspicious.\n"
            f"Suspicion score: **{score}/100** (threshold: {SUSPICION_FLAG})\n\n"
            f"**Signals detected:**\n" + "\n".join(f"• {r}" for r in reasons) +
            f"\n\n✅ **React ✅** — Approve (member stays)\n"
            f"❌ **React ❌** — Kick the member"
        ),
        color=discord.Color.orange(),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Nezuko Security Officer  •  Awaiting mod decision")
    try:
        ping = f"<@&{MOD_ROLE_ID}> " if MOD_ROLE_ID else ""
        msg  = await ch.send(content=ping or None, embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        _verify_pending[msg.id] = member.id
    except Exception as e:
        logger.warning(f"[Security] Verify post failed: {e}")


async def _security_check_join(member):
    now = datetime.datetime.utcnow().timestamp()
    _recent_joins.append((now, member.guild.id, member.id))
    cutoff = now - RAID_WINDOW_SEC
    while _recent_joins and _recent_joins[0][0] < cutoff:
        _recent_joins.popleft()

    guild_joins = [(t, mid) for t, gid, mid in _recent_joins if gid == member.guild.id]
    in_raid     = len(guild_joins) >= RAID_THRESHOLD

    if in_raid and member.guild.id not in _lockdown_guilds:
        await _lockdown(member.guild, f"Raid wave — {len(guild_joins)} joins in {RAID_WINDOW_SEC}s")
        for _, mid in guild_joins:
            raider = member.guild.get_member(mid)
            if not raider or not _safe_to_punish(raider) or raider.id in _sec_actioned:
                continue
            sc, rs = _score_member(raider, in_raid=True)
            _sec_actioned.add(raider.id)
            if sc >= SUSPICION_BAN:
                await _ban(raider, f"Raid participant (score {sc})", delete_days=1)
            elif sc >= SUSPICION_FLAG:
                await _handle_suspicious(raider, sc, rs)
        await _sec_log(member.guild, "⚡ Raid Wave Detected",
            f"**{len(guild_joins)}** accounts joined in **{RAID_WINDOW_SEC}s**.\n"
            f"Server locked down. Obvious bots banned, borderline accounts flagged for mod review.",
            color=discord.Color.dark_red())
        return

    if member.id not in _sec_actioned:
        sc, rs = _score_member(member, in_raid=False)
        if sc >= SUSPICION_FLAG:
            await _handle_suspicious(member, sc, rs)


async def _security_check_message(message):
    """Returns True if message was actioned and should stop further processing."""
    content = message.content
    member  = message.author
    if not _safe_to_punish(member):
        return False

    if TOKEN_RE.search(content):
        await _purge_msgs(message.channel, member)
        await _ban(member, "Posted a bot token / credential", delete_days=7)
        await _sec_log(message.guild, "🔑 CRITICAL: Token Leak — User Banned",
            f"**{member}** (`{member.id}`) posted what looks like a **bot token** in {message.channel.mention}.\n\n"
            f"✅ Messages deleted  ✅ User banned\n"
            f"⚠️ **If a real token was exposed, regenerate it NOW** in the Discord Developer Portal!",
            color=discord.Color.dark_red())
        return True

    if PHISH_RE.search(content):
        await _purge_msgs(message.channel, member)
        await _ban(member, "Posted phishing/IP-grabber link", delete_days=7)
        await _sec_log(message.guild, "🎣 Phishing Link — User Banned",
            f"**{member}** (`{member.id}`) posted a phishing link in {message.channel.mention}.\n"
            f"`{content[:300]}`\n\n✅ Messages deleted  ✅ User banned",
            color=discord.Color.dark_red())
        return True

    if INVITE_RE.search(content):
        await _purge_msgs(message.channel, member)
        await _kick(member, "Posted unauthorized Discord invite link")
        await _sec_log(message.guild, "🔗 Invite Link — User Kicked",
            f"**{member}** (`{member.id}`) posted an invite link in {message.channel.mention}.\n"
            f"`{content[:300]}`\n\n✅ Messages deleted  ✅ User kicked",
            color=discord.Color.orange())
        return True

    total_mentions = len(message.mentions) + len(message.role_mentions)
    if total_mentions >= MASS_MENTION_LIMIT:
        await _purge_msgs(message.channel, member)
        await _ban(member, f"Mass mention ({total_mentions} mentions)", delete_days=1)
        await _sec_log(message.guild, "📢 Mass Mention — User Banned",
            f"**{member}** (`{member.id}`) pinged **{total_mentions} users/roles** in {message.channel.mention}.\n"
            f"✅ Messages deleted  ✅ User banned", color=discord.Color.dark_red())
        return True

    if message.mention_everyone:
        try:
            await message.delete()
        except Exception:
            pass
        await _tmout(member, 60, "@everyone/@here without permission")
        await _sec_log(message.guild, "📣 @everyone Abuse — Timed Out",
            f"**{member}** (`{member.id}`) used **@everyone/@here** in {message.channel.mention}.\n"
            f"✅ Message deleted  ✅ User timed out **60 min**", color=discord.Color.orange())
        return True

    # Caps spam (90%+ caps, 20+ chars)
    if len(content) >= 20:
        alpha = [c for c in content if c.isalpha()]
        if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) >= 0.90:
            uid = str(member.id)
            data["warnings"][uid] = data["warnings"].get(uid, 0) + 1
            w = data["warnings"][uid]
            try:
                await message.delete()
            except Exception:
                pass
            if w >= 3:
                data["warnings"][uid] = 0
                await _tmout(member, 10, "Repeated caps spam")
                try:
                    await message.channel.send(
                        f"📢 {member.mention} timed out 10 min for caps spam. *Nezuko covers her ears~* 🌸",
                        delete_after=15)
                except Exception:
                    pass
            else:
                try:
                    await message.channel.send(
                        f"📢 {member.mention} — please don't shout! **(Warning {w}/3)** 🌸",
                        delete_after=10)
                except Exception:
                    pass
            return True

    return False

# ══════════════════════════════════════════════════════════════════════
#  ANTI-SPAM ENGINE
# ══════════════════════════════════════════════════════════════════════

SPAM_WARN_MSGS = [
    "🔴 *Nezuko's eyes go red* — spamming is NOT okay in this grove!",
    "😤 *Nezuko stamps her foot* — slow down! This isn't a spam contest!",
    "🌸 *Nezuko blocks the path* — too many messages! The grove needs quiet~",
    "🎋 *Nezuko shakes bamboo aggressively* — STOP the spam!",
    "👹 *Nezuko's demon form activates* — spammers don't belong in the grove!",
]

async def _check_spam(message):
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

    is_new    = bool(member.joined_at and (discord.utils.utcnow() - member.joined_at).days < NEW_MEMBER_DAYS)
    limit     = NEW_MEMBER_MAX_MSGS if is_new else SPAM_MAX_MSGS
    msg_count = len(history)
    dup_count = sum(1 for _, c in history if c.lower().strip() == message.content.lower().strip())

    if msg_count <= limit and dup_count < SPAM_DUP_COUNT:
        return False

    _spam_punished.add(uid)
    try:
        await message.channel.purge(limit=20, check=lambda m: m.author.id == member.id, bulk=True)
    except discord.Forbidden:
        try:
            await message.delete()
        except Exception:
            pass
    except Exception:
        pass

    reason = f"Spam ({msg_count} msgs/{SPAM_WINDOW_SEC}s)" + (" — new member" if is_new else "")
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=SPAM_TIMEOUT_MIN)
        await member.timeout(until, reason=f"Nezuko Anti-Spam: {reason}")
    except Exception as e:
        logger.warning(f"[AntiSpam] Timeout failed {member}: {e}")

    embed = discord.Embed(
        title="🚫 Spam Detected!",
        description=(
            f"{random.choice(SPAM_WARN_MSGS)}\n\n"
            f"{member.mention} timed out **{SPAM_TIMEOUT_MIN} minutes**.\n"
            + ("⚠️ *New members must be extra patient in the grove~* 🌸" if is_new
               else "🌸 *Please keep the bamboo grove peaceful for everyone~*")
        ),
        color=discord.Color.red(), timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text=f"Nezuko Anti-Spam  •  {reason}")
    try:
        await message.channel.send(embed=embed, delete_after=20)
    except Exception:
        pass
    await _log(f"🚫 **[Anti-Spam]** **{member}** (`{member.id}`) timed out {SPAM_TIMEOUT_MIN} min — {reason}.")

    async def _lift():
        await asyncio.sleep(SPAM_TIMEOUT_MIN * 60)
        _spam_punished.discard(uid)
        _spam_tracker.pop(uid, None)
    asyncio.create_task(_lift())
    return True

# ══════════════════════════════════════════════════════════════════════
#  GREETING HELPERS
# ══════════════════════════════════════════════════════════════════════

def _time_theme():
    h = datetime.datetime.utcnow().hour
    if   0 <= h <  6: return "midnight"
    elif 6 <= h < 12: return "morning"
    elif 12 <= h < 18: return "afternoon"
    else:              return "evening"

def _is_late_night():
    return 0 <= datetime.datetime.utcnow().hour < 5

def _vc_join_greeting(member):
    uid   = str(member.id)
    name  = member.display_name
    count = data["vc_visit_count"].get(uid, 0) + 1
    data["vc_visit_count"][uid] = count
    _update_streak(uid)
    if uid not in data["greeted_users"]:
        data["greeted_users"].append(uid)
        tier = random.choice(TIERS)
        data["user_tiers"][uid] = tier
        return TIER_FIRST_GREETINGS[tier].format(n=name)
    holiday = _get_holiday()
    if holiday and holiday in HOLIDAY_VC_JOINS and random.random() < 0.40:
        return random.choice(HOLIDAY_VC_JOINS[holiday]).format(n=name)
    if count % 10 == 0 or count % 5 == 0:
        return random.choice(MILESTONE_VC).format(n=name, c=count)
    pool = THEMED_VC.get(_time_theme(), []) + VC_JOIN_POOL
    return random.choice(pool).format(n=name)

def _vc_leave_greeting(display_name):
    return random.choice(VC_LEAVE_POOL).format(n=display_name)

async def _maybe_daily_open(channel):
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

def _vc_has_users(vc):
    return any(m for m in vc.members if not m.bot)

async def _move_bot(guild, go_to=None):
    if not VC_IDS or not VOICE_SUPPORT_AVAILABLE:
        return
    vc_client = guild.voice_client

    if go_to and go_to.id in VC_IDS:
        if vc_client and vc_client.is_connected():
            if vc_client.channel.id == go_to.id:
                return
            try:
                await vc_client.move_to(go_to)
            except Exception as e:
                logger.warning(f"[VC] move_to {go_to.name}: {e}")
        else:
            try:
                await go_to.connect()
            except Exception as e:
                logger.warning(f"[VC] connect {go_to.name}: {e}")
        return

    best = None
    for vid in VC_IDS:
        vc = guild.get_channel(vid)
        if vc and isinstance(vc, discord.VoiceChannel) and _vc_has_users(vc):
            best = vc; break

    if best:
        if vc_client and vc_client.is_connected():
            if vc_client.channel.id != best.id:
                try:
                    await vc_client.move_to(best)
                except Exception as e:
                    logger.warning(f"[VC] move_to {best.name}: {e}")
        else:
            try:
                await best.connect()
            except Exception as e:
                logger.warning(f"[VC] connect {best.name}: {e}")
        return

    if vc_client and vc_client.is_connected():
        return
    for vid in VC_IDS:
        vc = guild.get_channel(vid)
        if vc and isinstance(vc, discord.VoiceChannel):
            try:
                await vc.connect(); return
            except Exception as e:
                logger.warning(f"[VC] startup connect {vc.name}: {e}"); return

# ══════════════════════════════════════════════════════════════════════
#  LOG + SAFE SEND
# ══════════════════════════════════════════════════════════════════════

async def _log(msg):
    if not LOG_CHANNEL_ID:
        return
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(msg)
    except Exception:
        pass

async def _safe_send(channel, **kwargs):
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
            if not guild.voice_client or not guild.voice_client.is_connected():
                await _move_bot(guild)
        except Exception as e:
            logger.warning(f"[VC heartbeat] {e}")

@tasks.loop(hours=2)
async def rotate_status():
    try:
        atype_str, name = random.choice(STATUS_POOL)
        amap = {"listening": discord.ActivityType.listening,
                "watching":  discord.ActivityType.watching,
                "playing":   discord.ActivityType.playing}
        await bot.change_presence(
            activity=discord.Activity(type=amap[atype_str], name=name),
            status=discord.Status.online)
    except Exception:
        pass

@tasks.loop(minutes=35)
async def mood_drop():
    if not VC_TEXT_CHANNEL_ID:
        return
    channel = bot.get_channel(VC_TEXT_CHANNEL_ID)
    if not channel:
        return
    for vid in VC_IDS:
        vc = bot.get_channel(vid)
        if vc and isinstance(vc, discord.VoiceChannel) and [m for m in vc.members if not m.bot]:
            try:
                await channel.send(random.choice(VC_MOOD_POOL))
            except Exception:
                pass
            break

# ══════════════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="over the server  🛡️"),
            status=discord.Status.online)
    except Exception:
        pass
    for guild in bot.guilds:
        try:
            await _move_bot(guild)
        except Exception:
            pass
    for task in (autosave_task, vc_reconnect_heartbeat, rotate_status, mood_drop):
        if not task.is_running():
            task.start()
    asyncio.create_task(_keep_alive_server())
    await _log(f"🌸 **Nezuko Bot online** — `{bot.user}` | `{len(bot.guilds)}` guild(s) | 🛡️ Security Officer active")
    logger.info("🌸 Nezuko Bot ready!")

@bot.event
async def on_close():
    await save_data()

# ──────────────────────────────────────────────────────────────────────
#  MEMBER JOIN
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_member_join(member):
    data["join_dates"][str(member.id)] = datetime.date.today().isoformat()
    await save_data()

    # Security scoring — no auto-kick for normal humans
    await _security_check_join(member)

    # Welcome channel embed
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="🌸 A New Member Has Arrived!",
            description=(
                f"{random.choice(WELCOME_MSGS)}\n\n"
                f"Welcome to the server, {member.mention}! 🎋\n"
                f"You are member **#{member.guild.member_count}**!\n\n"
                f"📜 Please read the rules and enjoy your stay~ 🌸"
            ),
            color=NEZUKO_PINK, timestamp=datetime.datetime.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Nezuko Bot  •  🌸 Stay kind, have fun!")
        await channel.send(embed=embed)

    # DM welcome to every new member
    try:
        dm_embed = discord.Embed(
            title=f"🌸 Welcome to {member.guild.name}!",
            description=(
                f"Hey **{member.display_name}**! 🎋\n\n"
                f"I'm **Nezuko Bot**, the server's guardian~ I'll be watching over you!\n\n"
                f"📜 **Read the rules** before chatting — it keeps the grove peaceful!\n"
                f"💬 Be kind to everyone — Nezuko is always watching 👀\n"
                f"🎙️ Jump into a voice channel and say hi!\n\n"
                f"*Nezuko squeaks happily and waves her tiny hands~* 🌸🎋\n\n"
                f"We're really happy you're here! 💮"
            ),
            color=NEZUKO_PINK, timestamp=datetime.datetime.utcnow(),
        )
        if member.guild.icon:
            dm_embed.set_thumbnail(url=member.guild.icon.url)
        dm_embed.set_footer(text=f"{member.guild.name}  •  Nezuko Bot 🌸")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        pass  # DMs closed — fine

    await _log(f"✅ **{member}** (`{member.id}`) joined the server.")

# ──────────────────────────────────────────────────────────────────────
#  MEMBER LEAVE
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_member_remove(member):
    # Goodbye channel embed
    channel = bot.get_channel(GOODBYE_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="💮 A Member Has Left...",
            description=(
                f"{random.choice(GOODBYE_MSGS)}\n\n"
                f"**{member.name}** has left the server.\n"
                f"Nezuko will miss you... the door stays open~ 🌸"
            ),
            color=NEZUKO_LEAVE, timestamp=datetime.datetime.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Nezuko Bot  •  Come back soon! 🎋")
        await channel.send(embed=embed)

    # DM farewell
    try:
        dm_embed = discord.Embed(
            title=f"👋 You've left {member.guild.name}",
            description=(
                f"Hey **{member.display_name}**~ 🌸\n\n"
                f"*Nezuko sits quietly by the door, watching you go...*\n\n"
                f"We'll miss you in the bamboo grove! 🎋\n"
                f"If you ever want to come back, you're always welcome.\n\n"
                f"Take care of yourself out there~ 💮"
            ),
            color=NEZUKO_LEAVE, timestamp=datetime.datetime.utcnow(),
        )
        dm_embed.set_footer(text=f"{member.guild.name}  •  Nezuko Bot 🌸")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    await _log(f"👋 **{member}** (`{member.id}`) left the server.")

# ──────────────────────────────────────────────────────────────────────
#  VOICE STATE UPDATE
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_voice_state_update(member, before, after):
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

    if not channel:
        return

    if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
        await _maybe_daily_open(channel)
        greeting = _vc_join_greeting(member)
        others   = [m for m in after.channel.members if not m.bot and m.id != member.id]
        if not others:
            greeting += "\n*— the grove is all yours! Nezuko will keep you company~ 🌸*"
        elif len(others) >= 4:
            greeting += f"\n*— {len(others)} others are already here! So lively~ 💮*"

        uid    = str(member.id)
        tier   = data["user_tiers"].get(uid)
        badge  = _streak_badge(data["streaks"].get(uid, 0))
        footer = random.choice(VC_FOOTER_LINES)
        if tier:  footer = f"{footer}  ·  {TIER_LABELS[tier]}"
        if badge: footer = f"{footer}  ·  {badge}"

        embed = discord.Embed(
            title=random.choice(VC_JOIN_TITLES), description=greeting,
            color=NEZUKO_DARK if _is_late_night() else random.choice(JOIN_COLORS),
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
        embed = discord.Embed(
            title=random.choice(VC_LEAVE_TITLES),
            description=_vc_leave_greeting(member.display_name),
            color=random.choice(LEAVE_COLORS), timestamp=datetime.datetime.utcnow(),
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=random.choice(VC_LEAVE_FOOTER_LINES), icon_url=member.display_avatar.url)
        msg = await _safe_send(channel, embed=embed)
        if msg:
            try:
                await msg.add_reaction(random.choice(VC_LEAVE_REACTIONS))
            except Exception:
                pass

# ──────────────────────────────────────────────────────────────────────
#  MESSAGE HANDLER
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.TextChannel):
        if await _check_spam(message):
            return
        if await _security_check_message(message):
            return

    # Command channel filter
    if COMMAND_CHANNEL_ID and message.channel.id == COMMAND_CHANNEL_ID:
        if not message.content.strip().lower().startswith("neko"):
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(f"{NEKO_CMD_DISPLAY}\n{message.author.mention}")
            return

    # Bad word filter (skip NSFW channels)
    if isinstance(message.channel, discord.TextChannel) and not message.channel.is_nsfw():
        cl = message.content.lower()
        if BAD_WORDS and any(w in cl for w in BAD_WORDS) and not _is_mod(message.author):
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            uid   = str(message.author.id)
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
                        f"{message.author.mention} timed out **10 minutes**.\n"
                        f"Warnings reset. Be kind when you return~ 🌸"
                    ),
                    color=discord.Color.red(), timestamp=datetime.datetime.utcnow(),
                )
                embed.set_footer(text="3 warnings = 10 min timeout  •  Nezuko Bot 🌸")
                await message.channel.send(embed=embed)
                await _log(f"⏰ **{message.author}** timed out 10 min (3 bad language warnings).")
            else:
                embed = discord.Embed(
                    title="🌸 Language Warning!",
                    description=f"{message.author.mention} {random.choice(WARN_MSGS).format(w=warns)}",
                    color=NEZUKO_PINK, timestamp=datetime.datetime.utcnow(),
                )
                embed.set_footer(text="3 warnings = 10 min timeout!  •  Nezuko Bot 🌸")
                await message.channel.send(embed=embed, delete_after=15)
            await save_data()

    await bot.process_commands(message)

# ──────────────────────────────────────────────────────────────────────
#  REACTION ADD  (verify channel: ✅ approve / ❌ kick)
# ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    if payload.message_id not in _verify_pending:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    reactor = guild.get_member(payload.user_id)
    if not reactor or not _is_mod(reactor):
        return

    member_id = _verify_pending.pop(payload.message_id, None)
    if not member_id:
        return

    emoji = str(payload.emoji)

    # Edit the review message to show decision
    async def _update_msg(text):
        try:
            ch  = bot.get_channel(payload.channel_id)
            msg = await ch.fetch_message(payload.message_id)
            await msg.edit(content=f"**Decision recorded:** {text}")
        except Exception:
            pass

    if emoji == "❌":
        member = guild.get_member(member_id)
        if member:
            try:
                await member.send(
                    f"👋 You were kicked from **{guild.name}** after a security review of your account.\n"
                    f"If you believe this is a mistake, please contact a server moderator."
                )
            except Exception:
                pass
            await _kick(member, "Flagged as suspicious — kicked by mod via reaction")
            await _sec_log(guild, "❌ Suspicious Member — Kicked by Mod",
                f"**{member}** (`{member.id}`) kicked after mod review by {reactor.mention}.",
                color=discord.Color.orange())
        await _update_msg("Member **kicked** ❌")

    elif emoji == "✅":
        await _update_msg("Member **approved** ✅")
        await _sec_log(guild, "✅ Suspicious Member — Approved by Mod",
            f"Member ID `{member_id}` approved to stay by {reactor.mention}.",
            color=discord.Color.green())

# ══════════════════════════════════════════════════════════════════════
#  🛡️  SECURITY EVENTS
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_member_update(before, after):
    """Strip dangerous permissions from any role newly given to a member."""
    for role in set(after.roles) - set(before.roles):
        flagged = [p for p in DANGEROUS_PERMS if getattr(role.permissions, p, False)]
        if flagged:
            await _strip_perms(role, flagged, "was assigned to a member")

@bot.event
async def on_guild_role_create(role):
    flagged = [p for p in DANGEROUS_PERMS if getattr(role.permissions, p, False)]
    if flagged:
        await _strip_perms(role, flagged, "was created")

@bot.event
async def on_guild_role_update(before, after):
    newly = [p for p in DANGEROUS_PERMS
             if not getattr(before.permissions, p, False) and getattr(after.permissions, p, False)]
    if newly:
        await _strip_perms(after, newly, "was updated")

@bot.event
async def on_guild_channel_delete(channel):
    gid = str(channel.guild.id)
    now = datetime.datetime.utcnow().timestamp()
    if gid not in _channel_deletes:
        _channel_deletes[gid] = deque()
    _channel_deletes[gid].append(now)
    cutoff = now - CHANNEL_NUKE_WINDOW
    while _channel_deletes[gid] and _channel_deletes[gid][0] < cutoff:
        _channel_deletes[gid].popleft()
    if len(_channel_deletes[gid]) < CHANNEL_NUKE_THRESH:
        return

    _channel_deletes[gid].clear()
    await _lockdown(channel.guild, f"Channel nuke — {CHANNEL_NUKE_THRESH}+ deletions in {CHANNEL_NUKE_WINDOW}s")

    attacker = None
    try:
        async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_delete):
            if (now - entry.created_at.timestamp()) < CHANNEL_NUKE_WINDOW + 5:
                if entry.user and _safe_to_punish(entry.user):
                    attacker = entry.user; break
    except Exception:
        pass

    ban_line = ""
    if attacker and attacker.id not in _sec_actioned:
        _sec_actioned.add(attacker.id)
        ok = await _ban(attacker, "Channel nuke attacker", delete_days=7)
        ban_line = f"\n✅ Attacker **{attacker}** permanently banned." if ok else ""

    await _sec_log(channel.guild, "💣 CHANNEL NUKE — Emergency Response",
        f"Mass channel deletion detected!\nLast deleted: `#{channel.name}`\n\n"
        f"✅ Server locked down{ban_line}\nCheck audit log and restore missing channels.",
        color=discord.Color.dark_red())

@bot.event
async def on_guild_channel_create(channel):
    await _log(f"📁 Channel created: **`#{channel.name}`** (`{channel.id}`)")

@bot.event
async def on_webhooks_update(channel):
    """Auto-delete newly created webhooks — they are a common attack vector."""
    try:
        webhooks = await channel.webhooks()
        async for entry in channel.guild.audit_logs(limit=3, action=discord.AuditLogAction.webhook_create):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 30:
                wh = discord.utils.get(webhooks, id=entry.target.id)
                if wh:
                    try:
                        await wh.delete(reason="[Nezuko Security] Suspicious webhook auto-deleted")
                    except Exception:
                        pass
                await _sec_log(channel.guild, "🕵️ Webhook Auto-Deleted",
                    f"New webhook in {channel.mention} by **{entry.user}** — auto-deleted.\n"
                    f"If legitimate, recreate it and let mods know.",
                    color=discord.Color.orange())
                return
    except Exception:
        pass

@bot.event
async def on_guild_update(before, after):
    """Log server setting changes. Verification level is not auto-reversed (user preference)."""
    changes = []
    if before.name != after.name:
        changes.append(f"Server name: `{before.name}` → `{after.name}`")
    if before.verification_level != after.verification_level:
        direction = "⬇️ lowered" if after.verification_level < before.verification_level else "⬆️ raised"
        changes.append(f"Verification level **{direction}**: `{before.verification_level}` → `{after.verification_level}`")
    if before.owner_id != after.owner_id:
        changes.append(f"🔑 **Ownership transferred!** `{before.owner_id}` → `{after.owner_id}` — verify this!")
    if changes:
        await _sec_log(after, "Server Settings Changed",
            "\n".join(f"• {c}" for c in changes), color=discord.Color.orange())

@bot.event
async def on_member_ban(guild, user):
    await _log(f"🔨 **{user}** (`{user.id}`) was **banned** from `{guild.name}`.")

@bot.event
async def on_member_unban(guild, user):
    await _log(f"✅ **{user}** (`{user.id}`) was **unbanned** from `{guild.name}`.")

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    age = (discord.utils.utcnow() - message.created_at).total_seconds()
    if age < 10 and not isinstance(message.channel, discord.DMChannel):
        await _log(
            f"🗑️ **{message.author}** — message deleted in {message.channel.mention} "
            f"({age:.1f}s old): `{message.content[:200]}`"
        )

@bot.event
async def on_bulk_message_delete(messages):
    if not messages:
        return
    channel = messages[0].channel
    if isinstance(channel, discord.DMChannel):
        return
    count   = len(messages)
    deleter = None
    try:
        async for entry in channel.guild.audit_logs(limit=3, action=discord.AuditLogAction.message_bulk_delete):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 30:
                deleter = entry.user; break
    except Exception:
        pass

    action_line = ""
    if count >= 50 and deleter and _safe_to_punish(deleter):
        ok = await _tmout(deleter, 120, f"Suspicious bulk delete ({count} messages)")
        if ok:
            action_line = f"\n✅ Deleter **{deleter}** timed out **2 hours**."

    await _sec_log(channel.guild, "🗑️ Bulk Message Deletion",
        f"**{count} messages** bulk-deleted in {channel.mention}.\n"
        f"**By:** {deleter.mention if deleter else 'unknown — check audit log'}{action_line}",
        color=discord.Color.orange() if count < 50 else discord.Color.dark_red())

# ══════════════════════════════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, (commands.CommandNotFound,
                          commands.MissingRequiredArgument,
                          commands.BadArgument)):
        pass

# ══════════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════════

if not TOKEN:
    logger.error("TOKEN env var is not set!")
    sys.exit(1)

bot.run(TOKEN)
