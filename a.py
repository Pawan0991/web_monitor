import asyncio
import aiohttp
import logging
import json
import os
import sys
import re
import hashlib
import html
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import dateparser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
URLS_PY_PATH = os.path.join(SCRIPT_DIR, "urls.py")

def load_url_list(urls_py_path):
    import ast
    if not os.path.exists(urls_py_path):
        return None, FileNotFoundError(urls_py_path)
    src = open(urls_py_path, "r", encoding="utf-8", errors="replace").read()
    tree = ast.parse(src, filename="urls.py")
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "URL_LIST":
                    return ast.literal_eval(node.value), None
    return None, ValueError("URL_LIST assignment not found in urls.py")

URL_LIST, _urls_load_error = load_url_list(URLS_PY_PATH)

if not URL_LIST:
    print("❌ Error: urls.py not found or URL_LIST missing.")
    print(f"CWD: {os.getcwd()}")
    print(f"urls.py path: {URLS_PY_PATH}")
    try:
        print("Files:", sorted(os.listdir(os.getcwd()))[:200])
    except Exception:
        pass
    if _urls_load_error:
        print(f"urls.py load error: {type(_urls_load_error).__name__}: {_urls_load_error}")
    sys.exit(1)

# --- CONFIGURATION ---
load_dotenv("config.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "0").strip().lower() in ("1", "true", "yes", "y", "on")

# --- CONSTANTS FOR FILTERING ---
STRONG_KEYWORDS = [
    "recruitment", "vacancy", "job", "career", "admit card", "call letter", "interview", 
    "shortlist", "select list", "merit list", "selection", "appointment", "engagement", 
    "apprentice", "walk-in", "walk in", "answer key", "syllabus", "cut off", "marks", 
    "score card", "hall ticket", "notification" , "results", "advertisement", "circular", "notice","procurement"    
]

EXCLUDE_WORDS = [
    "home", "about", "contact", "login", "register", "sign in", "sign up", "faq", "help", 
    "terms", "policy", "disclaimer", "sitemap", "feedback", "accessibility", "copyright", 
    "skip", "back", "top", "previous", "next", "read more", "view all", "more", "admin",
    "archive", "gallery", "photo", "video", "press", "media", "tender", "procurement", 
    "auction", "circular", "act", "rule", "regulation", "gazette", "budget", "report", 
    "annual", "statistic", "data", "census", "voter", "election", "panchayat", "municipality", 
    "corporation", "council", "board", "authority", "academy", "university", "college", 
    "school", "hospital", "center", "centre", "unit", "wing", "branch", "division", "section", 
    "cell", "zone", "circle", "region", "district", "state", "india", "govt", "government", 
    "nic", "portal", "dashboard", "employee", "staff", "officer", "commissioner", "collector", 
    "magistrate", "minister", "governor", "president", "secretary", "director", "manager", 
    "head", "chief", "chairman", "member", "directory", "telephone", "address", "location", 
    "map", "tourist", "tourism", "culture", "heritage", "festival", "event", "tour", "visit", 
    "message", "speech", "award", "honor", "achievement", "success", "story", "testimonial", 
    "survey", "poll", "quiz", "contest", "winner", "alumni", "student", "parent", "teacher", 
    "faculty", "admission", "fee", "scholarship", "library", "lab", "facility", "infrastructure", 
    "sport", "game", "health", "medical", "safety", "security", "police", "fire", "emergency", 
    "helpline", "support", "care", "donation", "fund", "relief", "volunteer", "ngo", "society", 
    "trust", "foundation", "association", "union", "federation", "chamber", "commerce", "industry", 
    "business", "market", "trade", "economy", "finance", "bank", "loan", "insurance", "tax", 
    "revenue", "custom", "excise", "gst", "income", "audit", "account", "bill", "payment", 
    "transaction", "status", "enquiry", "inquiry", "check", "verify", "validate", "certificate", 
    "license", "permit", "registration", "application", "form", "download", "upload", "submit", 
    "apply", "online", "link", "url", "website", "webpage", "page", "site", "content", "text", 
    "image", "file", "document", "pdf", "rti", "grievance", "complaint", "citizen", "charter", 
    "whos who", "who's who", "organizational", "structure", "history", "vision", "mission", 
    "objective", "introduction", "profile", "bio", "screen reader", "main content", "navigation", 
    "menu", "search", "language", "english", "hindi", "museum", "adventure", "darshan", 
    "horticulture", "handloom", "village", "subdivision", "block", "glance", "sanctuary", "park", 
    "irad", "kendra", "shop", "shipping", "aviation", "stationery", "battalion", "informatics", 
    "hq", "blood", "arrest", "criminal", "protection", "governance", "dedication", "determination", 
    "development", "orders", "cpio", "faa", "list of", "directory of", "screen reader", "skip to", 
    "content", "footer", "header", "sidebar", "widget", "gadget"
]

# Speed Control
MAX_CONCURRENT = 10 
HISTORY_FILE = "sent_history.json"
TIMEOUT_RULES = aiohttp.ClientTimeout(total=25, connect=10, sock_read=10)

# --- LOGGING ---
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)

