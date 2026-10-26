"""The other station's local time.

Offline, like the prefix lookup: the DXCC entity (cty.dat) picks the zone. Countries with
several zones use, in order, the QRZ state/province, the call area (Canada and Australia tie it
to the province/state; US call areas no longer follow residence, so a US call area is a guess),
then the longitude. Guesses are marked approximate. Zones are IANA ids (daylight saving from
tzdata); an entity with no mapping falls back to cty.dat's fixed standard offset (approximate).
"""
import datetime as dt
from zoneinfo import ZoneInfo

from . import geo

ENTITIES = {
    'England': 'Europe/London',
    'Scotland': 'Europe/London',
    'Wales': 'Europe/London',
    'Northern Ireland': 'Europe/London',
    'Shetland Islands': 'Europe/London',
    'Isle of Man': 'Europe/Isle_of_Man',
    'Jersey': 'Europe/Jersey',
    'Guernsey': 'Europe/Guernsey',
    'Ireland': 'Europe/Dublin',
    'Portugal': 'Europe/Lisbon',
    'Madeira Islands': 'Atlantic/Madeira',
    'Azores': 'Atlantic/Azores',
    'Spain': 'Europe/Madrid',
    'Balearic Islands': 'Europe/Madrid',
    'Canary Islands': 'Atlantic/Canary',
    'Ceuta & Melilla': 'Africa/Ceuta',
    'France': 'Europe/Paris',
    'Corsica': 'Europe/Paris',
    'Monaco': 'Europe/Monaco',
    'Andorra': 'Europe/Andorra',
    'Belgium': 'Europe/Brussels',
    'Netherlands': 'Europe/Amsterdam',
    'Luxembourg': 'Europe/Luxembourg',
    'Fed. Rep. of Germany': 'Europe/Berlin',
    'Switzerland': 'Europe/Zurich',
    'Liechtenstein': 'Europe/Vaduz',
    'Austria': 'Europe/Vienna',
    'Vienna Intl Ctr': 'Europe/Vienna',
    'ITU HQ': 'Europe/Zurich',
    'Italy': 'Europe/Rome',
    'Sardinia': 'Europe/Rome',
    'Sicily': 'Europe/Rome',
    'African Italy': 'Europe/Rome',
    'San Marino': 'Europe/San_Marino',
    'Vatican City': 'Europe/Vatican',
    'Sov Mil Order of Malta': 'Europe/Rome',
    'Malta': 'Europe/Malta',
    'Gibraltar': 'Europe/Gibraltar',
    'Denmark': 'Europe/Copenhagen',
    'Faroe Islands': 'Atlantic/Faroe',
    'Norway': 'Europe/Oslo',
    'Svalbard': 'Arctic/Longyearbyen',
    'Bear Island': 'Arctic/Longyearbyen',
    'Jan Mayen': 'Arctic/Longyearbyen',
    'Sweden': 'Europe/Stockholm',
    'Finland': 'Europe/Helsinki',
    'Aland Islands': 'Europe/Mariehamn',
    'Market Reef': 'Europe/Mariehamn',
    'Iceland': 'Atlantic/Reykjavik',
    'Estonia': 'Europe/Tallinn',
    'Latvia': 'Europe/Riga',
    'Lithuania': 'Europe/Vilnius',
    'Poland': 'Europe/Warsaw',
    'Czech Republic': 'Europe/Prague',
    'Slovak Republic': 'Europe/Bratislava',
    'Hungary': 'Europe/Budapest',
    'Slovenia': 'Europe/Ljubljana',
    'Croatia': 'Europe/Zagreb',
    'Bosnia-Herzegovina': 'Europe/Sarajevo',
    'Serbia': 'Europe/Belgrade',
    'Montenegro': 'Europe/Podgorica',
    'Republic of Kosovo': 'Europe/Belgrade',
    'North Macedonia': 'Europe/Skopje',
    'Albania': 'Europe/Tirane',
    'Greece': 'Europe/Athens',
    'Mount Athos': 'Europe/Athens',
    'Dodecanese': 'Europe/Athens',
    'Crete': 'Europe/Athens',
    'Bulgaria': 'Europe/Sofia',
    'Romania': 'Europe/Bucharest',
    'Moldova': 'Europe/Chisinau',
    'Ukraine': 'Europe/Kyiv',
    'Belarus': 'Europe/Minsk',
    'Kaliningrad': 'Europe/Kaliningrad',
    'Franz Josef Land': 'Europe/Moscow',
    'European Turkey': 'Europe/Istanbul',
    'Asiatic Turkey': 'Europe/Istanbul',
    'Cyprus': 'Asia/Nicosia',
    'UK Base Areas on Cyprus': 'Asia/Nicosia',
    'Israel': 'Asia/Jerusalem',
    'Palestine': 'Asia/Hebron',
    'Lebanon': 'Asia/Beirut',
    'Syria': 'Asia/Damascus',
    'Jordan': 'Asia/Amman',
    'Iraq': 'Asia/Baghdad',
    'Iran': 'Asia/Tehran',
    'Kuwait': 'Asia/Kuwait',
    'Saudi Arabia': 'Asia/Riyadh',
    'Bahrain': 'Asia/Bahrain',
    'Qatar': 'Asia/Qatar',
    'United Arab Emirates': 'Asia/Dubai',
    'Oman': 'Asia/Muscat',
    'Yemen': 'Asia/Aden',
    'Georgia': 'Asia/Tbilisi',
    'Armenia': 'Asia/Yerevan',
    'Azerbaijan': 'Asia/Baku',
    'Turkmenistan': 'Asia/Ashgabat',
    'Uzbekistan': 'Asia/Tashkent',
    'Tajikistan': 'Asia/Dushanbe',
    'Kyrgyzstan': 'Asia/Bishkek',
    'Afghanistan': 'Asia/Kabul',
    'Pakistan': 'Asia/Karachi',
    'India': 'Asia/Kolkata',
    'Andaman & Nicobar Is.': 'Asia/Kolkata',
    'Lakshadweep Islands': 'Asia/Kolkata',
    'Sri Lanka': 'Asia/Colombo',
    'Maldives': 'Indian/Maldives',
    'Nepal': 'Asia/Kathmandu',
    'Bhutan': 'Asia/Thimphu',
    'Bangladesh': 'Asia/Dhaka',
    'Myanmar': 'Asia/Yangon',
    'Thailand': 'Asia/Bangkok',
    'Laos': 'Asia/Vientiane',
    'Cambodia': 'Asia/Phnom_Penh',
    'Vietnam': 'Asia/Ho_Chi_Minh',
    'West Malaysia': 'Asia/Kuala_Lumpur',
    'East Malaysia': 'Asia/Kuching',
    'Singapore': 'Asia/Singapore',
    'Brunei Darussalam': 'Asia/Brunei',
    'Philippines': 'Asia/Manila',
    'Timor - Leste': 'Asia/Dili',
    'China': 'Asia/Shanghai',
    'Hong Kong': 'Asia/Hong_Kong',
    'Macao': 'Asia/Macau',
    'Taiwan': 'Asia/Taipei',
    'Pratas Island': 'Asia/Taipei',
    'Republic of Korea': 'Asia/Seoul',
    'DPR of Korea': 'Asia/Pyongyang',
    'Japan': 'Asia/Tokyo',
    'Ogasawara': 'Asia/Tokyo',
    'Morocco': 'Africa/Casablanca',
    'Western Sahara': 'Africa/El_Aaiun',
    'Algeria': 'Africa/Algiers',
    'Tunisia': 'Africa/Tunis',
    'Libya': 'Africa/Tripoli',
    'Egypt': 'Africa/Cairo',
    'Sudan': 'Africa/Khartoum',
    'Republic of South Sudan': 'Africa/Juba',
    'Ethiopia': 'Africa/Addis_Ababa',
    'Eritrea': 'Africa/Asmara',
    'Djibouti': 'Africa/Djibouti',
    'Somalia': 'Africa/Mogadishu',
    'Kenya': 'Africa/Nairobi',
    'Uganda': 'Africa/Kampala',
    'Tanzania': 'Africa/Dar_es_Salaam',
    'Rwanda': 'Africa/Kigali',
    'Burundi': 'Africa/Bujumbura',
    'Mozambique': 'Africa/Maputo',
    'Malawi': 'Africa/Blantyre',
    'Zambia': 'Africa/Lusaka',
    'Zimbabwe': 'Africa/Harare',
    'Botswana': 'Africa/Gaborone',
    'Namibia': 'Africa/Windhoek',
    'South Africa': 'Africa/Johannesburg',
    'Lesotho': 'Africa/Maseru',
    'Kingdom of Eswatini': 'Africa/Mbabane',
    'Angola': 'Africa/Luanda',
    'Republic of the Congo': 'Africa/Brazzaville',
    'Gabon': 'Africa/Libreville',
    'Equatorial Guinea': 'Africa/Malabo',
    'Cameroon': 'Africa/Douala',
    'Central African Republic': 'Africa/Bangui',
    'Chad': 'Africa/Ndjamena',
    'Nigeria': 'Africa/Lagos',
    'Niger': 'Africa/Niamey',
    'Benin': 'Africa/Porto-Novo',
    'Togo': 'Africa/Lome',
    'Ghana': 'Africa/Accra',
    "Cote d'Ivoire": 'Africa/Abidjan',
    'Burkina Faso': 'Africa/Ouagadougou',
    'Mali': 'Africa/Bamako',
    'Senegal': 'Africa/Dakar',
    'The Gambia': 'Africa/Banjul',
    'Guinea': 'Africa/Conakry',
    'Guinea-Bissau': 'Africa/Bissau',
    'Sierra Leone': 'Africa/Freetown',
    'Liberia': 'Africa/Monrovia',
    'Mauritania': 'Africa/Nouakchott',
    'Cabo Verde': 'Atlantic/Cape_Verde',
    'Sao Tome & Principe': 'Africa/Sao_Tome',
    'Madagascar': 'Indian/Antananarivo',
    'Mauritius': 'Indian/Mauritius',
    'Reunion Island': 'Indian/Reunion',
    'Mayotte': 'Indian/Mayotte',
    'Comoros': 'Indian/Comoro',
    'Seychelles': 'Indian/Mahe',
    'St. Helena': 'Atlantic/St_Helena',
    'Ascension Island': 'Atlantic/St_Helena',
    'Alaska': 'America/Anchorage',
    'Hawaii': 'Pacific/Honolulu',
    'Puerto Rico': 'America/Puerto_Rico',
    'US Virgin Islands': 'America/St_Thomas',
    'Guantanamo Bay': 'America/Havana',
    'United Nations HQ': 'America/New_York',
    'St. Pierre & Miquelon': 'America/Miquelon',
    'Bermuda': 'Atlantic/Bermuda',
    'Bahamas': 'America/Nassau',
    'Cuba': 'America/Havana',
    'Cayman Islands': 'America/Cayman',
    'Jamaica': 'America/Jamaica',
    'Haiti': 'America/Port-au-Prince',
    'Dominican Republic': 'America/Santo_Domingo',
    'Turks & Caicos Islands': 'America/Grand_Turk',
    'Guatemala': 'America/Guatemala',
    'Belize': 'America/Belize',
    'El Salvador': 'America/El_Salvador',
    'Honduras': 'America/Tegucigalpa',
    'Nicaragua': 'America/Managua',
    'Costa Rica': 'America/Costa_Rica',
    'Panama': 'America/Panama',
    'Colombia': 'America/Bogota',
    'Venezuela': 'America/Caracas',
    'Ecuador': 'America/Guayaquil',
    'Galapagos Islands': 'Pacific/Galapagos',
    'Peru': 'America/Lima',
    'Bolivia': 'America/La_Paz',
    'Chile': 'America/Santiago',
    'Juan Fernandez Islands': 'America/Santiago',
    'Easter Island': 'Pacific/Easter',
    'Argentina': 'America/Argentina/Buenos_Aires',
    'Uruguay': 'America/Montevideo',
    'Paraguay': 'America/Asuncion',
    'Guyana': 'America/Guyana',
    'Suriname': 'America/Paramaribo',
    'French Guiana': 'America/Cayenne',
    'Falkland Islands': 'Atlantic/Stanley',
    'Fernando de Noronha': 'America/Noronha',
    'Barbados': 'America/Barbados',
    'Trinidad & Tobago': 'America/Port_of_Spain',
    'Grenada': 'America/Grenada',
    'St. Lucia': 'America/St_Lucia',
    'St. Vincent': 'America/St_Vincent',
    'Dominica': 'America/Dominica',
    'Martinique': 'America/Martinique',
    'Guadeloupe': 'America/Guadeloupe',
    'Antigua & Barbuda': 'America/Antigua',
    'St. Kitts & Nevis': 'America/St_Kitts',
    'Anguilla': 'America/Anguilla',
    'Montserrat': 'America/Montserrat',
    'British Virgin Islands': 'America/Tortola',
    'Aruba': 'America/Aruba',
    'Curacao': 'America/Curacao',
    'Bonaire': 'America/Kralendijk',
    'Sint Maarten': 'America/Lower_Princes',
    'St. Martin': 'America/Marigot',
    'St. Barthelemy': 'America/St_Barthelemy',
    'New Zealand': 'Pacific/Auckland',
    'Chatham Islands': 'Pacific/Chatham',
    'Norfolk Island': 'Pacific/Norfolk',
    'Lord Howe Island': 'Australia/Lord_Howe',
    'Macquarie Island': 'Australia/Hobart',
    'Christmas Island': 'Indian/Christmas',
    'Cocos (Keeling) Islands': 'Indian/Cocos',
    'Papua New Guinea': 'Pacific/Port_Moresby',
    'Solomon Islands': 'Pacific/Guadalcanal',
    'Vanuatu': 'Pacific/Efate',
    'New Caledonia': 'Pacific/Noumea',
    'Fiji': 'Pacific/Fiji',
    'Tonga': 'Pacific/Tongatapu',
    'Samoa': 'Pacific/Apia',
    'American Samoa': 'Pacific/Pago_Pago',
    'Guam': 'Pacific/Guam',
    'Mariana Islands': 'Pacific/Saipan',
    'Palau': 'Pacific/Palau',
    'Marshall Islands': 'Pacific/Majuro',
    'Nauru': 'Pacific/Nauru',
    'Tuvalu': 'Pacific/Funafuti',
    'Western Kiribati': 'Pacific/Tarawa',
    'Central Kiribati': 'Pacific/Kanton',
    'Eastern Kiribati': 'Pacific/Kiritimati',
    'French Polynesia': 'Pacific/Tahiti',
    'Austral Islands': 'Pacific/Tahiti',
    'Marquesas Islands': 'Pacific/Marquesas',
    'Wallis & Futuna Islands': 'Pacific/Wallis',
    'South Cook Islands': 'Pacific/Rarotonga',
    'North Cook Islands': 'Pacific/Rarotonga',
    'Niue': 'Pacific/Niue',
    'Tokelau Islands': 'Pacific/Fakaofo',
    'Pitcairn Island': 'Pacific/Pitcairn',
    'Wake Island': 'Pacific/Wake',
    'Midway Island': 'Pacific/Midway',
}

