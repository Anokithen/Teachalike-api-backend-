-- Rename the former teacher_profiles storage to the explicit
-- teacher_applications entity without losing submitted or reviewed data.
-- Safe to run repeatedly on MySQL.

SET @rename_teacher_profiles = IF(
    EXISTS(
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles'
    ) AND NOT EXISTS(
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'teacher_applications'
    ),
    'RENAME TABLE teacher_profiles TO teacher_applications',
    'SELECT 1'
);
PREPARE rename_teacher_profiles_stmt FROM @rename_teacher_profiles;
EXECUTE rename_teacher_profiles_stmt;
DEALLOCATE PREPARE rename_teacher_profiles_stmt;

CREATE TABLE IF NOT EXISTS teacher_applications (
    id INTEGER NOT NULL AUTO_INCREMENT,
    account_id INTEGER NOT NULL,
    phone_number VARCHAR(40) NULL,
    address VARCHAR(500) NULL,
    teacher_type VARCHAR(30) NULL,
    school_name VARCHAR(200) NULL,
    tuition_name VARCHAR(200) NULL,
    approval_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    reviewed_by_id INTEGER NULL,
    reviewed_at DATETIME NULL,
    rejection_reason VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT uq_teacher_applications_account_id UNIQUE (account_id),
    CONSTRAINT fk_teacher_applications_account FOREIGN KEY (account_id)
        REFERENCES parents (id) ON DELETE CASCADE,
    CONSTRAINT fk_teacher_applications_reviewer FOREIGN KEY (reviewed_by_id)
        REFERENCES parents (id) ON DELETE SET NULL,
    INDEX ix_teacher_applications_approval_status (approval_status),
    INDEX ix_teacher_applications_reviewed_by_id (reviewed_by_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO teacher_applications (account_id, approval_status, created_at, updated_at)
SELECT p.id, 'approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM parents p
LEFT JOIN teacher_applications ta ON ta.account_id = p.id
WHERE p.role = 'teacher' AND ta.id IS NULL;
