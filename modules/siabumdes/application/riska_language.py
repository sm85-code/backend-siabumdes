"""Conservative Indonesian intent/entity parsing. No model calls or guessed financial values."""
import calendar
import re
import shlex
from datetime import date, timedelta
from decimal import Decimal

MONTHS = ['januari', 'februari', 'maret', 'april', 'mei', 'juni', 'juli', 'agustus',
          'september', 'oktober', 'november', 'desember']
MONTH_ALIASES = {name: index + 1 for index, name in enumerate(MONTHS)}
MONTH_ALIASES.update(dict(zip(['jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'agu', 'ags', 'sep', 'sept', 'okt', 'nov', 'des'],
                              [1, 2, 3, 4, 6, 7, 8, 8, 9, 9, 10, 11, 12])))
NAMED_DATE = r'\b(\d{1,2})\s*(' + '|'.join(sorted(MONTH_ALIASES, key=len, reverse=True)) + r')\b'
FIELDS = {
    'imbal': ['mitra', 'tanggal', 'bulan', 'tahun', 'nominal'],
    'masuk': ['produk', 'qty', 'harga', 'pemasok', 'tanggal'],
    'modal': ['produk', 'qty', 'harga', 'tanggal'],
    'keluar': ['produk', 'qty', 'harga', 'customer', 'tanggal'],
    'rusak': ['produk', 'qty', 'tanggal'],
}
QUESTIONS = {
    'produk': 'Barang apa yang dicatat? Sebutkan SKU atau nama produk lengkap.',
    'qty': 'Berapa jumlah barangnya (pcs)?',
    'harga': 'Berapa harga per barangnya? Contoh: 20 ribu. Harga ini per barang, bukan total pembelian.',
    'pemasok': 'Dari pemasok siapa? Sebutkan ID atau nama lengkap pemasok yang terdaftar.',
    'customer': 'Untuk pelanggan siapa? Sebutkan ID atau nama lengkap pelanggan yang terdaftar.',
    'mitra': 'Dari mitra siapa? Sebutkan ID atau nama lengkap mitra yang terdaftar.',
    'tanggal': 'Tanggal transaksi kapan? Boleh jawab “hari ini”, “kemarin”, “10 Oktober 2026”, atau “10-10-2026”.',
    'bulan': 'Pembayaran imbal hasil untuk bulan berapa?',
    'tahun': 'Pembayaran imbal hasil untuk tahun berapa?',
    'nominal': 'Berapa nominal setoran imbal hasilnya? Contoh: 300 ribu.',
    'unit': 'Untuk unit mana? Sebutkan kode atau nama unit lengkap, atau pilih entitas di atas.',
}
AMOUNT = r'(?:Rp\s*)?\d[\d.,]*(?:\s*(?:ribu|juta|rb|jt|k))?'


def clean(message):
    return re.sub(r'^\s*riska\s*[,!:]?\s*', '', message.strip(), flags=re.I).strip()


def intent(message):
    text = clean(message).lower()
    # Negation must never turn into a write action.
    if re.search(r'\b(jangan|batalkan|batal|tidak|bukan)\b', text):
        return 'cancel'
    for action, pattern in [('lock', r'\bkunci periode\b'), ('close', r'\btutup buku\b'),
                            ('transfer', r'\btransfer bagi hasil\b')]:
        if re.search(pattern, text):
            return action
    for action, pattern in [('modal', r'\bbelanja modal(?: awal)?\b'),
                            ('masuk', r'\b(?:barang|stok|stock) masuk\b|^masuk\b'),
                            ('keluar', r'\b(?:barang|stok|stock) keluar\b|^keluar\b|\bjual barang\b'),
                            ('rusak', r'\b(?:barang|stok) rusak\b|^rusak\b'),
                            ('imbal', r'^imbal\s+\w+=|\b(?:input|catat|setoran|bayar|pembayaran|terima)\b.*\bimbal hasil\b')]:
        if re.search(pattern, text):
            return action
    if re.search(r'\b(laba|rugi|pendapatan|omzet|performa|profit|profitabilitas)\b', text):
        return 'profit'
    if re.search(r'\b(saldo|kas|bank)\b', text):
        return 'cash'
    return 'unknown'


def amount(value):
    raw = re.sub(r'^rp\s*', '', str(value).strip(), flags=re.I).lower()
    match = re.fullmatch(r'(\d[\d.,]*)\s*(ribu|juta|rb|jt|k)?', raw)
    if not match:
        raise ValueError('Nominal belum jelas; tulis misalnya 20000 atau 20 ribu.')
    number, suffix = match.groups()
    if ',' in number:
        # Indonesian decimal comma; dots, if present, must be thousands separators.
        whole, decimal = number.split(',')
        if '.' in whole and not re.fullmatch(r'\d{1,3}(?:\.\d{3})+', whole):
            raise ValueError('Pemisah nominal tidak valid.')
        number = whole.replace('.', '') + '.' + decimal
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', number):
        number = number.replace('.', '')
    elif number.count('.') > 1:
        raise ValueError('Pemisah nominal tidak valid.')
    result = Decimal(number) * {'ribu': 1000, 'rb': 1000, 'k': 1000, 'juta': 1000000, 'jt': 1000000}.get(suffix, 1)
    if not result.is_finite() or result <= 0 or result >= Decimal('1e16') or result.as_tuple().exponent < -2:
        raise ValueError('Nominal harus positif dan maksimal dua desimal.')
    return format(result, 'f')


def partial_transaction_date(message):
    """Keep an explicit day/month while asking only for its missing year."""
    text = clean(message).lower()
    match = re.fullmatch(r'(?:tanggal\s+)?' + NAMED_DATE + r'[.!?]?', text)
    if not match:
        return None
    day, month = int(match[1]), MONTH_ALIASES[match[2]]
    date(2000, month, day)  # Leap year permits 29 February; chosen year is validated later.
    return {'day': day, 'month': month}


def transaction_date(message, today):
    """Resolve explicit Indonesian dates without inventing a missing month/year."""
    text = message.lower()
    candidates = []
    for value in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text):
        candidates.append(date.fromisoformat(value))
    for day, month, year in re.findall(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text):
        candidates.append(date(int(year), int(month), int(day)))
    pattern = NAMED_DATE + r'\s+(\d{4})\b'
    for day, month, year in re.findall(pattern, text):
        candidates.append(date(int(year), MONTH_ALIASES[month], int(day)))
    if re.search(r"\bhari ini\b", text):
        candidates.append(today)
    if re.search(r"\bkemarin\b", text):
        candidates.append(today - timedelta(days=1))
    if len(set(candidates)) > 1:
        raise ValueError('Ada lebih dari satu tanggal transaksi. Sebutkan satu tanggal saja.')
    return candidates[0].isoformat() if candidates else None


