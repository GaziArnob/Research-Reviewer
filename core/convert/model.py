from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Block:
    kind: str  # para | bullets | figure | table
    text: str = ""  # paragraph text
    items: list[str] = field(default_factory=list)  # bullet items
    num: Optional[int] = None  # figure/table number
    caption: str = ""
    image: Optional[str] = None  # file name inside images/ when a raster image was matched


@dataclass
class Sec:
    heading: str
    level: int = 1
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Doc:
    title: str = ""
    authors_raw: list[str] = field(default_factory=list)
    abstract: str = ""
    footnotes: str = ""
    keywords: list[str] = field(default_factory=list)
    sections: list[Sec] = field(default_factory=list)
    acknowledgements: list[Block] = field(default_factory=list)
    references: list[tuple[str, str]] = field(default_factory=list)  # (key, text)
    images: dict[str, bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def body_words(self) -> int:
        n = len(self.abstract.split())
        for s in self.sections:
            for b in s.blocks:
                n += len((b.text + " " + " ".join(b.items)).split())
        return n
