"""
Tali's job alerts
Checks Adzuna for new medical front office / medical insurance jobs and
sends each new match to the ntfy app on your phones.
Runs on GitHub Actions (see .github/workflows/job-alerts.yml).
"""

import json
import os
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
MAX_DAYS_OLD = 3              # only look at postings from the last few days

# Words sent to Adzuna to pull candidate jobs (any one can match)
SEARCH_WORDS = [
    "medical", "insurance", "billing", "claims", "receptionist", "front desk",
    "patient", "authorization", "eligibility", "verification", "scheduler",
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
]

# Title OR description must mention one of these (keeps out car/home insurance etc.)
HEALTH_WORDS = [
    "medical", "health", "healthcare", "patient", "clinic", "hospital",
    "dental", "pharmacy", "physician", "medicare", "medicaid", "provider",
    "hmo", "ppo", "urgent care", "surgery", "pediatric", "dermatology",
    "orthopedic", "cardiology", "behavioral health", "chiropractic",
    "optometry", "vision", "eye care", "physical therapy",
    "imaging", "radiology", "laboratory", "family practice",
]

# Skip jobs whose TITLE contains any of these (need licenses/degrees, or not a fit)
EXCLUDE_TITLE = [
    "nurse", "rn", "lpn", "lvn", "physician", "doctor", "pharmacist",
    "therapist", "director", "manager", "coder", "coding", "sales",
    "insurance agent", "licensed agent", "dentist", "hygienist", "surgeon",
    "attorney", "actuary", "underwriter", "engineer", "developer",
]

# Skip jobs whose title OR description mentions any of these (animal care)
EXCLUDE_ANYWHERE = [
    "veterinary", "veterinarian", "vet clinic", "vet hospital", "animal hospital",
    "animal clinic", "animal care", "pet hospital", "pet care", "banfield", "vca",
]

MAX_ALERTS_PER_RUN = 10       # extras wait for the next run instead of spamming
FORGET_AFTER_DAYS = 45        # how long to remember jobs already sent

# =====================================================================

APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

SEEN_FILE = Path(__file__).with_name("seen_jobs.json")
ARIZONA = timezone(timedelta(hours=-7))  # Arizona has no daylight saving


def word_pattern(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


TITLE_RE = word_pattern(TITLE_KEYWORDS)
HEALTH_RE = word_pattern(HEALTH_WORDS)
EXCLUDE_RE = word_pattern(EXCLUDE_TITLE)
EXCLUDE_ANY_RE = word_pattern(EXCLUDE_ANYWHERE)


def clean(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def adzuna_search(extra_params):
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "results_per_page": 50,
        "what_or": " ".join(SEARCH_WORDS),
        "max_days_old": MAX_DAYS_OLD,
        "sort_by": "date",
        "content-type": "application/json",
    }
    params.update(extra_params)
    url = "https://api.adzuna.com/v1/api/jobs/us/search/1?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("results", [])


def is_match(job):
    title = clean(job.get("title"))
    text = title + " " + clean(job.get("description"))
    if not TITLE_RE.search(title):
        return False
    if EXCLUDE_RE.search(title) or EXCLUDE_ANY_RE.search(text):
        return False
    return bool(HEALTH_RE.search(text))


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
    try:
        dt = datetime.fromisoformat(job["created"].replace("Z", "+00:00")).astimezone(ARIZONA)
        return "Posted " + dt.strftime("%a %b %-d, %-I:%M %p")
    except Exception:
        return ""


def fingerprint(job):
    """Catches the same job reposted on several sites."""
    parts = [clean(job.get("title")), (job.get("company") or {}).get("display_name", ""),
             (job.get("location") or {}).get("display_name", "")]
    return "fp:" + re.sub(r"[^a-z0-9]+", "", "|".join(parts).lower())


def send_alert(job):
    company = (job.get("company") or {}).get("display_name", "Company not listed")
    location = (job.get("location") or {}).get("display_name", "")
    lines = [company, f"{location} · {work_mode(job)}", pay_text(job)]
    if job.get("contract_time"):
        lines.append(job["contract_time"].replace("_", " ").title())
    lines.append(posted_text(job))
    body = {
        "topic": NTFY_TOPIC,
        "title": clean(job.get("title"))[:120],
        "message": "\n".join(l for l in lines if l),
        "click": job.get("redirect_url", ""),
        "tags": ["briefcase"],
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

    jobs = {}
    for params in searches:
        try:
            for job in adzuna_search(params):
                jobs[str(job.get("id"))] = job
        except Exception as e:
            sys.exit(f"Adzuna search failed: {e}")

    seen = load_seen()
    new = [j for j_id, j in jobs.items()
           if is_match(j) and j_id not in seen and fingerprint(j) not in seen]
    new.sort(key=lambda j: j.get("created", ""), reverse=True)
    print(f"Checked {len(jobs)} postings, {len(new)} new matches.")

    now = time.time()
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
