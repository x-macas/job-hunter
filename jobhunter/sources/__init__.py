"""Job sources. Each one returns a list of RawJob."""

from dataclasses import dataclass, field


@dataclass
class RawJob:
    source: str           # greenhouse, lever, ashby, remotive, remoteok, hn, adzuna, link
    source_id: str
    source_name: str      # shown on the card
    company: str
    title: str
    url: str
    location: str = ""
    remote: bool = False
    apply_url: str | None = None
    description: str | None = None   # plain text
    posted_at: float | None = None   # unix time
    job_type: str = "unknown"
    salary_text: str = ""
    extra: dict = field(default_factory=dict)
