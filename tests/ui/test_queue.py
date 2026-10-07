import asyncio

import pytest

from coffee_house.conversation import TurnResult
from coffee_house.responses import CustomerReply
from coffee_house.ui.queue import AdmissionError, QueryQueue


class Client:
    def __init__(self):
        self.free={0,1,2}
        self.cleanups=set()


class Flow:
    def __init__(self):
        self.client=Client()
        self.started=[]
        self.events={}
        self.physical=asyncio.Event()
        self.physical.set()
    async def answer(self,question,on_slot,**kwargs):
        slot=min(self.client.free)
        self.client.free.remove(slot)
        on_slot(slot)
        self.started.append(question)
        self.events[question]=asyncio.Event()
        try:
            await self.events[question].wait()
            return TurnResult(CustomerReply(question,"available"),"selected","stock",0)
        finally:
            await self.physical.wait()
            self.client.free.add(slot)


async def spin():
    for _ in range(5):
        await asyncio.sleep(0)


def test_three_active_ten_waiting_fifo_and_one_per_session():
    async def run():
        flow=Flow();queue=QueryQueue(flow)
        tickets=[queue.submit(str(i),str(i)) for i in range(13)]
        await spin()
        assert len(queue.running)==3 and len(queue.waiting)==10
        assert flow.started==["0","1","2"]
        with pytest.raises(AdmissionError):
            queue.submit("extra","extra")
        with pytest.raises(AdmissionError):
            queue.submit("0","second")
        flow.events["0"].set();await spin()
        assert flow.started==["0","1","2","3"]
        queue.cancel("4",tickets[4].id)
        assert tickets[4].held is False and tickets[4].id not in queue.waiting
        await queue.close()
    asyncio.run(run())


def test_cancel_keeps_session_and_capacity_until_physical_stop():
    async def run():
        flow=Flow();queue=QueryQueue(flow)
        ticket=queue.submit("a","a");await spin()
        flow.physical.clear()
        queue.cancel("a",ticket.id);await spin()
        assert ticket.held and len(queue.running)==1
        with pytest.raises(AdmissionError):queue.submit("a","again")
        flow.physical.set();await spin()
        assert not ticket.held and flow.client.free=={0,1,2}
        queue.submit("a","again");await spin()
        await queue.close()
    asyncio.run(run())


def test_total_deadline_expires_waiting_without_model_call():
    async def run():
        flow=Flow();queue=QueryQueue(flow,limit_seconds=.02)
        tickets=[queue.submit(str(i),str(i)) for i in range(4)]
        await asyncio.sleep(.04);await spin()
        assert "3" not in flow.started
        assert all(t.status=="expired" and not t.held for t in tickets)
        await queue.close()
    asyncio.run(run())


def test_other_tab_cannot_cancel_or_read_ticket():
    async def run():
        flow=Flow();queue=QueryQueue(flow)
        ticket=queue.submit("owner","a");await spin()
        with pytest.raises(AdmissionError):queue.cancel("other",ticket.id)
        with pytest.raises(AdmissionError):queue.get("other",ticket.id)
        await queue.close()
    asyncio.run(run())
