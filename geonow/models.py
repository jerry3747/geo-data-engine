from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Question:
    id: str
    text: str
    enabled: bool = True


@dataclass
class PartnerArticle:
    url: str
    published_at: str | None = None
    title: str | None = None


@dataclass
class MonitorConfig:
    questions: list[Question]
    brand: str
    aliases: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    partner_articles: list[PartnerArticle] = field(default_factory=list)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    sampling: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MonitorConfig":
        questions = []
        for index, item in enumerate(raw.get("questions", []), 1):
            if isinstance(item, str):
                questions.append(Question(id=f"q{index}", text=item))
            else:
                questions.append(Question(
                    id=str(item.get("id") or f"q{index}"),
                    text=str(item["text"]),
                    enabled=bool(item.get("enabled", True)),
                ))
        brand = str(raw.get("brand", "")).strip()
        if not brand:
            raise ValueError("config.brand 不能为空")
        return cls(
            questions=questions,
            brand=brand,
            aliases=[str(x) for x in raw.get("aliases", [])],
            competitors=[str(x) for x in raw.get("competitors", [])],
            partner_articles=[PartnerArticle(
                url=str(x["url"]),
                published_at=x.get("published_at"),
                title=x.get("title"),
            ) for x in raw.get("partner_articles", [])],
            providers=dict(raw.get("providers", {})),
            sampling=dict(raw.get("sampling", {})),
        )

    @property
    def brand_terms(self) -> list[str]:
        return [self.brand, *self.aliases]

    def as_dict(self) -> dict[str, Any]:
        return {
            "brand": self.brand,
            "aliases": self.aliases,
            "competitors": self.competitors,
            "questions": [q.__dict__ for q in self.questions],
            "partner_articles": [a.__dict__ for a in self.partner_articles],
            "providers": self.providers,
            "sampling": self.sampling,
        }


def load_config(path: str | Path) -> MonitorConfig:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("读取 YAML 需要安装 PyYAML：pip install '.[yaml]'") from exc
        raw = yaml.safe_load(text) or {}
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是对象")
    return MonitorConfig.from_dict(raw)
