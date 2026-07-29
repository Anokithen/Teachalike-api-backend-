from flask import current_app, jsonify

from app.services.groq_service import GroqError, list_models


def list_available_models():
    """Return active Groq chat models without exposing the server API key."""
    try:
        models = list_models(current_app.config)
        configured_default = str(current_app.config.get("GROQ_MODEL") or "").strip()
        model_ids = {model["id"] for model in models}
        default_model = configured_default if configured_default in model_ids else (
            models[0]["id"] if models else None
        )
        return jsonify({"provider": "groq", "models": models, "default_model": default_model}), 200
    except GroqError as err:
        return jsonify({"error": str(err)}), 503
