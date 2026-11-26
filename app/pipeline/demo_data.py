"""
Demo dataset. Everything here is FICTIONAL and labelled as such wherever it is shown:
companies use reserved ``.example`` domains, people and emails are invented, and
research signals are simulated (source says "simulated").

Two organisations demonstrate tenant isolation; each has an admin, an operator and an
approver, a campaign with an ICP and a follow-up sequence, and its own target accounts.
"""

from __future__ import annotations

import hashlib
import uuid

DEMO_LABEL = "DEMO DATA — fictional"


def demo_uuid(*parts: str) -> uuid.UUID:
    """Stable ids, so reloading the dataset never duplicates anything."""
    return uuid.UUID(hashlib.md5(("oxygen-demo:" + "/".join(parts)).encode()).hexdigest())


ORGS = [
    {
        "key": "northstar", "name": "Northstar Revenue Cloud (Demo)", "slug": "demo-northstar",
        "users": [("Avery Collins", "admin"), ("Jordan Patel", "operator"), ("Morgan Reyes", "approver")],
        "campaign": {
            "name": "Q1 Mid-market SaaS Outbound",
            "description": "Book discovery calls with revenue leaders at growing B2B SaaS companies.",
            "offer": ("Northstar Revenue Cloud researches target accounts, drafts personalised outreach for human "
                      "approval and tracks every reply, so a small sales team covers more accounts without "
                      "sending generic email."),
            "value_points": ["Account research in minutes instead of hours",
                             "Every message approved by a person before it is sent",
                             "Native CRM sync with Salesforce and HubSpot"],
            "icp": {"industries": ["Software", "Fintech", "Data & Analytics"], "min_employees": 50,
                    "max_employees": 1500, "countries": ["United Kingdom", "Germany", "Netherlands", "Ireland",
                                                         "United States"],
                    "technologies": ["Salesforce", "HubSpot", "Outreach", "Snowflake"],
                    "titles": ["VP Sales", "Head of Sales", "CRO", "Head of RevOps", "Sales Director", "Founder"],
                    "departments": ["Sales", "Revenue Operations", "Executive"], "min_score": 60},
            "sequence": [{"step": 1, "delay_days": 0, "angle": "Relevant initiative from research"},
                         {"step": 2, "delay_days": 3, "angle": "Add a useful insight"},
                         {"step": 3, "delay_days": 7, "angle": "Polite close-the-loop"}],
        },
    },
    {
        "key": "helix", "name": "Helix Clinical Systems (Demo)", "slug": "demo-helix",
        "users": [("Sam Okafor", "admin"), ("Riley Chen", "operator"), ("Taylor Brooks", "approver")],
        "campaign": {
            "name": "Hospital Groups — Patient Flow",
            "description": "Introduce patient-flow analytics to operations leaders at private hospital groups.",
            "offer": ("Helix Clinical Systems gives hospital operations teams a live view of bed capacity and "
                      "discharge delays, so they can plan staffing a day ahead."),
            "value_points": ["Live bed-capacity view across sites", "Discharge-delay alerts",
                             "Integrates with Epic and Cerner"],
            "icp": {"industries": ["Healthcare"], "min_employees": 200, "max_employees": 20000,
                    "countries": ["United Kingdom", "Ireland"], "technologies": ["Epic", "Cerner"],
                    "titles": ["COO", "Director of Operations", "Head of Patient Flow", "CIO"],
                    "departments": ["Operations", "Executive", "IT"], "min_score": 60},
            "sequence": [{"step": 1, "delay_days": 0, "angle": "Operational pressure from research"},
                         {"step": 2, "delay_days": 4, "angle": "Short case example"}],
        },
    },
]

