"""Governed, bounded, read-only local discovery collectors."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from .config import KnowledgeConfig
from .models import (
    CollectorResult,
    DiscoveryEvidence,
    KnowledgeEntity,
    KnowledgeRelationship,
    RelationshipType,
    stable_hash,
    stable_id,
    utc_now,
)

ALLOWED_EXECUTABLES = frozenset({
    "crontab",
    "df",
    "docker",
    "git",
    "hostname",
    "ip",
    "launchctl",
    "ollama",
    "python",
    "python3",
    "systemctl",
    "tailscale",
})


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class CommandExecutor(Protocol):
    def __call__(
        self, argv: tuple[str, ...], timeout: int, maximum_bytes: int
    ) -> CommandResult: ...


def safe_execute(
    argv: tuple[str, ...], timeout: int, maximum_bytes: int
) -> CommandResult:
    if not argv or Path(argv[0]).name not in ALLOWED_EXECUTABLES:
        raise ValueError("discovery command is not allow-listed")
    executable = shutil.which(argv[0])
    if executable is None:
        return CommandResult(argv, 127, "", "command unavailable")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"}
    try:
        completed = subprocess.run(
            (executable, *argv[1:]),
            shell=False,
            capture_output=True,
            text=False,
            timeout=timeout,
            env=env,
            check=False,
        )
        return CommandResult(
            argv,
            completed.returncode,
            completed.stdout[:maximum_bytes].decode("utf-8", "replace"),
            completed.stderr[:maximum_bytes].decode("utf-8", "replace"),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            argv,
            124,
            (exc.stdout or b"")[:maximum_bytes].decode("utf-8", "replace"),
            "collector command timed out",
            True,
        )


def strip_remote_credentials(url: str) -> str:
    if "://" not in url:
        return re.sub(r"^[^@/]+@", "", url)
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname += f":{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, ""))


@dataclass(frozen=True)
class CollectorContext:
    config: KnowledgeConfig
    node_id: str
    observed_at: datetime
    execute: CommandExecutor


@dataclass(frozen=True)
class NormalizedCollection:
    entities: tuple[KnowledgeEntity, ...] = ()
    relationships: tuple[KnowledgeRelationship, ...] = ()
    evidence: tuple[DiscoveryEvidence, ...] = ()
    warning: str | None = None


class Collector(Protocol):
    collector_id: str
    supported_platforms: tuple[str, ...]
    required_commands: tuple[str, ...]

    def collect(self, context: CollectorContext) -> NormalizedCollection: ...


def _evidence(
    context: CollectorContext, collector: str, locator: str, record: dict
) -> DiscoveryEvidence:
    digest = stable_hash(record)
    return DiscoveryEvidence(
        evidence_id=stable_id("evidence", context.node_id, collector, locator, digest),
        collector=collector,
        node_id=context.node_id,
        collected_at=context.observed_at,
        source_kind="local_read_only",
        source_locator=locator,
        content_hash=digest,
        summary=f"{collector} observation from {locator}",
        raw_record=record,
    )


def _entity(
    context: CollectorContext,
    collector: str,
    kind: str,
    name: str,
    evidence_id: str,
    **values,
) -> KnowledgeEntity:
    canonical = name.strip().lower()
    return KnowledgeEntity(
        entity_id=stable_id("entity", context.node_id, kind, canonical),
        entity_type=kind,
        name=name,
        canonical_name=canonical,
        node_id=context.node_id,
        first_seen_at=context.observed_at,
        last_seen_at=context.observed_at,
        observed_at=context.observed_at,
        evidence_refs=(evidence_id,),
        source_collectors=(collector,),
        **values,
    )


class HostCollector:
    collector_id = "host"
    supported_platforms = ("darwin", "linux")
    required_commands = ()

    def collect(self, context: CollectorContext) -> NormalizedCollection:
        record = {
            "hostname": platform.node(),
            "platform": platform.system(),
            "platform_release": platform.release(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
        }
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            record["memory_bytes"] = pages * size
        except (AttributeError, OSError, ValueError):
            pass
        evidence = _evidence(context, self.collector_id, "platform", record)
        entity = _entity(
            context,
            self.collector_id,
            "host",
            record["hostname"] or context.node_id,
            evidence.evidence_id,
            operational_status="online",
            attributes=record,
            labels=(platform.system().lower(),),
        )
        return NormalizedCollection((entity,), evidence=(evidence,))


class StorageCollector:
    collector_id = "storage"
    supported_platforms = ("darwin", "linux")
    required_commands = ("df",)

    def collect(self, context: CollectorContext) -> NormalizedCollection:
        result = context.execute(
            ("df", "-Pk"),
            context.config.collector_timeout_seconds,
            context.config.maximum_output_bytes,
        )
        if result.returncode:
            return NormalizedCollection(warning=result.stderr or "df unavailable")
        host_id = stable_id("entity", context.node_id, "host", platform.node().lower())
        entities, relationships, evidence = [], [], []
        for line in result.stdout.splitlines()[1:501]:
            parts = line.split()
            if len(parts) < 6:
                continue
            record = {
                "filesystem": parts[0],
                "blocks_kb": parts[1],
                "used_kb": parts[2],
                "available_kb": parts[3],
                "capacity": parts[4],
                "mount": parts[-1],
            }
            ev = _evidence(context, self.collector_id, f"df:{parts[-1]}", record)
            entity = _entity(
                context,
                self.collector_id,
                "filesystem",
                parts[-1],
                ev.evidence_id,
                location=parts[-1],
                attributes=record,
            )
            rel = KnowledgeRelationship(
                relationship_id=stable_id(
                    "rel", host_id, RelationshipType.HOSTS.value, entity.entity_id
                ),
                source_entity_id=host_id,
                relationship_type=RelationshipType.HOSTS,
                target_entity_id=entity.entity_id,
                first_seen_at=context.observed_at,
                last_seen_at=context.observed_at,
                observed_at=context.observed_at,
                evidence_refs=(ev.evidence_id,),
                source_collectors=(self.collector_id,),
            )
            entities.append(entity)
            relationships.append(rel)
            evidence.append(ev)
        return NormalizedCollection(
            tuple(entities), tuple(relationships), tuple(evidence)
        )


class GitCollector:
    collector_id = "git"
    supported_platforms = ("darwin", "linux")
    required_commands = ("git",)

    def collect(self, context: CollectorContext) -> NormalizedCollection:
        entities, evidence, warnings = [], [], []
        for root in context.config.approved_repository_roots:
            candidates = (
                [root]
                if (root / ".git").exists()
                else [path.parent for path in sorted(root.glob("*/.git"))[:200]]
            )
            for repo in candidates:

                def git(*args: str) -> CommandResult:
                    return context.execute(
                        ("git", "-C", str(repo), *args),
                        context.config.collector_timeout_seconds,
                        context.config.maximum_output_bytes,
                    )

                head = git("rev-parse", "HEAD")
                if head.returncode:
                    warnings.append(f"{repo}: not readable as a repository")
                    continue
                branch = git("branch", "--show-current").stdout.strip()
                status = git("status", "--porcelain").stdout
                remotes = [
                    strip_remote_credentials(line.strip())
                    for line in git(
                        "remote", "get-url", "--all", "origin"
                    ).stdout.splitlines()
                    if line.strip()
                ]
                record = {
                    "path": str(repo),
                    "head": head.stdout.strip(),
                    "branch": branch,
                    "dirty": bool(status.strip()),
                    "remotes": remotes[:20],
                }
                ev = _evidence(context, self.collector_id, f"git:{repo}", record)
                entities.append(
                    _entity(
                        context,
                        self.collector_id,
                        "repository",
                        repo.name,
                        ev.evidence_id,
                        location=str(repo),
                        operational_status="dirty" if record["dirty"] else "clean",
                        version=record["head"],
                        attributes=record,
                        labels=("git",),
                    )
                )
                evidence.append(ev)
        return NormalizedCollection(
            tuple(entities),
            evidence=tuple(evidence),
            warning="; ".join(warnings)[:1024] or None,
        )


class CommandInventoryCollector:
    def __init__(
        self, collector_id: str, commands: Sequence[tuple[str, ...]], kind: str
    ) -> None:
        self.collector_id = collector_id
        self.commands = tuple(commands)
        self.kind = kind
        self.supported_platforms = ("darwin", "linux")
        self.required_commands = tuple(command[0] for command in commands)

    def collect(self, context: CollectorContext) -> NormalizedCollection:
        evidence, entities, warnings = [], [], []
        for argv in self.commands:
            result = context.execute(
                argv,
                context.config.collector_timeout_seconds,
                context.config.maximum_output_bytes,
            )
            if result.returncode:
                warnings.append(f"{argv[0]} unavailable")
                continue
            record = {"argv": list(argv), "output": result.stdout}
            ev = _evidence(context, self.collector_id, ":".join(argv), record)
            evidence.append(ev)
            for number, line in enumerate(result.stdout.splitlines()[:500]):
                name = line.strip()[:512]
                if not name:
                    continue
                entities.append(
                    _entity(
                        context,
                        self.collector_id,
                        self.kind,
                        name,
                        ev.evidence_id,
                        attributes={"record_number": number},
                    )
                )
        return NormalizedCollection(
            tuple(entities),
            evidence=tuple(evidence),
            warning="; ".join(warnings)[:1024] or None,
        )


def collector_registry() -> Mapping[str, Collector]:
    system = platform.system().lower()
    service_commands = (
        (("launchctl", "list"),)
        if system == "darwin"
        else (
            (
                "systemctl",
                "list-units",
                "--type=service",
                "--all",
                "--no-pager",
                "--no-legend",
            ),
        )
    )
    schedule_commands = (
        (("launchctl", "list"),)
        if system == "darwin"
        else (
            ("systemctl", "list-timers", "--all", "--no-pager", "--no-legend"),
            ("crontab", "-l"),
        )
    )
    collectors: list[Collector] = [
        HostCollector(),
        StorageCollector(),
        GitCollector(),
        CommandInventoryCollector(
            "network", (("tailscale", "status", "--json"),), "network"
        ),
        CommandInventoryCollector("services", service_commands, "service"),
        CommandInventoryCollector(
            "docker", (("docker", "ps", "--format", "{{json .}}"),), "container"
        ),
        CommandInventoryCollector("ollama", (("ollama", "list"),), "model"),
        CommandInventoryCollector("scheduled_jobs", schedule_commands, "schedule"),
        CommandInventoryCollector(
            "python", (("python3", "--version"),), "python_environment"
        ),
        CommandInventoryCollector("hermes_runtime", (), "hermes_runtime"),
        CommandInventoryCollector("backups", (), "backup"),
        CommandInventoryCollector("registries", (), "registry"),
    ]
    return {item.collector_id: item for item in collectors}


def run_collectors(
    config: KnowledgeConfig,
    *,
    selected: Sequence[str] | None = None,
    execute: CommandExecutor = safe_execute,
    clock: Callable[[], datetime] = utc_now,
) -> tuple[
    tuple[CollectorResult, ...],
    tuple[KnowledgeEntity, ...],
    tuple[KnowledgeRelationship, ...],
    tuple[DiscoveryEvidence, ...],
]:
    registry = collector_registry()
    requested = tuple(selected or config.enabled_collectors)
    unknown = sorted(set(requested) - set(registry))
    if unknown:
        raise ValueError(f"collectors are not allow-listed: {', '.join(unknown)}")
    entities, relationships, evidence, results = [], [], [], []
    for collector_id in requested:
        collector = registry[collector_id]
        started = time.monotonic()
        context = CollectorContext(config, config.node_id, clock(), execute)
        try:
            normalized = collector.collect(context)
            entities.extend(normalized.entities)
            relationships.extend(normalized.relationships)
            evidence.extend(normalized.evidence)
            results.append(
                CollectorResult(
                    collector_id=collector_id,
                    success=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    entity_ids=tuple(item.entity_id for item in normalized.entities),
                    relationship_ids=tuple(
                        item.relationship_id for item in normalized.relationships
                    ),
                    evidence_ids=tuple(
                        item.evidence_id for item in normalized.evidence
                    ),
                    warning=normalized.warning,
                )
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            results.append(
                CollectorResult(
                    collector_id=collector_id,
                    success=False,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=str(exc)[:1024],
                )
            )
    return tuple(results), tuple(entities), tuple(relationships), tuple(evidence)
