"""Immutable experiment inputs and explicit TOML overrides."""
import hashlib
import json
import re
from pathlib import Path

HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS
SCALE = 100_000_000


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def identity(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text() != encoded:
            raise ValueError(f"refusing to overwrite different experiment artifact: {path}")
        return
    with path.open("x") as stream:
        stream.write(encoded)


def seed(protocol_hash, train_hash, phase, role, ordinal):
    fields = [protocol_hash, train_hash, phase, role, str(ordinal)]
    return int.from_bytes(hashlib.sha256("\n".join(fields).encode()).digest()[:8], "big")


def override_toml(text, sections):
    """Replace only named keys, preserving every other baseline setting."""
    for section, fields in sections.items():
        pattern = re.compile(r"(?m)^\[" + re.escape(section) + r"\]\s*\n")
        match = pattern.search(text)
        if not match:
            raise ValueError(f"missing TOML section: {section}")
        next_section = re.search(r"(?m)^\[", text[match.end():])
        end = match.end() + next_section.start() if next_section else len(text)
        body = text[match.end():end]
        if body and not body.endswith("\n"):
            body += "\n"
        for key, value in fields.items():
            line = f"{key} = {json.dumps(value)}"
            key_pattern = re.compile(r"(?m)^" + re.escape(key) + r"\s*=.*$")
            body = key_pattern.sub(lambda _: line, body) if key_pattern.search(body) else body + line + "\n"
        text = text[:match.end()] + body + text[end:]
    return text


def verify_files(files):
    if not files:
        raise ValueError("no immutable input files")
    for item in files:
        if digest(item["path"]) != item["sha256"]:
            raise ValueError(f"changed input: {item['path']}")


def whole_days(start, end, expected=None):
    if start % DAY_NS or end % DAY_NS or end <= start:
        raise ValueError("partitions require ordered UTC midnight boundaries")
    days = (end - start) // DAY_NS
    if expected is not None and days != expected:
        raise ValueError(f"partition requires {expected} complete days")
    return days
