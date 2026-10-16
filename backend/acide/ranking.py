"""Priority: one number that says which postings to read first.

The two fit vectors answer "can you do it" and "does it move you where you
want to go". Neither answers the operator's actual question with seventeen
thousand postings open: *which ones do I look at first?* That depends on
things neither vector sees — how much weight the employer carries, whether
the role is one a graduate can be hired into, whether a passport rules it
out. So priority is a third, separate score, built from the operator's own
rubric. The rules use a fixed one (organization 25 · sector & mission 25 ·
CV fit 20 · growth 15 · impact 10 · feasibility 5); the review uses whatever
the brief defines, and reports each criterion with its own maximum.

It comes in two passes.

* **Rules** (`score_posting`) — keyword and table work, no network, a few
  seconds for the whole table. Every open posting has one. It is good at
  sinking what is plainly wrong (a sales role, a senior role, a US-citizens
  role, a lathe operator at a famous name) and at floating the employers and
  role families the rubric favours.
* **Review** (`review_top`) — the operator's written brief, sent with one
  posting at a time to the configured model. Its score replaces the rules
  score for that posting, with a reason and the problems to check. The rules
  first exclude what is not worth a call (a visa, a senior or sales role, an
  eligibility blocker); a search run reviews the new postings, and a change
  of brief makes the earlier reviews stale until they are run again.

The operator adds two things of their own. Filters (exclusions.py) exclude
a posting whose title, company or location has a word they never want to
see, before any of this. And the stars and removals they make teach the
review (calibration.py): every fifty, the model turns them into guidelines
that ride in the prompt between the brief and the instructions.

The fit vectors are left alone: priority neither reads nor writes them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import db, exclusions, geo, paths

logger = logging.getLogger(__name__)

#: Bump when the rules change, so every posting is ranked again.
RULES_VERSION = "7"

# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
#: Points for criterion 1, by tier.
TIER_POINTS = {1: 25, 2: 18, 3: 10}

#: Points for criterion 2, by sector. Space and European defence first, then
#: critical infrastructure, cyber and frontier AI, then everything else.
SECTOR_POINTS = {
    "space": 25,
    "defence": 23,
    "institution": 17,
    "infra": 16,
    "cyber": 16,
    "ai": 15,
    "deeptech": 13,
    "research": 10,
    "other": 6,
}

SECTOR_LABELS = {
    "space": "spazio",
    "defence": "difesa",
    "institution": "istituzioni UE",
    "infra": "infrastrutture critiche",
    "cyber": "cybersecurity",
    "ai": "AI",
    "deeptech": "deep tech",
    "research": "ricerca",
    "other": "altro",
}

#: (pattern over the company name, tier, sector). First match wins, so the
#: specific entries (Thales Alenia Space) come before the general (Thales).
_ORGANIZATIONS: tuple[tuple[str, int, str], ...] = (
    # --- Tier 1: the bodies and primes the brief names -----------------------
    (r"\bnato\b|\bncia\b", 1, "defence"),
    (r"\besa\b|european space agency", 1, "space"),
    (r"\beuspa\b", 1, "space"),
    (r"european defence agency|\beda\b", 1, "defence"),
    (r"\benisa\b", 1, "cyber"),
    (r"cert-eu", 1, "cyber"),
    (r"satcen|satellite centre", 1, "space"),
    (r"epso|eu careers|european commission|\beeas\b", 1, "institution"),
    (r"cybersecurity competence centre|\beccc\b", 1, "cyber"),
    (r"thales alenia", 1, "space"),
    (r"telespazio", 1, "space"),
    (r"aerospacelab", 1, "space"),
    (r"\bohb\b", 1, "space"),
    (r"^ses\b", 1, "space"),
    (r"eutelsat", 1, "space"),
    (r"hispasat", 1, "space"),
    (r"airbus", 1, "defence"),
    (r"thales", 1, "defence"),
    (r"leonardo", 1, "defence"),
    (r"\bindra\b", 1, "defence"),
    (r"anthropic|openai|deepmind|mistral", 1, "ai"),
    # --- Tier 2: large European defence, large cyber, the big institutes -----
    (r"\bmbda\b|safran|rheinmetall|\bbae\b|\bsaab\b|hensoldt|kongsberg|fincantieri", 2, "defence"),
    (r"knds|dassault aviation|rohde|patria|babcock|general dynamics|naval group", 2, "defence"),
    (r"\bdamen\b|rolls-royce|elt group|elettronica", 2, "defence"),
    (r"lockheed|boeing|northrop|l3harris|ge aerospace|anduril|palantir", 2, "defence"),
    (r"helsing", 2, "defence"),
    (r"iceye", 2, "space"),
    (r"arianegroup|\bdlr\b|\bcnes\b|eumetsat|\bgmv\b|beyond gravity|\bruag\b|\bavio\b", 2, "space"),
    (r"^planet\b", 2, "space"),
    (r"\btno\b", 2, "defence"),
    (r"\bcern\b|^cea\b", 2, "research"),
    (r"europol|eu-lisa", 2, "cyber"),
    (r"eurocontrol|aviation safety agency|\beasa\b", 2, "infra"),
    (r"institute for security studies|euiss", 2, "defence"),
    (r"hadea|chips joint|cepol|united nations", 2, "institution"),
    (r"cs group|sopra steria", 2, "defence"),
    (r"crowdstrike|palo alto|fortinet|zscaler|sentinelone|recorded future|darktrace", 2, "cyber"),
    (r"cloudflare|\bwiz\b|dragos|rapid7|tenable|qualys|sophos|trellix|bitdefender", 2, "cyber"),
    (r"orange cyberdefense|idemia|ncc group", 2, "cyber"),
    (r"microsoft", 2, "ai"),
    (r"telef[oó]nica|\beni\b|terna|siemens", 2, "infra"),
    (r"\basml\b|\bnxp\b|infineon|stmicro", 2, "deeptech"),
    # --- Tier 3: specialised SMEs and startups, by sector --------------------
    (
        r"d-orbit|exotrail|gomspace|isar aerospace|kin[ée]is|leaf space|lookup space|"
        r"loft orbital|mynaric|neuraspace|okapi|redwire|sateliot|solenix|"
        r"space applications|spaceopal|starion|exploration company|unseenlabs|vyoma|argotec",
        3,
        "space",
    ),
    (
        r"alpine eagle|arx robotics|auterion|beretta|comand ai|delian|destinus|\bdgs\b|"
        r"exail|fn browning|frankenburg|milrem|nordic air defence|origin robotics|"
        r"quantum systems|stark|tekever|tiberius|tytan|piaggio|john cockerill",
        3,
        "defence",
    ),
    (r"aleph alpha|axelera|physicsx", 3, "ai"),
    (r"pasqal|quandela|\biqm\b|riverlane|sandboxaq|sipearl|newcleo", 3, "deeptech"),
    (r"fondazione bruno kessler|\bfbk\b|cineca|rand europe|ecfr|foreign relations", 3, "research"),
    (r"wavestone|roland berger|snowflake|adyen|trumpf", 3, "other"),
)
_ORGANIZATION_RES = tuple((re.compile(p, re.I), tier, sector) for p, tier, sector in _ORGANIZATIONS)


def organization(company: str) -> tuple[int, str]:
    """The tier and sector of an employer. An employer the table does not
    know is tier 3; most of those left are specialised cyber firms."""
    for pattern, tier, sector in _ORGANIZATION_RES:
        if pattern.search(company):
            return tier, sector
    return 3, "cyber"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
def _literal(alternative: str) -> str:
    """The longest run of characters any match of `alternative` must contain.

    Only what sits outside groups and is not made optional counts, so the
    run is certain to be in the text whenever the alternative matches.
    """
    best, run = "", ""
    depth, index = 0, 0

    def flush() -> None:
        nonlocal best, run
        if depth == 0 and len(run.strip()) > len(best):
            best = run.strip()
        run = ""

    while index < len(alternative):
        char = alternative[index]
        if char == "\\":
            following = alternative[index + 1 : index + 2]
            index += 2
            if following.isalnum():  # \b \s \d \w: not a character of the text
                flush()
                continue
            char = following
        elif char in "()":
            flush()
            depth += 1 if char == "(" else -1
            index += 1
            continue
        elif char == "[":
            flush()
            index = alternative.index("]", index) + 1
            continue
        elif char in "?*{":
            # A quantifier on a group or class; one on a character was seen
            # coming, below, and that character was never added.
            flush()
            index = alternative.index("}", index) + 1 if char == "{" else index + 1
            continue
        elif char in "+.^$|":
            flush()
            index += 1
            continue
        else:
            index += 1
        if alternative[index : index + 1] in ("?", "*", "{"):
            continue  # optional itself; the quantifier ends the run
        if depth == 0:
            run += char
    flush()
    return best


class _Terms:
    """Any of several patterns, matched against lowercased text.

    `re` tries every alternative at every position, which made ranking the
    table a matter of minutes. So each alternative is compiled on its own and
    run only on text containing the plain substring it cannot match without
    (a C-speed check), and case-insensitive matching is avoided: callers
    lowercase the text instead.
    """

    def __init__(self, *alternatives: str) -> None:
        # A leading \b costs `re` its literal-prefix scan — four times slower
        # on an advert — so it is checked by hand on each match instead.
        self.parts = tuple(
            (
                _literal(item),
                re.compile(item[2:] if item.startswith("\\b") else item),
                item.startswith("\\b"),
            )
            for item in alternatives
        )

    def search(self, text: str) -> re.Match[str] | None:
        return next(iter(self.finditer(text)), None)

    def finditer(self, text: str) -> Iterator[re.Match[str]]:
        for anchor, pattern, bounded in self.parts:
            if anchor not in text:
                continue
            for match in pattern.finditer(text):
                start = match.start()
                if bounded and start and _is_word_char(text[start - 1]):
                    # A word boundary needs a word character on one side
                    # only; every bounded alternative here starts with one.
                    continue
                yield match


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _words(*alternatives: str) -> _Terms:
    return _Terms(*alternatives)


_IRIS = _words(r"iris\s?²", r"\biris\s?2\b", r"\biris2\b")

#: Sector words, read in the title (decisive) and the advert (two distinct
#: words needed: a defence group's boilerplate names every sector it serves).
_SECTOR_WORDS: dict[str, re.Pattern[str]] = {
    "space": _words(
        r"\bspace\b", r"\bspatial", r"\bspazial", r"\bespacial", r"\braumfahrt",
        r"satellit", r"spacecraft", r"\borbit", r"ground segment", r"segment sol",
        r"launcher", r"galileo", r"copernicus", r"\begnos\b", r"\bgnss\b",
        r"earth observation", r"constellation",
    ),
    "defence": _words(
        r"\bdefen[cs]e\b", r"\bdéfense\b", r"\bdifesa\b", r"\bverteidigung", r"military",
        r"militaire", r"armed forces", r"\bnato\b", r"missile", r"warfare", r"c4isr",
        r"\bisr\b", r"electronic warfare", r"battlefield",
    ),
    "cyber": _words(
        r"cyber", r"security", r"sécurité", r"sicurezza", r"seguridad", r"sicherheit",
        r"infosec", r"\bsoc\b", r"threat", r"crypt",
    ),
    "infra": _words(
        r"critical infrastructure", r"\bot security", r"\bscada\b", r"\bics\b",
        r"\benergy grid", r"power grid", r"telecom", r"\b5g\b",
    ),
    "ai": _words(
        r"machine learning", r"\bllm", r"\bai\b", r"artificial intelligence",
        r"deep learning", r"\bml\b", r"intelligence artificielle",
    ),
}

#: The role families, read in the title.
#: Selling, administering and supporting: the brief's "only worth it for the
#: name" roles.
_SUPPORT_ROLE = _words(
    r"\bsales\b", r"pre-?sales", r"account (executive|manager|director|exec)",
    r"business development", r"\bbdr\b", r"\bsdr\b", r"renewal", r"\bchannel\b",
    r"partner (manager|sales)", r"alliances?\b", r"commercial", r"\bvente", r"vertrieb",
    r"\bventas\b", r"marketing", r"communications?\b", r"public relations", r"\bbrand\b",
    r"\bevents?\b", r"recruit", r"talent", r"\bhr\b", r"human resources", r"people partner",
    r"payroll", r"accountant", r"accounting", r"comptab", r"\bfinance", r"financial",
    r"controller", r"controlling", r"\btax\b", r"treasury", r"\blegal\b", r"counsel",
    r"lawyer", r"paralegal", r"procurement", r"purchas", r"\bbuyer", r"acheteu",
    r"\bachats?\b", r"einkauf", r"compras", r"supply chain", r"logisti", r"warehous",
    r"magasin", r"administrati", r"assistant", r"secretar", r"receptionist",
    r"office manager", r"facilit", r"customer success", r"customer support",
    r"technical support", r"support engineer", r"help ?desk", r"service desk",
    r"\bdriver\b", r"\bchauffeur", r"segreteria", r"cleaner", r"\bcook\b", r"solutions? consultant",
    r"sales engineer", r"systems engineer \((enterprise|commercial|major)",
)
#: Physical security and occupational safety share the word "security".
_NOT_CYBER = _words(
    r"security (guard|officer - site|agent)", r"physical security", r"gardiennage",
    r"sicurezza fisica", r"seguridad física", r"physische sicherheit", r"\(safety\)",
    r"fpga", r"hardware engineer",
    r"health (and|&) safety", r"\bhse\b", r"sécurité incendie", r"fire safety",
    r"site security",
)
#: Roles a cybersecurity graduate is not hired into.
_OFF_PROFILE_ROLE = _words(
    r"technician", r"technicien", r"techniker", r"operator", r"opérat", r"mechanic",
    r"machinist", r"\bcnc\b", r"welder", r"soudeu", r"assembl", r"monteur", r"electrician",
    r"électricien", r"production", r"manufactur", r"fabrication", r"maintenance",
    r"quality", r"qualité", r"qualität", r"mechanical", r"mécani", r"mechanik", r"\basic\b",
    r"\brf\b", r"hyperfréquence", r"antenna", r"thermal", r"structur", r"propulsion",
    r"chemi", r"physic", r"plasma", r"material", r"métrolog", r"metrolog", r"nuclear",
    r"nucléaire", r"radioprotection", r"optic", r"photonic", r"process engineer",
    r"civil engineer", r"construction", r"piping", r"welding", r"\bnurse", r"medical",
    r"\bux\b", r"\bui\b", r"graphic", r"designer", r"acoustic", r"hydraul", r"electrical",
    r"électrique", r"elektrik", r"power electronics", r"electronics engineer",
    r"électronique", r"flight test", r"test pilot", r"\bpilot\b", r"machining", r"lathe",
    r"composite", r"aerodynam", r"stress engineer", r"\bait\b", r"aiv\b", r"harness",
    r"cabling", r"painter", r"inspector", r"\bplanner", r"scheduler", r"estimator",
    r"cost engineer", r"silicon", r"wafer", r"lithograph", r"\beuv\b", r"yield",
    r"mechanism",
)
#: Roles that are themselves cybersecurity, risk or intelligence work.
_CORE_ROLE = _words(
    r"cyber", r"security", r"sécurité", r"securite", r"sicurezza", r"seguridad",
    r"sicherheit", r"infosec", r"secops", r"devsecops", r"\bsoc\b", r"csirt", r"\bcert\b",
    r"threat", r"menace", r"\brisk", r"risque", r"rischi", r"riesgo", r"risiko", r"\bgrc\b",
    r"governance", r"compliance", r"conformit", r"resilien", r"résilien", r"crypt",
    r"\bpki\b", r"forensic", r"incident", r"vulnerab", r"pentest", r"penetration",
    r"red team", r"blue team", r"purple team", r"malware", r"reverse engineer",
    r"detection", r"intelligence analyst", r"\bcti\b", r"\biam\b", r"identity",
    r"\bisso\b", r"\bciso\b", r"\bssi\b", r"\brssi\b", r"hardening", r"sûreté",
)
#: Technical or strategic work next to the profile: engineering, data,
#: research, policy.
_ADJACENT_ROLE = _words(
    r"software", r"developer", r"développeu", r"entwickler", r"sviluppat",
    r"desarrollador", r"engineer", r"ingénieu", r"ingegner", r"ingenier", r"ingenieur",
    r"\bdata\b", r"machine learning", r"\bai\b", r"\bml\b", r"\bllm", r"research",
    r"recherche", r"scientist", r"cloud", r"devops", r"network", r"réseau", r"systems",
    r"système", r"embedded", r"embarqu", r"firmware", r"mission", r"ground segment",
    r"satellite", r"spacecraft", r"architect", r"analyst", r"analyste", r"consultant",
    r"policy", r"strateg", r"stratég", r"programme officer", r"project officer",
    r"intelligence", r"operations engineer",
)
#: Operations work the brief calls repetitive.
_REPETITIVE = _words(
    r"\blevel ?1\b", r"\bl1\b", r"\btier ?1\b", r"\bn1\b", r"monitoring", r"operator",
    r"soc analyst", r"analyste soc", r"\bnoc\b",
)

#: What the CV has done, as it reads in an advert. Each distinct one counts.
_CV_TERMS: dict[str, re.Pattern[str]] = {
    "threat modelling": _words(r"threat model", r"modélisation des menaces"),
    "risk assessment": _words(
        r"risk (assessment|analysis|management)", r"analyse de risque",
        r"gestion des risques", r"analisi del rischio", r"\bebios\b", r"risikoanalyse",
    ),
    "ISO 27005/27001": _words(r"27005", r"27001", r"\bisms\b"),
    "threat intelligence": _words(r"threat intel", r"\bcti\b", r"renseignement sur les menaces"),
    "MITRE ATT&CK": _words(r"mitre", r"att&ck", r"\bttps?\b"),
    "security architecture": _words(r"security architect", r"architecture de sécurité"),
    "security requirements": _words(r"security requirement", r"exigences de sécurité"),
    "sicurezza spaziale": _words(
        r"space (cyber)?security", r"satellite security", r"cyber ?security (for|of) space",
        r"space systems? security", r"secure space", r"sécurité des systèmes spatiaux",
    ),
    "resilienza": _words(r"resilien", r"résilien"),
    "crittografia": _words(r"cryptograph", r"post-quantum", r"\bpqc\b", r"cryptograph"),
    "policy e regolazione": _words(
        r"\bnis ?2\b", r"\bdora\b", r"cyber resilience act", r"security polic",
        r"cyber ?(security)? strateg", r"security strateg", r"security governance",
    ),
    "sistemi embedded": _words(
        r"\bqemu\b", r"freertos", r"embedded security", r"firmware security"
    ),
    "AI/LLM": _words(r"\bllms?\b", r"large language model", r"ai security", r"anonymi"),
    "programmi spaziali UE": _words(
        r"galileo", r"copernicus", r"\begnos\b", r"iris\s?²", r"\biris\s?2\b"
    ),
}

# ---------------------------------------------------------------------------
# Level
# ---------------------------------------------------------------------------
#: Structured entry routes: the brief's ideal level.
_PROGRAMME = _words(
    r"graduate", r"young graduate", r"\bygt\b", r"traineeship", r"\btrainee\b",
    r"blue book", r"\bv\.?i\.?e\.?\b", r"volontariat international", r"early career",
    r"new grad", r"rotational", r"jeune diplômé", r"junior professional", r"absolvent",
    r"berufseinsteiger", r"neolaureat", r"recién titulad",
)
_JUNIOR = _words(
    r"junior", r"\bjr\.?\b", r"entry[- ]level", r"\bentry\b", r"débutant", r"\bassociate\b",
    r"\b(engineer|analyst|developer|consultant|specialist|scientist) i\b",
    r"einsteiger",
)
_INTERN = _words(
    r"intern\b", r"internship", r"\bstage\b", r"stagiaire", r"praktik", r"prácticas",
    r"tirocin", r"stagista", r"\bbecari",
)
#: Contracts that require being enrolled in a degree, which ends in October.
_ENROLLED = _words(
    r"alternan", r"apprenti", r"apprentissage", r"werkstudent", r"working student",
    r"\bthesis\b", r"\btesi\b", r"masterarbeit", r"abschlussarbeit", r"bachelorarbeit",
    r"mémoire", r"\bstudent\b", r"étudiant", r"duales studium",
)
_DOCTORAL = _words(r"\bph\.?d\b", r"doctora", r"doktorand", r"post-?doc", r"\bthèse\b")
_SENIOR = _words(
    r"senior", r"\bsr\.?\b", r"\blead\b", r"principal", r"\bstaff\b", r"\bhead\b",
    r"director", r"directeur", r"directora?\b", r"\bchief\b", r"\bvp\b",
    r"vice president", r"\bexpert", r"leiter", r"\bleader\b", r"\b(engineer|analyst) (iii|iv|v)\b",
    r"\bmanager\b", r"\bresponsable\b", r"\bmanag", r"\bhead of\b", r"\bmgr\b",
)
#: "5+ years of experience", in the languages the adverts are written in.
_YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|or more|plus)?\s*(?:-|to|à|a|bis)?\s*(?:\d{1,2})?\s*\+?\s*"
    r"(?:years?|yrs|ans|anni|años|jahre)\b[^.\n]{0,60}?"
    r"(?:experience|expérience|esperienza|experiencia|erfahrung|berufserfahrung)",
)

# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------
_US_ONLY = _words(
    r"\bu\.?s\.? citizen", r"us citizenship", r"united states citizen", r"\bu\.?s\.? person",
    r"\bitar\b", r"ts/sci", r"top secret", r"secret clearance", r"active (secret|ts)\b",
    r"\bdod clearance", r"public trust", r"must be (a )?u\.?s\.?",
)
_NATIONAL_ONLY = _words(
    r"nationalité française", r"french (nationality|citizenship)",
    r"deutsche staatsangehörigkeit", r"german (citizenship|nationality)",
    r"british (citizen|national)", r"sole uk national", r"uk eyes only",
)
_UK_CLEARANCE = _words(
    r"\b(sc|dv)\b (level )?clearance", r"security check(ed)? \(sc\)", r"developed vetting"
)
_FR_CLEARANCE = _words(r"habilitation", r"habilitable")
_DE_CLEARANCE = _words(r"sicherheitsüberprüfung", r"\bü2\b", r"\bü3\b")
_EU_NATO_CLEARANCE = _words(
    r"nato secret", r"cosmic top secret", r"eu secret", r"nulla osta", r"\bnos\b"
)

_LANGUAGES = {
    "francese": r"french|français|francais|französisch",
    "tedesco": r"german|allemand|deutsch",
    "olandese": r"dutch|néerlandais|niederländisch",
    "spagnolo": r"spanish|espagnol|español|castellano",
    "polacco": r"polish|polski",
    "svedese": r"swedish|svenska",
    "norvegese": r"norwegian|norsk",
    "danese": r"danish|dansk",
    "finlandese": r"finnish|suomi",
    "ebraico": r"hebrew",
    "portoghese": r"portuguese|português",
    "ceco": r"czech",
    "giapponese": r"japanese",
    "cinese": r"mandarin|chinese",
}
_LANGUAGE_NAMES = {name: _Terms(*words.split("|")) for name, words in _LANGUAGES.items()}
#: Said next to a language, it means the language is asked for.
_PROFICIENCY = _words(
    r"fluen", r"native", r"proficien", r"business[- ]level", r"excellent", r"good",
    r"strong", r"working knowledge", r"mother tongue", r"maîtrise", r"courant",
    r"verhandlungssicher", r"fließend", r"sehr gute", r"c1", r"c2", r"b2",
    r"kenntnisse", r"required", r"requis", r"must",
)


def _required_languages(text: str) -> list[str]:
    """Languages other than English and Italian the advert asks for."""
    found: list[str] = []
    for name, terms in _LANGUAGE_NAMES.items():
        for match in terms.finditer(text):
            window = text[max(0, match.start() - 60) : match.end() + 30]
            if _PROFICIENCY.search(window):
                found.append(name)
                break
    return found


#: Function words that give away the language an advert is written in.
_STOPWORDS = {
    "francese": (" les ", " des ", " vous ", " pour ", " une ", " avec ", " dans "),
    "tedesco": (" und ", " der ", " die ", " mit ", " für ", " sie ", " wir "),
    "spagnolo": (" los ", " las ", " para ", " con ", " una ", " del ", " nuestro "),
    "olandese": (" het ", " van ", " een ", " voor ", " wij ", " jouw ", " zijn "),
    "svedese": (" och ", " att ", " för ", " med ", " som ", " vi "),
    "polacco": (" oraz ", " jest ", " się ", " dla ", " przez "),
}
_ENGLISH_STOPWORDS = (" the ", " and ", " you ", " with ", " for ", " our ", " will ")

#: Where an Italian citizen needs a visa to work: outside the EU, the EEA and
#: Switzerland (the United Kingdom is matched on its own, below).
_OUTSIDE_EUROPE = _words(
    r"serbia", r"belgrade", r"beograd", r"novi sad", r"bosnia", r"sarajevo", r"macedonia",
    r"skopje", r"albania", r"tirana", r"montenegro", r"podgorica", r"kosovo", r"pristina",
    r"moldova", r"chisinau", r"chișinău", r"ukraine", r"\bkyiv\b", r"\bkiev\b", r"\blviv\b",
    r"kharkiv", r"belarus", r"\bminsk\b", r"russia", r"moscow", r"tbilisi", r"armenia",
    r"yerevan", r"azerbaijan", r"\bbaku\b", r"istanbul", r"ankara", r"izmir", r"georgia",
    r"united states", r"\busa\b", r"\bu\.s\.", r", us\b", r"\bus,", r"canada", r"india\b",
    r"china", r"singapore", r"japan", r"australia", r"israel", r"brazil", r"mexico",
    r"saudi", r"emirates", r"\buae\b", r"dubai", r"abu dhabi", r"qatar", r"korea", r"taiwan",
    r"malaysia", r"philippines", r"new zealand", r"south africa", r"argentina", r"chile",
    r"colombia", r"hong kong", r"vietnam", r"thailand", r"indonesia", r"turkey", r"türkiye",
    r"egypt", r"morocco", r"nigeria", r"kenya", r"\bperu\b", r"bangalore", r"bengaluru",
    r"\bpune\b", r"hyderabad", r"chennai", r"mumbai", r"delhi", r"noida", r"gurgaon",
    r"gurugram", r"tokyo", r"shanghai", r"beijing", r"shenzhen", r"tel aviv", r"riyadh",
    r"san francisco", r"new york", r"seattle", r"\baustin\b", r"\bboston\b", r"washington",
    r"virginia", r"texas", r"california", r"florida", r"colorado", r"arizona", r"\bohio\b",
    r"maryland", r"alabama", r"quebec", r"québec", r"ontario", r"toronto", r"montreal",
    r"vancouver", r"\baus\b", r"sydney", r"melbourne", r"queensland", r"new south wales",
    r"\bind\b",
    r", in$", r", cn$", r", sg$", r", jp$", r", ca$", r", au$", r", il$", r", br$",
)
#: "Fort Worth, TX": a US state code ending the location. DE, MT, AL and ME
#: are left out — as country codes they are Germany, Malta, Albania and
#: Montenegro, and "Darmstadt, DE" is far more common here than Delaware.
_US_STATE_END = re.compile(
    r", (ak|az|ar|ca|co|ct|dc|fl|ga|hi|ia|id|il|in|ks|ky|la|ma|md|mi|mn|mo|ms|nc|nd|"
    r"ne|nh|nj|nm|nv|ny|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|va|vt|wa|wi|wv|wy)$"
)
_IN_THE_US = _words(r"united states", r"\busa\b", r"\bu\.s\.", r", us\b", r"\bus,")
#: Said of a role in the US, any of these means a US clearance.
_US_CLASSIFIED = _words(r"clearance", r"classified", r"\bdod\b", r"department of defense")
_UNITED_KINGDOM = _words(
    r"united kingdom", r"\buk\b", r"england", r"scotland", r"wales", r"london", r", gb$"
)
#: Employers whose staff work under the organisation's own status, wherever
#: the post is (NATO in Izmir or Norfolk, an EU delegation abroad): no visa.
_INTERNATIONAL_ORG = _words(
    r"\bnato\b", r"united nations", r"\besa\b", r"european space agency", r"eumetsat",
    r"\bcern\b", r"eu careers", r"\bepso\b", r"european defence agency", r"european union",
    r"european commission", r"\beeas\b", r"\benisa\b", r"cert-eu", r"europol", r"frontex",
    r"eurocontrol", r"satcen", r"satellite centre", r"\beuspa\b", r"council of europe",
    r"european central bank", r"\becb\b", r"\beppo\b", r"\bcepol\b", r"\bhadea\b",
    r"eu-lisa", r"\beccc\b", r"cybersecurity competence centre", r"\beasa\b",
    r"aviation safety agency", r"institute for security studies",
)

#: Why a posting is not worth a review call, best reason first. A posting
#: with one of these keeps its rules score, but the model never sees it and
#: the grid leaves it out unless asked to show it.
EXCLUDED_NOT_ELIGIBLE = "Non candidabile"
EXCLUDED_VISA = "Serve un visto"
EXCLUDED_SENIOR = "Senior"
EXCLUDED_SUPPORT = "Ruolo commerciale o amministrativo"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
@dataclass
class Flag:
    text: str
    #: "block" — cannot apply; "warn" — check before applying; "info" — worth knowing.
    level: str = "warn"


@dataclass
class Verdict:
    score: int
    tier: int
    sector: str
    criteria: dict[str, int]
    reasons: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    #: One of the EXCLUDED_* reasons, or "" when the posting is worth a review.
    excluded: str = ""

    @property
    def blocked(self) -> bool:
        return any(flag.level == "block" for flag in self.flags)

    def as_json(self) -> str:
        return json.dumps(
            {
                "tier": self.tier,
                "sector": self.sector,
                "criteria": self.criteria,
                "reasons": self.reasons,
                "flags": [{"text": f.text, "level": f.level} for f in self.flags],
                # The rules' own reason, kept apart from the column, which the
                # operator's filters may override (exclusions.py).
                "excluded": self.excluded,
            },
            ensure_ascii=False,
        )


def _min_years(text: str) -> int | None:
    found = [int(match.group(1)) for match in _YEARS.finditer(text)]
    found = [years for years in found if 0 < years <= 20]
    return min(found) if found else None


def _advert_language(text: str) -> str | None:
    """The language an advert is written in, if not English or Italian."""
    sample = f" {text[:3000].lower()} "
    english = sum(sample.count(word) for word in _ENGLISH_STOPWORDS)
    best, hits = None, 0
    for language, words in _STOPWORDS.items():
        count = sum(sample.count(word) for word in words)
        if count > hits:
            best, hits = language, count
    return best if best and hits > max(8, english) else None


def _sector_of_posting(title: str, description: str) -> str | None:
    """The best sector the posting itself names, or None."""
    ranked = sorted(_SECTOR_WORDS, key=lambda sector: -SECTOR_POINTS[sector])
    for sector in ranked:
        if _SECTOR_WORDS[sector].search(title):
            return sector
    for sector in ranked:
        if sector == "cyber":
            continue  # "security" appears in every advert's privacy notice
        if len({m.group(0).lower() for m in _SECTOR_WORDS[sector].finditer(description)}) >= 2:
            return sector
    return None


def score_posting(
    *,
    title: str,
    company: str,
    location: str = "",
    description: str = "",
    level: str = "",
    deadline: str | None = None,
    today: date | None = None,
) -> Verdict:
    """Rank one posting by the brief's rubric, from what the posting says."""
    # Every pattern here is written in lower case and matched against
    # lowercased text (see _words).
    title, description, location = title.lower(), description.lower(), location.lower()
    text = f"{title}\n{description}"
    lowered_title = f"{title} {level.lower()}"
    reasons: list[str] = []
    flags: list[Flag] = []
    multiplier = 1.0

    # 1. Organization
    tier, company_sector = organization(company)
    org_points = TIER_POINTS[tier]
    reasons.append(f"Fascia {tier}")

    # 2. Sector and mission
    sector = company_sector
    posting_sector = _sector_of_posting(title, description)
    if posting_sector and SECTOR_POINTS[posting_sector] > SECTOR_POINTS[sector]:
        sector = posting_sector
    sector_points = SECTOR_POINTS[sector]
    if _IRIS.search(text):
        sector, sector_points = "space", 25
        reasons.append("IRIS²")
    reasons.append(f"Settore: {SECTOR_LABELS[sector]}")

    # Role family
    if _SUPPORT_ROLE.search(lowered_title):
        family = "support"
    elif _NOT_CYBER.search(lowered_title):
        family = "off"
    elif _CORE_ROLE.search(lowered_title):
        family = "core"
    elif _OFF_PROFILE_ROLE.search(lowered_title):
        family = "off"
    elif _ADJACENT_ROLE.search(lowered_title):
        family = "adjacent"
    else:
        family = "other"

    # 3. CV fit
    in_title = [name for name, pattern in _CV_TERMS.items() if pattern.search(title)]
    in_text = [name for name, pattern in _CV_TERMS.items() if pattern.search(description)]
    hits = len(set(in_title)) * 3 + len(set(in_text) - set(in_title))
    cv_points = {
        "core": 12 + min(hits, 8),
        "adjacent": 6 + min(hits, 6),
        "other": 2 + min(hits, 3),
        "off": 1 + min(hits, 2),
        "support": min(hits, 2),
    }[family]
    if in_title:
        reasons.append(f"Ruolo: {', '.join(in_title[:2])}")
    elif in_text:
        reasons.append(f"Nell'annuncio: {', '.join(in_text[:3])}")

    # 4. Growth — and the level the posting is pitched at
    years = _min_years(description)
    senior = False
    if _DOCTORAL.search(lowered_title):
        growth = 5
        multiplier *= 0.5
        flags.append(Flag("Dottorato / post-doc"))
    elif _ENROLLED.search(lowered_title):
        growth = 4
        multiplier *= 0.55
        flags.append(Flag("Richiede iscrizione a un corso di studi"))
    elif _PROGRAMME.search(lowered_title):
        growth = 15
        reasons.append("Livello: graduate / trainee")
    elif _INTERN.search(lowered_title):
        growth = 8
        multiplier *= 0.85
        flags.append(Flag("Stage: spesso riservato a studenti iscritti", "info"))
    elif _JUNIOR.search(lowered_title):
        growth = 13
        reasons.append("Livello: junior")
    elif _SENIOR.search(lowered_title) or (years is not None and years >= 5):
        senior = True
        growth = 4
        multiplier *= 0.5
        flags.append(Flag(f"Senior ({years}+ anni)" if years and years >= 5 else "Senior"))
    elif years is not None and years >= 3:
        growth = 8
        multiplier *= 0.85
        flags.append(Flag(f"Chiede {years}+ anni di esperienza", "info"))
    else:
        growth = 10

    # 5. Impact
    if family == "core":
        impact = 5 if _REPETITIVE.search(lowered_title) else 10
    else:
        impact = {"adjacent": 7, "other": 4, "off": 3, "support": 1}[family]

    if family == "support":
        multiplier *= 0.4
        flags.append(Flag("Ruolo commerciale, amministrativo o di supporto"))
    elif family == "off":
        multiplier *= 0.55
        flags.append(Flag("Fuori dal tuo profilo", "info"))
    elif family == "other":
        multiplier *= 0.8

    # 6. Feasibility: eligibility, place, language
    feasibility = 5
    if _US_ONLY.search(text):
        multiplier *= 0.25
        feasibility = 0
        flags.append(Flag("Non candidabile: cittadinanza o clearance USA", "block"))
    if _NATIONAL_ONLY.search(text):
        multiplier *= 0.25
        feasibility = 0
        flags.append(Flag("Non candidabile: cittadinanza nazionale richiesta", "block"))
    if _UK_CLEARANCE.search(text):
        multiplier *= 0.7
        feasibility -= 3
        flags.append(Flag("Clearance UK (SC/DV): di solito residenza UK"))
    if _FR_CLEARANCE.search(text):
        multiplier *= 0.8
        feasibility -= 2
        flags.append(Flag("Habilitation défense: verificare se aperta a cittadini UE"))
    if _DE_CLEARANCE.search(text):
        multiplier *= 0.85
        feasibility -= 2
        flags.append(Flag("Sicherheitsüberprüfung (Ü2/Ü3)"))
    if _EU_NATO_CLEARANCE.search(text):
        flags.append(Flag("Nulla osta NATO/UE: ottenibile da cittadino italiano", "info"))

    in_the_us = bool(_IN_THE_US.search(location) or _US_STATE_END.search(location))
    if in_the_us and _US_CLASSIFIED.search(text) and not _US_ONLY.search(text):
        multiplier *= 0.25
        feasibility = 0
        flags.append(Flag("Non candidabile: ruolo USA con clearance", "block"))
    # A posting offered in several places needs a visa only if none of them
    # is in the EU, the EEA, Switzerland or the UK ("Barcelona; Dubai;
    # Krakow" can be taken in Barcelona). The countries come from geo.py;
    # the patterns are the fallback when it reads none.
    codes = geo.countries(location)
    if codes:
        free = any(code in geo.FREE_MOVEMENT for code in codes)
        abroad = not free and "GB" not in codes
        in_the_uk = not free and "GB" in codes
    else:
        abroad = bool(in_the_us or _OUTSIDE_EUROPE.search(location))
        in_the_uk = not abroad and bool(_UNITED_KINGDOM.search(location))
    # The United Kingdom needs a visa too, but its employers sponsor one far
    # more readily: the operator asked for UK roles to be neither excluded
    # nor marked down for it (2026-10-10). Only a note remains.
    visa = abroad and not _INTERNATIONAL_ORG.search(company.lower())
    if abroad and not visa:
        flags.append(Flag("Organizzazione internazionale: niente visto", "info"))
    elif abroad:
        multiplier *= 0.7
        feasibility -= 2
        flags.append(Flag("Fuori da UE/SEE/Svizzera: serve un visto"))
    elif in_the_uk:
        flags.append(Flag("Regno Unito: visto, di solito sponsorizzato", "info"))

    languages = _required_languages(description)
    written_in = _advert_language(description)
    if written_in and written_in not in languages:
        languages.append(written_in)
    if languages:
        multiplier *= 0.85
        feasibility -= 2
        flags.append(Flag(f"Lingua: {', '.join(languages)}"))

    if deadline:
        try:
            if date.fromisoformat(deadline) < (today or date.today()):
                multiplier *= 0.5
                flags.append(Flag("Scadenza passata"))
        except ValueError:
            pass

    criteria = {
        "organizzazione": org_points,
        "settore": sector_points,
        "cv": cv_points,
        "crescita": growth,
        "impatto": impact,
        "fattibilita": max(0, feasibility),
    }
    raw = sum(criteria.values())
    score = max(0, min(100, round(raw * multiplier)))
    if any(flag.level == "block" for flag in flags):
        excluded = EXCLUDED_NOT_ELIGIBLE
    elif visa:
        excluded = EXCLUDED_VISA
    elif senior:
        excluded = EXCLUDED_SENIOR
    elif family == "support":
        excluded = EXCLUDED_SUPPORT
    else:
        excluded = ""
    return Verdict(score, tier, sector, criteria, reasons, flags, excluded)


