import threading
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat_sessions import ChatSession
from app.models.messages import Message
from app.models.knowledge import SessionWorkspace, MessageEvidence, Document
from app.models.chat_turn import ChatTurn
from app.models.session_profile import SessionProfile
from app.schemas.chat import ChatRequest, ChatResponse
from app.models.learning import LearningContext
from app.services.deletion_service import delete_sessions
from app.services.learning_service import ensure_context, context_data, apply_observation
from app.services.knowledge_service import owned_workspace, retrieve
from app.services.rag_service import grounded_answer
from app.services.smart_routing import decide, previous_decision, explicit_evidence, VERSION
from app.services.conversation_language import resolve

router = APIRouter()
chat_slot = threading.BoundedSemaphore(1)


def mode():
    return f"embedding={settings.EMBEDDING_BACKEND};llm={settings.LLM_BACKEND}"


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    evidence_deadline = time.perf_counter() + 75
    citation_diagnostics = {}
    if not chat_slot.acquire(blocking=False):
        raise HTTPException(
            429, "A chat is already running locally. Please try again shortly."
        )
    try:
        existing = db.get(ChatTurn, str(payload.request_id))
        if existing:
            binding = db.get(SessionWorkspace, existing.session_id)
            if (
                existing.user_id != user_id
                or existing.question != payload.message.strip()
                or existing.response.get("answer_mode", "smart") != payload.answer_mode
                or existing.response.get("learning_mode", "direct")
                != payload.learning_mode
                or not binding
                or binding.workspace_id != payload.workspace_id
                or (payload.session_id and payload.session_id != existing.session_id)
                or (payload.session_type is not None and payload.session_type != existing.response.get("session_type"))
            ):
                raise HTTPException(409, "Request ID already used for a different turn")
            return existing.response
        if not payload.workspace_id:
            raise HTTPException(422, "Select a Workspace before asking about materials")
        owned_workspace(db, payload.workspace_id, user_id)
        question = payload.message.strip()
        if not question:
            raise HTTPException(422, "Question cannot be blank")
        session = None
        profile = None
        history = []
        if payload.session_id:
            session = (
                db.query(ChatSession)
                .filter_by(id=payload.session_id, user_id=user_id)
                .first()
            )
            binding = db.get(SessionWorkspace, payload.session_id)
            if (
                not session
                or not binding
                or binding.workspace_id != payload.workspace_id
            ):
                raise HTTPException(404, "Session not found in this Workspace")
            profile = db.get(SessionProfile, session.id)
            if payload.session_type is not None and (not profile or payload.session_type != profile.session_type):
                raise HTTPException(409, "The type of an existing conversation cannot be changed. Start a new chat instead.")
            # Each completed turn has distinct timestamps, including on PostgreSQL.
            recent = (
                db.query(Message)
                .filter_by(session_id=session.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(12)
                .all()
            )
            budget = 12000
            for message in recent:
                if len(message.content) > budget:
                    break
                history.insert(0, {"role": message.role, "content": message.content})
                budget -= len(message.content)
        is_learning = (profile.session_type if profile else payload.session_type) == "learning"
        # Read original user messages for language continuity, including after refresh.
        # This is separate from retrieval/routing history and never writes old records.
        language_history = [{"role": "user", "content": row.content} for row in
            db.query(Message).filter_by(session_id=session.id, role="user").order_by(Message.created_at, Message.id).all()] if session else []
        language_policy = resolve(question, language_history, payload.ui_locale)
        learning_data = None
        learning_update = {}
        if is_learning:
            ctx = db.get(LearningContext, session.id) if session else None
            if ctx and ctx.status == "finished":
                raise HTTPException(409, "This session is finished. Resume Learning before sending; no answer was saved.")
            learning_data = context_data(ctx, profile) if ctx else {"goal": profile.learning_goal if profile else question, "focus": "", "notes": "", "evidence": []}
            # Only JSON-safe fields are included in the model context.
            learning_data = {k:v for k,v in learning_data.items() if k in {"goal","focus","notes","evidence","next_step"}}
            if not session:
                learning_data["initialize_goal"] = True
        if learning_data and learning_data.get("evidence"):
            learning_data["evidence"] = learning_data["evidence"][-20:]
        previous = {}
        anchor = None
        if session:
            last_assistant = next((m for m in recent if m.role == "assistant"), None)
            evidence = db.get(MessageEvidence, last_assistant.id) if last_assistant else None
            previous = previous_decision(evidence.ai_mode if evidence else "")
            anchor = db.get(Message, previous["anchor"]) if previous.get("anchor") else None
            if anchor and anchor.session_id != session.id:
                anchor = None
            # Old sessions are read as-is; infer intent from the last actual user turn.
            if not previous.get("action"):
                last_user = next((m for m in recent if m.role == "user"), None)
                if last_user and explicit_evidence(last_user.content):
                    previous = {"action": "retrieve"}
                    anchor = last_user
        if anchor and not any(m["role"] == "user" and m["content"] == anchor.content for m in history):
            history.insert(0, {"role": "user", "content": anchor.content})
        started = time.perf_counter()
        try:
            # Learning selects the teaching task before deciding whether evidence is needed.
            # Questioning retains its existing evidence-first routing policy.
            decision = ({"action": "direct", "reason": "learning_task", "origin": "model"} if is_learning
                        else decide(db, user_id, question, history, previous, payload.answer_mode))
        except HTTPException as exc:
            logging.getLogger(__name__).warning("Smart routing failed: request=%s status=%s category=%s", payload.request_id, exc.status_code,
                "format" if "format" in str(exc.detail) else "provider")
            raise
        route_ms = round((time.perf_counter() - started) * 1000, 2)
        search_question = question
        inherited = decision["reason"] == "context_followup" and anchor is not None
        if inherited:
            search_question = anchor.content[:1000] + " " + question
        started = time.perf_counter()
        sources = []
        knowledge_context = None
        if decision["action"] == "retrieve":
            try:
                sources = retrieve(db, payload.workspace_id, search_question,
                    **({"document_ids": previous["documents"]} if is_learning and previous.get("documents") else {}))
            except HTTPException as exc:
                from app.services.runtime_metrics import record_error
                record_error("retrieval_failure")
                logging.getLogger(__name__).warning("Smart retrieval failed: request=%s status=%s", payload.request_id, exc.status_code)
                if exc.status_code == 409 and is_learning:
                    logging.getLogger(__name__).warning("Learning search: index_not_ready request=%s", payload.request_id)
                elif exc.status_code in {429, 409}:
                    raise
                else:
                    raise HTTPException(503, "Material search failed or timed out. Check the document index and retry; no answer was saved.")
        if is_learning:
            from app.services.learning_knowledge import acquire
            sources, knowledge_context = acquire(
                db, user_id, payload.workspace_id, question, history, learning_data,
                previous, sources, retrieve, evidence_deadline, payload.answer_mode)
            decision = dict(decision, action="retrieve" if knowledge_context.get("use_documents") else "direct",
                            reason="learning_" + knowledge_context["task"])
        retrieval_ms = round((time.perf_counter() - started) * 1000, 2)
        logging.getLogger(__name__).warning(
            "Smart decision: request=%s version=%s route=%s reason=%s origin=%s candidates=%s route_ms=%s retrieval_ms=%s",
            payload.request_id, VERSION, decision["action"], decision["reason"], decision["origin"], len(sources), route_ms, retrieval_ms)
        # End the read transaction before waiting on the external provider.
        db.rollback()
        generation_started = time.perf_counter()
        try:
            answer, selected, basis = grounded_answer(
                db,
                user_id,
                question,
                history,
                sources,
                payload.answer_mode,
                payload.learning_mode,
                evidence_requested=decision["action"] == "retrieve",
                language_policy=language_policy,
                **({"knowledge_context": knowledge_context} if is_learning else {}),
                **({"evidence_deadline": evidence_deadline, "citation_diagnostics": citation_diagnostics} if settings.CITATION_AUDIT_ENABLED or is_learning else {}),
                **({"learning_context": learning_data, "learning_update": learning_update} if is_learning else {}),
            )
        except HTTPException as exc:
            logging.getLogger(__name__).warning("Chat generation failed: request=%s status=%s category=%s", payload.request_id, exc.status_code, getattr(exc, "generation_category", "timeout" if exc.status_code == 504 else "format" if "format" in str(exc.detail) else "provider"))
            raise

        logging.getLogger(__name__).warning(
            "Smart completed: request=%s version=%s route=%s reason=%s origin=%s candidates=%s basis=%s route_ms=%s retrieval_ms=%s generation_ms=%s",
            payload.request_id, VERSION, decision["action"], decision["reason"], decision["origin"], len(sources), basis,
            route_ms, retrieval_ms, round((time.perf_counter() - generation_started) * 1000, 2))
        if knowledge_context:
            if knowledge_context.get("retrieval_status") == "failed":
                answer += "\n\n" + ("本轮资料搜索暂不可用；讲解先使用通用知识和已读取的资料片段。" if language_policy["language"] == "zh-CN" else "Material search is temporarily unavailable; teaching uses general knowledge and any available excerpts.")
            pending = [d for d in knowledge_context["documents"] if d["id"] in knowledge_context.get("scope", []) and not d["searchable"]]
            if pending:
                labels = {"processing": "正在建立索引", "failed": "索引失败"}
                if language_policy["language"] == "zh-CN":
                    notice = "资料已存在但暂不可检索：" + "；".join(f'{d["name"]}（{labels.get(d["status"], "需要重新索引")}）' for d in pending)
                else:
                    notice = "Materials exist but are not searchable yet: " + "; ".join(f'{d["name"]} ({d["status"] if d["status"] != "ready" else "reindex required"})' for d in pending)
                answer += "\n\n" + notice
        if not session:
            session = ChatSession(
                id=str(uuid.uuid4()), user_id=user_id, title=question[:60]
            )
            db.add(session)
            db.flush()
            db.add(
                SessionWorkspace(
                    session_id=session.id, workspace_id=payload.workspace_id
                )
            )
            profile = SessionProfile(session_id=session.id, session_type=payload.session_type or "questioning",
                                     learning_goal=((learning_update.get("value") or {}).get("initial_goal") or question) if payload.session_type == "learning" else None)
            db.add(profile)
        # Revalidate sources in case a document was deleted during generation.
        current_ids = {
            row[0]
            for row in db.query(Document.id)
            .filter(
                Document.workspace_id == payload.workspace_id,
                Document.status == "ready",
            )
            .all()
        }
        if any(source["document_id"] not in current_ids for source in selected):
            raise HTTPException(
                409, "Source documents changed during the answer. Please retry."
            )
        now = datetime.now(timezone.utc)
        user_message = Message(
            id=str(uuid.uuid4()),
            session_id=session.id,
            role="user",
            content=question,
            created_at=now,
        )
        assistant = Message(
            id=str(uuid.uuid4()),
            session_id=session.id,
            role="assistant",
            content=answer,
            created_at=now + timedelta(microseconds=1),
        )
        db.add_all([user_message, assistant])
        db.flush()
        db.add(
            MessageEvidence(
                message_id=assistant.id,
                sources=selected,
                ai_mode=f"{mode()};basis={basis};answer={payload.answer_mode};learning={payload.learning_mode};policy={VERSION};route={decision['action']};reason={decision['reason']};origin={decision['origin']};citation_check={citation_diagnostics.get('outcome', 'not_required')};citation_binding={citation_diagnostics.get('binding_outcome', 'unchanged')};anchor={anchor.id if inherited else user_message.id};documents={','.join((knowledge_context or {}).get('scope', []) or previous.get('documents', []))};document_order={','.join((knowledge_context or {}).get('document_order', previous.get('document_order', [])))}",
            )
        )
        if is_learning:
            ctx = ensure_context(db, session.id)
            apply_observation(ctx, learning_update.get("value"), question, user_message.id)
            ctx.revision += 1
        session.updated_at = now
        response = ChatResponse(
            answer=answer,
            session_id=session.id,
            session_type=profile.session_type if profile else None,
            learning_goal=profile.learning_goal if profile else None,
            sources=selected,
            ai_mode=mode(),
            answer_basis=basis,
            answer_mode=payload.answer_mode,
            learning_mode=payload.learning_mode,
        ).model_dump()
        db.add(
            ChatTurn(
                request_id=str(payload.request_id),
                user_id=user_id,
                session_id=session.id,
                question=question,
                response=response,
            )
        )
        db.commit()
        return response
    finally:
        chat_slot.release()


@router.get("/chat/turns/{request_id}")
def completed_turn(
    request_id: uuid.UUID,
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    turn = db.get(ChatTurn, str(request_id))
    if not turn or turn.user_id != user_id:
        raise HTTPException(404, "Completed answer not found")
    return turn.response


@router.get("/chat/sessions")
def sessions(
    workspace_id: str | None = None,
    session_id: str | None = None,
    sort: Literal["recent", "oldest", "title"] = "recent",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    query = db.query(ChatSession).filter_by(user_id=user_id)
    if workspace_id:
        owned_workspace(db, workspace_id, user_id)
        query = query.join(
            SessionWorkspace, ChatSession.id == SessionWorkspace.session_id
        ).filter(SessionWorkspace.workspace_id == workspace_id)
    if session_id:
        query = query.filter(ChatSession.id == session_id)
    ordering = {"recent": ChatSession.updated_at.desc(),
                "oldest": ChatSession.updated_at.asc(), "title": ChatSession.title.asc()}
    rows = query.order_by(ordering[sort], ChatSession.id).offset(offset).limit(limit).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "session_type": profile.session_type if (profile := db.get(SessionProfile, row.id)) else None,
            "learning_goal": profile.learning_goal if profile else None,
            "learning_status": (context.status if (context := db.get(LearningContext, row.id)) else "active") if profile and profile.session_type == "learning" else None,
            "workspace_id": binding.workspace_id
            if (binding := db.get(SessionWorkspace, row.id))
            else None,
        }
        for row in rows
    ]


@router.get("/chat/sessions/{session_id}/messages")
def messages(
    session_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=200),
    user_id=Depends(get_current_user_id), db: Session = Depends(get_db)
):
    session = db.query(ChatSession).filter_by(id=session_id, user_id=user_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    # Page backwards from newest; each page is returned in reading order.
    rows = (
        db.query(Message)
        .filter_by(session_id=session_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .offset(offset).limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "created_at": row.created_at,
            "sources": evidence.sources
            if (evidence := db.get(MessageEvidence, row.id))
            else [],
            "ai_mode": evidence.ai_mode if evidence else "legacy",
            "answer_basis": next(
                (
                    part.split("=", 1)[1]
                    for part in evidence.ai_mode.split(";")
                    if part.startswith("basis=")
                ),
                "grounded",
            )
            if evidence
            else "legacy",
            "learning_mode": next(
                (
                    part.split("=", 1)[1]
                    for part in evidence.ai_mode.split(";")
                    if part.startswith("learning=")
                ),
                "direct",
            )
            if evidence
            else "direct",
        }
        for row in reversed(rows)
    ]


@router.delete("/chat/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, user_id=Depends(get_current_user_id), db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter_by(id=session_id, user_id=user_id).first()
    if not session:
        raise HTTPException(404, "Conversation not found")
    binding = db.get(SessionWorkspace, session_id)
    if binding:
        owned_workspace(db, binding.workspace_id, user_id)
    if not chat_slot.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current answer to finish")
    try:
        delete_sessions(db, [session_id])
        db.commit()
    finally:
        chat_slot.release()
