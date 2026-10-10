"""Explicit cleanup for existing databases whose foreign keys lack cascades."""
from app.models.chat_sessions import ChatSession
from app.models.messages import Message
from app.models.knowledge import MessageEvidence, SessionWorkspace
from app.models.chat_turn import ChatTurn
from app.models.session_profile import SessionProfile
from app.models.learning import LearningContext, LearningRecap, RecapDraft


def delete_sessions(db, session_ids):
    if not session_ids:
        return
    messages = db.query(Message.id).filter(Message.session_id.in_(session_ids))
    db.query(MessageEvidence).filter(MessageEvidence.message_id.in_(messages)).delete(synchronize_session=False)
    for model in (ChatTurn, RecapDraft, LearningRecap, LearningContext, SessionProfile,
                  Message, SessionWorkspace):
        db.query(model).filter(model.session_id.in_(session_ids)).delete(synchronize_session=False)
    db.query(ChatSession).filter(ChatSession.id.in_(session_ids)).delete(synchronize_session=False)
