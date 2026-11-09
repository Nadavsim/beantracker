"""robots.txt matching per RFC 9309.

The standard library's RobotFileParser treats `*` literally and returns the first rule that
matches, so `Disallow: /*?sort_by` blocks nothing and rule order changes the answer. Shopify
and WooCommerce robots files rely on wildcards, so this module does it properly:
user-agent groups, `*` and `$` in paths, and the longest matching rule wins (Allow on a tie).
"""

import contextlib
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = re.escape(body).replace(r"\*", ".*") + ("$" if anchored else "")
    return re.compile(regex)


def agent_token(user_agent: str) -> str:
    """'beantracker/0.1 (+https://...)' -> 'beantracker': the product token robots.txt names."""
    match = re.match(r"[A-Za-z0-9_-]+", user_agent)
    return (match.group(0) if match else user_agent).lower()


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: float | None = None


class Robots:
    def __init__(self, groups: list[_Group]) -> None:
        self._groups = groups

    @classmethod
    def allow_all(cls) -> "Robots":
        return cls([])

    @classmethod
    def disallow_all(cls) -> "Robots":
        return cls.parse("User-agent: *\nDisallow: /")

    @classmethod
    def parse(cls, text: str) -> "Robots":
        groups: list[_Group] = []
        current = _Group()
        in_rules = False  # a user-agent line after any rule line starts the next group
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:
                    groups.append(current)
                    current, in_rules = _Group(), False
                current.agents.append(value.lower())
            elif key in ("allow", "disallow") and current.agents:
                in_rules = True
                if value:  # an empty Disallow means "nothing is disallowed"
                    current.rules.append(_Rule(key == "allow", value, _compile(value)))
            elif key == "crawl-delay" and current.agents:
                in_rules = True
                # Not part of RFC 9309 but widely published: seconds between requests.
                with contextlib.suppress(ValueError):
                    current.crawl_delay = max(0.0, float(value))
        if current.agents:
            groups.append(current)
        return cls(groups)

    def _groups_for(self, token: str) -> list[_Group]:
        """Groups naming this crawler; the `*` groups only if none does."""
        named = [g for g in self._groups if token in g.agents]
        return named or [g for g in self._groups if "*" in g.agents]

    def _rules_for(self, token: str) -> list[_Rule]:
        return [r for g in self._groups_for(token) for r in g.rules]

    def crawl_delay(self, token: str) -> float | None:
        """Seconds the site asks between requests, if it says so for this crawler."""
        delays = [g.crawl_delay for g in self._groups_for(token.lower()) if g.crawl_delay]
        return max(delays) if delays else None

    def allowed(self, token: str, path: str) -> bool:
        """`path` includes the query string, as rules may match on it."""
        best: _Rule | None = None
        for rule in self._rules_for(token.lower()):
            if not rule.regex.match(path):
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow)
            ):
                best = rule
        return best is None or best.allow
