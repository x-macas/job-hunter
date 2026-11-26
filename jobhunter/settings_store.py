"""All the rules you can change on the Settings page.

Each setting has a type, a default and limits. The defaults here are
general. Your own choices are stored in the database, so they never
appear in the code on GitHub.
"""

import json
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    group: str
    kind: str  # "int", "list", "bool" or "text"
    default: object
    help: str = ""
    min: int | None = None
    max: int | None = None
    choices: tuple = field(default_factory=tuple)


SETTINGS = [
    # Jobs
    Setting("roles", "Roles to look for", "Jobs", "list", [
        "Software Engineer", "Software Development Engineer", "Software Developer",
        "Software Dev Engineer", "Application Developer", "Technology Analyst",
        "SDE", "SWE", "Member of Technical Staff",
        "Backend Developer", "Backend Engineer", "Forward Deployed Engineer",
        "Python Developer", "Java Developer", "Node.js Developer", "C++ Developer",
        "Platform Engineer", "Infrastructure Engineer", "Graduate Software Engineer",
        "Associate Software Engineer", "Trainee Software Engineer",
        "Full Stack Developer", "AI Engineer", "LLM Engineer",
        "Solutions Engineer", "Applications Engineer",
    ], "One per line. A job title must contain one of these."),
    Setting("title_blocklist", "Hide titles with these words", "Jobs", "list", [
        "Senior", "Sr.", "Lead", "Staff", "Principal", "Manager", "Director",
        "Architect", "Head of", "VP", "AVP", "Vice President", "QA", "Test Engineer",
        "Support", "Sales",
        "Frontend",
    ], "One per line."),
    Setting("max_experience_years", "Most years of experience a job can ask for", "Jobs",
            "int", 1, min=0, max=10),
    Setting("hide_level_2_plus", "Hide level II+ titles (e.g. 'SDE 2', 'Engineer III')", "Jobs",
            "bool", True, "These usually need 2+ years."),
    Setting("locations", "Locations", "Jobs", "list",
            ["India", "Remote"], "One per line. 'Remote' covers remote jobs anywhere."),
    Setting("max_job_age_days", "Hide jobs older than (days)", "Jobs", "int", 7, min=1, max=60),
    Setting("daily_top_n", "Jobs on the Today page", "Jobs", "int", 20, min=5, max=100),
    Setting("min_score", "Lowest Claude score to show (0-100)", "Jobs", "int", 30,
            "Jobs scored below this stay off your list. Jobs you add yourself always show.",
            min=0, max=100),
    Setting("other_companies_fulltime_only", "Other companies: full-time jobs only", "Jobs",
            "bool", True, "Big companies can show internships, part-time and contract too."),
    Setting("skills", "Your skills", "Jobs", "list", [],
            "One per line. Jobs using these get a higher score."),

    # Pay
    Setting("min_lpa_fulltime", "Lowest pay for full-time jobs (LPA)", "Pay", "int", 10,
            "Only used when the job lists a salary.", min=0, max=200),
    Setting("min_lpa_startup_service", "Lowest pay for startups and service companies (LPA)",
            "Pay", "int", 12, "Jobs with no salary listed are still shown, ranked lower.",
            min=0, max=200),
    Setting("usd_to_inr", "1 US dollar in rupees (for dollar salaries)", "Pay", "int", 88,
            min=50, max=200),

    # Companies
    Setting("big_companies", "Good companies (most pay freshers ₹15 LPA+)", "Companies", "list", [
        # Big tech and product companies
        "Google", "Microsoft", "Amazon", "Apple", "Meta", "Netflix", "Adobe", "Salesforce",
        "Oracle", "SAP", "SAP Labs", "Atlassian", "Uber", "LinkedIn", "Intuit", "PayPal", "ServiceNow",
        "Nutanix", "Rubrik", "Cohesity", "Databricks", "Snowflake", "MongoDB", "Datadog",
        "Cloudflare", "Okta", "Zscaler", "Palo Alto Networks", "Arista Networks", "VMware",
        "Broadcom", "Cisco", "Nvidia", "Qualcomm", "Intel", "AMD", "Texas Instruments",
        "Cadence", "Synopsys", "Samsung", "Expedia", "Agoda", "Grab", "Twilio", "Stripe",
        "Coinbase", "Airbnb", "Pinterest", "Reddit", "Dropbox", "OpenAI", "Anthropic", "Elastic",
        "GitLab", "Visa", "Mastercard", "American Express", "Walmart Global Tech", "Rippling",
        "Harness", "Postman", "BrowserStack", "Sprinklr", "Media.net", "Trilogy Innovations",
        "CodeNation", "Tekion",
        # Trading and finance
        "D. E. Shaw", "Arcesium", "Tower Research Capital", "Graviton Research Capital",
        "Quadeye", "Optiver", "IMC Trading", "Squarepoint Capital", "AlphaGrep", "Jane Street",
        "Citadel", "Goldman Sachs", "Morgan Stanley", "JPMorgan Chase", "J.P. Morgan",
        "Deutsche Bank", "Barclays", "UBS",
        # Indian product companies
        "Flipkart", "Myntra", "Swiggy", "Zomato", "Eternal", "Blinkit", "PhonePe", "Razorpay",
        "CRED", "Meesho", "Zepto", "Groww", "Dream11", "ShareChat", "Navi Technologies",
        "Paytm", "Ola Cabs", "Ola Electric", "Juspay", "InMobi", "Glance", "Zeta", "slice",
        "Urban Company", "Udaan", "Upstox", "Zerodha", "Freshworks", "Druva", "Innovaccer",
        # More companies reported at 15+ LPA for freshers
        "Workday", "Autodesk", "PTC", "Siemens Digital Industries Software", "Ansys", "Dassault Systemes",
        "MathWorks", "Zebra Technologies", "NetApp", "Pure Storage", "Commvault", "Veritas",
        "Akamai", "F5", "Fortinet", "Check Point", "CrowdStrike", "Juniper Networks", "Ciena",
        "Red Hat", "IBM", "Hewlett Packard Enterprise", "HP", "Dell Technologies", "Motorola Solutions",
        "Citrix", "Splunk", "Zendesk", "Confluent", "Couchbase", "HashiCorp", "New Relic",
        "ThoughtSpot", "Chargebee", "Hasura", "Atlan", "Rocketlane", "Whatfix", "Darwinbox",
        "Icertis", "HighRadius", "LeadSquared", "Mindtickle", "Gupshup", "Yellow.ai", "Observe.AI",
        "Uniphore", "MoEngage", "CleverTap", "WebEngage", "Netcore Cloud", "Capillary Technologies",
        "PubMatic", "Zeotap", "Sigmoid", "Hevo Data", "Kissflow", "Zoho", "Salesloft", "Gojek",
        "DoorDash", "Booking.com", "Twitter", "Spotify", "Wayfair", "Target", "Lowe's", "Tesco",
        "Sabre", "Amadeus", "Thomson Reuters", "Bloomberg", "LSEG", "S&P Global", "MSCI", "Moody's",
        "Gartner", "Rakuten", "Sony", "LG Soft India", "Nokia", "Ericsson", "Siemens Healthineers",
        "Philips", "GE HealthCare", "Mercedes-Benz Research and Development India", "Harman",
        "Aptiv", "Arm", "Analog Devices", "Marvell", "Micron", "Western Digital", "Seagate",
        "NXP", "Infineon", "STMicroelectronics", "Renesas", "MediaTek", "Microchip", "KLA",
        "Applied Materials", "Lam Research", "Keysight", "Sandisk", "Citi", "HSBC", "Wells Fargo",
        "BNY", "Nomura", "BlackRock", "Fidelity Investments", "State Street", "Societe Generale",
        "BNP Paribas", "Standard Chartered", "Natwest", "Lloyds Banking Group", "Northern Trust",
        "Invesco", "Millennium", "WorldQuant", "Dolat Capital", "iRage", "Plutus Research",
        "NK Securities", "Futures First", "QuantBox", "Trexquant", "Edelweiss", "Kotak Mahindra Bank",
        "HDFC Bank", "ICICI Bank", "Rapido", "Nykaa", "Lenskart", "Licious", "BigBasket", "Dunzo",
        "MPL", "Games24x7", "Pocket FM", "Kuku FM", "Dailyhunt", "Unacademy", "Physics Wallah",
        "upGrad", "Scaler", "Eruditus", "MakeMyTrip", "ixigo", "Cleartrip", "OYO", "Delhivery",
        "Shiprocket", "Porter", "Ajio", "Reliance Jio", "Tata Digital", "Airtel", "Cars24",
        "Spinny", "CarDekho", "PolicyBazaar", "Acko", "Digit Insurance", "Ather Energy", "INDmoney",
        "Jupiter", "Fi Money", "Jar", "smallcase", "Cashfree Payments", "Setu", "M2P Fintech",
        "Perfios", "Pine Labs", "BharatPe", "MobiKwik", "PayU", "CoinDCX", "CoinSwitch", "Polygon Labs",
        "Sarvam AI", "Krutrim", "Haptik", "Pocket Aces", "Apna", "NoBroker", "Housing.com",
        "Magicpin", "Cult.fit", "HealthifyMe", "Practo", "PharmEasy", "Tata 1mg", "Info Edge",
        "Naukri", "Urban Ladder", "Pepperfry", "Fampay", "Khatabook", "OkCredit", "Vedantu",
        "Classplus", "Teachmint", "Testbook", "Simplilearn", "Turing", "Toplyne", "Atomicwork",
        "Composio", "LambdaTest", "Testsigma", "DeepSource", "Appsmith", "ToolJet", "SpotDraft",
        "Deel", "Multiplier", "Skuad",
        # Health tech, banks, GCCs and others added on 2026-11-27 (pay is roughly 12-20 LPA; check each job)
        "Optum", "UnitedHealth Group", "ModMed", "Athenahealth", "eClinicalWorks", "McKesson",
        "Carelon", "Evernorth", "CVS Health", "Veradigm", "Medtronic", "Abbott", "Becton Dickinson",
        "Thermo Fisher Scientific", "Stryker", "Komodo Health", "Qure.ai", "Wolters Kluwer",
        "Elsevier", "Novartis", "AstraZeneca", "Sanofi", "Eli Lilly", "Amgen", "MSD", "Zenoti",
        "Pristyn Care", "MediBuddy", "Veeva Systems", "Bank of America", "Capital One", "Synchrony",
        "Fiserv", "FIS", "Discover Financial", "Macquarie", "Nasdaq", "Intercontinental Exchange",
        "CME Group", "Broadridge", "Morningstar", "FactSet", "Clearwater Analytics", "Addepar",
        "Franklin Templeton", "Swiss Re", "Chubb", "Travelers", "Guidewire", "Verisk", "Angel One",
        "KreditBee", "Moneyview", "OneCard", "Easebuzz", "Uni Cards", "Fibe", "Netskope", "Sumo Logic",
        "Infoblox", "Gainsight", "Automation Anywhere", "DocuSign", "Zoom", "Glean", "Sprinto",
        "Zluri", "Plivo", "Exotel", "Locus", "Shipsy", "FarEye", "Tally Solutions", "Highspot",
        "Extreme Networks", "Veeam", "Qualys", "Sophos", "Trellix", "Tenable", "Barracuda", "Cloudera",
        "Teradata", "Informatica", "Alteryx", "MicroStrategy", "Amagi", "Leena AI", "Unbxd", "Vue.ai",
        "Fyle", "Hiver", "Yubi", "Rambus", "Silicon Labs", "onsemi", "SiFive", "Tenstorrent",
        "Ampere Computing", "Lattice Semiconductor", "JioStar", "Times Internet", "Winzo", "Zupee",
        "Zetwerk", "Infra.Market", "OfBusiness", "Moglix", "Jumbotail", "BlackBuck", "FreshToHome",
        "Country Delight", "Mercari", "Sainsbury's", "Marks and Spencer", "Nike", "Lululemon",
        "Albertsons", "eBay", "Electronic Arts", "Take-Two Interactive",
    ], "One per line. They get a score boost, saved spots, and any job type. Pay changes yearly: edit freely."),
    Setting("big_company_boost", "Score boost for big companies", "Companies", "int", 15,
            min=0, max=50),
    Setting("big_company_spots", "Spots kept for big companies on the Today page",
            "Companies", "int", 10, min=0, max=100),
    Setting("blocked_companies", "Never show or email", "Companies", "list", [],
            "One per line."),
    Setting("hidden_companies", "Hidden from job lists", "Companies", "list", [],
            "Filled when you skip a job with 'Not this company'. Delete a line to see them again."),
    Setting("extra_boards", "Extra company job boards", "Companies", "list", [],
            "One per line, like: greenhouse:companyslug Company Name (also lever:, ashby:, smartrecruiters:, workable:)."),
    Setting("watch_list", "Watch list (instant alert)", "Companies", "list", [],
            "One per line. New matching jobs here send a Telegram alert right away."),
    Setting("watch_interval_minutes", "Check company sites for new jobs every (minutes)", "Companies", "int",
            30, min=15, max=720,
            help="Reads the newest jobs on every company site and sends a Telegram alert for new ones that fit."),
    Setting("alerts_per_check", "Most job alerts per check", "Companies", "int", 8, min=0, max=30,
            help="If more new jobs fit, you get one line saying how many more are on the website."),
    Setting("alert_min_score", "Only alert for jobs scoring at least", "Companies", "int", 45, min=0, max=100,
            help="Jobs below this still show on the website; they just don't send a Telegram message."),
    Setting("hand_check_reminder", "Remind me on Telegram to check companies by hand", "Companies", "bool", True,
            help="Sent at the evening nudge time if some companies on the Check page aren't ticked yet."),
    Setting("alert_max_age_days", "Only alert for jobs posted in the last (days)", "Companies", "int",
            2, min=1, max=7),
    Setting("sitemap_hours", "Read job lists (sitemaps) of companies like Optum and Nomura every (hours)",
            "Companies", "int", 12, min=4, max=48,
            help="These sites ask bots to stay off their job search but list every job in a sitemap. "
                 "It's a big file and the sites update it about once a day, so twice a day is plenty."),

    # Emails
    Setting("email_daily_cap", "Most emails per day", "Emails", "int", 15, min=0, max=40),
    Setting("approvals_per_kind", "Approvals needed per email kind before auto-send",
            "Emails", "int", 2, min=1, max=50),
    Setting("followup_1_days", "First follow-up after (days)", "Emails", "int", 5,
            min=1, max=30),
    Setting("followup_2_days", "Second follow-up after (days)", "Emails", "int", 10,
            min=2, max=60),

    # Daily routine
    Setting("run_after_hour", "Daily run: first laptop start after (hour, 0-23)",
            "Daily routine", "int", 9, min=0, max=23),
    Setting("goal_applications", "Daily goal: applications", "Daily routine", "int", 3,
            min=0, max=50),
    Setting("goal_emails", "Daily goal: emails", "Daily routine", "int", 5, min=0, max=50),
    Setting("nudge_hour", "Evening nudge at (hour, 0-23)", "Daily routine", "int", 19,
            min=0, max=23),

    # AI
    Setting("ai_model", "Claude model for scoring jobs", "AI", "text", "haiku",
            "haiku uses the least of your Claude plan. sonnet is smarter but uses more.",
            choices=("haiku", "sonnet", "opus")),
    Setting("ai_jobs_per_run", "Most jobs Claude scores per run", "AI", "int", 60,
            "Keeps your Claude plan usage low.", min=5, max=300),

    # Backup
    Setting("backup_dir", "Backup folder", "Backup", "text", "~/job-hunter-backups",
            "A folder on this laptop."),
    Setting("backup_keep", "Backups to keep", "Backup", "int", 30, min=1, max=365),
]

