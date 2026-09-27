import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados, para búsquedas."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return " ".join(sin_tildes.lower().split())
