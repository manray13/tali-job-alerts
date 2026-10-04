"""
Tali's job alerts
Checks Adzuna for new medical front office / medical insurance jobs and
sends each new match to the ntfy app on your phones.
Runs on GitHub Actions (see .github/workflows/job-alerts.yml).
"""

import json
import os
import concurrent.futures
import urllib.error
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# =====================================================================
#  TALI'S CRITERIA  (edit these lists anytime)
# =====================================================================

HOME_ZIP = "85306"            # Glendale, AZ
LOCAL_RADIUS_MILES = 25       # jobs within this distance of Glendale
INCLUDE_REMOTE_ARIZONA = True # remote jobs listed for Arizona
INCLUDE_REMOTE_OUT_OF_STATE = False  # change to True if she's open to remote jobs
                                     # for companies based outside Arizona
MIN_HOURLY_PAY = 25          # skip jobs whose listed pay tops out below this
KEEP_JOBS_WITHOUT_PAY = True  # many postings don't list pay; keep them
MAX_DAYS_OLD = 3              # only look at postings from the last few days

# Google Jobs searches (via SerpAPI). Each line = 1 search per day (free plan: 250/month)
GOOGLE_LOCAL_SEARCHES = [
    "medical receptionist front desk front office",
    "member services representative healthcare insurance",
    "patient access prior authorization medical billing",
]
GOOGLE_REMOTE_SEARCH = "remote member services healthcare insurance Arizona"
GOOGLE_RADIUS_MILES = 25

# State of Arizona jobs (azstatejobs.gov), including AHCCCS. Free, no key needed.
STATE_JOBS = True
STATE_SEARCHES = ["AHCCCS", "customer service", "eligibility", "medical", "claims",
                  "member services", "Medicaid", "health"]

# Words Adzuna uses to pull candidate jobs (any one can match)
SEARCH_WORDS = [
    "receptionist", "patient", "registration", "scheduler", "insurance",
    "billing", "biller", "claims", "authorization", "eligibility",
    "verification", "intake", "referral", "enrollment", "secretary",
    "member", "bilingual", "administrative", "healthcare", "medical",
    "honorhealth", "banner", "abrazo", "aetna", "unitedhealthcare", "humana",
    "cigna", "molina", "ahcccs", "medicaid", "medicare",
]

# Employers she especially wants to watch (hospitals + major private and
# state-funded/AHCCCS/Medicare insurance plans). These get a 🏥 in the alert.
WATCH_EMPLOYERS = [
    "honorhealth", "honor health", "banner", "abrazo",
    "unitedhealth", "unitedhealthcare", "united healthcare", "optum",
    "aetna", "cvs health", "mercy care", "cigna", "humana", "molina",
    "blue cross", "bcbs", "bcbsaz", "anthem", "elevance", "centene",
    "arizona complete health", "ambetter", "wellcare", "health net",
    "care1st", "banner university family care", "banner plans",
    "ahcccs", "alignment health", "devoted health", "oscar health",
    "kaiser", "medicare", "medicaid",
]

# Adzuna drops jobs mentioning these, so they don't crowd out good ones
SEARCH_EXCLUDE = [
    "nurse", "rn", "lpn", "lvn", "cna", "caregiver", "physician", "therapist",
    "technician", "technologist", "driver", "cdl", "engineer", "teacher",
    "instructor", "tutor", "warehouse", "forklift", "software", "pharmacist",
    "surgeon", "travel", "veterinary", "sales",
]

# Job TITLE must contain at least one of these
TITLE_KEYWORDS = [
    "front office", "front desk", "receptionist", "patient access",
    "patient service", "patient registration", "patient coordinator",
    "registration", "scheduler", "scheduling", "check-in", "check in",
    "insurance", "billing", "biller", "claims", "prior auth", "authorization",
    "eligibility", "benefits", "verification", "revenue cycle", "collections",
    "enrollment", "member service", "member advocate", "customer service",
    "referral", "intake", "medical office", "office assistant",
    "administrative assistant", "admin assistant",
    "office coordinator", "medical secretary", "unit secretary",
    "administrative coordinator", "member services", "member representative",
    "patient care specialist", "patient care coordinator", "patient advocate",
    "patient experience", "care coordinator", "provider services",
    "customer care", "call center", "contact center", "bilingual",
    "pharmacy customer", "pharmacy service", "pharmacy support",
    "unit clerk", "health unit coordinator", "access representative",
    "access specialist", "financial counselor", "patient financial",
    "admitting", "admissions representative", "concierge", "clerk",
    "health plan", "appeals", "grievance", "credentialing",
]

