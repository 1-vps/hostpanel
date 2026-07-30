#!/usr/bin/env python3
"""Generate the Japanese HostPanel catalog with exact token preservation."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Iterable

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL_ID = "staka/fugumt-en-ja"
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"https?://[^\s<>]+")
PATH_RE = re.compile(r"(?<!\w)/(?:etc|opt|var|usr|home|root|tmp|srv|run|dev|proc|sys|mnt|media|boot)(?:/[A-Za-z0-9._~%+@:-]+)*")
FLAG_RE = re.compile(r"(?<!\w)--[A-Za-z0-9][A-Za-z0-9_-]*")
ENV_RE = re.compile(r"\b(?:HP|HOSTPANEL)_[A-Z0-9_]+\b")
VERSION_RE = re.compile(r"\bv?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?\b")
BACKTICK_RE = re.compile(r"`[^`]+`")
FORMAT_RE = re.compile(r"%(?:\([^)]+\))?[#0 +\-]?(?:\d+|\*)?(?:\.\d+|\.\*)?[diouxXeEfFgGcrs%]")
CERT_RE = re.compile(r"-----BEGIN [A-Z0-9 ]+-----|-----END [A-Z0-9 ]+-----")
SCOPE_RE = re.compile(r"^[A-Za-z0-9_.:-]+(?:,[A-Za-z0-9_.:-]+)+$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(?:json|ya?ml|toml|ini|conf|pem|key|crt|csr|sql|csv|tsv|txt|log|zip|tar|gz|tgz|deb|rpm|php|py|js|css|html?)$")
PROTECTED_PATTERNS = (
    BACKTICK_RE, CERT_RE, URL_RE, EMAIL_RE, PLACEHOLDER_RE, ENV_RE, FLAG_RE,
    PATH_RE, FORMAT_RE, VERSION_RE,
)
WORD_RE = re.compile(r"[A-Za-z]")


def preserve_whole(text: str) -> bool:
    value = text.strip()
    if not value or not WORD_RE.search(value):
        return True
    if URL_RE.fullmatch(value) or EMAIL_RE.fullmatch(value) or PATH_RE.fullmatch(value):
        return True
    if SCOPE_RE.fullmatch(value) or FILENAME_RE.fullmatch(value):
        return True
    if value.startswith("-----BEGIN ") or value.startswith("-----END "):
        return True
    if re.fullmatch(r"[A-Za-z0-9_.:/+@-]+", value) and not re.search(r"\s", value):
        return True
    return False


def split_protected(text: str) -> list[tuple[bool, str]]:
    matches: list[tuple[int, int]] = []
    for pattern in PROTECTED_PATTERNS:
        matches.extend((match.start(), match.end()) for match in pattern.finditer(text))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int]] = []
    end = -1
    for start, stop in matches:
        if start >= end:
            selected.append((start, stop))
            end = stop
    if not selected:
        return [(False, text)]
    result: list[tuple[bool, str]] = []
    cursor = 0
    for start, stop in selected:
        if start > cursor:
            result.append((False, text[cursor:start]))
        result.append((True, text[start:stop]))
        cursor = stop
    if cursor < len(text):
        result.append((False, text[cursor:]))
    return result


def batches(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=pathlib.Path)
    parser.add_argument("output_dir", type=pathlib.Path)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    english = json.loads((args.source_root / "app/static/i18n.en.json").read_text(encoding="utf-8"))
    layouts: dict[str, list[tuple[bool, str]]] = {}
    segments: list[str] = []
    for value in dict.fromkeys(english.values()):
        if preserve_whole(value):
            layouts[value] = [(True, value)]
            continue
        layout = split_protected(value)
        layouts[value] = layout
        for protected, segment in layout:
            if not protected and WORD_RE.search(segment):
                segments.append(segment)

    unique_segments = list(dict.fromkeys(segments))
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID)
    model.eval()
    translated: dict[str, str] = {}
    completed = 0
    for batch in batches(unique_segments, args.batch_size):
        encoded = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=512)
        with torch.inference_mode():
            output = model.generate(**encoded, num_beams=1, do_sample=False, max_new_tokens=512)
        decoded = tokenizer.batch_decode(output, skip_special_tokens=True)
        for source, candidate in zip(batch, decoded):
            translated[source] = candidate.strip() or source
        completed += len(batch)
        print(f"ja: {completed}/{len(unique_segments)} segments", flush=True)

    catalog: dict[str, str] = {}
    for key, source in english.items():
        pieces: list[str] = []
        for protected, segment in layouts[source]:
            if protected or not WORD_RE.search(segment):
                pieces.append(segment)
            else:
                pieces.append(translated[segment])
        catalog[key] = "".join(pieces).strip()
        if not catalog[key]:
            catalog[key] = source
        if sorted(PLACEHOLDER_RE.findall(catalog[key])) != sorted(PLACEHOLDER_RE.findall(source)):
            raise SystemExit(f"placeholder mismatch after reassembly: {key}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "i18n.ja.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "model": MODEL_ID,
        "keys": len(catalog),
        "unique_source_values": len(set(english.values())),
        "translated_segments": len(unique_segments),
        "source_identical_keys": [key for key, source in english.items() if catalog[key] == source],
    }
    (args.output_dir / "report.ja.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
