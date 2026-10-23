"""Plans and limits. These are placeholders for a future commercial offer: no prices and no payment integration yet."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    papers_per_month: int  # 0 = unlimited
    llm_review: bool
    description: str


PLANS = {
    "free": Plan("free", "Starter", 10, True, "Individual reviewers trying the tool."),
    "team": Plan("team", "Team", 100, True, "Programme committees and labs."),
    "institution": Plan("institution", "Institution", 0, True, "Publishers and conferences with their own policies."),
}
DEFAULT_PLAN = "free"


def plan_of(key: str) -> Plan:
    return PLANS.get(key, PLANS[DEFAULT_PLAN])
