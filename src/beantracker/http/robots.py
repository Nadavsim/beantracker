"""robots.txt matching per RFC 9309.

The standard library's RobotFileParser treats `*` literally and returns the first rule that
matches, so `Disallow: /*?sort_by` blocks nothing and rule order changes the answer. Shopify
and WooCommerce robots files rely on wildcards, so this module does it properly:
user-agent groups, `*` and `$` in paths, and the longest matching rule wins (Allow on a tie).
"""

import re
from dataclasses import dataclass


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


class Robots:
    def __init__(self, groups: list[tuple[list[str], list[_Rule]]]) -> None:
        self._groups = groups

    @classmethod
    def allow_all(cls) -> "Robots":
        return cls([])

    @classmethod
    def disallow_all(cls) -> "Robots":
        return cls.parse("User-agent: *\nDisallow: /")

    @classmethod
    def parse(cls, text: str) -> "Robots":
        groups: list[tuple[list[str], list[_Rule]]] = []
        agents: list[str] = []
        rules: list[_Rule] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:  # a user-agent line after rules starts the next group
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow") and agents:
                in_rules = True
                if value:  # an empty Disallow means "nothing is disallowed"
                    rules.append(_Rule(key == "allow", value, _compile(value)))
        if agents:
            groups.append((agents, rules))
        return cls(groups)

    def _rules_for(self, token: str) -> list[_Rule]:
        """Rules of every group naming this crawler; the `*` groups only if none does."""
        named = [r for agents, rs in self._groups if token in agents for r in rs]
        if any(token in agents for agents, _ in self._groups):
            return named
        return [r for agents, rs in self._groups if "*" in agents for r in rs]

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
