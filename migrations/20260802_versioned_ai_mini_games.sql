-- Feature 6: versioned, server-graded automatic mini-games.
-- Safe to run repeatedly on MySQL 8. Existing rows become fallback version 1.

DROP PROCEDURE IF EXISTS teachalike_add_column;
DELIMITER $$
CREATE PROCEDURE teachalike_add_column(
    IN table_name_value VARCHAR(64),
    IN column_name_value VARCHAR(64),
    IN definition_value VARCHAR(512)
)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = table_name_value
          AND column_name = column_name_value
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `', table_name_value, '` ADD COLUMN `', column_name_value, '` ', definition_value);
        PREPARE statement_value FROM @ddl;
        EXECUTE statement_value;
        DEALLOCATE PREPARE statement_value;
    END IF;
END$$
DELIMITER ;

CALL teachalike_add_column('mini_games', 'generation_status', 'VARCHAR(20) NULL');
CALL teachalike_add_column('mini_games', 'generator_provider', 'VARCHAR(40) NULL');
CALL teachalike_add_column('mini_games', 'generator_model', 'VARCHAR(200) NULL');
CALL teachalike_add_column('mini_games', 'generator_version', 'VARCHAR(50) NULL');
CALL teachalike_add_column('mini_games', 'source_content_hash', 'VARCHAR(64) NULL');
CALL teachalike_add_column('mini_games', 'generated_at', 'DATETIME NULL');
CALL teachalike_add_column('mini_games', 'generation_error', 'VARCHAR(500) NULL');
CALL teachalike_add_column('mini_games', 'content_version', 'INT NULL');

CALL teachalike_add_column('game_results', 'correct_answers', 'INT NULL');
CALL teachalike_add_column('game_results', 'total_questions', 'INT NULL');
CALL teachalike_add_column('game_results', 'answers_data', 'JSON NULL');
CALL teachalike_add_column('game_results', 'game_content_version', 'INT NULL');
CALL teachalike_add_column('game_results', 'points_awarded', 'INT NULL');

UPDATE mini_games
SET generation_status = COALESCE(generation_status, 'fallback'),
    generator_provider = COALESCE(generator_provider, 'legacy'),
    generator_version = COALESCE(generator_version, 'legacy-v1'),
    content_version = COALESCE(content_version, 1);
UPDATE game_results SET points_awarded = COALESCE(points_awarded, score);

-- Preserve unexpected legacy duplicates while making their versions unique.
-- The oldest row keeps its original version; later duplicates retain their
-- IDs/results and receive a stable negative historical version.
UPDATE mini_games AS duplicate_game
JOIN (
    SELECT * FROM (
        SELECT book_id, game_type, content_version, MIN(id) AS keeper_id
        FROM mini_games
        GROUP BY book_id, game_type, content_version
        HAVING COUNT(*) > 1
    ) AS duplicate_groups_materialized
) AS duplicate_group
  ON duplicate_game.book_id = duplicate_group.book_id
 AND duplicate_game.game_type = duplicate_group.game_type
 AND duplicate_game.content_version = duplicate_group.content_version
SET duplicate_game.content_version = -duplicate_game.id
WHERE duplicate_game.id <> duplicate_group.keeper_id;

DROP PROCEDURE IF EXISTS teachalike_add_index;
DELIMITER $$
CREATE PROCEDURE teachalike_add_index(
    IN table_name_value VARCHAR(64),
    IN index_name_value VARCHAR(64),
    IN columns_value VARCHAR(255),
    IN unique_value BOOLEAN
)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = table_name_value
          AND index_name = index_name_value
    ) THEN
        SET @ddl = CONCAT(
            'CREATE ', IF(unique_value, 'UNIQUE ', ''), 'INDEX `', index_name_value,
            '` ON `', table_name_value, '` (', columns_value, ')'
        );
        PREPARE statement_value FROM @ddl;
        EXECUTE statement_value;
        DEALLOCATE PREPARE statement_value;
    END IF;
END$$
DELIMITER ;

CALL teachalike_add_index('mini_games', 'ix_mini_games_book_id', '`book_id`', FALSE);
CALL teachalike_add_index('mini_games', 'ix_mini_games_generation_status', '`generation_status`', FALSE);
CALL teachalike_add_index('mini_games', 'ix_mini_games_source_content_hash', '`source_content_hash`', FALSE);
CALL teachalike_add_index('mini_games', 'uq_mini_games_book_type_version', '`book_id`, `game_type`, `content_version`', TRUE);
CALL teachalike_add_index('game_results', 'ix_game_results_child_id', '`child_id`', FALSE);
CALL teachalike_add_index('game_results', 'ix_game_results_game_id', '`game_id`', FALSE);

DROP PROCEDURE IF EXISTS teachalike_add_index;
DROP PROCEDURE IF EXISTS teachalike_add_column;
