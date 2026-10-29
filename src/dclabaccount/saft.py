"""SAF-T (PT) files: a generator for the lab's synthetic corpus, and a parser with validation rules.

The structure is a subset of the Portuguese SAF-T accounting file (header, general-ledger accounts, customers and
suppliers, general-ledger entries). It is **not** validated against the official XSD and the lab makes no claim to
produce or accept a compliant file. The parser reads untrusted XML: defusedxml refuses entities and external
references, the size is capped, and the lab accepts only files that carry its own synthetic watermark in the header, so
a real company's file is quarantined, never imported.
"""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from dclabaccount.generator import ACCOUNT_CODES, CHART, WATERMARK, make_nif, valid_nif

NS = "urn:OECD:StandardAuditFile-Tax:PT_1.04_01"
MAX_BYTES = 60 * 1024 * 1024
MAX_LINES = 2_000_000
ET.register_namespace("", NS)  # write the namespace as the default, as SAF-T files do


def _t(tag: str) -> str:
    return f"{{{NS}}}{tag}"


@dataclass(frozen=True)
class Problem:
    code: str
    message: str


@dataclass
class Line:
    record_id: str
    account: str
    debit: Decimal
    credit: Decimal
    description: str


@dataclass
class Transaction:
    id: str
    journal: str
    day: date
    lines: list[Line] = field(default_factory=list)


@dataclass
class Parsed:
    taxpayer: str = ""
    company: str = ""
    comment: str = ""
    start: date | None = None
    end: date | None = None
    declared_debit: Decimal | None = None
    declared_credit: Decimal | None = None
    declared_entries: int | None = None
    accounts: dict[str, str] = field(default_factory=dict)
    parties: list[dict[str, str]] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)

    @property
    def period(self) -> str | None:
        if self.start and self.end and (self.start.year, self.start.month) == (self.end.year, self.end.month):
            return f"{self.start.year}-{self.start.month:02d}"
        return None

    @property
    def lines(self) -> int:
        return sum(len(t.lines) for t in self.transactions)


# --- generator ------------------------------------------------------------------------------------


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start, date.fromordinal(end.toordinal() - 1)


def generate(name: str, nif: str, year: int, month: int, transactions: int, *, seed: int = 1) -> bytes:
    """A synthetic monthly file: a chart, a few customers and suppliers, and balanced two-line transactions."""
    rng = random.Random(f"{nif}-{year}-{month}-{seed}")
    start, end = _month_bounds(year, month)
    root = ET.Element(_t("AuditFile"))

    def add(parent, tag, text=None):
        node = ET.SubElement(parent, _t(tag))
        if text is not None:
            node.text = str(text)
        return node

    header = add(root, "Header")
    for tag, text in (
        ("AuditFileVersion", "1.04_01"), ("CompanyID", nif), ("TaxRegistrationNumber", nif), ("TaxAccountingBasis", "C"),
        ("CompanyName", name), ("FiscalYear", year), ("StartDate", start), ("EndDate", end), ("CurrencyCode", "EUR"),
        ("DateCreated", date(year, month, 1)), ("TaxEntity", "Global"), ("ProductCompanyTaxID", make_nif(999)),
        ("SoftwareCertificateNumber", 0), ("ProductID", "DcLabAccount/synthetic"), ("ProductVersion", "0.1"),
        ("HeaderComment", WATERMARK),
    ):
        add(header, tag, text)
    master = add(root, "MasterFiles")
    ledger = add(master, "GeneralLedgerAccounts")
    for code, label in CHART:
        account = add(ledger, "Account")
        add(account, "AccountID", code)
        add(account, "AccountDescription", label)
        add(account, "GroupingCategory", "GM")
    for index in range(1, 4):
        customer = add(master, "Customer")
        add(customer, "CustomerID", f"C{index:03d}")
        add(customer, "AccountID", "21")
        add(customer, "CustomerTaxID", make_nif(index * 31, "2"))
        add(customer, "CompanyName", f"Cliente Exemplo {index:03d}")
        add(add(customer, "Contact"), "Email", f"c{index:03d}@example.test")
    for index in range(1, 3):
        supplier = add(master, "Supplier")
        add(supplier, "SupplierID", f"S{index:03d}")
        add(supplier, "AccountID", "22")
        add(supplier, "SupplierTaxID", make_nif(index * 37, "2"))
        add(supplier, "CompanyName", f"Fornecedor Exemplo {index:03d}")
        add(add(supplier, "Contact"), "Email", f"s{index:03d}@example.test")

    entries = add(root, "GeneralLedgerEntries")
    total = Decimal("0")
    journal = ET.Element(_t("Journal"))
    add(journal, "JournalID", "GEN")
    add(journal, "Description", "Synthetic journal")
    for number in range(1, transactions + 1):
        day = date(year, month, 1 + rng.randrange(end.day))
        amount = Decimal(rng.randrange(5000, 500000)) / 100
        debit_code, credit_code = rng.sample(ACCOUNT_CODES, 2)
        txn = add(journal, "Transaction")
        add(txn, "TransactionID", f"{year}-GEN-{month:02d}-{number:06d}")
        add(txn, "Period", month)
        add(txn, "TransactionDate", day)
        add(txn, "SourceID", "synthetic")
        add(txn, "Description", "synthetic")
        add(txn, "DocArchivalNumber", number)
        add(txn, "TransactionType", "N")
        add(txn, "GLPostingDate", day)
        lines = add(txn, "Lines")
        for tag, amount_tag, code, record in (("DebitLine", "DebitAmount", debit_code, 1), ("CreditLine", "CreditAmount", credit_code, 2)):
            line = add(lines, tag)
            add(line, "RecordID", f"{number}-{record}")
            add(line, "AccountID", code)
            add(line, "SystemEntryDate", f"{day}T00:00:00")
            add(line, "Description", "synthetic")
            add(line, amount_tag, f"{amount:.2f}")
        total += amount
    add(entries, "NumberOfEntries", transactions)
    add(entries, "TotalDebit", f"{total:.2f}")
    add(entries, "TotalCredit", f"{total:.2f}")
    entries.append(journal)
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


