import re


def normalize_phone(phone: str) -> str:
    """
    Normaliza un número de teléfono al formato E.164 (+XXXXXXXXXXX).

    Reglas:
    - Elimina todos los caracteres no numéricos excepto el '+' inicial.
    - Si empieza por '+', usa los dígitos que siguen como número completo.
    - Si tiene 9 dígitos y empieza por 6, 7, 8 o 9: asume España (prefijo 34).
    - Si ya tiene prefijo de país (10-15 dígitos sin '+'): usa tal cual.
    - Valida que el resultado tenga entre 7 y 15 dígitos tras el '+'.
    - Lanza ValueError si no puede normalizar.
    """
    if not phone or not phone.strip():
        raise ValueError("El número de teléfono no puede estar vacío.")

    raw = phone.strip()

    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)

    if not digits:
        raise ValueError(f"No se encontraron dígitos en '{phone}'.")

    if has_plus:
        normalized_digits = digits
    elif len(digits) == 9 and digits[0] in "6789":
        normalized_digits = "34" + digits
    elif 10 <= len(digits) <= 15:
        normalized_digits = digits
    else:
        raise ValueError(
            f"No se puede determinar el prefijo de país para '{phone}'. "
            "Proporcione el número en formato E.164 (ej. +34612345678)."
        )

    if not (7 <= len(normalized_digits) <= 15):
        raise ValueError(
            f"El número normalizado tiene {len(normalized_digits)} dígitos; "
            "debe tener entre 7 y 15."
        )

    return "+" + normalized_digits


def normalize_phone_safe(phone: str) -> str | None:
    """
    Como normalize_phone pero devuelve None en lugar de lanzar excepción.
    Útil para migraciones de datos donde se quieren omitir registros inválidos.
    """
    try:
        return normalize_phone(phone)
    except (ValueError, AttributeError):
        return None