# name, domain, industry, employees, city, country, technologies, funding, focus (used in signals), org key
COMPANIES = [
    ("Northwind Analytics", "northwind-analytics.example", "Data & Analytics", 240, "London", "United Kingdom",
     ["Salesforce", "Snowflake", "Outreach"], "Series B", "expansion into Germany and the Nordics", "northstar"),
    ("Ledgerly", "ledgerly.example", "Fintech", 120, "Dublin", "Ireland", ["HubSpot", "Stripe"], "Series A",
     "consolidating its sales tooling ahead of a Series B", "northstar"),
    ("Quarry Labs", "quarry-labs.example", "Software", 410, "Amsterdam", "Netherlands", ["Salesforce", "Gong"],
     "Series C", "a new enterprise sales team", "northstar"),
    ("Brightpath HR", "brightpath-hr.example", "Software", 320, "Manchester", "United Kingdom",
     ["HubSpot", "Outreach"], "Series B", "launching a mid-market product line", "northstar"),
    ("Cobalt Payments", "cobalt-payments.example", "Fintech", 780, "Berlin", "Germany", ["Salesforce", "Snowflake"],
     "Series C", "entering the French market", "northstar"),
    ("Tessellate AI", "tessellate-ai.example", "Software", 95, "Bristol", "United Kingdom", ["HubSpot"], "Seed",
     "hiring its first account executives", "northstar"),
    ("Meridian Cloud", "meridian-cloud.example", "Software", 1350, "Boston", "United States",
     ["Salesforce", "Outreach", "Snowflake"], "Growth", "moving from founder-led to team-led sales", "northstar"),
    ("Harbor Freight Logistics", "harbor-logistics.example", "Logistics", 2600, "Rotterdam", "Netherlands",
     ["SAP"], "Private", "automating warehouse scheduling", "northstar"),
    ("Pinecrest Retail", "pinecrest-retail.example", "Retail", 5200, "Leeds", "United Kingdom", ["Shopify"],
     "Public", "a loyalty programme relaunch", "northstar"),
    ("Atlas Metrics", "atlas-metrics.example", "Data & Analytics", 35, "Cork", "Ireland", ["HubSpot"], "Seed",
     "its first paid pilots", "northstar"),
    ("Verdant Energy Software", "verdant-energy.example", "Software", 560, "Munich", "Germany",
     ["Salesforce"], "Series B", "a partner-led go-to-market", "northstar"),
    ("Kestrel Security", "kestrel-security.example", "Software", 210, "Edinburgh", "United Kingdom",
     ["HubSpot", "Gong"], "Series A", "SOC 2 certification and US expansion", "northstar"),
    # Helix (healthcare) — a separate organisation's accounts
    ("St Aldric Hospital Group", "st-aldric.example", "Healthcare", 6400, "Birmingham", "United Kingdom",
     ["Epic"], "Private", "a new day-surgery centre", "helix"),
    ("Riverside Private Clinics", "riverside-clinics.example", "Healthcare", 1800, "Dublin", "Ireland", ["Cerner"],
     "Private", "extended weekend clinic hours", "helix"),
    ("Westmoor Care", "westmoor-care.example", "Healthcare", 950, "Bristol", "United Kingdom", ["Cerner"],
     "Private", "reducing delayed discharges", "helix"),
    ("Lumen Diagnostics", "lumen-diagnostics.example", "Medical Devices", 140, "Glasgow", "United Kingdom", [],
     "Series A", "a new imaging product", "helix"),
]

FIRST = ["Priya", "Marco", "Elena", "Sam", "Hannah", "Daniel", "Aisha", "Lucas", "Chloe", "Tomás", "Grace", "Omar",
         "Freya", "Noah", "Isla", "Ravi", "Zoe", "Felix", "Leah", "Ethan", "Maya", "Jonas", "Clara", "Ben", "Nina",
         "Arjun", "Sofia", "Owen", "Mila", "Theo", "Ruth", "Kai"]
LAST = ["Shah", "Rossi", "Novak", "Taylor", "Becker", "O'Neill", "Rahman", "Moreau", "Walsh", "García", "Lindqvist",
        "Haddad", "Morgan", "Kowalski", "Fraser", "Iyer", "Brennan", "Vogel", "Adeyemi", "Clarke", "Jansen", "Ito"]
