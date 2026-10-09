"""Small authenticated local receiver: allowlisted scalars only, no free text."""
from datetime import datetime,timezone
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Response,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from app.core.deps import get_current_user_id
from app.services.runtime_metrics import write_event
from collections import deque
from threading import Lock
import time

router=APIRouter(); arrivals=deque();lock=Lock()
class ClientTiming(BaseModel):
    model_config=ConfigDict(extra='forbid')
    trace_id:UUID
    elapsed_ms:float=Field(ge=0,le=300000,allow_inf_nan=False)
    status:int=Field(ge=0,le=599)
    outcome:Literal['ok','http_error','network','aborted']

@router.post('/runtime-metrics/client',status_code=204)
def client_timing(payload:ClientTiming,user_id=Depends(get_current_user_id)):
    with lock:
        now=time.monotonic()
        while arrivals and arrivals[0]<now-60:arrivals.popleft()
        if len(arrivals)>=240:raise HTTPException(429,'Local metrics limit reached')
        arrivals.append(now)
    write_event({'version':'runtime-v1','kind':'client_request','trace_id':str(payload.trace_id),
        'client_elapsed_ms':round(payload.elapsed_ms,2),'status':payload.status,'outcome':payload.outcome,
        'measurement':'fetch_start_to_body_parsed','trust':'client_reported',
        'timestamp_utc':datetime.now(timezone.utc).isoformat()})
    return Response(status_code=204)
