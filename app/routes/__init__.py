from app.routes.auth_routes import auth_bp
from app.routes.parent_routes import parent_bp
from app.routes.child_routes import child_bp
from app.routes.voice_profile_routes import voice_profile_bp
from app.routes.book_routes import book_bp
from app.routes.reading_session_routes import reading_session_bp
from app.routes.mini_game_routes import mini_game_bp
from app.routes.leaderboard_routes import leaderboard_bp
from app.routes.sync_routes import sync_bp
from app.routes.admin_routes import admin_bp
from app.routes.book_narration_routes import book_narration_bp
from app.routes.ai_routes import ai_bp
from app.routes.asset_routes import asset_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(parent_bp)
    app.register_blueprint(child_bp)
    app.register_blueprint(voice_profile_bp)
    app.register_blueprint(book_bp)
    app.register_blueprint(reading_session_bp)
    app.register_blueprint(mini_game_bp)
    app.register_blueprint(leaderboard_bp)
    app.register_blueprint(sync_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(book_narration_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(asset_bp)
