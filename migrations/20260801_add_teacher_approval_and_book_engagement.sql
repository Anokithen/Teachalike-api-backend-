-- Teacher approval and book engagement schema. Safe to run repeatedly on MySQL.
-- Teacher-only application fields remain nullable so legacy/admin-created
-- teachers can be backfilled without inventing personal information.

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

CREATE TABLE IF NOT EXISTS book_views (
    id INTEGER NOT NULL AUTO_INCREMENT,
    book_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    viewed_on DATE NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT uq_book_views_daily UNIQUE (book_id, account_id, viewed_on),
    CONSTRAINT fk_book_views_book FOREIGN KEY (book_id)
        REFERENCES books (id) ON DELETE CASCADE,
    CONSTRAINT fk_book_views_account FOREIGN KEY (account_id)
        REFERENCES parents (id) ON DELETE CASCADE,
    INDEX ix_book_views_book_id (book_id),
    INDEX ix_book_views_account_id (account_id),
    INDEX ix_book_views_viewed_on (viewed_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS book_likes (
    id INTEGER NOT NULL AUTO_INCREMENT,
    book_id INTEGER NOT NULL,
    child_id INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT uq_book_likes_child UNIQUE (book_id, child_id),
    CONSTRAINT fk_book_likes_book FOREIGN KEY (book_id)
        REFERENCES books (id) ON DELETE CASCADE,
    CONSTRAINT fk_book_likes_child FOREIGN KEY (child_id)
        REFERENCES children (id) ON DELETE CASCADE,
    INDEX ix_book_likes_book_id (book_id),
    INDEX ix_book_likes_child_id (child_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