# ---------------------------------------------------------------------------
# Applying the rules to the table
# ---------------------------------------------------------------------------
_refresh_lock = threading.Lock()


def refresh(force: bool = False) -> int:
    """Rank every posting that changed, or was ranked by older rules.

    Cheap when nothing changed: one indexed scan. Returns how many were ranked.
    """
    with _refresh_lock:
        return _refresh(force)


def _refresh(force: bool) -> int:
    today = date.today()
    filters = exclusions.load()
    matcher = exclusions.Matcher(filters)
    with db.connect() as conn:
        query = """
            SELECT id, title, company, location, description, seniority, deadline
            FROM jobs
        """
        if not force:
            query += """
            WHERE priority_version IS NOT ?
               OR priority_ranked_at IS NULL
               OR priority_ranked_at < updated_at
            """
        rows = conn.execute(query, () if force else (RULES_VERSION,)).fetchall()
        updates = []
        for row in rows:
            verdict = score_posting(
                title=row["title"],
                company=row["company"],
                location=row["location"],
                description=row["description"] or "",
                level=row["seniority"] or "",
                deadline=row["deadline"],
                today=today,
            )
            excluded = (
                matcher.reason(title=row["title"], company=row["company"], location=row["location"])
                or verdict.excluded
            )
            updates.append(
                (
                    verdict.score,
                    verdict.as_json(),
                    excluded,
                    geo.encode(geo.countries(row["location"] or "")),
                    RULES_VERSION,
                    row["id"],
                )
            )
        conn.executemany(
            """
            UPDATE jobs SET priority_score = ?, priority_rules = ?, priority_excluded = ?,
                            countries = ?, priority_version = ?, priority_ranked_at = datetime('now')
            WHERE id = ?
            """,
            updates,
        )
        if updates:
            _fill_countries_from_company(conn)
        _apply_filters_if_changed(conn, filters, matcher)
    if updates:
        logger.info("ranking: %d postings ranked", len(updates))
    return len(updates)


