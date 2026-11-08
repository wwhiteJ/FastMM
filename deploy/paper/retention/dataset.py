"""Seal a local, immutable copy of consecutive journal parts for one partition."""
import shutil
import struct
from pathlib import Path

from .artifacts import DAY_NS, digest, verify_files, whole_days, write_json

EVENT = struct.Struct("<IBBBBIIQQqqQII")


def journal_parts(first):
    first = Path(first).resolve()
    parts = [first, *sorted(first.parent.glob(first.stem + ".*.fmj"),
                           key=lambda p: int(p.stem.rsplit(".", 1)[1]))]
    for index, path in enumerate(parts[1:], 1):
        if path != first.with_name(f"{first.stem}.{index}.fmj"):
            raise ValueError("data_invalid: missing journal part")
    return parts


def inspect_part(path, previous, clock):
    minimum, maximum, session, trailer = None, None, None, False
    with open(path, "rb") as stream:
        header = stream.read(256)
        if len(header) != 256 or header[:4] != b"FMJ1":
            raise ValueError("invalid journal header")
        session = struct.unpack_from("<Q", header, 16)[0]
        stream.seek(struct.unpack_from("<I", header, 8)[0])
        while block := stream.read(64):
            if len(block) != 64 or block[:4] != b"FMJB":
                raise ValueError("invalid journal block")
            length = struct.unpack_from("<I", block, 4)[0]
            payload = stream.read(length)
            if len(payload) != length:
                raise ValueError("unsealed/truncated journal part")
            trailer |= bool(struct.unpack_from("<I", block, 32)[0] & 1)
            offset = 0
            while offset < length:
                event = EVENT.unpack_from(payload, offset)
                size, kind, _, _, flags, _, delta, sequence = event[:8]
                if size < 64 or size % 64 or offset + size > length:
                    raise ValueError("invalid journal record")
                if previous is not None and sequence != previous + 1:
                    raise ValueError("data_invalid: journal sequence gap")
                previous = sequence
                if flags & 32:
                    raise ValueError("data_invalid: dropped journal record")
                if kind == 25:
                    clock = struct.unpack_from("<q", payload, offset + 64)[0]
                elif flags & 16 and clock is not None:
                    clock += delta if delta < 2**31 else delta - 2**32
                if clock is not None:
                    minimum = clock if minimum is None else min(minimum, clock)
                    maximum = clock if maximum is None else max(maximum, clock)
                offset += size
    if not trailer:
        raise ValueError("unsealed journal part: no trailer")
    return previous, clock, minimum, maximum, session


def prepare(first, output, start, end, funding_path, phase):
    whole_days(start, end, 7 if phase in ("train", "validation") else None)
    if phase == "test" and end - start < 14 * DAY_NS:
        raise ValueError("test needs at least 14 complete days")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    previous = clock = session = earliest = latest = None
    files = []
    for source in journal_parts(first):
        before = digest(source)
        target = output / source.name
        shutil.copyfile(source, target)
        if digest(target) != before or digest(source) != before:
            raise ValueError("source changed during immutable snapshot")
        target.chmod(0o444)
        previous, clock, low, high, current_session = inspect_part(target, previous, clock)
        if session is not None and session != current_session:
            raise ValueError("partition cannot reset at a collector restart")
        session = current_session
        earliest = low if earliest is None else min(earliest, low)
        latest = high if latest is None else max(latest, high)
        files.append({"path": str(target), "sha256": before})
        if latest >= end + 30_000_000_000:
            break
    if earliest is None or earliest > start - 60_000_000_000 or latest < end + 30_000_000_000:
        raise ValueError("insufficient_data: warmup or endpoint not covered")
    funding_target = output / "funding.json"
    shutil.copyfile(funding_path, funding_target)
    manifest = {"phase": phase, "start_ns": start, "end_ns": end, "session_id": session,
                "first": files[0]["path"], "files": files, "first_engine_ns": earliest,
                "last_engine_ns": latest, "funding": {"path": str(funding_target), "sha256": digest(funding_target)}}
    write_json(output / "dataset_manifest.json", manifest)
    return manifest


def verify(manifest):
    verify_files([*manifest["files"], manifest["funding"]])
    actual = {str(p) for p in journal_parts(manifest["first"])}
    if actual != {item["path"] for item in manifest["files"]}:
        raise ValueError("unregistered journal part")