# Title OR description must mention one of these (keeps out car/home insurance etc.)
HEALTH_WORDS = [
    "medical", "health", "healthcare", "patient", "clinic", "hospital",
    "dental", "pharmacy", "physician", "medicare", "medicaid", "provider",
    "hmo", "ppo", "urgent care", "surgery", "pediatric", "dermatology",
    "orthopedic", "cardiology", "behavioral health", "chiropractic",
    "optometry", "vision", "eye care", "physical therapy",
    "imaging", "radiology", "laboratory", "family practice",
    "family medicine", "internal medicine", "pediatrics", "oncology", "cancer",
    "dialysis", "kidney", "neurology", "podiatry", "spine", "pain management",
    "allergy", "sleep center", "rehab", "rehabilitation", "home health", "hospice",
    "retina", "eye", "ob/gyn", "obgyn", "women's health", "clinical", "doctor",
    "doctors", "medicine", "healthcare", "banner", "honorhealth", "abrazo",
    "dignity", "mayo", "valleywise", "phoenix children's", "unitedhealth",
    "aetna", "cvs health", "cigna", "humana", "blue cross", "centene",
    "mercy care", "ahcccs", "health plan", "hipaa", "prescription", "medical",
    "honor health", "unitedhealthcare", "united healthcare", "optum", "molina",
    "bcbs", "anthem", "elevance", "arizona complete health", "ambetter",
    "wellcare", "health net", "care1st", "alignment health", "devoted health",
    "oscar health", "kaiser",
]

# Skip jobs whose TITLE contains any of these (need licenses/degrees, or not a fit)
EXCLUDE_TITLE = [
    "nurse", "rn", "lpn", "lvn", "physician", "doctor", "pharmacist",
    "therapist", "director", "manager", "coder", "coding", "sales",
    "insurance agent", "licensed agent", "dentist", "hygienist", "surgeon",
    "attorney", "actuary", "underwriter", "engineer", "developer",
    "pharmacy technician", "pharmacy tech", "medical assistant", "dental assistant",
    "phlebotomist", "phlebotomy", "technologist", "surgical", "cpc",
    "clinical nurse", "case manager", "nurse practitioner",
]

# Skip jobs whose title OR description mentions any of these (animal care)
EXCLUDE_ANYWHERE = [
    "veterinary", "veterinarian", "vet clinic", "vet hospital", "animal hospital",
    "animal clinic", "animal care", "pet hospital", "pet care", "banfield", "vca",
    # things her resume doesn't cover
    "bachelor's degree required", "bachelors degree required", "bachelor degree required",
    "associate's degree required", "rn license", "active rn", "cpc required",
    "certified medical assistant", "cma required", "ptcb",
]

# Before sending, open each posting's link. Closed/expired/filled jobs are skipped.
# Jobs we actually see on the page are sent as ✅ Confirmed open. Jobs we can't
# reach (bot checks, blocked sites) are sent quietly as ❓ Possibly expired.
CHECK_IF_STILL_OPEN = True

MAX_ALERTS_PER_RUN = 25       # extras wait for the next run instead of spamming
FORGET_AFTER_DAYS = 45        # how long to remember jobs already sent

# =====================================================================

APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()

SEEN_FILE = Path(__file__).with_name("seen_jobs.json")
ARIZONA = timezone(timedelta(hours=-7))  # Arizona has no daylight saving


def word_pattern(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


TITLE_RE = word_pattern(TITLE_KEYWORDS)
HEALTH_RE = word_pattern(HEALTH_WORDS)
EXCLUDE_RE = word_pattern(EXCLUDE_TITLE)
EXCLUDE_ANY_RE = word_pattern(EXCLUDE_ANYWHERE)
WATCH_RE = word_pattern(WATCH_EMPLOYERS)


def clean(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def adzuna_search(extra_params):
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "results_per_page": 50,
        "what_or": " ".join(SEARCH_WORDS),
        "what_exclude": " ".join(SEARCH_EXCLUDE),
        "max_days_old": MAX_DAYS_OLD,
        "sort_by": "date",
        "content-type": "application/json",
    }
    params.update(extra_params)
    url = "https://api.adzuna.com/v1/api/jobs/us/search/1?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(3):  # Adzuna sometimes hiccups (503); wait and retry
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp).get("results", [])
        except Exception as e:
            if attempt == 2:
                raise
            print(f"Adzuna busy ({e}), retrying in 30 seconds...")
            time.sleep(30)


JOB_BOARDS = ("linkedin", "indeed", "ziprecruiter", "glassdoor", "monster",
              "simplyhired", "talent.com", "jooble", "adzuna", "snagajob", "lensa")