US_STATE_NAMES = {'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR', 'CALIFORNIA': 'CA', 'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE', 'DISTRICT OF COLUMBIA': 'DC', 'FLORIDA': 'FL', 'GEORGIA': 'GA', 'HAWAII': 'HI', 'IDAHO': 'ID', 'ILLINOIS': 'IL', 'INDIANA': 'IN', 'IOWA': 'IA', 'KANSAS': 'KS', 'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME', 'MARYLAND': 'MD', 'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN', 'MISSISSIPPI': 'MS', 'MISSOURI': 'MO', 'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV', 'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ', 'NEW MEXICO': 'NM', 'NEW YORK': 'NY', 'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH', 'OKLAHOMA': 'OK', 'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC', 'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT', 'VERMONT': 'VT', 'VIRGINIA': 'VA', 'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV', 'WISCONSIN': 'WI', 'WYOMING': 'WY'}
_US = {**{s: "America/New_York" for s in "CT DE DC FL GA IN KY ME MD MA MI NH NJ NY NC OH PA RI SC VT VA WV".split()},
       **{s: "America/Chicago" for s in "AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI".split()},
       **{s: "America/Denver" for s in "CO ID MT NM UT WY".split()},
       "AZ": "America/Phoenix", **{s: "America/Los_Angeles" for s in "CA NV OR WA".split()},
       "AK": "America/Anchorage", "HI": "Pacific/Honolulu"}


