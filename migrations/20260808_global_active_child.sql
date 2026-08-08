SET @db = DATABASE();
SET @exists = (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@db AND table_name='children' AND column_name='child_access_version');
SET @sql = IF(@exists=0,'ALTER TABLE children ADD COLUMN child_access_version INT NOT NULL DEFAULT 1','SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
CREATE TABLE IF NOT EXISTS child_access_sessions (
 id INT NOT NULL AUTO_INCREMENT, parent_id INT NOT NULL, child_id INT NOT NULL,
 token_hash CHAR(64) NOT NULL, child_access_version INT NOT NULL,
 created_at DATETIME NOT NULL, last_used_at DATETIME NOT NULL, expires_at DATETIME NOT NULL,
 revoked_at DATETIME NULL, revoke_reason VARCHAR(80) NULL, PRIMARY KEY(id),
 UNIQUE KEY uq_child_access_token_hash(token_hash), KEY ix_child_access_parent(parent_id),
 KEY ix_child_access_child(child_id), KEY ix_child_access_expiry(expires_at), KEY ix_child_access_revoked(revoked_at),
 CONSTRAINT fk_child_access_parent FOREIGN KEY(parent_id) REFERENCES parents(id) ON DELETE CASCADE,
 CONSTRAINT fk_child_access_child FOREIGN KEY(child_id) REFERENCES children(id) ON DELETE CASCADE
) ENGINE=InnoDB;
