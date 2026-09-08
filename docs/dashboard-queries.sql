-- Read-only operational review queries. Unknown/pending outcomes remain separate.
SELECT json_extract(event_json,'$.repository') repository,
       json_extract(event_json,'$.conclusion') conclusion, COUNT(*) observations
FROM events GROUP BY repository,conclusion;
SELECT state,COUNT(*) actions FROM actions GROUP BY state;
SELECT incident,COUNT(*) reserved_budget FROM actions GROUP BY incident;
SELECT kind,COUNT(*) audit_entries FROM audit GROUP BY kind;