def _apply_filters_if_changed(
    conn: Any, filters: exclusions.Filters, matcher: exclusions.Matcher
) -> None:
    """Re-apply the operator's filters to every posting when they changed.

    The postings just ranked already have them; the others carry whatever
    the filters were at their own ranking. Only the three fields the filters
    read are needed, and the rules' own reason is in their stored verdict, so
    this takes a second where ranking again would take half a minute.
    """
    digest = filters.digest()
    stored = conn.execute("SELECT value FROM meta WHERE key = 'exclusions_digest'").fetchone()
    if stored and stored["value"] == digest:
        return
    rows = conn.execute(
        "SELECT id, title, company, location, priority_rules, priority_excluded FROM jobs "
        "WHERE priority_rules IS NOT NULL"
    ).fetchall()
    updates = []
    for row in rows:
        rules = json.loads(row["priority_rules"])
        current = row["priority_excluded"] or ""
        own = rules.get("excluded")
        if own is None:  # ranked before the rules kept their reason apart
            own = "" if current.startswith(exclusions.REASON_PREFIX) else current
        posting = {key: row[key] or "" for key in ("title", "company", "location")}
        excluded = matcher.reason(**posting) or own
        if excluded != current:
            updates.append((excluded, row["id"]))
    conn.executemany("UPDATE jobs SET priority_excluded = ? WHERE id = ?", updates)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('exclusions_digest', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (digest,),
    )
    if updates:
        logger.info("ranking: filters changed the exclusion of %d postings", len(updates))


