import asyncio

from tahmeed.services import truck_service


def test_fleet_facade_fetches_all_pages() -> None:
    async def scenario() -> None:
        calls: list[dict] = []

        async def request(_method: str, path: str, *, params: dict) -> dict:
            assert path == "v1/trucks"
            calls.append(dict(params))
            offset = params["offset"]
            total = 601
            stop = min(offset + params["limit"], total)
            return {
                "items": [
                    {"number": f"T{index:04d}", "active": True}
                    for index in range(offset, stop)
                ],
                "total": total,
                "limit": params["limit"],
                "offset": offset,
            }

        original = truck_service.api_client.request
        truck_service.api_client.request = request
        try:
            trucks = await truck_service.get_all_trucks(active_only=True)
        finally:
            truck_service.api_client.request = original

        assert len(trucks) == 601
        assert [call["offset"] for call in calls] == [0, 500]
        assert all(call["active"] == "true" for call in calls)

    asyncio.run(scenario())


def test_fleet_facade_preserves_page_and_count_contracts() -> None:
    async def scenario() -> None:
        calls: list[dict] = []

        async def request(_method: str, path: str, *, params: dict) -> dict:
            calls.append({"path": path, **params})
            stop = min(params["offset"] + params["limit"], 73)
            return {
                "items": [
                    {"number": f"T{index:03d}", "active": False}
                    for index in range(params["offset"], stop)
                ],
                "total": 73,
                "limit": params["limit"],
                "offset": params["offset"],
            }

        original = truck_service.api_client.request
        truck_service.api_client.request = request
        try:
            page = await truck_service.list_trucks(
                search="T",
                active_filter="inactive",
                limit=25,
                skip=50,
            )
            count = await truck_service.count_trucks(
                search="T",
                active_filter="inactive",
            )
        finally:
            truck_service.api_client.request = original

        assert len(page) == 23
        assert page[0] == {"number": "T050", "active": False}
        assert page[-1] == {"number": "T072", "active": False}
        assert count == 73
        assert calls[0] == {
            "path": "v1/trucks",
            "search": "T",
            "active": "false",
            "limit": 25,
            "offset": 50,
        }
        assert calls[1]["limit"] == 1
        assert calls[1]["offset"] == 0

    asyncio.run(scenario())


def test_fleet_cache_tolerates_missing_optional_routes() -> None:
    """Older APIs without motor_vehicles must still load trucks/trailers."""
    from tahmeed.services.api_client import ApiError

    async def scenario() -> None:
        truck_service.invalidate_fleet_cache()

        async def request(_method: str, path: str, *, params: dict) -> dict:
            if path == "v1/motor_vehicles":
                raise ApiError("missing", status_code=404)
            if path == "v1/trailers":
                return {
                    "items": [{"number": "T999 TRL", "active": True}],
                    "total": 1,
                    "limit": 500,
                    "offset": 0,
                }
            assert path == "v1/trucks"
            return {
                "items": [{"number": "T688 EAF", "active": True}],
                "total": 1,
                "limit": 500,
                "offset": 0,
            }

        original = truck_service.api_client.request
        truck_service.api_client.request = request
        try:
            fleet = await truck_service.get_fleet_numbers()
            kinds = await truck_service.get_fleet_kinds()
        finally:
            truck_service.api_client.request = original
            truck_service.invalidate_fleet_cache()

        assert fleet == {"T688 EAF", "T999 TRL"}
        assert kinds["T688 EAF"] == "truck"
        assert kinds["T999 TRL"] == "trailer"

    asyncio.run(scenario())


def test_fleet_cache_requires_trucks_route() -> None:
    from tahmeed.services.api_client import ApiError

    async def scenario() -> None:
        truck_service.invalidate_fleet_cache()

        async def request(_method: str, path: str, *, params: dict) -> dict:
            raise ApiError("missing", status_code=404)

        original = truck_service.api_client.request
        truck_service.api_client.request = request
        try:
            try:
                await truck_service.get_fleet_numbers()
                assert False, "expected ApiError"
            except ApiError as exc:
                assert exc.status_code == 404
                assert "v1/trucks" in str(exc)
        finally:
            truck_service.api_client.request = original
            truck_service.invalidate_fleet_cache()

    asyncio.run(scenario())