def _table(rows):
    out = {}
    for keys, zone in rows:
        for k in keys:
            out[k] = zone
    return out


_CANADA = _table([
    (("NS", "NOVA SCOTIA", "NB", "NEW BRUNSWICK", "PE", "PEI", "PRINCE EDWARD ISLAND"), "America/Halifax"),
    (("NL", "NF", "NEWFOUNDLAND", "NEWFOUNDLAND AND LABRADOR"), "America/St_Johns"),
    (("QC", "PQ", "QUEBEC", "QUÉBEC", "ON", "ONTARIO"), "America/Toronto"),
    (("MB", "MANITOBA"), "America/Winnipeg"), (("SK", "SASKATCHEWAN"), "America/Regina"),
    (("AB", "ALBERTA"), "America/Edmonton"), (("BC", "BRITISH COLUMBIA"), "America/Vancouver"),
    (("YT", "YUKON"), "America/Whitehorse"), (("NT", "NORTHWEST TERRITORIES"), "America/Yellowknife"),
    (("NU", "NUNAVUT"), "America/Iqaluit")])
_AUSTRALIA = _table([
    (("ACT", "NSW", "NEW SOUTH WALES", "AUSTRALIAN CAPITAL TERRITORY"), "Australia/Sydney"),
    (("VIC", "VICTORIA"), "Australia/Melbourne"), (("QLD", "QUEENSLAND"), "Australia/Brisbane"),
    (("SA", "SOUTH AUSTRALIA"), "Australia/Adelaide"), (("WA", "WESTERN AUSTRALIA"), "Australia/Perth"),
    (("TAS", "TASMANIA"), "Australia/Hobart"), (("NT", "NORTHERN TERRITORY"), "Australia/Darwin")])