def _fill_countries_from_company(conn: Any) -> None:
    """A posting whose location names no country ("Saclay") takes its
    employer's usual one, when nearly all of its other postings agree."""
    rows = conn.execute(
        "SELECT company, location, countries FROM jobs WHERE closed_at IS NULL"
    ).fetchall()
    parsed = [(row["company"], geo.countries(row["location"] or "")) for row in rows]
    defaults = geo.company_defaults(parsed)
    conn.executemany(
        "UPDATE jobs SET countries = ? WHERE company = ? AND countries = ''",
        [(geo.encode([code]), company) for company, code in defaults.items()],
    )


def refresh_quietly() -> None:
    """refresh(), for callers that must neither fail nor wait because of it.

    A page load that finds a ranking already under way serves the ranks as
    they stand instead of queueing behind it.
    """
    if not _refresh_lock.acquire(blocking=False):
        return
    try:
        _refresh(force=False)
    except Exception:  # noqa: BLE001 — a ranking bug must not take the grid down
        logger.exception("ranking: refresh failed")
    finally:
        _refresh_lock.release()


# ---------------------------------------------------------------------------
# Review by the model, against the operator's brief
# ---------------------------------------------------------------------------
#: The answer asked of the model. It does not name the brief's criteria: the
#: brief is the operator's to rewrite (its rubric changed from six criteria
#: to four plus bonuses), so the model reports whatever criteria the brief
#: defines, each with its points and its maximum.
_CRITERION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "points": {"type": "integer"},
        "max": {"type": "integer"},
    },
    "required": ["name", "points", "max"],
    "additionalProperties": False,
}
_ADJUSTMENT: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "points": {"type": "integer"}},
    "required": ["name", "points"],
    "additionalProperties": False,
}
REVIEW_PROPERTIES: dict[str, Any] = {
    "criteria": {"type": "array", "items": _CRITERION},
    "adjustments": {"type": "array", "items": _ADJUSTMENT},
    "total": {"type": "integer"},
    "cap": {"type": "integer"},
    "eligible": {"type": "boolean"},
    "justification": {"type": "string"},
    "issues": {"type": "array", "items": {"type": "string"}},
    "cv_tip": {"type": "string"},
    "deadline": {"type": "string"},
    "location": {"type": "string"},
    "languages": {"type": "string"},
    "clearance": {"type": "string"},
}
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": REVIEW_PROPERTIES,
    # strict structured output needs every property listed (see llm.py).
    "required": list(REVIEW_PROPERTIES),
    "additionalProperties": False,
}