def parse_google_salary(text):
    """'$20–$25 an hour' or '$45K–$55K a year' -> yearly (lo, hi)."""
    if not text:
        return None, None
    nums = []
    for n, k in re.findall(r"\$?\s*([\d,.]+)\s*([kK])?", text):
        try:
            v = float(n.replace(",", ""))
        except ValueError:
            continue
        nums.append(v * 1000 if k else v)
    nums = [n for n in nums if n > 0]
    if not nums:
        return None, None
    t = text.lower()
    mult = 2080 if "hour" in t else 1
    if "month" in t:
        mult = 12
    if "week" in t:
        mult = 52
    return min(nums) * mult, max(nums) * mult


def google_days_old(label):
    label = (label or "").lower()
    if not label or "hour" in label or "minute" in label or "just" in label or "today" in label:
        return 0
    m = re.search(r"(\d+)\s*day", label)
    if m:
        return int(m.group(1))
    return 999  # weeks / months ago


def google_to_job(g):
    ext = g.get("detected_extensions") or {}
    options = g.get("apply_options") or []
    direct = [o for o in options if not any(b in (o.get("title", "") + o.get("link", "")).lower()
                                             for b in JOB_BOARDS)]
    others = [o for o in options if o not in direct]
    apply_links = [o.get("link") for o in direct + others if o.get("link")]
    link = (apply_links or [g.get("share_link", "")])[0]
    lo, hi = parse_google_salary(ext.get("salary"))
    loc = g.get("location", "")
    desc = g.get("description", "")
    if ext.get("work_from_home"):
        desc = "Remote work from home. " + desc
    sched = (ext.get("schedule_type") or "").lower().replace("-", "_")
    import hashlib
    return {
        "id": "g:" + hashlib.md5((g.get("job_id") or g.get("title", "") + loc).encode()).hexdigest()[:16],
        "title": g.get("title", ""),
        "company": {"display_name": g.get("company_name", "")},
        "location": {"display_name": loc, "area": [p.strip() for p in loc.split(",") if p.strip()]},
        "description": desc,
        "redirect_url": link,
        "apply_links": apply_links,
        "salary_min": lo, "salary_max": hi, "salary_is_predicted": "0",
        "contract_time": sched if sched in ("full_time", "part_time") else None,
        "posted_label": ext.get("posted_at", ""),
        "source": "Google Jobs" + (f" via {g.get('via', '').replace('via ', '')}" if g.get("via") else ""),
    }


def google_search(query, location, remote=False):
    params = {
        "engine": "google_jobs", "q": query, "location": location,
        "hl": "en", "gl": "us", "api_key": SERPAPI_KEY,
    }
    if remote:
        params["ltype"] = 1
    else:
        params["lrad"] = round(GOOGLE_RADIUS_MILES * 1.609)
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.load(resp)
    if data.get("error") and "hasn't returned any results" not in data["error"]:
        raise RuntimeError(data["error"])
    jobs = []
    for g in data.get("jobs_results", []):
        job = google_to_job(g)
        if google_days_old(job["posted_label"]) <= MAX_DAYS_OLD:
            jobs.append(job)
    return jobs


# ---------------------------------------------------------------------
#  State of Arizona jobs (azstatejobs.gov)
# ---------------------------------------------------------------------

STATE_SITE = "https://www.azstatejobs.gov"

# Rough map spots so in-person state jobs can be measured from Glendale
AZ_CITIES = {
    "glendale": (33.54, -112.19), "phoenix": (33.45, -112.07), "peoria": (33.58, -112.24),
    "surprise": (33.63, -112.37), "sun city": (33.60, -112.27), "el mirage": (33.61, -112.32),
    "youngtown": (33.59, -112.30), "avondale": (33.44, -112.35), "goodyear": (33.44, -112.36),
    "tolleson": (33.45, -112.26), "litchfield park": (33.49, -112.36), "scottsdale": (33.49, -111.93),
    "tempe": (33.43, -111.94), "mesa": (33.42, -111.83), "chandler": (33.31, -111.84),
    "gilbert": (33.35, -111.79), "buckeye": (33.37, -112.58), "cave creek": (33.83, -111.95),
    "tucson": (32.22, -110.97), "flagstaff": (35.20, -111.65), "yuma": (32.69, -114.63),
    "prescott": (34.54, -112.47), "casa grande": (32.88, -111.76), "florence": (33.03, -111.39),
}


