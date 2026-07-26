from src.collectors.ashby import AshbyCollector
from src.collectors.base import BaseCollector, CollectionResult, CollectorError, CompanySource, RawJob, load_companies
from src.collectors.custom_page import CustomPageCollector
from src.collectors.government import GovernmentCollector
from src.collectors.greenhouse import GreenhouseCollector
from src.collectors.lever import LeverCollector
from src.collectors.workday import WorkdayCollector

COLLECTOR_REGISTRY: dict[str, type[BaseCollector]] = {
    "greenhouse": GreenhouseCollector,
    "lever": LeverCollector,
    "ashby": AshbyCollector,
    "government": GovernmentCollector,
    "custom": CustomPageCollector,
    "workday": WorkdayCollector,
}


def get_collector_class(ats_type: str) -> type[BaseCollector]:
    try:
        return COLLECTOR_REGISTRY[ats_type]
    except KeyError as exc:
        raise ValueError(f"No collector registered for ats_type={ats_type!r}") from exc


__all__ = [
    "BaseCollector",
    "CollectionResult",
    "CollectorError",
    "CompanySource",
    "RawJob",
    "load_companies",
    "GreenhouseCollector",
    "LeverCollector",
    "AshbyCollector",
    "GovernmentCollector",
    "CustomPageCollector",
    "WorkdayCollector",
    "COLLECTOR_REGISTRY",
    "get_collector_class",
]