#: Appended to the brief. The brief says who the operator is and how to
#: score; this says what one call is and what to answer, so the brief can be
#: written for a person, several offers at a time, and still be used as is.
REVIEW_INSTRUCTIONS = """
## This request
You receive ONE job offer, not a list. Ignore what the brief above says about
the output format for several offers (tables, a final ranking, top lists):
reply only with the JSON object requested, written in the language of the
brief.

- criteria: one entry per scoring criterion the brief defines, in the brief's
  order: its name as the brief writes it, the points given, and the maximum
  the brief allows for it.
- adjustments: each bonus or malus the brief defines, with the points given
  (negative for a malus, 0 if it does not apply); an empty list if the brief
  defines none.
- total: the final score, the criteria plus the adjustments.
- cap: the highest total the brief's caps or ceilings allow for this offer
  (by category, internship, enrolment, ...), the lowest if several apply; 0
  if the brief sets none for it. The total is held to it.
- eligible: false only if I cannot apply at all: a citizenship, clearance or
  residence I do not have, or a requirement the brief calls a total blocker.
- justification: at most two lines on why this score.
- issues: what to check before applying; an empty list if nothing.
- cv_tip: one sentence on what to stress in my CV for this offer.
- deadline, location, languages, clearance: as the offer states them, or
  "missing data" when it does not. Never invent them.
"""

