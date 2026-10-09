"""Local learning lifecycle with version checks and atomic finish/recap persistence."""

import uuid
from contextlib import contextmanager
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat_sessions import ChatSession
from app.models.session_profile import SessionProfile
from app.models.knowledge import SessionWorkspace
from app.models.messages import Message
from app.models.learning import LearningContext, LearningRecap, RecapDraft, now
from app.schemas.learning import NotesEdit, GenerateRecap, Finish, Revision, RecapEdit
from app.services.learning_service import (
    ensure_context,
    context_data,
    snapshot,
    generate_recap,
)
from app.services.knowledge_service import owned_workspace
from app.api.routes.chat import chat_slot

router = APIRouter(prefix="/chat/sessions/{session_id}/learning")


@contextmanager
def mutation():
    if not chat_slot.acquire(blocking=False):
        raise HTTPException(
            409,
            "A response or learning update is in progress. Your draft is kept; try again shortly.",
        )
    try:
        yield
    finally:
        chat_slot.release()


def owned(db, session_id, user_id):
    session = db.query(ChatSession).filter_by(id=session_id, user_id=user_id).first()
    profile = db.get(SessionProfile, session_id)
    binding = db.get(SessionWorkspace, session_id)
    if not session or not profile or profile.session_type != "learning" or not binding:
        raise HTTPException(404, "Learning session not found")
    owned_workspace(db, binding.workspace_id, user_id)
    return session, profile


def check(ctx, revision):
    if ctx.revision != revision:
        raise HTTPException(
            409,
            "Learning records changed. Reload the latest version before saving; your editing draft is kept.",
        )