def extract_operation(message, action, today, expecting=None):
    text = clean(message)
    data = {}
    if '=' in text:
        for token in shlex.split(text):
            if '=' not in token:
                continue
            key, value = token.split('=', 1)
            if key not in FIELDS[action] or key in data:
                raise ValueError('Parameter tidak dikenal atau ditulis dua kali.')
            data[key] = value
    else:
        # Quotes preserve multi-word names. Unquoted values stop at semantic delimiters.
        stop = r'(?=\s+(?:sebanyak|qty|jumlah|harga|seharga|dengan|dari|untuk|tanggal|hari ini|kemarin|bulan|tahun|nominal)\b|[?!]?$)'
        patterns = {
            'produk': rf'\b(?:sku|produk|barang (?:masuk|keluar|rusak)(?: buat| untuk)?|buat)\s+(?!sebanyak\b|harga\b|hari\b)("[^"]+"|.+?){stop}',
            'pemasok': rf'\bpemasok\s+(?:id\s+)?("[^"]+"|.+?){stop}',
            'customer': rf'\b(?:customer|pelanggan)\s+(?:id\s+)?("[^"]+"|.+?){stop}',
            'mitra': rf'\bmitra\s+(?:id\s+)?("[^"]+"|.+?){stop}',
            'qty': r'\b(?:sebanyak|qty|jumlah)\s+(\d+)\b|\b(\d+)\s*(?:pcs|buah|unit)\b',
            'harga': rf'\b(?:harga|seharga)\s+({AMOUNT})',
            'nominal': rf'\b(?:nominal|sebesar|setoran)\s+({AMOUNT})',
            'bulan': r'\bbulan\s+(\d{1,2}|'+ '|'.join(MONTHS) + r')\b',
            'tahun': r'\btahun\s+(20\d{2})\b',
        }
        sku = re.search(r'\bSKU-[\w-]+\b', text, re.I)
        product_name = re.search(rf'\b(?:sku|produk)\s+("[^"]+"|.+?){stop}', text, re.I)
        if sku:
            data['produk'] = sku.group()
        elif product_name:
            data['produk'] = product_name.group(1).strip().strip('"')
        for key in FIELDS[action]:
            if key in {'qty', 'harga', 'nominal', 'bulan', 'tahun'} and len(list(re.finditer(patterns[key], text, re.I))) > 1:
                raise ValueError(f'{key} ditulis lebih dari sekali; sebutkan satu nilai yang pasti.')
            if key in patterns and key not in data:
                match = re.search(patterns[key], text, re.I)
                if match:
                    data[key] = next(v for v in match.groups() if v is not None).strip().strip('"')
        when = transaction_date(text, today)
        if when:
            data['tanggal'] = when
    if expecting and expecting not in data:
        # A short follow-up answers only the specific field Riska requested.
        raw = text.strip().strip('"')
        if expecting in {'harga', 'nominal'} and re.fullmatch(AMOUNT, raw, re.I):
            data[expecting] = raw
        elif expecting == 'qty' and re.fullmatch(r'\d+(?:\s*(?:pcs|buah))?', raw, re.I):
            data[expecting] = re.search(r'\d+', raw).group()
        elif expecting in {'produk', 'mitra', 'pemasok', 'customer'}:
            data[expecting] = raw
        elif expecting in {'bulan', 'tahun'} and (raw.isdigit() or raw.lower() in MONTHS):
            data[expecting] = raw
    for key in ('harga', 'nominal'):
        if key in data:
            data[key] = amount(data[key])
    if 'tanggal' in data:
        resolved = transaction_date(data['tanggal'], today)
        if not resolved:
            raise ValueError('Tanggal belum lengkap. Sebutkan hari, bulan, dan tahun, atau jawab hari ini/kemarin.')
        data['tanggal'] = resolved
    if 'bulan' in data and data['bulan'].lower() in MONTHS:
        data['bulan'] = str(MONTHS.index(data['bulan'].lower()) + 1)
    for key, low, high in [('qty', 1, 100000000), ('bulan', 1, 12), ('tahun', 2000, 2100)]:
        if key in data and (not data[key].isdigit() or not low <= int(data[key]) <= high):
            raise ValueError(f'{key} harus berupa angka dalam batas {low}–{high}.')
    return data


