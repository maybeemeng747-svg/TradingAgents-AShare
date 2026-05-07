import asyncio

from scheduler import main as scheduler


def _reset_queue_state(limit: int) -> None:
    scheduler.SCHEDULER_CONCURRENCY = limit
    scheduler._semaphore = asyncio.Semaphore(limit)


def test_scheduler_concurrency_slot_waits_when_limit_is_full():
    async def scenario():
        _reset_queue_state(1)
        entered = []
        release_first = asyncio.Event()
        first_started = asyncio.Event()

        async def first():
            async with scheduler._concurrency_slot("job-first", "300750.SZ"):
                entered.append("job-first")
                first_started.set()
                await release_first.wait()

        async def second():
            await first_started.wait()
            async with scheduler._concurrency_slot("job-second", "600519.SH"):
                entered.append("job-second")

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())

        await first_started.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert entered == ["job-first"]

        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)

        assert entered == ["job-first", "job-second"]

    asyncio.run(scenario())


def test_scheduler_concurrency_slot_respects_max_concurrency():
    async def scenario():
        _reset_queue_state(2)
        in_flight = 0
        max_in_flight = 0
        gate = asyncio.Event()

        async def worker(job_id: str):
            nonlocal in_flight, max_in_flight
            async with scheduler._concurrency_slot(job_id, job_id):
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                await gate.wait()
                in_flight -= 1

        tasks = [asyncio.create_task(worker(f"job-{i}")) for i in range(3)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert max_in_flight == 2

        gate.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    asyncio.run(scenario())