#: A posting the review says cannot be applied for scores at most this, so
#: it sinks below every posting that can, whatever its other merits (a model
#: once ruled a role out for a US clearance and still gave it 64).
NOT_ELIGIBLE_CAP = 30


class BriefMissing(RuntimeError):
    """No brief to review against."""


def load_brief() -> str:
    path = paths.RANKING_BRIEF_PATH
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise BriefMissing(
            f"no brief at {path} — write there who you are, what you want and how to score"
        ) from None
    if not text:
        raise BriefMissing(f"{path} is empty")
    return text


def save_brief(text: str) -> str:
    """Replace the brief; returns its digest. Reviews made against the
    previous one are kept, and marked stale until reviewed again."""
    text = text.strip()
    if not text:
        raise BriefMissing("the brief cannot be empty")
    paths.RANKING_BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.RANKING_BRIEF_PATH.write_text(text + "\n", encoding="utf-8")
    return brief_digest(text)


def brief_digest(brief: str) -> str:
    """What a review was made against: the brief and the fixed instructions."""
    return hashlib.sha256(f"{brief.strip()}\n{REVIEW_INSTRUCTIONS}".encode("utf-8")).hexdigest()[:12]


_digest_cache: tuple[float, str] = (-1.0, "")


def current_digest() -> str:
    """The digest of the brief on disk, re-read only when the file changed."""
    global _digest_cache
    try:
        stamp = paths.RANKING_BRIEF_PATH.stat().st_mtime
    except OSError:
        return ""
    if _digest_cache[0] != stamp:
        try:
            _digest_cache = (stamp, brief_digest(load_brief()))
        except BriefMissing:
            _digest_cache = (stamp, "")
    return _digest_cache[1]


#: The advert is sent whole up to this length. EU and NATO notices put the
#: eligibility, experience and language requirements near the end, and run
#: to 20,000+ characters; a 9,000 cut once dropped them from the review.
MAX_ADVERT_CHARS = 30_000


#: Added to the offer for a role in the United Kingdom. The operator decided
#: (2026-10-10) that a UK visa, which employers there sponsor readily, must
#: not count against a role. Said with the offer rather than in the fixed
#: instructions, so the reviews already made stay current.
UK_VISA_NOTE = (
    "Note: this role is in the United Kingdom. A UK work visa is usually sponsored by the "
    "employer: do not treat it as a blocker, and do not deduct points for it.\n\n"
)


def review_prompt(row: dict[str, Any]) -> str:
    in_the_uk = "GB" in geo.countries(row["location"] or "")
    return (
        (UK_VISA_NOTE if in_the_uk else "")
        + "## Offer to evaluate\n"
        f"Company: {row['company']}\n"
        f"Role: {row['title']}\n"
        f"Location: {row['location'] or 'missing data'}\n"
        f"Level/contract: {row['seniority'] or 'missing data'}\n"
        f"Published: {row['date_posted'] or 'missing data'}\n"
        f"Deadline: {row['deadline'] or 'missing data'}\n"
        f"Link: {row['apply_url']}\n\n"
        f"{(row['description'] or 'missing data: no description')[:MAX_ADVERT_CHARS]}"
    )