def report_range(message, today):
    text = message.lower()
    dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
    if dates:
        if len(dates) != 2:
            raise ValueError('Untuk rentang tanggal, sebutkan tanggal awal dan akhir: dari YYYY-MM-DD sampai YYYY-MM-DD.')
        start, end = map(date.fromisoformat, dates)
    else:
        years = set(re.findall(r'\b20\d{2}\b', text))
        named_month = any(re.search(rf'\b{m}\b', text) for m in MONTHS)
        annual = re.search(r'\btahun\s+(ini|lalu|20\d{2})\b', text) or re.fullmatch(r'\s*(20\d{2})\s*', text)
        if annual and not named_month and not re.search(r'\b20\d{2}-\d{2}\b', text):
            if len(years) > 1:
                raise ValueError('Mohon pilih satu tahun, atau sebutkan rentang tanggal awal dan akhir.')
            value = annual.group(1)
            year = today.year if value == 'ini' else today.year - 1 if value == 'lalu' else int(value)
            if year > today.year:
                raise ValueError('Tahun tersebut belum berjalan. Mohon pilih tahun berjalan atau tahun sebelumnya.')
            return date(year, 1, 1), today if year == today.year else date(year, 12, 31)
        periods = re.findall(r'\b(20\d{2})-(0[1-9]|1[0-2])\b', text)
        if len(periods) > 1:
            raise ValueError('Sebutkan satu bulan atau gunakan rentang tanggal lengkap.')
        months = [MONTHS.index(m) + 1 for m in MONTHS if re.search(rf'\b{m}\b', text)]
        if len(months) > 1:
            raise ValueError('Sebutkan satu bulan atau gunakan rentang tanggal lengkap.')
        if periods:
            year, month = map(int, periods[0])
        elif months:
            month = months[0]
            years = re.findall(r'\b20\d{2}\b', text)
            if len(set(years)) > 1:
                raise ValueError('Sebutkan satu tahun.')
            year = int(years[0]) if years else today.year
        elif 'bulan lalu' in text:
            previous = today.replace(day=1) - timedelta(days=1)
            year, month = previous.year, previous.month
        else:
            if re.search(r'\b(tahun|kemarin|minggu|lalu)\b', text):
                raise ValueError('Sebutkan bulan dan tahun, atau rentang tanggal lengkap.')
            year, month = today.year, today.month
        start = date(year, month, 1)
        end = today if (year, month) == (today.year, today.month) else date(year, month, calendar.monthrange(year, month)[1])
    if start > end or (end - start).days > 3660:
        raise ValueError('Rentang laporan tidak valid atau melebihi 10 tahun.')
    return start, end