BY_KEY = {s.key: s for s in SETTINGS}
GROUPS = list(dict.fromkeys(s.group for s in SETTINGS))

# The emergency stop. Not on the Settings form; it has its own button.
PAUSED_KEY = "paused"


def get(conn, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return json.loads(row["value"])
    if key == PAUSED_KEY:
        return False
    return BY_KEY[key].default


def get_all(conn) -> dict:
    return {s.key: get(conn, s.key) for s in SETTINGS}


def put(conn, key: str, value) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), time.time()),
    )


def parse_form(form) -> tuple[dict, dict]:
    """Turn the Settings form into clean values.

    Returns (values, errors). Nothing is saved if there are any errors.
    """
    values, errors = {}, {}
    for s in SETTINGS:
        raw = form.get(s.key, "")
        if s.kind == "bool":
            values[s.key] = raw == "on"
        elif s.kind == "list":
            items = [line.strip() for line in raw.splitlines()]
            values[s.key] = list(dict.fromkeys(i for i in items if i))[:500]
        elif s.kind == "text":
            text = raw.strip()[:300]
            if s.choices and text not in s.choices:
                errors[s.key] = "Choose one of: " + ", ".join(s.choices)
            else:
                values[s.key] = text
        elif s.kind == "int":
            try:
                number = int(raw.strip())
            except ValueError:
                errors[s.key] = "Enter a whole number."
                continue
            if s.min is not None and number < s.min:
                errors[s.key] = f"Must be at least {s.min}."
            elif s.max is not None and number > s.max:
                errors[s.key] = f"Must be at most {s.max}."
            else:
                values[s.key] = number
    if (
        "followup_1_days" in values
        and "followup_2_days" in values
        and values["followup_2_days"] <= values["followup_1_days"]
    ):
        errors["followup_2_days"] = "Must be later than the first follow-up."
    return values, errors