ROLES_BY_INDUSTRY = {
    "default": [("VP Sales", "Sales", "VP"), ("Head of RevOps", "Revenue Operations", "Head"),
                ("Sales Development Manager", "Sales", "Manager")],
    "Healthcare": [("Director of Operations", "Operations", "Director"), ("Head of Patient Flow", "Operations", "Head"),
                   ("CIO", "IT", "C-level")],
    "Medical Devices": [("Head of Sales", "Sales", "Head"), ("Operations Manager", "Operations", "Manager")],
    "Logistics": [("Head of Procurement", "Procurement", "Head"), ("Operations Director", "Operations", "Director")],
    "Retail": [("Marketing Director", "Marketing", "Director"), ("Head of CRM", "Marketing", "Head")],
}
VERIFICATION = ["verified", "verified", "unverified", "guessed"]


def contacts_for(company: tuple) -> list[dict]:
    """The simulated Apollo people search result for one company (deterministic)."""
    name, domain, industry = company[0], company[1], company[2]
    roles = ROLES_BY_INDUSTRY.get(industry, ROLES_BY_INDUSTRY["default"])
    seed = int(hashlib.md5(domain.encode()).hexdigest(), 16)
    out = []
    for i, (title, dept, seniority) in enumerate(roles):
        first = FIRST[(seed + i * 7) % len(FIRST)]
        last = LAST[(seed // 3 + i * 5) % len(LAST)]
        if i == 0 and name == "Northwind Analytics":
            first, last = "Priya", "Shah"
        out.append({
            "external_id": f"demo-apollo-{domain}-{i}", "first_name": first, "last_name": last,
            "email": f"{first.lower()}.{last.lower().replace(chr(39), '').replace(' ', '')}@{domain}".replace("á", "a"),
            "title": title, "department": dept, "seniority": seniority, "location": f"{company[4]}, {company[5]}",
            "verification_status": VERIFICATION[(seed + i) % len(VERIFICATION)],
        })
    return out


SIGNAL_TYPES = {
    "news": "Company news",
    "technology": "Technology adoption",
    "expansion": "Business expansion",
    "hiring": "Hiring trend",
    "digital_transformation": "Digital transformation",
    "challenge": "Potential business challenge",
    "buying_signal": "Buying signal",
}


def signals_for(company: tuple, offer_topic: str) -> list[dict]:
    """Simulated research signals. Each one is labelled simulated, has a source and says why it matters."""
    name, domain, industry, employees, city, country, tech, funding, focus = company[:9]
    src = "Demo research provider (simulated)"
    sig = [
        ("news", f"{name} announced {focus}.", f"https://{domain}/news (simulated press release)",
         f"A visible initiative gives a specific, non-generic reason to reach out now about {offer_topic}.", "strong"),
        ("hiring", f"{name} lists {3 + employees % 7} open roles in sales and operations.",
         f"https://{domain}/careers (simulated job board)",
         "Hiring in go-to-market roles usually means new process and tooling decisions in the next quarter.",
         "moderate"),
        ("technology", f"{name} uses {', '.join(tech) if tech else 'no tracked tools'}.",
         f"https://{domain}/integrations (simulated technographics)",
         "Technology overlap shows whether our product fits their stack without a migration.",
         "moderate" if tech else "weak"),
    ]
    if employees >= 200:
        sig.append(("expansion", f"{name} opened an office in a new city this year ({city} region growth).",
                    f"https://{domain}/about (simulated)",
                    "Expansion strains existing processes; teams look for leverage rather than headcount.", "moderate"))
    if funding in ("Series A", "Series B", "Series C", "Growth"):
        sig.append(("buying_signal", f"{name} raised a {funding} round in the last 12 months.",
                    "Simulated funding database",
                    "New funding comes with growth targets and budget for go-to-market tooling.", "strong"))
    sig.append(("digital_transformation", f"{name}'s leadership talks publicly about data-driven operations.",
                f"https://{domain}/blog (simulated)",
                "Stated data-driven goals make an evidence-based pitch more credible.", "weak"))
    sig.append(("challenge", f"Reviews mention slow response times as {name} grows.",
                "Simulated review site",
                "A pain point we can address directly — but it is an inference, so treat it as a hypothesis.", "weak"))
    return [{"type": t, "label": SIGNAL_TYPES[t], "statement": s, "source": src, "source_ref": ref,
             "why_it_matters": why, "strength": strength, "demo": True} for t, s, ref, why, strength in sig]


# Response intelligence knowledge base. Answers may ONLY use these approved statements;
# a question that matches nothing is escalated to a person instead of being answered.
KNOWLEDGE_BASE = {
    "northstar": [
        {"id": "kb-pricing", "topic": "Pricing", "keywords": ["price", "pricing", "cost", "how much", "licen"],
         "answer": "Pricing is per seat per month with an annual plan; a pilot for one team is fixed-price for "
                   "six weeks so you can judge results before committing."},
        {"id": "kb-crm", "topic": "CRM integration", "keywords": ["salesforce", "hubspot", "crm", "integrat"],
         "answer": "Northstar syncs contacts, activities and replies with Salesforce and HubSpot both ways; setup "
                   "takes about a day with an admin."},
        {"id": "kb-security", "topic": "Security", "keywords": ["security", "soc 2", "soc2", "gdpr", "data protection",
                                                               "privacy"],
         "answer": "Northstar is SOC 2 Type II audited, hosts data in the EU for EU customers and signs a GDPR "
                   "data processing agreement."},
        {"id": "kb-approval", "topic": "Human approval", "keywords": ["approve", "control", "brand", "tone",
                                                                     "automatic", "spam"],
         "answer": "Nothing is sent without a person approving it; reviewers can edit every message and see the "
                   "evidence behind it."},
        {"id": "obj-budget", "topic": "Budget objection", "objection": True,
         "keywords": ["budget", "too expensive", "no money", "cost too", "spend freeze"],
         "answer": "Understood. Many teams start with the fixed-price pilot for a single team, which usually comes "
                   "from an existing tooling budget rather than new spend."},
        {"id": "obj-competitor", "topic": "Already have a tool", "objection": True,
         "keywords": ["already use", "already have", "we use", "happy with", "outreach.io", "competitor"],
         "answer": "That makes sense. Northstar sits before your sequencing tool: it does the account research and "
                   "first draft, then hands approved messages to the tool you already use."},
        {"id": "obj-timing", "topic": "Timing objection", "objection": True,
         "keywords": ["no time", "busy", "bandwidth", "not a priority", "too much on"],
         "answer": "Completely fair. Setup takes about a day; if timing is the issue I'm happy to reconnect "
                   "when things are quieter."},
    ],
    "helix": [
        {"id": "kb-ehr", "topic": "EHR integration", "keywords": ["epic", "cerner", "ehr", "integrat"],
         "answer": "Helix reads bed and discharge data from Epic and Cerner through their standard interfaces; "
                   "no change to clinical workflows is needed."},
        {"id": "kb-ig", "topic": "Information governance", "keywords": ["dspt", "governance", "nhs", "gdpr",
                                                                       "privacy", "security"],
         "answer": "Helix meets the NHS Data Security and Protection Toolkit standard and keeps data in UK data "
                   "centres."},
        {"id": "obj-budget-h", "topic": "Budget objection", "objection": True,
         "keywords": ["budget", "too expensive", "funding", "cost"],
         "answer": "Understood. Most groups start with one site for a quarter, priced against the cost of the "
                   "delayed discharges it removes."},
    ],
}

# Reply templates the presenter can trigger in the simulated inbox (the classifier sees only the text).
REPLY_TEMPLATES = {
    "interested": "Thanks — this sounds interesting and relevant to what we're working on. Tell me more.",
    "not_interested": "Thanks for reaching out, but we're not interested at the moment.",
    "meeting_request": "Happy to talk. Could you send times next week for a 30-minute call?",
    "question": "Quick question before we talk: how does it integrate with our CRM, and how long does setup take?",
    "unsupported_question": "Do you support on-premise deployment inside our own data centre in Singapore?",
    "objection": "We already use Outreach for sequencing, so I'm not sure we need another tool.",
    "budget_objection": "Honestly the budget is tight this year and this looks too expensive for us.",
    "out_of_office": "I'm out of office until next week with limited access to email. I'll be back on Monday.",
    "unsubscribe": "Please remove me from your mailing list and unsubscribe me from future emails.",
}
