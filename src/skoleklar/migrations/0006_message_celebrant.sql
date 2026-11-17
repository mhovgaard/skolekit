-- 0006 message_celebrant (message-events-v4, restricted-messages-v5).
-- A birthday-party fact from a message may keep one name: the first name of the
-- birthday child the family's child is invited to, so the board can say
-- 'Emmas fødselsdag'. Null for every other fact. See docs/message-harvesting.md.

ALTER TABLE message_event_facts ADD COLUMN celebrant TEXT;
