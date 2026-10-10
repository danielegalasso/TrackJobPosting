"""Which countries a posting's location names, for the "Where" filter.

Locations come from two hundred sites, each writing them its own way:
"Madrid, MD, ES", "IT - Roma - Via Laurentina, Italy", "Office - USA - CA",
"Bengaluru, IND, India", "Remote (...); UK, France, Netherlands", or a bare
"Grenoble". `countries()` reads them all into ISO 3166 alpha-2 codes:

* country names, in English and in the languages the sites use;
* country codes, alpha-2 and alpha-3, when written in capitals as a token of
  their own ("ES", "DEU"), so "in" or "it" as words are never read as codes;
* a US state code ending the location ("King of Prussia, PA");
* the cities that sites write without a country, most of them French
  research centres and US hubs.

What still names no country takes its employer's usual one, when nearly all
of that employer's other postings agree (CEA writes "Saclay", "Grenoble").
"""

from __future__ import annotations

import re
from collections import Counter

#: (code, English name, other spellings and codes, in Europe)
_COUNTRIES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AL", "Albania", ("ALB", "shqipëri"), True),
    ("AD", "Andorra", ("AND",), True),
    ("AT", "Austria", ("AUT", "österreich", "autriche"), True),
    ("BY", "Belarus", ("BLR",), True),
    ("BE", "Belgium", ("BEL", "belgique", "belgië", "belgien", "belgio"), True),
    ("BA", "Bosnia and Herzegovina", ("BIH", "bosnia"), True),
    ("BG", "Bulgaria", ("BGR", "bulgarie"), True),
    ("HR", "Croatia", ("HRV", "hrvatska", "croatie"), True),
    ("CY", "Cyprus", ("CYP", "chypre"), True),
    ("CZ", "Czechia", ("CZE", "czech republic", "česko", "république tchèque"), True),
    ("DK", "Denmark", ("DNK", "danmark", "danemark"), True),
    ("EE", "Estonia", ("EST", "eesti", "estonie"), True),
    ("FI", "Finland", ("FIN", "suomi", "finlande"), True),
    # Not "Francia": in these locations it is a street ("C.so Francia, Torino").
    ("FR", "France", ("FRA", "frankreich"), True),
    ("DE", "Germany", ("DEU", "deutschland", "allemagne", "germania", "alemania"), True),
    ("GR", "Greece", ("GRC", "hellas", "grèce", "grecia"), True),
    ("HU", "Hungary", ("HUN", "magyarország", "hongrie"), True),
    ("IS", "Iceland", ("ISL", "ísland"), True),
    ("IE", "Ireland", ("IRL", "irlande", "éire"), True),
    ("IT", "Italy", ("ITA", "italia", "italie", "italien"), True),
    ("XK", "Kosovo", ("XKX",), True),
    ("LV", "Latvia", ("LVA", "latvija", "lettonie"), True),
    ("LI", "Liechtenstein", ("LIE",), True),
    ("LT", "Lithuania", ("LTU", "lietuva", "lituanie"), True),
    ("LU", "Luxembourg", ("LUX", "luxemburg", "lussemburgo"), True),
    ("MT", "Malta", ("MLT", "malte"), True),
    ("MD", "Moldova", ("MDA",), True),
    ("MC", "Monaco", ("MCO",), True),
    ("ME", "Montenegro", ("MNE",), True),
    ("NL", "Netherlands", ("NLD", "nederland", "the netherlands", "pays-bas", "paesi bassi", "holland"), True),
    ("MK", "North Macedonia", ("MKD", "macedonia"), True),
    ("NO", "Norway", ("NOR", "norge", "norvège"), True),
    ("PL", "Poland", ("POL", "polska", "pologne", "polonia"), True),
    ("PT", "Portugal", ("PRT", "portogallo"), True),
    ("RO", "Romania", ("ROU", "roumanie", "românia"), True),
    ("RU", "Russia", ("RUS", "russian federation"), True),
    ("SM", "San Marino", ("SMR",), True),
    ("RS", "Serbia", ("SRB", "srbija"), True),
    ("SK", "Slovakia", ("SVK", "slovensko", "slovaquie"), True),
    ("SI", "Slovenia", ("SVN", "slovenija", "slovénie"), True),
    ("ES", "Spain", ("ESP", "españa", "espagne", "spagna", "spanien"), True),
    ("SE", "Sweden", ("SWE", "sverige", "suède", "svezia", "schweden"), True),
    ("CH", "Switzerland", ("CHE", "schweiz", "suisse", "svizzera"), True),
    ("UA", "Ukraine", ("UKR", "україна"), True),
    ("GB", "United Kingdom", ("GBR", "UK", "great britain", "england", "scotland", "wales", "northern ireland", "royaume-uni", "regno unito"), True),
    ("VA", "Vatican City", ("VAT",), True),
    ("AR", "Argentina", ("ARG",), False),
    ("AM", "Armenia", ("ARM",), False),
    ("AU", "Australia", ("AUS",), False),
    ("AZ", "Azerbaijan", ("AZE",), False),
    ("BH", "Bahrain", ("BHR",), False),
    ("BD", "Bangladesh", ("BGD",), False),
    ("BR", "Brazil", ("BRA", "brasil"), False),
    ("CA", "Canada", ("CAN",), False),
    ("CL", "Chile", ("CHL",), False),
    ("CN", "China", ("CHN", "people's republic of china"), False),
    ("CO", "Colombia", ("COL",), False),
    ("CR", "Costa Rica", ("CRI",), False),
    ("DO", "Dominican Republic", ("DOM",), False),
    ("PR", "Puerto Rico", ("PRI",), False),
    ("EG", "Egypt", ("EGY",), False),
    ("GE", "Georgia", ("GEO",), False),
    ("GF", "French Guiana", ("GUF", "guyane"), False),
    ("HK", "Hong Kong", ("HKG",), False),
    ("IN", "India", ("IND",), False),
    ("ID", "Indonesia", ("IDN",), False),
    ("IL", "Israel", ("ISR",), False),
    ("JP", "Japan", ("JPN",), False),
    ("JO", "Jordan", ("JOR",), False),
    ("KZ", "Kazakhstan", ("KAZ",), False),
    ("KE", "Kenya", ("KEN",), False),
    ("KR", "South Korea", ("KOR", "korea", "korea, republic of", "republic of korea"), False),
    ("KW", "Kuwait", ("KWT",), False),
    ("MY", "Malaysia", ("MYS",), False),
    ("MX", "Mexico", ("MEX", "méxico"), False),
    ("MA", "Morocco", ("MAR", "maroc"), False),
    ("NZ", "New Zealand", ("NZL",), False),
    ("NG", "Nigeria", ("NGA",), False),
    ("OM", "Oman", ("OMN",), False),
    ("PK", "Pakistan", ("PAK",), False),
    ("PE", "Peru", ("PER", "perú"), False),
    ("PH", "Philippines", ("PHL",), False),
    ("QA", "Qatar", ("QAT",), False),
    ("SA", "Saudi Arabia", ("SAU", "ksa"), False),
    ("SG", "Singapore", ("SGP",), False),
    ("ZA", "South Africa", ("ZAF",), False),
    ("LK", "Sri Lanka", ("LKA",), False),
    ("TW", "Taiwan", ("TWN",), False),
    ("TH", "Thailand", ("THA",), False),
    ("TN", "Tunisia", ("TUN", "tunisie"), False),
    ("TR", "Türkiye", ("TUR", "turkey", "turkiye"), False),
    ("AE", "United Arab Emirates", ("ARE", "UAE", "emirates"), False),
    ("US", "United States", ("USA", "united states of america", "u.s.", "u.s.a."), False),
    ("UY", "Uruguay", ("URY",), False),
    ("VN", "Vietnam", ("VNM", "viet nam"), False),
)

