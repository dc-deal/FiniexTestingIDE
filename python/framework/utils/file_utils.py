import re


def sanitize_filename(name: str) -> str:
    """
    Sanitize a string to make it safe for use as a filename.
    Replaces all invalid characters with underscores.
    """
    # Windows verbotene Zeichen:  \ / : * ? " < > |
    # Zusätzlich auch Steuerzeichen entfernen
    name = re.sub(r'[\\/:*?"<>|\x00-\x1F]', '_', name)

    # Optional: whitespace am Anfang/Ende entfernen
    name = name.strip()

    # Optional: Doppelte Unterstriche reduzieren
    name = re.sub(r'_+', '_', name)

    # Falls alles wegfällt → fallback
    if not name:
        name = "_"

    return name


def pad_int(value: int, width: int = 2) -> str:
    """
    Pad an integer with leading zeros up to the given width.
    Returns a string.
    """
    return str(value).zfill(width)


def file_name_for_scenario(scenario_index: int, name: str):
    return f"{pad_int(scenario_index+1)}_{sanitize_filename(name)}"
