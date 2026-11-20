-- Core Memory engine schema, version 2: an index for recall.
--
-- Recall finds the chunks whose text holds some words, with PostgreSQL's
-- own full-text search. It is a query over chunk_text, the one copy of
-- source text, and keeps nothing else: no second store, nothing to keep
-- in step with the first, and an erase still removes a message's text in
-- one transaction.
--
-- The index holds the words of each chunk's text as the English
-- configuration reduces them, which is what recall's query asks for. The
-- configuration is part of the index: changing it is a new migration.
--
-- Only the first 100,000 characters of a chunk are indexed. A tsvector
-- holds at most 1 MB, and computing one for a longer text would fail the
-- admission that stores it. The default chunkers cut text far shorter.
--
-- ANALYZE samples an index's expressions as it samples columns, and for a
-- tsvector it keeps the most common words. They would be words of what a
-- source said, in pg_statistic, where an erase cannot reach them, so the
-- expression is left out of the planner's statistics as the text is.
--
-- An erase deletes a chunk's text, and VACUUM then removes it from this
-- index. Words may stay in the index's own pages as keys until
-- REINDEX INDEX core_memory.chunk_text_search_idx rebuilds it.

CREATE INDEX chunk_text_search_idx ON core_memory.chunk_text
    USING gin (pg_catalog.to_tsvector('pg_catalog.english'::pg_catalog.regconfig,
                                      pg_catalog."left"(text, 100000)));

ALTER INDEX core_memory.chunk_text_search_idx ALTER COLUMN 1 SET STATISTICS 0;