# --- OLD SCRIPT DATE LOGIC ---
TODAY = datetime.now().date()
WEEK_START = TODAY - timedelta(days=7)
WEEK_END = TODAY

def extract_date_from_text(text):
    """
    Exact logic from old script: Parse date from arbitrary text.
    """
    if not text:
        return None
    
    lower = text.lower()
    
    # 1. Handle Today/Tomorrow
    if "today" in lower:
        return TODAY
    if "tomorrow" in lower:
        return TODAY + timedelta(days=1)

    # 2. Try Dateparser
    try:
        dt = dateparser.parse(text, settings={'PREFER_DAY_OF_MONTH': 'first', 'DATE_ORDER': 'DMY', 'STRICT_PARSING': False})
        if dt:
            return dt.date()
    except:
        pass

    # 3. Fallback Regex Patterns
    patterns = [
        r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})',
        r'(\d{4})[-/](\d{2})[-/](\d{2})',
        r'(\d{1,2})[.](\d{1,2})[.](\d{4})',
        r'(january|february|march|april|may|june|july|august|september|october|november|december)[\s\-]+(\d{1,2}),?\s*(\d{4})',
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b [\d]{1,2},? ?\d{2,4}'
    ]
    
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            try:
                dt = dateparser.parse(m.group(0), settings={'DATE_ORDER': 'DMY'})
                if dt:
                    return dt.date()
            except:
                continue
            
    return None

def date_within_week(parsed_date):
    if not parsed_date:
        return False
    return parsed_date >= WEEK_START

# --- HISTORY ---
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_to_history(fingerprint):
    history = load_history()
    if fingerprint not in history:
        history.append(fingerprint)
        if len(history) > 5000:
            history = history[-5000:]
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=4)

def make_fingerprint(title, date_text, link):
    t = re.sub(r'\s+', ' ', (title or "").strip().lower())
    d = str(date_text) if date_text else ""
    l = link.strip().lower()
    base = f"{t}|{d}|{l}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def clean_text(text):
    if not text:
        return ""
    return " ".join(text.split())

# --- TELEGRAM SENDER ---
async def send_telegram_msg(session, site_name, title, date_str, direct_link):
    if not ENABLE_TELEGRAM:
        return
    if not BOT_TOKEN or not CHAT_ID:
        return
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    if date_str:
        title = title.replace(date_str, "").strip()
    
    title = html.escape(title)

    msg = (
        f"<b>🏛️ {site_name}</b>\n\n"
        f"⚡ <b>New Update Detected</b>\n"
        f"📝 <b>{title}</b>\n"
        f"📅 Date: {date_str}\n\n"
        f"🔗 <a href='{direct_link}'>Click here to Open Link</a>"
    )
    payload = { "chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": False }
    try:
        async with session.post(url, json=payload, timeout=10) as resp:
            if resp.status == 200:
                logging.info(f"🔔 [SENT] {site_name}")
    except:
        pass

async def send_telegram_text(session, text):
    if not ENABLE_TELEGRAM:
        return
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        await session.post(url, json=payload, timeout=10)
    except:
        pass