def review_system_prompt(brief: str, calibration_section: str = "") -> str:
    """The prompt every review of a run opens with, identical across calls.

    The calibration sits between the brief and the instructions: it refines
    the one, and the other must stay last, where the model reads what to
    answer.
    """
    parts = [brief.strip(), calibration_section.strip(), REVIEW_INSTRUCTIONS.strip()]
    return "\n\n".join(part for part in parts if part) + "\n"


def _int(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        return max(low, min(high, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def parse_review(payload: dict[str, Any]) -> dict[str, Any]:
    """The model's verdict, held to the ranges it states itself.

    The score is the sum of the criteria and adjustments, not the model's own
    addition, which a cheap model gets wrong often enough to reorder postings.
    """
    criteria = []
    for item in payload.get("criteria") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        top = _int(item.get("max"), 0, 1000)
        points = _int(item.get("points"), 0, top or 1000)
        criteria.append({"name": str(item["name"]).strip(), "points": points, "max": top or points})
    adjustments = [
        {"name": str(item["name"]).strip(), "points": _int(item.get("points"), -100, 100)}
        for item in payload.get("adjustments") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    stated = _int(payload.get("total"), -1000, 1000)
    computed = sum(c["points"] for c in criteria) + sum(a["points"] for a in adjustments)
    score = computed if criteria else stated
    # The brief's caps (a GRC role at most 65, an internship at most 40) are
    # the model's to judge and the code's to apply: left to the model's own
    # addition, a trainee role it had capped at 40 still came out at 113.
    cap = _int(payload.get("cap"), 0, 1000)
    if cap and score > cap:
        adjustments.append({"name": f"Tetto {cap}", "points": cap - score})
        score = cap
    eligible = bool(payload.get("eligible", True))
    if not eligible:
        score = min(score, NOT_ELIGIBLE_CAP)
    review: dict[str, Any] = {
        "score": score,
        "stated_total": stated,
        "criteria": criteria,
        "adjustments": adjustments,
        "cap": cap,
        "eligible": eligible,
        "issues": [str(p).strip() for p in payload.get("issues") or [] if str(p).strip()],
    }
    for key in ("justification", "cv_tip", "deadline", "location", "languages", "clearance"):
        review[key] = str(payload.get(key) or "").strip()
    return review


_PENDING = {
    "new": "AND priority_llm IS NULL",
    "stale": "AND (priority_llm IS NULL OR json_extract(priority_llm, '$.brief') IS NOT :digest)",
    # Reviewed against this brief, before the latest calibration (calibration.py).
    "calibration": "AND json_extract(priority_llm, '$.brief') IS :digest "
    "AND COALESCE(json_extract(priority_llm, '$.calibration'), 0) < :calibration",
    "all": "",
}


def candidates_for_review(
    limit: int | None, digest: str, *, again: bool = False, mode: str = "stale"
) -> list[dict[str, Any]]:
    """Open postings worth a review, best by rules first.

    `mode` "new": never reviewed (what a search run just found); "stale": not
    reviewed against this brief (after the brief changed); "calibration":
    reviewed against it, but before the latest calibration. `again` takes
    them whatever their review. Postings the rules or the operator's filters
    exclude are never candidates.
    """
    from . import calibration

    pending = _PENDING["all" if again else mode]
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, company, location, seniority, date_posted, deadline,
                   apply_url, description
            FROM jobs
            WHERE closed_at IS NULL AND dismissed = 0 AND priority_score IS NOT NULL
              AND COALESCE(priority_excluded, '') = '' {pending}
            ORDER BY priority_score DESC, deadline IS NULL, deadline
            LIMIT :limit
            """,
            {
                "digest": digest,
                "calibration": calibration.version(),
                "limit": -1 if limit is None else limit,
            },
        ).fetchall()
    return [dict(row) for row in rows]


def review_counts(digest: str) -> dict[str, int]:
    """How the open postings stand against the brief, for the panel."""
    from . import calibration

    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS open,
              SUM(COALESCE(priority_excluded, '') != '') AS excluded,
              SUM(COALESCE(priority_excluded, '') = '' AND priority_llm IS NULL) AS never,
              SUM(COALESCE(priority_excluded, '') = '' AND priority_llm IS NOT NULL
                  AND json_extract(priority_llm, '$.brief') IS NOT :digest) AS stale,
              SUM(COALESCE(priority_excluded, '') = ''
                  AND json_extract(priority_llm, '$.brief') IS :digest) AS current,
              SUM(COALESCE(priority_excluded, '') = ''
                  AND json_extract(priority_llm, '$.brief') IS :digest
                  AND COALESCE(json_extract(priority_llm, '$.calibration'), 0) < :calibration)
                  AS older_calibration
            FROM jobs WHERE closed_at IS NULL AND dismissed = 0
            """,
            {"digest": digest, "calibration": calibration.version()},
        ).fetchone()
        reasons = conn.execute(
            """
            SELECT priority_excluded AS reason, COUNT(*) AS n FROM jobs
            WHERE closed_at IS NULL AND dismissed = 0 AND COALESCE(priority_excluded, '') != ''
            GROUP BY 1 ORDER BY 2 DESC
            """
        ).fetchall()
    counts = {
        key: int(row[key] or 0)
        for key in ("open", "excluded", "never", "stale", "current", "older_calibration")
    }
    counts["excluded_by_reason"] = {r["reason"]: r["n"] for r in reasons}  # type: ignore[assignment]
    return counts


def average_cost(model: str, sample: int = 200) -> tuple[float | None, int]:
    """What a review with `model` has cost on average, from the last ones stored."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT CAST(json_extract(priority_llm, '$.cost') AS REAL) AS cost FROM jobs
            WHERE json_extract(priority_llm, '$.model') = ?
              AND json_extract(priority_llm, '$.cost') > 0
            ORDER BY json_extract(priority_llm, '$.reviewed_at') DESC
            LIMIT ?
            """,
            (model, sample),
        ).fetchall()
    costs = [row["cost"] for row in rows]
    return (sum(costs) / len(costs), len(costs)) if costs else (None, 0)


def store_review(job_id: str, review: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET priority_llm_score = ?, priority_llm = ? WHERE id = ?",
            (review["score"], json.dumps(review, ensure_ascii=False), job_id),
        )


#: Seconds one review call may take before it counts as failed.
REVIEW_TIMEOUT = 300.0

#: This many failures in a row means the gateway, not the postings: a
#: DNS outage once failed 312 reviews in a row, each a wasted attempt.
GIVE_UP_AFTER = 8


@dataclass
class ReviewRun:
    mode: str = ""
    model: str = ""
    effort: str = ""
    total: int = 0
    reviewed: int = 0
    failed: int = 0
    cost: float = 0.0
    #: The calibration the prompt carried (calibration.py), 0 for none.
    calibration: int = 0
    #: Prompt tokens sent, and how many of them the provider read from its cache.
    prompt_tokens: int = 0
    cached_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    #: Why the run stopped before the end, if it did.
    stopped: str = ""
    running: bool = False
    started_at: str = ""
    finished_at: str = ""


#: The review in progress, or the last one, as the panel shows it.
review_state = ReviewRun()
_review_lock = threading.Lock()
_stop_review = threading.Event()


def review_running() -> bool:
    return review_state.running


def stop_review() -> None:
    """Ask the review in progress to stop; what is reviewed so far is kept."""
    _stop_review.set()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def review_top(
    limit: int | None,
    *,
    again: bool = False,
    mode: str = "stale",
    say: Callable[[str], None] = print,
    client: Any = None,
) -> ReviewRun:
    """Review the `limit` best candidates (all of them with None) against the brief."""
    global review_state
    from . import calibration
    from . import config as config_module
    from .llm import OpenRouterClient, parse_response

    brief = load_brief()
    digest = brief_digest(brief)
    if not _review_lock.acquire(blocking=False):
        raise RuntimeError("a review is already running")
    try:
        _stop_review.clear()
        refresh()
        rows = candidates_for_review(limit, digest, again=again, mode=mode)
        settings = config_module.load()
        calibrated = calibration.load_state()["current"]
        run = ReviewRun(
            mode=mode,
            model=settings.openrouter.model,
            effort=settings.openrouter.reasoning_effort,
            calibration=int(calibrated["version"]) if calibrated else 0,
            total=len(rows),
            running=bool(rows),
            started_at=_now(),
        )
        review_state = run
        if not rows:
            say("nothing to review: every posting worth a review is reviewed against this brief")
            run.finished_at = _now()
            return run

        system = review_system_prompt(brief, calibration.prompt_section(calibrated))
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "priority_review", "strict": True, "schema": REVIEW_SCHEMA},
        }
        owned = client is None
        if client is None:
            import httpx

            # A review at high reasoning effort can think for minutes on a slow
            # provider; the default two minutes failed a run of them at once.
            client = OpenRouterClient(
                settings, httpx.Client(timeout=httpx.Timeout(REVIEW_TIMEOUT, connect=15.0))
            )
            client._owns_client = True  # closed with it at the end of the run

        # Every call of the run opens with the same system prompt (the brief,
        # the calibration, the instructions) and the same schema; only the
        # offer after it differs. Providers cache that prefix, and bill it at
        # a fifth of the price when read back — but only on the endpoint that
        # cached it. OpenRouter keeps a session on one endpoint; without a
        # session it keys stickiness on the first user message too, which is
        # a different offer each time, so nothing would stick.
        session = f"acide-review-{digest}-c{run.calibration}"

        def ask(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            payload = client.complete_raw(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": review_prompt(row)},
                ],
                response_format=response_format,
                session_id=session,
            )
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = None
            return parse_review(parse_response(content or "")), payload

        def review(row: dict[str, Any]) -> tuple[dict[str, Any], float]:
            if _stop_review.is_set():
                raise _Stopped()
            try:
                result, payload = ask(row)
            except Exception:  # noqa: BLE001
                # Once more before giving up on it: a dropped connection or a
                # malformed answer rarely happens twice for the same posting.
                if _stop_review.is_set():
                    raise _Stopped() from None
                result, payload = ask(row)
            usage = payload.get("usage") or {}
            cost = float(usage.get("cost") or 0.0)
            result.update(
                brief=digest,
                calibration=run.calibration,
                model=run.model,
                effort=run.effort,
                cost=cost,
                # What the cost is made of, for comparing models and efforts.
                tokens={
                    "prompt": usage.get("prompt_tokens"),
                    "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                    "completion": usage.get("completion_tokens"),
                    "reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                },
                provider=payload.get("provider", ""),
                reviewed_at=_now(),
            )
            return result, cost

        effort = "" if run.effort == "none" else f", reasoning {run.effort}"
        calibrated_note = f", calibration {run.calibration}" if run.calibration else ""
        say(f"reviewing {len(rows)} postings with {run.model}{effort}{calibrated_note}")
        try:
            with ThreadPoolExecutor(max_workers=settings.openrouter.max_concurrency) as pool:
                # The first call alone, then the rest in parallel: the first
                # writes the shared prefix to the provider's cache, and those
                # sent together after it read it instead of each paying for it.
                batches = [rows[:1], rows[1:]] if len(rows) > 1 else [rows]
                done = in_a_row = 0
                for batch in batches:
                    if _stop_review.is_set():
                        break
                    futures = {pool.submit(review, row): row for row in batch}
                    for future in as_completed(futures):
                        done += 1
                        row = futures[future]
                        if future.cancelled():
                            continue
                        try:
                            result, cost = future.result()
                        except _Stopped:
                            continue
                        except Exception as exc:  # noqa: BLE001 — one bad reply, not the run
                            run.failed += 1
                            in_a_row += 1
                            run.errors = [*run.errors[-19:], f"{row['company']} — {row['title']}: {exc}"]
                            say(f"[{done}/{len(rows)}] FAILED {row['company']} — {row['title']}: {exc}")
                            if in_a_row >= GIVE_UP_AFTER and not run.stopped:
                                # What was not reviewed is picked up by the next run.
                                run.stopped = f"{in_a_row} failures in a row, last: {exc}"
                                say(f"stopping: {run.stopped}")
                                _stop_review.set()
                            continue
                        in_a_row = 0
                        store_review(row["id"], result)
                        run.reviewed += 1
                        run.cost += cost
                        run.prompt_tokens += int(result["tokens"]["prompt"] or 0)
                        run.cached_tokens += int(result["tokens"]["cached"] or 0)
                        say(f"[{done}/{len(rows)}] {result['score']:4d}  {row['company']} — {row['title']}")
                if _stop_review.is_set() and not run.stopped:
                    run.stopped = "stopped on request"
        finally:
            if owned:
                client.close()
        run.running = False
        run.finished_at = _now()
        cached = (
            f", {run.cached_tokens / run.prompt_tokens:.0%} of the prompt from cache"
            if run.prompt_tokens
            else ""
        )
        say(
            f"reviewed {run.reviewed}, failed {run.failed}, cost ${run.cost:.4f}{cached}"
            + (f" — {run.stopped}" if run.stopped else "")
        )
        return run
    finally:
        review_state.running = False
        _review_lock.release()


class _Stopped(Exception):
    """A review not started because the run was asked to stop."""


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------
#: The rules' criteria (and those of reviews made before the brief named its
#: own), as the card shows them.
_RULES_CRITERIA = (
    ("organizzazione", "Organizzazione", 25),
    ("settore", "Settore e missione", 25),
    ("cv", "Aderenza al CV", 20),
    ("crescita", "Crescita", 15),
    ("impatto", "Impatto", 10),
    ("fattibilita", "Fattibilità", 5),
)


def _named(criteria: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"name": label, "points": int(criteria[key]), "max": top}
        for key, label, top in _RULES_CRITERIA
        if key in criteria
    ]


