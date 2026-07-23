import asyncio

from tahmeed.services import people_service


def test_people_facade_fetches_all_pages() -> None:
    async def scenario() -> None:
        calls: list[dict] = []

        async def request(_method: str, path: str, *, params: dict) -> dict:
            assert path == "v1/people"
            calls.append(dict(params))
            offset = params["offset"]
            total = 601
            stop = min(offset + params["limit"], total)
            return {
                "items": [
                    {"name": f"PERSON {index:04d}", "active": True}
                    for index in range(offset, stop)
                ],
                "total": total,
                "limit": params["limit"],
                "offset": offset,
            }

        original = people_service.api_client.request
        people_service.invalidate_people_cache()
        people_service.api_client.request = request
        try:
            names = await people_service.get_people_names(active_only=True)
        finally:
            people_service.api_client.request = original
            people_service.invalidate_people_cache()

        assert len(names) == 601
        assert names[0] == "PERSON 0000"
        assert [call["offset"] for call in calls] == [0, 500]
        assert all(call["active"] == "true" for call in calls)

    asyncio.run(scenario())


def test_people_facade_preserves_page_and_count_contracts() -> None:
    async def scenario() -> None:
        calls: list[dict] = []

        async def request(_method: str, path: str, *, params: dict) -> dict:
            calls.append({"path": path, **params})
            stop = min(params["offset"] + params["limit"], 73)
            return {
                "items": [
                    {"name": f"PERSON {index:03d}", "active": False}
                    for index in range(params["offset"], stop)
                ],
                "total": 73,
                "limit": params["limit"],
                "offset": params["offset"],
            }

        original = people_service.api_client.request
        people_service.api_client.request = request
        try:
            page = await people_service.list_people(
                search="PER",
                active_filter="inactive",
                limit=25,
                skip=50,
            )
            count = await people_service.count_people(
                search="PER",
                active_filter="inactive",
            )
        finally:
            people_service.api_client.request = original

        assert len(page) == 23
        assert page[0] == {"name": "PERSON 050", "active": False}
        assert page[-1] == {"name": "PERSON 072", "active": False}
        assert count == 73
        assert calls[0] == {
            "path": "v1/people",
            "search": "PER",
            "active": "false",
            "limit": 25,
            "offset": 50,
        }

    asyncio.run(scenario())