NAMES: dict[str, str] = {code: name for code, name, _, _ in _COUNTRIES}
EUROPE: frozenset[str] = frozenset(code for code, _, _, europe in _COUNTRIES if europe)
#: Where an Italian citizen works without a visa: the EU, the EEA and Switzerland.
FREE_MOVEMENT: frozenset[str] = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "
    "IS LI NO CH".split()
)
#: The filter value meaning "anywhere in Europe".
EUROPE_FILTER = "EUROPE"

#: Two-letter codes that are also US state codes. As the last token of a
#: location they are read as a state ("San Diego, CA"), not a country; DE, IN,
#: ME, MT and AL are left to the country, which is far more common here
#: ("Darmstadt, DE").
_US_STATES = frozenset(
    "AK AZ AR CA CO CT DC FL GA HI IA ID IL KS KY LA MA MD MI MN MO MS NC ND NE NH NJ NM NV NY "
    "OH OK OR PA RI SC SD TN TX UT VA VT WA WI WV WY".split()
)
_US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington, d.c.", "west virginia", "wisconsin", "wyoming",
)

#: Cities sites write without a country.
_CITIES: dict[str, str] = {
    # France
    "paris": "FR", "toulouse": "FR", "grenoble": "FR", "saclay": "FR", "versailles": "FR",
    "bruyères-le-châtel": "FR", "fontenay-aux-roses": "FR", "saint-paul-lez-durance": "FR",
    "cadarache": "FR", "marcoule": "FR", "élancourt": "FR", "vélizy": "FR", "massy": "FR",
    "gif-sur-yvette": "FR", "le ripault": "FR", "valduc": "FR", "cesta": "FR", "gramat": "FR",
    "le barp": "FR", "monts": "FR", "lyon": "FR", "marseille": "FR", "bordeaux": "FR",
    "nice": "FR", "sophia antipolis": "FR", "rennes": "FR", "brest": "FR", "cannes": "FR",
    "mérignac": "FR", "saint-cloud": "FR", "argonay": "FR", "cergy": "FR", "istres": "FR",
    "seclin": "FR", "brétigny-sur-orge": "FR", "saint-médard-en-jalles": "FR",
    "le haillan": "FR", "plessis-robinson": "FR", "issy-les-moulineaux": "FR",
    "kourou": "GF",
    # Germany, Austria, Switzerland
    "munich": "DE", "münchen": "DE", "berlin": "DE", "hamburg": "DE", "frankfurt": "DE",
    "darmstadt": "DE", "bremen": "DE", "stuttgart": "DE", "cologne": "DE", "köln": "DE",
    "ottobrunn": "DE", "friedrichshafen": "DE", "ulm": "DE", "oberpfaffenhofen": "DE",
    "vienna": "AT", "wien": "AT", "zurich": "CH", "zürich": "CH", "geneva": "CH",
    "genève": "CH", "bern": "CH", "lausanne": "CH",
    # Benelux
    "brussels": "BE", "bruxelles": "BE", "brussel": "BE", "mons": "BE", "leuven": "BE",
    "liège": "BE", "antwerp": "BE", "the hague": "NL", "den haag": "NL", "amsterdam": "NL",
    "noordwijk": "NL", "rotterdam": "NL", "utrecht": "NL", "eindhoven": "NL", "delft": "NL",
    "luxembourg": "LU", "betzdorf": "LU",
    # Italy, Spain, Portugal
    "rome": "IT", "roma": "IT", "milan": "IT", "milano": "IT", "turin": "IT", "torino": "IT",
    "naples": "IT", "napoli": "IT", "frascati": "IT", "genoa": "IT", "genova": "IT",
    "trento": "IT", "la spezia": "IT", "heidelberg": "DE", "maastricht": "NL",
    "stavanger": "NO", "northwood": "GB",
    "madrid": "ES", "barcelona": "ES", "seville": "ES", "sevilla": "ES", "lisbon": "PT",
    "lisboa": "PT", "porto": "PT",
    # Nordics, Baltics, central and eastern Europe
    "stockholm": "SE", "gothenburg": "SE", "linköping": "SE", "oslo": "NO", "kongsberg": "NO",
    "copenhagen": "DK", "helsinki": "FI", "espoo": "FI", "tallinn": "EE", "tartu": "EE",
    "riga": "LV", "vilnius": "LT", "warsaw": "PL", "warszawa": "PL", "krakow": "PL",
    "kraków": "PL", "prague": "CZ", "praha": "CZ", "brno": "CZ", "bratislava": "SK",
    "budapest": "HU", "bucharest": "RO", "bucurești": "RO", "sofia": "BG", "athens": "GR",
    "kyiv": "UA", "belgrade": "RS",
    # United Kingdom and Ireland
    "london": "GB", "manchester": "GB", "cambridge, uk": "GB", "oxford": "GB",
    "edinburgh": "GB", "cheltenham": "GB", "bristol": "GB", "dublin": "IE",
    # North America
    "new york": "US", "san francisco": "US", "seattle": "US", "san diego": "US",
    "washington": "US", "austin": "US", "boston": "US", "chicago": "US", "denver": "US",
    "arlington": "US", "reston": "US", "mclean": "US", "huntsville": "US", "palo alto": "US",
    "toronto": "CA", "montreal": "CA", "montréal": "CA", "vancouver": "CA", "ottawa": "CA",
    # Asia and elsewhere
    "bangalore": "IN", "bengaluru": "IN", "pune": "IN", "hyderabad": "IN", "chennai": "IN",
    "mumbai": "IN", "noida": "IN", "gurgaon": "IN", "gurugram": "IN", "tel aviv": "IL",
    "tokyo": "JP", "東京都": "JP", "seoul": "KR", "shanghai": "CN", "beijing": "CN", "shenzhen": "CN",
    "dubai": "AE", "abu dhabi": "AE", "riyadh": "SA", "sydney": "AU", "melbourne": "AU",
}


