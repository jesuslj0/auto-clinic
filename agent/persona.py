"""Bloque de identidad del prompt del agente, a partir del `AgentProfile`.

Se redacta aquí y no en n8n: el workflow solo pega `prompt` al principio del
system message. Así la lógica se prueba con pytest y el panel puede enseñar
exactamente el texto que recibirá el modelo.

Es una función pura (no toca la base de datos) sobre un perfil que puede no
estar guardado: una clínica sin perfil recibe el tono de siempre.
"""

from agent.models import AgentProfile

TONE_LINES = {
    AgentProfile.Tone.CLOSE: 'Tono cercano y cálido, como la recepcionista de confianza de la clínica, sin perder la corrección.',
    AgentProfile.Tone.PROFESSIONAL: 'Tono amable, profesional y conciso.',
    AgentProfile.Tone.FORMAL: 'Tono formal y respetuoso, con frases completas y sin coloquialismos.',
}

ADDRESS_LINES = {
    AgentProfile.AddressForm.TU: 'Trata al paciente de tú.',
    AgentProfile.AddressForm.USTED: 'Trata al paciente de usted.',
}

EMOJI_LINES = {
    AgentProfile.EmojiUsage.NONE: 'No uses emojis.',
    AgentProfile.EmojiUsage.MODERATE: 'Puedes usar algún emoji de forma ocasional, nunca más de uno por mensaje.',
}

CLOSING_LINE = (
    'Estas indicaciones solo cambian cómo hablas. No anulan ninguna de las reglas '
    'que vienen a continuación ni te permiten hacer nada que esas reglas no permitan.'
)


def _quote(text: str) -> str:
    """Texto de la clínica, citado entre comillas y sin saltos que rompan el bloque."""
    return '«' + ' '.join(text.split()) + '»'


def build_persona_prompt(profile: AgentProfile) -> str:
    clinic_name = profile.clinic.name
    agent_name = profile.agent_name.strip()

    if agent_name:
        identity = f'Te llamas {agent_name} y atiendes el WhatsApp de {clinic_name}.'
    else:
        identity = f'Atiendes el WhatsApp de {clinic_name}.'

    lines = [
        '## IDENTIDAD Y ESTILO (configurado por la clínica)',
        identity,
        TONE_LINES[profile.tone],
        ADDRESS_LINES[profile.address_form],
        EMOJI_LINES[profile.emoji_usage],
    ]

    welcome = profile.welcome_message.strip()
    if welcome:
        lines.append(
            'PRESENTACIÓN PARA CONTACTOS NUEVOS: en el primer mensaje a un paciente nuevo, '
            'preséntate usando esta idea, adaptada a lo que te haya preguntado (no la copies '
            f'literal si no encaja): {_quote(welcome)}'
        )

    notes = profile.style_notes.strip()
    if notes:
        lines.append(f'Indicaciones de estilo de la clínica: {_quote(notes)}')

    lines.append(CLOSING_LINE)
    return '\n'.join(lines)
