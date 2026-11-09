import pytest

from beantracker.http.robots import Robots, agent_token

UA = "beantracker"


def allowed(robots_txt: str, path: str, agent: str = UA) -> bool:
    return Robots.parse(robots_txt).allowed(agent, path)


@pytest.mark.parametrize(
    ("user_agent", "token"),
    [
        ("beantracker/0.1 (+https://github.com/x/y; portfolio project)", "beantracker"),
        ("BeanTracker", "beantracker"),
        ("beantracker-test/0.1", "beantracker-test"),
    ],
)
def test_agent_token(user_agent: str, token: str) -> None:
    assert agent_token(user_agent) == token


def test_wildcard_in_a_path_matches_any_characters() -> None:
    txt = "User-agent: *\nDisallow: /*?sort_by"
    assert not allowed(txt, "/collections/all?sort_by=price")
    assert allowed(txt, "/collections/all")


def test_dollar_anchors_the_end_of_the_path() -> None:
    txt = "User-agent: *\nDisallow: /*.pdf$"
    assert not allowed(txt, "/docs/menu.pdf")
    assert allowed(txt, "/docs/menu.pdf?download=1")  # no longer ends in .pdf


def test_a_plain_rule_is_a_prefix() -> None:
    txt = "User-agent: *\nDisallow: /cart"
    assert not allowed(txt, "/cart")
    assert not allowed(txt, "/cart/items")
    assert not allowed(txt, "/carton")  # anything that starts with the rule is blocked
    assert allowed(txt, "/car")


@pytest.mark.parametrize(
    "txt",
    [
        "User-agent: *\nDisallow: /private/\nAllow: /private/ok",
        "User-agent: *\nAllow: /private/ok\nDisallow: /private/",
    ],
)
def test_longest_match_wins_whatever_the_order(txt: str) -> None:
    assert allowed(txt, "/private/ok")
    assert not allowed(txt, "/private/secret")


def test_allow_wins_a_tie() -> None:
    assert allowed("User-agent: *\nDisallow: /a\nAllow: /a", "/a")


def test_named_group_beats_the_wildcard_group() -> None:
    txt = "User-agent: *\nDisallow: /\n\nUser-agent: beantracker\nDisallow: /admin"
    assert allowed(txt, "/products")
    assert not allowed(txt, "/admin")
    assert not allowed(txt, "/products", agent="otherbot")


def test_consecutive_user_agent_lines_share_one_group() -> None:
    txt = "User-agent: otherbot\nUser-agent: beantracker\nDisallow: /x"
    assert not allowed(txt, "/x")


def test_empty_disallow_means_everything_is_allowed() -> None:
    assert allowed("User-agent: *\nDisallow:", "/anything")


def test_comments_case_and_unknown_lines_are_ignored() -> None:
    txt = (
        "# hello\nUSER-AGENT: *   # all\nDISALLOW: /x  # no\n"
        "Sitemap: https://a/s.xml\nCrawl-delay: 5"
    )
    assert not allowed(txt, "/x")
    assert allowed(txt, "/y")


def test_no_rules_or_no_matching_group_allows() -> None:
    assert allowed("", "/x")
    assert allowed("User-agent: googlebot\nDisallow: /", "/x")


def test_rule_before_any_user_agent_is_ignored() -> None:
    assert allowed("Disallow: /x\nUser-agent: *\nAllow: /", "/x")


def test_disallow_all_and_allow_all_helpers() -> None:
    assert not Robots.disallow_all().allowed(UA, "/")
    assert Robots.allow_all().allowed(UA, "/x")