def _phrase(text: str) -> str:
    # A hyphen separates too: "Türkiye-Izmir", "United States-Illinois-...".
    return r"(?<!\w)" + re.escape(text) + r"(?!\w)"


#: Lower-case names and spellings, longest first so "united states of america"
#: wins over "united states", and "new south wales" is not read as Wales.
_NAMES_RE = re.compile(
    "|".join(
        f"(?P<n{index}>{_phrase(text)})"
        for index, text in enumerate(
            sorted(
                {
                    alias.lower()
                    for code, name, aliases, _ in _COUNTRIES
                    for alias in (name, *aliases)
                    if not alias.isupper()
                },
                key=len,
                reverse=True,
            )
        )
    ),
    re.I,
)
_NAME_CODES: dict[str, str] = {
    alias.lower(): code
    for code, name, aliases, _ in _COUNTRIES
    for alias in (name, *aliases)
    if not alias.isupper()
}
#: Upper-case codes: alpha-2 and alpha-3, and abbreviations such as UK, UAE.
_CODES: dict[str, str] = {code: code for code in NAMES}
_CODES.update(
    {alias: code for code, _, aliases, _ in _COUNTRIES for alias in aliases if alias.isupper()}
)
_TOKEN = re.compile(r"\b[A-Z]{2,3}\b")
_STATE_NAMES_RE = re.compile("|".join(_phrase(name) for name in _US_STATE_NAMES), re.I)
_CITIES_RE = re.compile(
    "|".join(_phrase(city) for city in sorted(_CITIES, key=len, reverse=True)), re.I
)
#: "New South Wales" must not read as Wales, nor "New Mexico" as Mexico.
_MASKED = re.compile(r"new south wales|new mexico|georgia, (?:usa|us|united states)", re.I)


