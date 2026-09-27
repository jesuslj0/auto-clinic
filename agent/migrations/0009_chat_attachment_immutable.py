"""Adjuntos de chat irremplazables también por debajo del ORM.

`ChatAttachment.save()` rechaza modificar una fila existente, pero eso no para un
`UPDATE` en SQL crudo ni un `queryset.update()`. Este trigger sí: la fila de un
adjunto no admite ningún `UPDATE`. El `DELETE` se permite solo porque lo exige
el borrado en cascada de una conversación; el objeto del bucket se conserva.

Que no haya dos adjuntos para un mismo mensaje lo garantiza la restricción
única del `OneToOneField` (migración 0008).
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION chat_attachment_no_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'agent: UPDATE no permitido sobre chat_attachments (adjunto irremplazable, id=%)', OLD.id
        USING ERRCODE = 'raise_exception';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chat_attachment_no_update ON chat_attachments;
CREATE TRIGGER chat_attachment_no_update
    BEFORE UPDATE ON chat_attachments
    FOR EACH ROW EXECUTE FUNCTION chat_attachment_no_update();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS chat_attachment_no_update ON chat_attachments;
DROP FUNCTION IF EXISTS chat_attachment_no_update();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('agent', '0008_chat_attachment'),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