_AU_AREA = {1: "Australia/Sydney", 2: "Australia/Sydney", 3: "Australia/Melbourne", 4: "Australia/Brisbane",
            5: "Australia/Adelaide", 6: "Australia/Perth", 7: "Australia/Hobart", 8: "Australia/Darwin"}
_US_AREA = {1: "America/New_York", 2: "America/New_York", 3: "America/New_York", 4: "America/New_York", 8: "America/New_York",
            5: "America/Chicago", 9: "America/Chicago", 0: "America/Chicago", 6: "America/Los_Angeles", 7: "America/Denver"}
_CA_AREA = {1: "America/Halifax", 9: "America/Halifax", 2: "America/Toronto", 3: "America/Toronto", 4: "America/Winnipeg",
            5: "America/Regina", 6: "America/Edmonton", 7: "America/Vancouver", 8: "America/Yellowknife"}


def _home_part(call):
    parts = [p for p in call.split("/") if p]
    return max(parts, key=len) if parts else ""


def _area_digit(call):
    for ch in _home_part(call):
        if ch.isdigit():
            return int(ch)
    return None


def _region(table, region):
    if not region or not region.strip():
        return None
    r = region.strip().upper()
    r = US_STATE_NAMES.get(r, r)
    if r in table:
        return table[r]
    for part in r.split(","):                     # QRZ gives "County, State"
        p = US_STATE_NAMES.get(part.strip(), part.strip())
        if p in table:
            return table[p]
    return None