def _read_review(review: dict[str, Any]) -> dict[str, Any]:
    """A stored review in the current shape, whichever shape it was stored in."""
    if "punteggio" in review:  # reviews made before 2026-10-05, Italian keys
        facts = {"Scadenza": "scadenza", "Sede": "sede", "Lingue": "lingue", "Nulla osta": "nulla_osta"}
        return {
            "criteria": _named(review.get("criteria") or {}),
            "adjustments": [],
            "eligible": review.get("candidabile", True),
            "justification": review.get("motivo", ""),
            "issues": review.get("problemi", []),
            "cv_tip": review.get("enfasi_cv", ""),
            "facts": {label: review.get(key, "") for label, key in facts.items()},
            "tier": review.get("fascia"),
        }
    facts = {"Deadline": "deadline", "Location": "location", "Languages": "languages", "Clearance": "clearance"}
    return {
        "criteria": review.get("criteria") or [],
        "adjustments": review.get("adjustments") or [],
        "eligible": review.get("eligible", True),
        "justification": review.get("justification", ""),
        "issues": review.get("issues", []),
        "cv_tip": review.get("cv_tip", ""),
        "facts": {label: review.get(key, "") for label, key in facts.items()},
        "tier": None,
    }


def describe(row: Any) -> dict[str, Any]:
    """The priority fields of a Job, from a jobs row."""
    rules = json.loads(row["priority_rules"]) if row["priority_rules"] else {}
    stored = json.loads(row["priority_llm"]) if row["priority_llm"] else {}
    review = _read_review(stored) if stored else {}
    flags = [Flag(item["text"], item["level"]) for item in rules.get("flags", [])]
    if review and not review["eligible"]:
        flags.insert(0, Flag("Non candidabile secondo la revisione", "block"))
    if review:
        score, source = row["priority_llm_score"], "review"
    elif row["priority_score"] is not None:
        score, source = row["priority_score"], "rules"
    else:
        score, source = None, ""
    keys = row.keys()
    return {
        "priority": score,
        "priority_source": source,
        # Reviewed against a brief (or instructions) that has since changed.
        "priority_stale": bool(stored) and stored.get("brief") != current_digest(),
        "priority_model": stored.get("model", ""),
        "priority_tier": review.get("tier") or rules.get("tier"),
        "priority_criteria": review.get("criteria") or _named(rules.get("criteria") or {}),
        "priority_adjustments": review.get("adjustments", []),
        "priority_reasons": rules.get("reasons", []),
        "priority_flags": [{"text": f.text, "level": f.level} for f in _unique(flags)],
        "priority_summary": review.get("justification", ""),
        "priority_issues": review.get("issues", []),
        "priority_cv_tip": review.get("cv_tip", ""),
        "priority_facts": {k: v for k, v in review.get("facts", {}).items() if v},
        "priority_excluded": (row["priority_excluded"] if "priority_excluded" in keys else "") or "",
    }


def _unique(flags: Iterable[Flag]) -> list[Flag]:
    seen: set[str] = set()
    out = []
    for flag in flags:
        if flag.text not in seen:
            seen.add(flag.text)
            out.append(flag)
    return out