def recap_data(row):
    return dict(
        id=row.id,
        cycle=row.cycle,
        content=row.content,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("")
def read(
    session_id: str, user_id=Depends(get_current_user_id), db: Session = Depends(get_db)
):
    _, profile = owned(db, session_id, user_id)
    ctx = db.get(LearningContext, session_id)
    if not ctx:
        with mutation():
            ctx = ensure_context(db, session_id)
            db.commit()
    result = context_data(ctx, profile)
    result["recaps"] = [
        recap_data(r)
        for r in db.query(LearningRecap)
        .filter_by(session_id=session_id)
        .order_by(LearningRecap.cycle.desc())
        .all()
    ]
    result["drafts"] = [
        dict(
            id=d.id,
            cycle=d.cycle,
            context_revision=d.context_revision,
            target_recap=d.target_recap,
            content=d.content,
        )
        for d in db.query(RecapDraft)
        .filter_by(session_id=session_id, cycle=ctx.cycle)
        .order_by(RecapDraft.created_at.desc())
        .limit(10)
        .all()
    ]
    return result


@router.put("/notes")
def notes(
    session_id: str,
    payload: NotesEdit,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    with mutation():
        session, profile = owned(db, session_id, user_id)
        ctx = ensure_context(db, session_id)
        check(ctx, payload.revision)
        for item in payload.evidence:
            message = db.get(Message, item.message_id)
            if (
                not message
                or message.session_id != session_id
                or message.role != "user"
                or item.quote not in message.content
            ):
                raise HTTPException(
                    422,
                    "Each observation must retain a real quotation from this conversation.",
                )
        if not payload.goal.strip():
            raise HTTPException(422, "A learning goal cannot be blank.")
        profile.learning_goal = payload.goal.strip()
        ctx.focus = payload.focus
        ctx.notes = payload.notes
        ctx.next_step = payload.next_step
        ctx.evidence = [e.model_dump() for e in payload.evidence]
        ctx.revision += 1
        ctx.updated_at = now()
        ctx.update_warning = False
        db.commit()
    return read(session_id, user_id, db)


@router.post("/recap-draft")
def draft(
    session_id: str,
    payload: GenerateRecap,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    with mutation():
        _, profile = owned(db, session_id, user_id)
        ctx = ensure_context(db, session_id)
        existing = db.get(RecapDraft, str(payload.request_id))
        if existing:
            if (
                existing.session_id != session_id
                or existing.target_recap != payload.target_recap
            ):
                raise HTTPException(409, "This request belongs to another recap.")
            return dict(
                id=existing.id,
                cycle=existing.cycle,
                context_revision=existing.context_revision,
                target_recap=existing.target_recap,
                content=existing.content,
            )
        check(ctx, payload.revision)
        revision, cycle = ctx.revision, ctx.cycle
        if payload.target_recap:
            target = db.get(LearningRecap, payload.target_recap)
            if (
                not target
                or target.session_id != session_id
                or target.content is not None
            ):
                raise HTTPException(409, "This recap is unavailable or already saved.")
            data = target.snapshot
            cycle = target.cycle
        else:
            if ctx.status != "active":
                raise HTTPException(
                    409, "Choose the finished record to create its recap."
                )
            data = snapshot(db, session_id, ctx, profile)
        # Copy rather than mutate a saved historical snapshot.
        data = {**data, "ui_locale": payload.ui_locale}
        db.commit()  # persist lazy legacy context before provider rollback
        content = generate_recap(db, user_id, data)
        d = RecapDraft(
            id=str(payload.request_id),
            session_id=session_id,
            cycle=cycle,
            context_revision=revision,
            target_recap=payload.target_recap,
            content=content,
            snapshot=data,
        )
        db.add(d)
        db.commit()
        return dict(
            id=d.id,
            cycle=d.cycle,
            context_revision=d.context_revision,
            target_recap=d.target_recap,
            content=d.content,
        )


@router.post("/finish")
def finish(
    session_id: str,
    payload: Finish,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    with mutation():
        session, profile = owned(db, session_id, user_id)
        ctx = ensure_context(db, session_id)
        prior = (
            db.query(LearningRecap)
            .filter_by(session_id=session_id, cycle=payload.cycle)
            .first()
        )
        if prior and ctx.status == "finished" and ctx.cycle == payload.cycle:
            # Recover a committed finish whose response was lost. Never overwrite its recap.
            return read(session_id, user_id, db)
        check(ctx, payload.revision)
        if ctx.status != "active" or ctx.cycle != payload.cycle:
            raise HTTPException(409, "Session state changed. Reload before finishing.")
        if payload.without_recap:
            if payload.draft_id or payload.content:
                raise HTTPException(422, "Choose one finish action.")
            content = None
            data = snapshot(db, session_id, ctx, profile)
        else:
            d = db.get(RecapDraft, payload.draft_id or "")
            if (
                not d
                or d.session_id != session_id
                or d.target_recap
                or d.cycle != ctx.cycle
                or d.context_revision != ctx.revision
                or not payload.content
            ):
                raise HTTPException(
                    409,
                    "This recap draft is out of date. Keep your edits and create a fresh draft before finishing.",
                )
            content = payload.content.model_dump()
            data = d.snapshot
        db.add(
            LearningRecap(
                id=str(uuid.uuid4()),
                session_id=session_id,
                cycle=ctx.cycle,
                content=content,
                snapshot=data,
            )
        )
        ctx.status = "finished"
        ctx.finished_at = now()
        ctx.revision += 1
        ctx.updated_at = now()
        session.updated_at = now()
        db.commit()
    return read(session_id, user_id, db)


@router.post("/resume")
def resume(
    session_id: str,
    payload: Revision,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    with mutation():
        session, _ = owned(db, session_id, user_id)
        ctx = ensure_context(db, session_id)
        if ctx.status == "active" and ctx.revision == payload.revision + 1:
            return read(session_id, user_id, db)
        check(ctx, payload.revision)
        if ctx.status != "finished":
            raise HTTPException(409, "This learning session is already active.")
        ctx.status = "active"
        ctx.cycle += 1
        ctx.revision += 1
        ctx.finished_at = None
        ctx.updated_at = now()
        session.updated_at = now()
        db.commit()
    return read(session_id, user_id, db)


@router.put("/recaps/{recap_id}")
def edit_recap(
    session_id: str,
    recap_id: str,
    payload: RecapEdit,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    with mutation():
        owned(db, session_id, user_id)
        row = db.get(LearningRecap, recap_id)
        if not row or row.session_id != session_id:
            raise HTTPException(404, "Recap not found")
        if row.revision != payload.revision:
            raise HTTPException(
                409, "This recap changed. Reload before saving; your draft is kept."
            )
        row.content = payload.content.model_dump()
        row.revision += 1
        row.updated_at = now()
        db.commit()
    return read(session_id, user_id, db)