def _us_by_lon(lon):
    return "America/Los_Angeles" if lon < -114.5 else "America/Denver" if lon < -101.5 else "America/Chicago" if lon < -86.5 else "America/New_York"


def _canada_by_area(call):
    p = _home_part(call)
    for prefix, zone in (("VO1", "America/St_Johns"), ("VO2", "America/Goose_Bay"), ("VY0", "America/Iqaluit"),
                         ("VY1", "America/Whitehorse"), ("VY2", "America/Halifax")):
        if p.startswith(prefix):
            return zone
    return _CA_AREA.get(_area_digit(call))


def _canada_by_lon(lon):
    return ("America/Vancouver" if lon < -120 else "America/Edmonton" if lon < -110 else "America/Regina" if lon < -102
            else "America/Winnipeg" if lon < -90 else "America/Toronto" if lon < -64 else "America/Halifax")


def _by_longitude(country, lon):
    if country == "European Russia":
        return "Europe/Samara" if lon >= 48.5 else "Europe/Moscow"
    if country == "Asiatic Russia":
        for limit, zone in ((67, "Asia/Yekaterinburg"), (77, "Asia/Omsk"), (87, "Asia/Novosibirsk"), (99, "Asia/Krasnoyarsk"),
                            (119, "Asia/Irkutsk"), (135, "Asia/Yakutsk"), (146, "Asia/Vladivostok"), (160, "Asia/Magadan")):
            if lon < limit:
                return zone
        return "Asia/Kamchatka"
    if country == "Brazil":
        return "America/Rio_Branco" if lon < -67.5 else "America/Manaus" if lon < -53 else "America/Sao_Paulo"
    if country == "Mexico":
        return "America/Tijuana" if lon < -114.7 else "America/Hermosillo" if lon < -108 else "America/Mazatlan" if lon < -104.5 else "America/Mexico_City"
    if country == "Indonesia":
        return "Asia/Jakarta" if lon < 114.5 else "Asia/Makassar" if lon < 127 else "Asia/Jayapura"
    if country == "Dem. Rep. of the Congo":
        return "Africa/Kinshasa" if lon < 20 else "Africa/Lubumbashi"
    if country == "Mongolia":
        return "Asia/Hovd" if lon < 100 else "Asia/Ulaanbaatar"
    if country == "Micronesia":
        return "Pacific/Chuuk" if lon < 154 else "Pacific/Pohnpei"
    if country == "Greenland":
        return "America/Scoresbysund" if lon > -25 else "America/Thule" if lon < -60 else "America/Nuuk"
    if country == "Kazakhstan":
        return "Asia/Aqtau" if lon < 56 else "Asia/Almaty"
    return None