# --- parser and validation ------------------------------------------------------------------------


def _text(node, tag: str) -> str:
    child = node.find(_t(tag))
    return (child.text or "").strip() if child is not None else ""


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _money(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except Exception:
        return None


def parse(content: bytes) -> tuple[Parsed | None, list[Problem]]:
    """Parse untrusted bytes. Returns (parsed, problems): parsed is None when the file cannot be read at all."""
    if len(content) > MAX_BYTES:
        return None, [Problem("TOO_LARGE", f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB")]
    try:
        root = fromstring(content)
    except DefusedXmlException:
        return None, [Problem("UNSAFE_XML", "the file uses entities or external references, which are refused")]
    except ParseError as error:
        return None, [Problem("XML_NOT_WELL_FORMED", f"not well-formed XML: {error}")]
    if root.tag != _t("AuditFile"):
        return None, [Problem("NOT_SAFT", "the root element is not a SAF-T AuditFile in the expected namespace")]
    problems: list[Problem] = []
    parsed = Parsed()
    header = root.find(_t("Header"))
    if header is None:
        return None, [Problem("NO_HEADER", "the file has no Header")]
    parsed.taxpayer = _text(header, "TaxRegistrationNumber")
    parsed.company = _text(header, "CompanyName")
    parsed.comment = _text(header, "HeaderComment")
    parsed.start, parsed.end = _day(_text(header, "StartDate")), _day(_text(header, "EndDate"))
    master = root.find(_t("MasterFiles"))
    if master is not None:
        for account in master.iter(_t("Account")):
            parsed.accounts[_text(account, "AccountID")] = _text(account, "AccountDescription")
        for tag, key, kind in (("Customer", "CustomerID", "customer"), ("Supplier", "SupplierID", "supplier")):
            for node in master.findall(_t(tag)):
                email = node.find(f"{_t('Contact')}/{_t('Email')}")
                parsed.parties.append(
                    {"kind": kind, "ref": _text(node, key), "tax_id": _text(node, f"{tag}TaxID"), "name": _text(node, "CompanyName"),
                     "email": (email.text or "").strip() if email is not None else ""}
                )
    entries = root.find(_t("GeneralLedgerEntries"))
    if entries is None:
        return None, [Problem("NO_ENTRIES", "the file has no GeneralLedgerEntries")]
    parsed.declared_entries = int(_text(entries, "NumberOfEntries") or 0)
    parsed.declared_debit, parsed.declared_credit = _money(_text(entries, "TotalDebit")), _money(_text(entries, "TotalCredit"))
    count = 0
    for journal in entries.findall(_t("Journal")):
        journal_id = _text(journal, "JournalID")
        for node in journal.findall(_t("Transaction")):
            txn = Transaction(_text(node, "TransactionID"), journal_id, _day(_text(node, "TransactionDate")) or date.min)
            for line in node.iterfind(f"{_t('Lines')}/*"):
                debit, credit = _money(_text(line, "DebitAmount") or "0"), _money(_text(line, "CreditAmount") or "0")
                if debit is None or credit is None:
                    problems.append(Problem("BAD_AMOUNT", f"{txn.id}: an amount is not a number"))
                    continue
                txn.lines.append(Line(_text(line, "RecordID"), _text(line, "AccountID"), debit, credit, _text(line, "Description")))
                count += 1
            parsed.transactions.append(txn)
            if count > MAX_LINES:
                return None, [Problem("TOO_MANY_LINES", f"more than {MAX_LINES} lines")]
    return parsed, problems


def validate(parsed: Parsed, *, nif: str, expected_period: str | None = None) -> list[Problem]:
    """Every rule the lab applies before anything enters the ledger. Empty when the file is acceptable."""
    problems: list[Problem] = []
    if WATERMARK not in parsed.comment:
        problems.append(Problem("NOT_SYNTHETIC", "the lab accepts only the synthetic files it generates, never a real company's"))
    if not valid_nif(parsed.taxpayer):
        problems.append(Problem("BAD_TAXPAYER", f"tax number {parsed.taxpayer!r} fails the checksum"))
    elif parsed.taxpayer != nif:
        problems.append(Problem("WRONG_TAXPAYER", "the file belongs to another taxpayer than this tenant"))
    if parsed.period is None:
        problems.append(Problem("BAD_PERIOD", "the header must cover exactly one calendar month"))
    elif expected_period and parsed.period != expected_period:
        problems.append(Problem("WRONG_PERIOD", f"the file covers {parsed.period}, not {expected_period}"))
    seen: set[str] = set()
    total_debit = total_credit = Decimal("0")
    for txn in parsed.transactions:
        if txn.id in seen:
            problems.append(Problem("DUPLICATE_TRANSACTION", f"transaction {txn.id} appears twice"))
        seen.add(txn.id)
        if parsed.start and parsed.end and not (parsed.start <= txn.day <= parsed.end):
            problems.append(Problem("DATE_OUTSIDE_PERIOD", f"{txn.id} is dated {txn.day}, outside the period"))
        debit, credit = sum((x.debit for x in txn.lines), Decimal("0")), sum((x.credit for x in txn.lines), Decimal("0"))
        total_debit, total_credit = total_debit + debit, total_credit + credit
        if debit != credit:
            problems.append(Problem("UNBALANCED_TRANSACTION", f"{txn.id}: debit {debit} is not credit {credit}"))
        for line in txn.lines:
            if line.account not in parsed.accounts:
                problems.append(Problem("UNKNOWN_ACCOUNT", f"{txn.id}: account {line.account!r} is not in the chart"))
            if line.debit < 0 or line.credit < 0 or (line.debit and line.credit):
                problems.append(Problem("BAD_LINE", f"{txn.id}: a line must have one non-negative amount"))
    if parsed.declared_debit != total_debit or parsed.declared_credit != total_credit:
        problems.append(Problem("TOTALS_DIFFER", f"declared {parsed.declared_debit}/{parsed.declared_credit}, lines add up to {total_debit}/{total_credit}"))
    if parsed.declared_entries != len(parsed.transactions):
        problems.append(Problem("ENTRY_COUNT_DIFFERS", f"declared {parsed.declared_entries} entries, found {len(parsed.transactions)}"))
    # one problem per kind is enough to read; keep the first three of each
    kept: list[Problem] = []
    counts: dict[str, int] = {}
    for problem in problems:
        counts[problem.code] = counts.get(problem.code, 0) + 1
        if counts[problem.code] <= 3:
            kept.append(problem)
    return kept


def check(content: bytes, *, nif: str, expected_period: str | None = None) -> tuple[Parsed | None, list[Problem]]:
    parsed, problems = parse(content)
    if parsed is None:
        return None, problems
    return parsed, problems + validate(parsed, nif=nif, expected_period=expected_period)


# --- ways to break a file, for the quarantine tests and the evidence --------------------------------


def corrupt(content: bytes, kind: str) -> bytes:
    text = content.decode("utf-8")
    if kind == "unbalanced":
        return text.replace("<DebitAmount>", "<DebitAmount>1", 1).encode()
    if kind == "wrong_taxpayer":
        return text.replace("<TaxRegistrationNumber>5", "<TaxRegistrationNumber>6", 1).encode()
    if kind == "unknown_account":  # drop three accounts from the chart; the lines still use them
        for code in ("21", "62", "71"):
            text = re.sub(rf"<Account>\s*<AccountID>{code}</AccountID>.*?</Account>", "", text, count=1, flags=re.S)
        return text.encode()
    if kind == "duplicate_transaction":
        marker = "<TransactionID>"
        first = text.index(marker) + len(marker)
        ident = text[first:text.index("<", first)]
        second = text.index(marker, first) + len(marker)
        return (text[:second] + ident + text[text.index("<", second):]).encode()
    if kind == "wrong_period":  # the header now starts a month before it ends
        return re.sub(r"<StartDate>(\d{4})-(\d{2})-", lambda m: f"<StartDate>{m.group(1)}-{int(m.group(2)) - 1:02d}-", text, count=1).encode()
    if kind == "malformed":
        return content[: len(content) // 2]
    if kind == "unsafe_xml":
        bomb = '<?xml version="1.0"?><!DOCTYPE bomb [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><AuditFile>&b;</AuditFile>'
        return bomb.encode()
    if kind == "not_synthetic":
        return text.replace(WATERMARK, "Ficheiro real").encode()
    raise ValueError(kind)


CORRUPTIONS: dict[str, str] = {
    "unbalanced": "UNBALANCED_TRANSACTION",
    "wrong_taxpayer": "BAD_TAXPAYER",
    "unknown_account": "UNKNOWN_ACCOUNT",
    "wrong_period": "BAD_PERIOD",
    "duplicate_transaction": "DUPLICATE_TRANSACTION",
    "malformed": "XML_NOT_WELL_FORMED",
    "unsafe_xml": "UNSAFE_XML",
    "not_synthetic": "NOT_SYNTHETIC",
}
