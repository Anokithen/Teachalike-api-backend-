from datetime import timedelta
from hashlib import sha256
import secrets
from flask import current_app, g, jsonify, request
from flask_jwt_extended import current_user
from werkzeug.security import check_password_hash
from app.extensions import db
from app.models.child_model import Child
from app.models.child_access_session_model import ChildAccessSession
from app.security import pin_attempts
from app.utils import utc_now

def _hash(value): return sha256(value.encode()).hexdigest()
def revoke_child_sessions(child_id, reason):
    ChildAccessSession.query.filter_by(child_id=child_id, revoked_at=None).update({"revoked_at": utc_now(), "revoke_reason": reason}, synchronize_session=False)

def activate_child(payload=None):
    data = payload if payload is not None else (request.get_json(silent=True) or {})
    try: child_id = int(data.get("child_id"))
    except (TypeError, ValueError): child_id = 0
    child = db.session.get(Child, child_id) if child_id > 0 else None
    if not child or child.parent_id != current_user.id: return jsonify({"error":"Child not found.","error_code":"CHILD_NOT_FOUND"}), 404
    if not child.child_pin_hash: return jsonify({"error":"This child does not have a profile PIN.","error_code":"CHILD_PIN_NOT_SET"}), 400
    pin = data.get("pin", data.get("password", ""))
    if not isinstance(pin, str) or not pin.isdigit() or len(pin) != 6: return jsonify({"error":"Enter the 6-digit profile PIN.","error_code":"CHILD_PIN_REQUIRED"}), 400
    key = f"child-pin:{current_user.id}:{child.id}:{request.remote_addr or 'unknown'}"
    limit, window = current_app.config["PIN_RATE_LIMIT_ATTEMPTS"], current_app.config["PIN_RATE_LIMIT_WINDOW_SECONDS"]
    blocked, retry = pin_attempts.blocked(key, limit, window)
    if blocked:
        response = jsonify({"error":"Too many incorrect attempts. Please try again later.","error_code":"CHILD_PIN_INCORRECT"}); response.status_code = 429; response.headers["Retry-After"] = str(retry); return response
    if not check_password_hash(child.child_pin_hash, pin):
        pin_attempts.record_failure(key, window)
        return jsonify({"error":"That child PIN was not correct. Please try again.","error_code":"CHILD_PIN_INCORRECT"}), 401
    pin_attempts.reset(key)
    now = utc_now(); expires = now + timedelta(minutes=current_app.config.get("CHILD_ACCESS_SESSION_MINUTES", 30)); raw = secrets.token_urlsafe(32)
    session = ChildAccessSession(parent_id=current_user.id, child_id=child.id, token_hash=_hash(raw), child_access_version=child.child_access_version or 1, created_at=now, last_used_at=now, expires_at=expires)
    db.session.add(session); db.session.commit()
    return jsonify({"message":"Child profile activated.","active_child":session.to_dict(),"child_session_token":raw,"expires_at":session.expiry()}), 200

def get_active_child():
    session = getattr(g, "child_access_session", None)
    return jsonify({"active_child": session.to_dict() if session else None, "status": "active" if session else "locked", "expires_at": session.expiry() if session else None}), 200
def lock_child():
    session = getattr(g, "child_access_session", None)
    if session: session.revoked_at = utc_now(); session.revoke_reason = "locked"; db.session.commit()
    return jsonify({"message":"Child mode locked."}), 200