def _zone(zid):
    try:
        return ZoneInfo(zid)
    except Exception:
        return None


def station_time(call, region=None, lon=None, when=None):
    """{"zone", "local", "offset", "approx", "text"} for a callsign, or None if it cannot be placed.
    text reads "22:14 (UTC+9)", "≈ 08:14 (UTC−5)" or "17:44 (UTC+5:30)"."""
    entity = geo.locate_call(call)
    if not entity:
        return None
    call = (call or "").strip().upper()
    country, approx = entity["country"], False
    if country == "United States":
        zid = _region(_US, region)
        if not zid:
            zid = _us_by_lon(lon) if lon is not None else (_US_AREA.get(_area_digit(call)) or "America/Chicago")
            approx = True
    elif country == "Canada":
        zid = _region(_CANADA, region) or _canada_by_area(call)
        if not zid:
            zid, approx = (_canada_by_lon(lon) if lon is not None else "America/Toronto"), True
    elif country == "Australia":
        zid = _region(_AUSTRALIA, region) or _AU_AREA.get(_area_digit(call))
        if not zid:
            a = lon if lon is not None else 150
            zid, approx = ("Australia/Perth" if a < 129 else "Australia/Adelaide" if a < 141 else "Australia/Sydney"), True
    else:
        zid = _by_longitude(country, lon if lon is not None else entity["lon"])
        approx = zid is not None
        zid = zid or ENTITIES.get(country)
    now = when or dt.datetime.now(dt.timezone.utc)
    zone = _zone(zid) if zid else None
    if zone:
        local = now.astimezone(zone)
        offset = local.utcoffset()
    elif entity.get("gmt") is not None:
        offset, approx, zid = dt.timedelta(hours=entity["gmt"]), True, None
        local = now + offset
    else:
        return None
    mins = int(offset.total_seconds() // 60)
    h, m = divmod(abs(mins), 60)
    off = "UTC" if mins == 0 else f"UTC{'−' if mins < 0 else '+'}{h}" + (f":{m:02d}" if m else "")
    return {"zone": zid, "local": local.strftime("%H:%M"), "offset": off, "approx": approx,
            "text": ("≈ " if approx else "") + f"{local:%H:%M} ({off})"}