def state_get(url, accept="text/html,application/xhtml+xml", extra=None):
    req = urllib.request.Request(url, headers={**BROWSER_HEADERS, "Accept": accept, **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read(3_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:
        return None, str(e)[:80], ""


STATE_LINK_RE = re.compile(r"/jobs/([a-z0-9][a-z0-9\-]{5,})(?=[\"'?#\s<\\])")


def state_links(body):
    found = set(STATE_LINK_RE.findall(body.replace("\\/", "/")))
    return {f for f in found if f not in ("search", "search-results")}


def find_state_jobs():
    """Read azstatejobs.gov search results. -> {slug: {"queries": set, "snippet": text}}"""
    q = urllib.parse.quote
    found, notes = {}, []
    for kw in STATE_SEARCHES:
        code, ctype, body = state_get(f"{STATE_SITE}/jobs/search?page=1&query={q(kw)}")
        body = (body or "").replace("\\/", "/")
        slugs = state_links(body)
        if not slugs:
            notes.append(f"search '{kw}': HTTP {code}, {ctype or 'no type'}, {len(body)} chars, 0 job links")
        for slug in slugs:
            info = found.setdefault(slug, {"queries": set(), "snippet": ""})
            info["queries"].add(kw.lower())
            if not info["snippet"]:
                i = body.find("/jobs/" + slug)
                info["snippet"] = visible_text(body[i:i + 1500]) if i >= 0 else ""
        time.sleep(1)
    return found, notes


UUID_TAIL = re.compile(r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def state_slug_to_job(slug, info):
    """Build the job from its web address, e.g.
    customer-service-representative-2-remote-options-arizona-united-states-phoenix-<id>"""
    words = UUID_TAIL.sub("", slug)
    before, _, after = words.partition("-arizona-united-states")
    remote = False
    if before.endswith("-remote-options"):
        remote, before = True, before[: -len("-remote-options")]
    city = ""
    for name in sorted(AZ_CITIES, key=len, reverse=True):
        dashed = name.replace(" ", "-")
        if before.endswith("-" + dashed):
            city, before = name, before[: -len(dashed) - 1]
            break
    if not city:
        for name in AZ_CITIES:  # city sometimes comes after "arizona-united-states"
            if after.strip("-").startswith(name.replace(" ", "-")):
                city = name
                break
    if before.endswith("-various-statewide"):
        before = before[: -len("-various-statewide")]
    title = " ".join(w.upper() if w in ("ii", "iii", "iv") else w.capitalize()
                     for w in before.split("-") if w)
    if not title:
        return None
    snippet = info.get("snippet", "")
    queries = info.get("queries", set())
    health_hint = ""
    if queries & {"ahcccs", "medicaid"} or "ahcccs" in snippet.lower():
        health_hint = "AHCCCS Medicaid health plan. "
    agency = "AHCCCS" if "ahcccs" in (snippet.lower() + " " + " ".join(queries)) else "State of Arizona"
    lat = lon = None
    if city and not remote:
        lat, lon = AZ_CITIES[city]
    import hashlib
    return {
        "id": "az:" + hashlib.md5(slug.encode()).hexdigest()[:16],
        "title": title,
        "company": {"display_name": agency},
        "location": {"display_name": f"{'Remote options' if remote else city.title() or 'Statewide'}, Arizona",
                     "area": ["US", "Arizona", city.title() or "Statewide"]},
        "latitude": lat, "longitude": lon,
        "description": ("Remote options. " if remote else "") + health_hint + snippet,
        "redirect_url": f"{STATE_SITE}/jobs/{slug}",
        "salary_min": None, "salary_max": None, "salary_is_predicted": "0",
        "contract_time": None,
        "source": "AZ State Jobs (azstatejobs.gov)",
        "confirmed_open": True,  # it's in the state's live list right now
    }


def meta_tags(html):
    tags = {}
    for tag in re.findall(r"(?is)<meta\b[^>]*>", html):
        attrs = dict((k.lower(), v) for k, _, v in re.findall(r'([\w:-]+)\s*=\s*(["\'])(.*?)\2', tag))
        key = attrs.get("property") or attrs.get("name")
        if key and "content" in attrs:
            tags[key.lower()] = __import__("html").unescape(attrs["content"])
    return tags


def state_page_to_job(slug, page):
    meta = meta_tags(page)
    og_title = meta.get("og:title") or meta.get("twitter:title") or ""
    if not og_title:
        t = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
        og_title = clean(t.group(1)) if t else ""
    title, _, loc = og_title.rpartition(" - ")
    if not title:
        title, loc = og_title, ""
    if not title:
        return None
    text = visible_text(page)
    core = text.split("benefits:")[0] if len(text.split("benefits:")[0]) > 300 else text
    city = loc.split(",")[0].strip().title()
    remote = "remote" in (loc + " " + title).lower()
    if "ahcccs" in core:
        agency = "AHCCCS"
    else:
        first = (meta.get("og:description") or "").strip().split("\n")[0].strip()
        agency = first.title() if first and len(first) < 80 else "State of Arizona"
    lo = hi = None
    m = re.search(r"salary:?\s*(.{0,80}?)(?:grade|open until|closing|job summary|$)", core)
    if m and "$" in m.group(1):
        lo, hi = parse_google_salary(m.group(1))
        if lo and lo < 1000:
            lo, hi = lo * 2080, hi * 2080  # hourly amount
    lat = lon = None
    spot = AZ_CITIES.get(city.lower())
    if spot and not remote:
        lat, lon = spot
    import hashlib
    return {
        "id": "az:" + hashlib.md5(slug.encode()).hexdigest()[:16],
        "title": title.strip(),
        "company": {"display_name": agency},
        "location": {"display_name": f"{'Remote options' if remote else city or 'Statewide'}, Arizona",
                     "area": ["US", "Arizona", city or "Statewide"]},
        "latitude": lat, "longitude": lon,
        "description": ("Remote options. " if remote else "") + core,
        "redirect_url": f"{STATE_SITE}/jobs/{slug}",
        "salary_min": lo, "salary_max": hi, "salary_is_predicted": "0",
        "contract_time": "part_time" if "part-time" in core[:2000] and "full-time" not in core[:2000] else "full_time",
        "source": "AZ State Jobs (azstatejobs.gov)",
        "confirmed_open": True,  # it's in the state's live list right now
    }


def state_jobs(seen):
    """State job pages block scripts, so build each job from the search results."""
    found, notes = find_state_jobs()
    for n in notes:
        print("  AZ State Jobs " + n)
    if not found:
        raise RuntimeError("couldn't get the job list (details above)")
    jobs = []
    for slug, info in found.items():
        job = state_slug_to_job(slug, info)
        if job and job["id"] not in seen:
            jobs.append(job)
    print(f"AZ State Jobs: {len(found)} jobs in search results, {len(jobs)} not seen before.")
    return jobs

# ---------------------------------------------------------------------
#  "Is this job still open?" check
# ---------------------------------------------------------------------

CLOSED_PHRASES = [
    "no longer accepting applications", "not accepting applications",
    "this job has expired", "job has expired", "job posting has expired",
    "posting has expired", "this job is no longer available",
    "job is no longer available", "position is no longer available",
    "posting is no longer available", "this job is no longer open",
    "position has been filled", "job has been filled", "requisition has been filled",
    "this position is closed", "this job is closed", "job posting is closed",
    "this job has been closed", "posting has been closed", "job has been removed",
    "the job you are looking for is no longer", "the job you're looking for is no longer",
    "job you are trying to apply for has been filled", "this requisition is no longer",
    "applications are closed", "applications have closed", "has expired on indeed",
    "job not found", "we couldn't find this job", "job is not available",
]
CLOSED_URL_HINTS = ["expired", "jobnotfound", "job-not-found", "notfound",
                    "no-longer-available", "jobclosed", "job-closed", "error=404"]
# "Are you a real person?" pages (Cloudflare, Indeed, Imperva, DataDome, etc.)
BOT_CHECK_TEXT = [
    "verify you are human", "verifying you are human", "verify you're human",
    "confirm you are human", "are you a robot", "are you a human", "not a robot",
    "checking your browser", "checking if the site connection is secure",
    "just a moment...", "press & hold", "press and hold", "unusual traffic",
    "security check", "complete the security check", "enable javascript and cookies",
    "access denied", "request unsuccessful", "please verify you are a human",
    "attention required", "bot detection", "human verification",
]
BOT_CHECK_CODE = [
    "challenge-platform", "cf-chl", "cf_chl", "cf-turnstile", "captcha-delivery",
    "px-captcha", "_incapsula_resource", "perimeterx", "datadome",
]
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(url, timeout=15):
    """-> (http status or None, final url after redirects, page text)"""
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.geturl(), resp.read(800_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except Exception:
        return None, url, ""


def visible_text(html):
    """Drop scripts/styles (they often contain 'job expired' wording on every page)."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()


def is_bot_check(html, text):
    """True if we landed on a 'prove you're a person' page instead of the job."""
    raw = html.lower()
    if any(m in raw for m in BOT_CHECK_CODE):
        return True
    if any(p in text for p in BOT_CHECK_TEXT):
        return True
    # a tiny page with a captcha widget is a check page, not a job posting
    return len(text) < 1500 and ("captcha" in raw or "turnstile" in raw)


def workday_api_url(url):
    """Workday career pages load the job with JavaScript, so ask Workday directly."""
    u = urllib.parse.urlparse(url)
    if "myworkdayjobs.com" not in u.netloc:
        return None
    m = re.match(r"^/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/(job/.+)$", u.path)
    if not m:
        return None
    tenant = u.netloc.split(".")[0]
    return f"https://{u.netloc}/wday/cxs/{tenant}/{m.group(1)}/{m.group(2)}"


def link_status(url, title=""):
    """'closed', 'open' (we saw the actual job), or 'unknown' (blocked / couldn't tell)."""
    if not url:
        return "unknown"
    api = workday_api_url(url)
    if api:
        code, _, body = fetch(api)
        if code in (404, 410):
            return "closed"
        if code == 200:
            try:
                info = json.loads(body).get("jobPostingInfo") or {}
                return "closed" if info.get("canApply") is False else "open"
            except Exception:
                pass
    code, final, html = fetch(url)
    if code in (404, 410):
        return "closed"
    if code is None or code >= 400 or not html:
        return "unknown"
    if final != url:
        f = urllib.parse.urlparse(final)
        if any(h in (f.path + "?" + f.query).lower() for h in CLOSED_URL_HINTS):
            return "closed"
        if f.path.strip("/") == "" and urllib.parse.urlparse(url).path.strip("/"):
            return "closed"  # job link bounced to the site's home page
    text = visible_text(html)
    if is_bot_check(html, text):
        return "unknown"
    if any(p in text for p in CLOSED_PHRASES):
        return "closed"
    posting = job_posting_data(html)
    if posting is not None:
        ends = parse_date(posting.get("validThrough"))
        if ends and ends < datetime.now(timezone.utc):
            return "closed"  # the page's own "apply by" date has passed
        return "open"
    if title_on_page(title, text):
        return "open"
    return "unknown"  # page loaded, but we couldn't see the job on it


def job_posting_data(html):
    """Most job pages include hidden 'JobPosting' data for Google. Returns it, or None."""
    for block in re.findall(r'(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict):
                kind = item.get("@type")
                kinds = kind if isinstance(kind, list) else [kind]
                if "JobPosting" in kinds:
                    return item
    return None


def parse_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(value.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ARIZONA)


TITLE_STOPWORDS = {"and", "or", "the", "of", "a", "an", "to", "for", "in", "at", "with",
                   "i", "ii", "iii", "iv", "1", "2", "3", "sr", "jr", "full", "part", "time"}


def title_on_page(title, text):
    """Most of the job title's words appear on the page."""
    words = [w for w in re.findall(r"[a-z]+", (title or "").lower())
             if w not in TITLE_STOPWORDS and len(w) > 2]
    if not words:
        return False
    found = sum(1 for w in words if re.search(r"\b" + re.escape(w), text))
    return found / len(words) >= 0.75


def job_status(job):
    """Try up to 3 links for the job. Any open one wins and becomes the alert link."""
    if job.get("confirmed_open"):
        return "open"
    links = list(dict.fromkeys((job.get("apply_links") or []) + [job.get("redirect_url")]))
    results = []
    for link in [l for l in links if l][:3]:
        status = link_status(link, clean(job.get("title")))
        if status == "open":
            job["redirect_url"] = link
            return "open"
        results.append(status)
    return "closed" if results and all(r == "closed" for r in results) else "unknown"


def check_still_open(jobs):
    """Checks jobs in parallel; returns {id: status}."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = pool.map(job_status, jobs)
        return {str(j.get("id")): s for j, s in zip(jobs, statuses)}


def is_match(job):
    title = clean(job.get("title"))
    company = (job.get("company") or {}).get("display_name", "")
    text = " ".join([title, company, clean(job.get("description"))])
    if not TITLE_RE.search(title):
        return False
    if EXCLUDE_RE.search(title) or EXCLUDE_ANY_RE.search(text):
        return False
    if not pay_ok(job):
        return False
    if not location_ok(job):
        return False
    return bool(HEALTH_RE.search(text))


HOME_LAT, HOME_LON = 33.62, -112.18  # 85306, Glendale AZ


def miles_from_home(lat, lon):
    import math
    r = 3959
    p1, p2 = math.radians(HOME_LAT), math.radians(lat)
    dp, dl = p2 - p1, math.radians(lon - HOME_LON)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def location_ok(job):
    """Must be in Arizona; in-person/hybrid jobs must also be near Glendale."""
    loc = job.get("location") or {}
    places = " ".join(loc.get("area") or []) + " " + loc.get("display_name", "")
    remote = work_mode(job) == "Remote"
    if not re.search(r"\barizona\b|\baz\b", places, re.I):
        return remote and INCLUDE_REMOTE_OUT_OF_STATE
    if remote:
        return True
    miles = job_miles(job)
    if miles is None:
        return True  # no exact spot listed; Adzuna's radius search already limited it
    return miles <= LOCAL_RADIUS_MILES + 5


def job_miles(job):
    loc = job.get("location") or {}
    lat, lon = job.get("latitude"), job.get("longitude")
    if lat is None or lon is None or len(loc.get("area") or []) < 3:
        return None
    return miles_from_home(float(lat), float(lon))


def priority(job):
    """Closest in-person/hybrid jobs first, then ones with no exact spot, then remote."""
    if work_mode(job) == "Remote":
        return (2, 0)
    miles = job_miles(job)
    return (0, miles) if miles is not None else (1, 0)


def pay_ok(job):
    """Listed (not estimated) pay must reach MIN_HOURLY_PAY at the top of its range."""
    lo, hi = job.get("salary_min"), job.get("salary_max")
    listed = lo and str(job.get("salary_is_predicted")) != "1"
    if not listed:
        return KEEP_JOBS_WITHOUT_PAY
    top = (hi or lo) / 2080  # Adzuna gives yearly amounts
    return top >= MIN_HOURLY_PAY


def work_mode(job):
    text = (clean(job.get("title")) + " " + clean(job.get("description"))).lower()
    if "hybrid" in text:
        return "Hybrid"
    if re.search(r"\bremote\b|work from home|work-from-home|\bwfh\b", text):
        return "Remote"
    return "In person"


def pay_text(job):
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if not lo or str(job.get("salary_is_predicted")) == "1":
        return "Pay not listed"
    hi = hi or lo
    if lo == hi:
        return f"${lo / 2080:.0f}/hr (about ${lo / 1000:.0f}k/yr)"
    return f"${lo / 2080:.0f}-${hi / 2080:.0f}/hr (about ${lo / 1000:.0f}k-${hi / 1000:.0f}k/yr)"


def posted_text(job):
    if job.get("posted_label"):
        return "Posted " + job["posted_label"]
    try:
        dt = datetime.fromisoformat(job["created"].replace("Z", "+00:00")).astimezone(ARIZONA)
        return "Posted " + dt.strftime("%a %b %-d, %-I:%M %p")
    except Exception:
        return ""


def fingerprint(job):
    """Catches the same job reposted on several sites."""
    parts = [clean(job.get("title")), (job.get("company") or {}).get("display_name", "")]
    return "fp2:" + re.sub(r"[^a-z0-9]+", "", "|".join(parts).lower())


BILINGUAL_RE = re.compile(r"\b(bilingual|spanish)\b", re.I)


def send_alert(job):
    company = (job.get("company") or {}).get("display_name", "Company not listed")
    location = (job.get("location") or {}).get("display_name", "")
    mode = work_mode(job)
    miles = job_miles(job)
    where = f"{location} · {mode}"
    if mode != "Remote" and miles is not None:
        where += f" · about {miles:.0f} mi from home"
    lines = [company, where, pay_text(job)]
    if WATCH_RE.search(company + " " + clean(job.get("description"))):
        lines.insert(0, "🏥 Watched employer / insurance plan")
    text = clean(job.get("title")) + " " + clean(job.get("description"))
    if BILINGUAL_RE.search(text):
        lines.insert(0, "⭐ Bilingual / Spanish role — her Spanish is a plus")
    if job.get("contract_time"):
        lines.append(job["contract_time"].replace("_", " ").title())
    lines.append(posted_text(job))
    lines.append("Found on " + job.get("source", "Adzuna"))
    status = job.get("open_status")
    if status == "open":
        lines.insert(0, "✅ Confirmed open — the posting loaded")
    elif status == "unknown":
        lines.insert(0, "❓ Possibly expired — couldn't reach the posting to confirm")
    body = {
        "topic": NTFY_TOPIC,
        "title": (("❓ " if status == "unknown" else "") + clean(job.get("title")))[:120],
        "message": "\n".join(l for l in lines if l),
        "click": job.get("redirect_url", ""),
        "tags": ["briefcase"],
        "priority": 2 if status == "unknown" else 3,  # 2 = quiet, no sound or buzz
        "actions": [
            {"action": "view", "label": "Open posting", "url": job.get("redirect_url", "")},
            {"action": "view", "label": "Find on Google",
             "url": "https://www.google.com/search?" + urllib.parse.urlencode(
                 {"q": f"{clean(job.get('title'))} {company} {location} job", "ibp": "htl;jobs"})},
        ],
    }
    req = urllib.request.Request(
        "https://ntfy.sh/", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except Exception:
            pass
    return {}


def save_seen(seen):
    cutoff = time.time() - FORGET_AFTER_DAYS * 86400
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True))


def main():
    missing = [n for n, v in [("ADZUNA_APP_ID", APP_ID), ("ADZUNA_APP_KEY", APP_KEY),
                              ("NTFY_TOPIC", NTFY_TOPIC)] if not v]
    if missing:
        sys.exit("Missing secrets: " + ", ".join(missing))

    searches = [{"where": HOME_ZIP, "distance": round(LOCAL_RADIUS_MILES * 1.609)}]
    if INCLUDE_REMOTE_ARIZONA:
        searches.append({"where": "Arizona", "what_and": "remote"})
    if INCLUDE_REMOTE_OUT_OF_STATE:
        searches.append({"what_and": "remote"})

    jobs = {}
    failures = 0
    for params in searches:
        try:
            for job in adzuna_search(params):
                jobs[str(job.get("id"))] = job
        except Exception as e:
            failures += 1
            print(f"Adzuna search failed: {e}")

    google_count = 0
    if SERPAPI_KEY:
        google_searches = [(q, "Glendale, Arizona, United States", False) for q in GOOGLE_LOCAL_SEARCHES]
        if INCLUDE_REMOTE_ARIZONA or INCLUDE_REMOTE_OUT_OF_STATE:
            google_searches.append((GOOGLE_REMOTE_SEARCH, "Arizona, United States", True))
        for q, where, remote in google_searches:
            try:
                for job in google_search(q, where, remote):
                    jobs.setdefault(job["id"], job)
                    google_count += 1
            except Exception as e:
                failures += 1
                print(f"Google Jobs search failed ({q}): {e}")
        print(f"Google Jobs returned {google_count} recent postings.")

    if STATE_JOBS:
        try:
            state_found = state_jobs(load_seen())
            for job in state_found:
                jobs.setdefault(job["id"], job)
            print(f"AZ State Jobs returned {len(state_found)} postings to check.")
        except Exception as e:
            failures += 1
            print(f"AZ State Jobs search failed: {e}")

    if not jobs and failures:
        sys.exit("All searches failed; will try again next run.")

    seen = load_seen()
    new = [j for j_id, j in jobs.items()
           if is_match(j) and j_id not in seen and fingerprint(j) not in seen]
    new.sort(key=lambda j: j.get("created", ""), reverse=True)
    new.sort(key=priority)  # closest to Glendale first
    print(f"Checked {len(jobs)} postings, {len(new)} new matches.")
    skipped = [j for j in jobs.values() if not is_match(j)]
    if skipped:
        print("Some skipped jobs (for tuning):")
        for j in skipped[:20]:
            print(f"  - {clean(j.get('title'))} | {(j.get('company') or {}).get('display_name', '')}")

    now = time.time()
    if CHECK_IF_STILL_OPEN and new:
        to_check = new[:MAX_ALERTS_PER_RUN * 2]
        statuses = check_still_open(to_check)
        closed = [j for j in to_check if statuses[str(j.get("id"))] == "closed"]
        for j in to_check:
            j["open_status"] = statuses[str(j.get("id"))]
        for j in closed:
            seen[str(j.get("id"))] = now  # don't check it again tomorrow
        print(f"Still-open check: {sum(1 for s in statuses.values() if s == 'open')} confirmed open, "
              f"{sum(1 for s in statuses.values() if s == 'unknown')} possibly expired, "
              f"{len(closed)} closed/expired skipped.")
        for j in closed:
            print(f"  - closed: {clean(j.get('title'))} | {(j.get('company') or {}).get('display_name', '')}")
        new = [j for j in to_check if j["open_status"] != "closed"]
        new.sort(key=lambda j: j["open_status"] != "open")  # confirmed ones first

    sent = 0
    for job in new[:MAX_ALERTS_PER_RUN]:
        try:
            send_alert(job)
        except Exception as e:
            print(f"Alert failed, will retry next run: {e}")
            continue
        seen[str(job.get("id"))] = now
        seen[fingerprint(job)] = now
        sent += 1
        print(f"  Sent: {clean(job.get('title'))}")
        time.sleep(1)

    # Also remember non-matching jobs so we don't re-check them forever
    for j_id, job in jobs.items():
        if not is_match(job):
            seen.setdefault(j_id, now)

    save_seen(seen)
    print(f"Sent {sent} alert(s).")


if __name__ == "__main__":
    main()
