"""Read-only projection; never infer a lifecycle from message modes."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.knowledge import Workspace, Document, SessionWorkspace
from app.models.chat_sessions import ChatSession
from app.models.session_profile import SessionProfile
from app.models.learning import LearningContext

router = APIRouter()

@router.get("/dashboard")
def dashboard(user_id=Depends(get_current_user_id), db: Session = Depends(get_db)):
    spaces = db.query(Workspace).filter(Workspace.user_id == user_id).order_by(Workspace.created_at.desc(), Workspace.id).all()
    documents = dict(db.query(Document.workspace_id, func.count(Document.id)).join(Workspace, Workspace.id == Document.workspace_id).filter(Workspace.user_id == user_id).group_by(Document.workspace_id).all())
    chats = db.query(ChatSession, SessionWorkspace.workspace_id).join(SessionWorkspace, SessionWorkspace.session_id == ChatSession.id).join(Workspace, Workspace.id == SessionWorkspace.workspace_id).filter(ChatSession.user_id == user_id, Workspace.user_id == user_id)
    counts = dict(chats.with_entities(SessionWorkspace.workspace_id, func.count(ChatSession.id)).group_by(SessionWorkspace.workspace_id).all())
    recent = chats.order_by(ChatSession.updated_at.desc(), ChatSession.id).limit(6).all()
    names = {space.id: space.name for space in spaces}
    active = chats.join(SessionProfile, SessionProfile.session_id == ChatSession.id).outerjoin(LearningContext, LearningContext.session_id == ChatSession.id).filter(SessionProfile.session_type == "learning", or_(LearningContext.status == "active", LearningContext.session_id.is_(None))).order_by(ChatSession.updated_at.desc(), ChatSession.id).first()
    def entry(c,w):
        value=dict(id=c.id,title=c.title,workspace_id=w,workspace_name=names[w],updated_at=c.updated_at)
        profile=db.get(SessionProfile,c.id); context=db.get(LearningContext,c.id)
        if profile and profile.session_type=="learning": value.update(session_type="learning",learning_status=context.status if context else "active",focus=context.focus if context else profile.learning_goal)
        return value
    return {
        **({"continue_learning": entry(*active)} if active else {}),
        "workspaces": [{"id": s.id, "name": s.name, "material_count": documents.get(s.id, 0), "conversation_count": counts.get(s.id, 0)} for s in spaces],
        "conversations": [entry(c,w) for c,w in recent],
    }
