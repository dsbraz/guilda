"""Conversation on a channel (ADR 0006, slice 11).

- pxos.channel_identities binds an identity on a channel (a user id there) to one actor;
  the owner writes it at bootstrap and the application only reads it;
- pxos.messages keeps the messages of a thread, each with the cycle of work it belongs
  to, an opaque reference. The author is the session actor. An agent writes its own
  posts, with no sender; any other message must be written by the internal person bound
  to its sender identity, acting for themselves (PX019). External actors are refused by
  the session (PX003). The channel's message id makes a redelivery a no-op;
- each accepted message from a person leaves a ``message.received`` event with
  identifiers only. The application may only insert and read messages, under RLS.
"""

from alembic import op

revision = "0011"
down_revision = "0010"

STATEMENTS = [
    """
    CREATE TABLE pxos.channel_identities (
        channel_id text PRIMARY KEY CHECK (channel_id <> ''),
        actor_id text NOT NULL REFERENCES pxos.actors
    )
    """,
    "GRANT SELECT ON pxos.channel_identities TO pxos_app",
    """
    CREATE TABLE pxos.messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        thread text NOT NULL CHECK (thread <> ''),
        channel_message text NOT NULL CHECK (channel_message <> ''),
        cycle text NOT NULL CHECK (cycle <> ''),
        sender text,
        author text NOT NULL REFERENCES pxos.actors,
        text text NOT NULL,
        received_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (thread, channel_message)
    )
    """,
    """
    CREATE FUNCTION pxos.message_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pxos, pg_temp AS $$
    DECLARE
        author_kind text;
    BEGIN
        NEW.author := current_actor();
        NEW.received_at := now();
        SELECT kind INTO STRICT author_kind FROM actors WHERE id = NEW.author;
        IF current_principal() <> NEW.author
           OR (author_kind = 'agent' AND NEW.sender IS NOT NULL)
           OR (author_kind <> 'agent' AND NOT EXISTS (
                SELECT FROM channel_identities
                WHERE channel_id = NEW.sender AND actor_id = NEW.author
           )) THEN
            RAISE EXCEPTION '% cannot write a message from %', NEW.author, NEW.sender
                USING ERRCODE = 'PX019';
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER message_guard BEFORE INSERT ON pxos.messages
    FOR EACH ROW EXECUTE FUNCTION pxos.message_guard()
    """,
    """
    CREATE FUNCTION pxos.message_record() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pxos, pg_temp AS $$
    BEGIN
        IF NEW.sender IS NOT NULL THEN
            INSERT INTO outbox (event_type, entity_id, payload) VALUES (
                'message.received', NEW.id,
                jsonb_build_object('message_id', NEW.id, 'cycle', NEW.cycle)
            );
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER message_record AFTER INSERT ON pxos.messages
    FOR EACH ROW EXECUTE FUNCTION pxos.message_record()
    """,
    "REVOKE ALL ON FUNCTION pxos.message_guard(), pxos.message_record() FROM PUBLIC",
    "GRANT SELECT, INSERT (thread, channel_message, cycle, sender, text) ON pxos.messages"
    " TO pxos_app",
    "ALTER TABLE pxos.messages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE pxos.messages FORCE ROW LEVEL SECURITY",
    # Any session reads the conversation; without an actor, or as an external, none.
    """
    CREATE POLICY messages_read ON pxos.messages FOR SELECT USING (
        pxos.current_actor() IS NOT NULL
    )
    """,
    "CREATE POLICY messages_insert ON pxos.messages FOR INSERT WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
