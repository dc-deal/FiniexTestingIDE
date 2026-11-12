"""
FiniexTestingIDE - Drawing Style Types (#379)

What a drawing on a run's chart MEANS, never which colour it has. The viewer's palette is computed:
every role colour is checked against both themes and against red-green colour blindness, and a
literal colour from an algo would escape that check. So a drawing names a role, an index that only
tells several lines apart, and an emphasis, and the viewer's theme picks the colour and the width.
"""

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# Every field is served, a default included, so the serialization schema marks each one required


class DrawingRole(StrEnum):
    """A fixed meaning the viewer's theme maps to a colour."""
    NEGATIVE = 'negative'
    POSITIVE = 'positive'
    WARNING = 'warning'
    ANNOTATION = 'annotation'
    NEUTRAL = 'neutral'


class DrawingEmphasis(StrEnum):
    """How strongly a drawing stands out — in place of a line width or an opacity."""
    STRONG = 'strong'
    NORMAL = 'normal'
    QUIET = 'quiet'


class DrawingStyle(BaseModel):
    """
    The style of one drawing, as a role rather than a colour.

    Args:
        role: What the drawing means
        series: Tells several drawings of one role apart (three moving averages) — 0, 1, 2 …; no
            meaning beyond that
        emphasis: How strongly it stands out; None leaves it to the viewer
        color: An explicit colour, documented as leaving the theme and the colour-blindness check —
            the responsibility is visible where it is used
    """
    model_config = ConfigDict(frozen=True, extra='forbid',
                              json_schema_serialization_defaults_required=True)

    role: DrawingRole = DrawingRole.NEUTRAL
    series: Optional[int] = Field(default=None, ge=0)
    emphasis: Optional[DrawingEmphasis] = None
    color: Optional[str] = None
