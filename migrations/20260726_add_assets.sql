-- Cloudinary metadata ledger. Apply to the existing MySQL database before
-- deploying asset endpoints. Cloudinary folders are created by first upload.
-- Existing installations created this cache constraint. Removing it permits
-- distinct generation IDs for multiple narration versions.
SET @drop_narration_unique = IF(
    EXISTS(
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'book_narrations'
          AND index_name = 'uq_book_voice_narration'
    ),
    'ALTER TABLE book_narrations DROP INDEX uq_book_voice_narration',
    'SELECT 1'
);
PREPARE drop_narration_unique_stmt FROM @drop_narration_unique;
EXECUTE drop_narration_unique_stmt;
DEALLOCATE PREPARE drop_narration_unique_stmt;

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER NOT NULL AUTO_INCREMENT,
    owner_user_id INTEGER NOT NULL,
    child_id INTEGER NULL,
    book_id INTEGER NULL,
    admin_id INTEGER NULL,
    voice_profile_id INTEGER NULL,
    generation_id INTEGER NULL,
    asset_category VARCHAR(40) NOT NULL,
    active_slot VARCHAR(255) NULL,
    cloudinary_asset_id VARCHAR(255) NOT NULL,
    cloudinary_public_id VARCHAR(500) NOT NULL,
    cloudinary_secure_url VARCHAR(1000) NOT NULL,
    cloudinary_resource_type VARCHAR(20) NOT NULL,
    cloudinary_delivery_type VARCHAR(30) NOT NULL DEFAULT 'upload',
    cloudinary_format VARCHAR(30) NULL,
    cloudinary_asset_folder VARCHAR(500) NOT NULL,
    original_filename VARCHAR(255) NULL,
    file_size_bytes BIGINT NULL,
    width INTEGER NULL,
    height INTEGER NULL,
    duration_seconds FLOAT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    deleted_at DATETIME NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_assets_owner FOREIGN KEY (owner_user_id) REFERENCES parents (id) ON DELETE CASCADE,
    CONSTRAINT fk_assets_child FOREIGN KEY (child_id) REFERENCES children (id) ON DELETE SET NULL,
    CONSTRAINT fk_assets_book FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE SET NULL,
    CONSTRAINT fk_assets_admin FOREIGN KEY (admin_id) REFERENCES parents (id) ON DELETE SET NULL,
    CONSTRAINT fk_assets_voice_profile FOREIGN KEY (voice_profile_id) REFERENCES voice_profiles (id) ON DELETE SET NULL,
    CONSTRAINT fk_assets_generation FOREIGN KEY (generation_id) REFERENCES book_narrations (id) ON DELETE SET NULL,
    CONSTRAINT uq_assets_active_slot UNIQUE (active_slot),
    INDEX ix_assets_owner_user_id (owner_user_id),
    INDEX ix_assets_book_id (book_id),
    INDEX ix_assets_voice_profile_id (voice_profile_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
