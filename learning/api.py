from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated

from learning.ledger import Conflict, Ledger

ledger = Ledger()
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]


class Line(BaseModel):
    code: Identifier
    units: int = Field(gt=0)
    cents: int = Field(ge=0)


class Claim(BaseModel):
    hospital: Identifier
    source: Identifier
    payer: Identifier
    revision: int = Field(ge=1)
    type: Literal["I", "P"]
    service_date: date
    lines: list[Line] = Field(min_length=1, max_length=500)


@asynccontextmanager
async def lifespan(app):
    ledger.initialize()
    yield


app = FastAPI(title="Claims Platform Learning API", lifespan=lifespan)


@app.post("/claims", status_code=202)
def intake(claim: Claim):
    try:
        return ledger.accept(claim.model_dump(mode="json"))
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/deliveries/{delivery_id}")
def get_delivery(delivery_id: int):
    delivery = ledger.get(delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery
