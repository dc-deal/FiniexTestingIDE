"""
FiniexTestingIDE - Root Exception Base
Shared base class for all project exceptions.
"""


class FiniexError(Exception):
    """
    Root base for every FiniexTestingIDE exception.

    Provides a project-wide catch-all (`except FiniexError`) and a single place
    to later attach structured context (error codes, report hooks). Concrete and
    raisable on purpose — it is not an abstract class.
    """
    pass