def countries(location: str) -> list[str]:
    """The ISO codes a location names, in the order they appear; [] if none."""
    text = _MASKED.sub(lambda m: "US" if "georgia" in m.group(0).lower() else " ", location or "")
    found: dict[str, int] = {}

    def add(code: str, at: int) -> None:
        found.setdefault(code, at)

    for match in _NAMES_RE.finditer(text):
        add(_NAME_CODES[match.group(0).lower()], match.start())
    tokens = list(_TOKEN.finditer(text))
    for index, match in enumerate(tokens):
        token = match.group(0)
        last = index == len(tokens) - 1 and not text[match.end() :].strip(" ,;)")
        # "IT - Roma - ...", "DE - Darmstadt": Leonardo's country-first form.
        leading = match.start() == 0 and text[match.end() :].startswith(" - ")
        if _CODES.get(token) in found:
            continue  # "Tel Aviv, Israel, IL": the country already named
        if token in _US_STATES and (last or "US" in found):
            add("US", match.start())
        elif token in _CODES and (len(token) == 3 or token in ("UK",) or last or leading):
            # A two-letter code elsewhere is more often a region ("Madrid,
            # MD, ES": the Comunidad de Madrid, not Moldova).
            add(_CODES[token], match.start())
    if not found:
        for match in _STATE_NAMES_RE.finditer(text):
            add("US", match.start())
    if not found:
        for match in _CITIES_RE.finditer(text):
            add(_CITIES[match.group(0).lower()], match.start())
    return sorted(found, key=found.__getitem__)


def encode(codes: list[str]) -> str:
    """How the codes are stored: ",IT,FR," so one LIKE finds any of them."""
    return f",{','.join(codes)}," if codes else ""


def company_defaults(rows: list[tuple[str, list[str]]], share: float = 0.8) -> dict[str, str]:
    """Each employer's usual country, where at least `share` of the postings
    that name one name only that one."""
    per_company: dict[str, Counter[str]] = {}
    for company, codes in rows:
        if len(codes) == 1:
            per_company.setdefault(company, Counter())[codes[0]] += 1
    defaults = {}
    for company, counts in per_company.items():
        code, n = counts.most_common(1)[0]
        if n >= 3 and n / sum(counts.values()) >= share:
            defaults[company] = code
    return defaults
