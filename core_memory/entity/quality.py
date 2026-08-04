"""Deterministic presentation hygiene for canonical entity labels."""

from __future__ import annotations

import re

_GENERIC_ENTITY_STOPWORDS = frozenset(
    """
    test tests testing spec specs fixture fixtures doc docs documentation readme changelog license
    src lib libs app apps api apis core sdk util utils helper helpers common shared misc main master
    dev develop staging prod production build builds config configs configuration settings setup script
    scripts asset assets public static dist package packages module modules index temp tmp cache backup
    archive log logs debug data file files folder folders directory document documents item items record
    records event events message messages note notes info information content text detail details general
    other others unknown untitled default example examples sample samples demo user users admin account
    accounts name title label value type status session memory turn context summary update updates reply
    follow follows following precede precedes supersede supersedes create created delete deleted add added
    remove removed fix fixed change changes changed please thanks okay yes no none null undefined true false
    should would could will have has about before after because there here when where what which who why how
    this that these those with from into your their
    """.split()
)

_URL_RE = re.compile(r"^(https?://|www\.)", re.I)
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_HASH_RE = re.compile(r"^[0-9a-f]{7,64}$", re.I)
_NUMBER_VERSION_RE = re.compile(r"^v?\d+([._-]\d+)*$", re.I)
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?")
_FILE_NAME_RE = re.compile(r"^\S+\.[a-z0-9]{1,5}$", re.I)


def is_meaningful_entity_label(value: str) -> bool:
    """Return whether an entity label is suitable for a curated worldline."""

    label = " ".join(str(value or "").split())
    if len(label) < 2 or len(label) > 96 or "/" in label or "\\" in label:
        return False
    if (
        _URL_RE.match(label)
        or _EMAIL_RE.search(label)
        or _UUID_RE.match(label)
        or _HEX_HASH_RE.match(label)
        or _NUMBER_VERSION_RE.match(label)
        or _DATE_RE.match(label)
    ):
        return False
    if len(label.split(" ")) == 1:
        lower = label.lower()
        if lower in _GENERIC_ENTITY_STOPWORDS or _FILE_NAME_RE.match(label):
            return False
        if label == lower and label.isalpha():
            return False
        if label == lower and len(label) < 4 and not any(character.isdigit() for character in label):
            return False
    letters = len(re.findall(r"[^\W\d_]", label, re.UNICODE))
    return letters / max(1, len(label.replace(" ", ""))) >= 0.5


__all__ = ["is_meaningful_entity_label"]
