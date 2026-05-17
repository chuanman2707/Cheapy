from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from cheapy.providers.base import ProviderExactRoundTripRequest, ProviderResult
from cheapy.providers.traveloka.provider import create_provider


ROUTES = (
    ("SGN", "BKK", "2026-06-12", "2026-06-17"),
    ("HAN", "SIN", "2026-07-03", "2026-07-08"),
    ("DAD", "KUL", "2026-07-05", "2026-07-10"),
    ("SGN", "HKG", "2026-07-09", "2026-07-14"),
    ("SGN", "NRT", "2026-07-11", "2026-07-18"),
)


def matrix_record(
    *,
    run_label: str,
    request: ProviderExactRoundTripRequest,
    result: ProviderResult,
    duration_ms: int,
) -> dict[str, object]:
    if run_label not in {"baseline", "refactored"}:
        raise ValueError("run_label must be baseline or refactored")
    return {
        "run_label": run_label,
        "origin": request.origin,
        "destination": request.destination,
        "departure_date": request.departure_date,
        "return_date": request.return_date,
        "status": result.status.value,
        "offer_count": len(result.offers),
        "comparable_offer_count": sum(1 for offer in result.offers if offer.comparable),
        "failure_types": sorted(
            {
                str(error.details.get("failure_type"))
                for error in result.errors
                if error.details.get("failure_type") is not None
            }
        ),
        "duration_ms": duration_ms,
    }


async def run_matrix(*, run_label: str) -> list[dict[str, object]]:
    provider = create_provider()
    records: list[dict[str, object]] = []
    for origin, destination, departure_date, return_date in ROUTES:
        request = ProviderExactRoundTripRequest(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
        )
        started = perf_counter()
        result = await provider.search_exact_round_trip(request)
        records.append(
            matrix_record(
                run_label=run_label,
                request=request,
                result=result,
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
            )
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-label", choices=("baseline", "refactored"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = asyncio.run(run_matrix(run_label=args.run_label))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