# --- STATE MAPPING LOADER ---
def load_state_mapping():
    """
    urls.py ko text file ki tarah padh kar State aur URL ka map banata hai.
    Comments (e.g., #Bihar) ko State maanta hai.
    """
    mapping = {}
    current_state = "Pan India" # Default agar koi comment na mile
    try:
        with open(URLS_PY_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                
                # Agar line # se start ho rahi hai to wo State hai
                if line.startswith("#"):
                    clean_state = line.lstrip("#").strip()
                    # 'url.py' ya 'list' jaise comments ko ignore karein
                    if "url.py" not in clean_state.lower() and "list" not in clean_state.lower():
                        current_state = clean_state
                
                # Agar line mein http hai to wo URL hai
                elif "http" in line:
                    # Quotes hata kar URL nikalein
                    url = line.strip().strip(",").strip('"').strip("'")
                    if url:
                        mapping[url] = current_state
    except Exception as e:
        logging.error(f"⚠️ Error mapping states: {e}")
    return mapping

URL_STATE_MAP = load_state_mapping()

# --- PHP WEBHOOK SENDER ---
async def send_data_to_php(session, state, site_name, title, date_str, direct_link, last_date_str=None):
    if not WEBHOOK_URL:
        return False

    payload = {
        "secret": WEBHOOK_SECRET,
        "state": state,
        "site": site_name,
        "title": title,
        "date": date_str,
        "link": direct_link,
        "last_date": last_date_str
    }

    try:
        async with session.post(WEBHOOK_URL, json=payload) as resp:
            if resp.status != 200:
                logging.error(f"⚠️ [PHP FAIL] {resp.status} - {await resp.text()}")
                return False
            logging.info(f"📤 [PHP SENT] {state} - {title[:15]}...")
            return True
    except Exception as e:
        logging.error(f"⚠️ [PHP ERROR] {e}")
        return False

# --- ADVANCED PARSER ---
def find_date_in_container(elem):
    """
    Looks for date using Tag Check AND Whole Text Scan.
    """
    # 1. Check <time> tag
    time_tag = elem.find("time")
    if time_tag:
        val = time_tag.get("datetime") or time_tag.get_text(strip=True)
        if val:
            return val

    # 2. Check Meta tags
    meta = elem.find("meta", attrs={"itemprop": "datePublished"})
    if meta and meta.get("content"):
        return meta.get("content")

    # 3. Check Classes (Elementor/WordPress)
    d_like = elem.find(["span", "div", "p"], class_=re.compile(r"(date|time|meta|posted-on|entry-date|elementor-post-date)", re.I))
    if d_like:
        return d_like.get_text(strip=True)
    
    # 4. FALLBACK: Scan WHOLE text
    text_snippet = elem.get_text(" ", strip=True)
    snippet_short = text_snippet[:300] 
    
    m = re.search(r'((?:\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4})|(?:\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b [\d]{1,2},? ?\d{2,4}))', snippet_short, re.I)
    if m:
        return m.group(1)
        
    return None

# --- CORE SCRAPER ---
async def process_url(sem, session, url, processed_hashes, date_counters, counters_lock):
    async with sem:
        try:
            logging.info(f"⏳ [CHECKING] {url}")
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}

            async with session.get(url, headers=headers, timeout=TIMEOUT_RULES, ssl=False) as response:
                if response.status != 200:
                    return
                
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'lxml')
                
                # Containers for Posts
                containers = soup.find_all(["article", "div", "li", "tr"], class_=re.compile(r"(post|article|entry|news|notice|job)", re.I))
                
                if not containers:
                    containers = soup.find_all("a", href=True)
                
                found_count = 0
                
                for item in containers[:50]:
                    title = ""
                    full_link = ""
                    raw_date_text = None
                    parsed_date = None
                    
                    if item.name == 'a':
                        link_tag = item
                        container = item.parent
                    else:
                        link_tag = item.find("a", href=True)
                        container = item
                    
                    if not link_tag:
                        continue
                    
                    href = link_tag.get("href")
                    if not href or href.startswith(('#', 'javascript', 'mailto')):
                        continue
                    
                    full_link = urljoin(url, href)

                    # Extract Title
                    title = clean_text(link_tag.get_text())
                    if len(title) < 5:
                        h_tag = container.find(re.compile("^h[1-6]$"))
                        if h_tag:
                            title = clean_text(h_tag.get_text())

                    # --- EXTRACT DATE ---
                    raw_date_text = find_date_in_container(container)
                    
                    if raw_date_text:
                        parsed_date = extract_date_from_text(raw_date_text)
                    
                    if not parsed_date and item.name == 'a':
                        prev = item.find_previous_sibling()
                        if prev:
                            parsed_date = extract_date_from_text(prev.get_text())

                    # --- DEBUG LOG FOR YOUR SITE ---
                    if "jobshikhar" in url and "CUET" in title:
                         print(f"   [DEBUG] Found: {title[:15]}... | DateRaw: {raw_date_text} | Parsed: {parsed_date}")

                    # --- SEND LOGIC ---
                    should_send = False
                    date_display = ""
                    last_date_display = None

                    if parsed_date:
                        # LOGIC: Agar date Future ki hai (e.g. > Today + 2 days), to wo Last Date hai
                        if parsed_date > (datetime.now().date() + timedelta(days=2)):
                            should_send = True
                            last_date_display = parsed_date.strftime("%d-%m-%Y")
                            # Published date aaj ki maan lo kyunki abhi detect hua hai
                            date_display = datetime.now().strftime("%d-%m-%Y")
                        
                        elif date_within_week(parsed_date):
                            should_send = True
                            date_display = parsed_date.strftime("%d-%m-%Y")
                    
                    elif "2026" in (title + " " + str(raw_date_text)).lower():
                        should_send = True
                        date_display = datetime.now().strftime("%d-%m-%Y")
                    
                    # --- NEW LOGIC: Fallback for Important Links without Date ---
                    else:
                        title_lower = title.lower()
                        
                        # Check for Strong Keywords (Always Allow)
                        is_strong = any(k in title_lower for k in STRONG_KEYWORDS)
                        
                        # Check for Excluded Words (Block if not strong)
                        is_excluded = any(bad in title_lower for bad in EXCLUDE_WORDS)
                        
                        if is_strong:
                            should_send = True
                            date_display = datetime.now().strftime("%d-%m-%Y")
                        elif len(title) > 3 and not is_excluded:
                            # Extra check: Single word titles are usually navigation items
                            if len(title.split()) > 1:
                                should_send = True
                                date_display = datetime.now().strftime("%d-%m-%Y")

                    # Extra Check: Try to find explicit "Last Date" in text if not already found
                    if not last_date_display:
                        ld_match = re.search(r'(?:Last Date|Deadline|Closing Date)[\s:-]*(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b [\d]{1,2},? ?\d{2,4})', title + " " + str(raw_date_text), re.I)
                        if ld_match:
                            ld_dt = extract_date_from_text(ld_match.group(1))
                            if ld_dt:
                                last_date_display = ld_dt.strftime("%d-%m-%Y")

                    if should_send:
                        # Smart Fingerprint:
                        # Agar date humne khud banayi hai (parsed_date is None), to fingerprint me static string use karein.
                        # Isse kya hoga: Agle din jab date change hogi, tab bhi hash same rahega -> No Telegram Spam.
                        f_date = date_display
                        if not parsed_date:
                            f_date = "static_no_date"
                        
                        fingerprint = make_fingerprint(title, f_date, full_link)
                        if fingerprint in processed_hashes:
                            continue
                        
                        domain = urlparse(url).netloc.replace("www.", "")
                        
                        # URL se State pata karein
                        state_name = URL_STATE_MAP.get(url, "General")
                        
                        # Per-notification Telegram alerts disabled; only start/stop + summary is sent.
                        saved = await send_data_to_php(session, state_name, domain, title, date_display, full_link, last_date_display)
                        if saved:
                            async with counters_lock:
                                date_counters[date_display] = date_counters.get(date_display, 0) + 1
                        
                        processed_hashes.add(fingerprint)
                        save_to_history(fingerprint)
                        found_count += 1

                logging.info(f"✅ [DONE] {url} (New: {found_count})")

        except Exception as e:
            pass

# --- MAIN ---
async def main():
    print(f"--- [START] HYBRID MONITOR (Fixed Indentation) ---")
    
    if os.path.exists(HISTORY_FILE):
        if os.path.getsize(HISTORY_FILE) < 5: 
            os.remove(HISTORY_FILE)
    
    processed_hashes = set(load_history())
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(limit=100)
    date_counters = {}
    counters_lock = asyncio.Lock()

    async with aiohttp.ClientSession(connector=connector) as session:
        await send_telegram_text(session, "🚀 Monitor Started")
        try:
            tasks = []
            for url in URL_LIST:
                tasks.append(process_url(sem, session, url, processed_hashes, date_counters, counters_lock))
            await asyncio.gather(*tasks)
        finally:
            async with counters_lock:
                items = list(date_counters.items())

            total_saved = sum(c for _, c in items)
            if items:
                def _sort_key(kv):
                    d, _ = kv
                    try:
                        return datetime.strptime(d, "%d-%m-%Y")
                    except Exception:
                        return datetime.max

                items.sort(key=_sort_key)
                by_date = "\n".join([f"{d}: {c}" for d, c in items])
                msg = f"✅ Monitor Finished\n\nDB Saved: {total_saved}\n\nBy Date:\n{by_date}"
            else:
                msg = "✅ Monitor Finished\n\nDB Saved: 0"
            await send_telegram_text(session, msg)
    print("--- [FINISHED] ---")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
