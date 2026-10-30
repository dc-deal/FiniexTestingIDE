"""
FiniexTestingIDE - Order Feature Validation (#625)

A decision logic declares the order features it uses, and a run refuses what its venue's API does
not hold. The same answer and the same sentence are needed in three places — the batch's main
process, the scenario's own start and the session's — so they are written once here. Each returns
what is short or a message; raising stays with the caller, as in the hook validators.
"""

from typing import Collection, Iterable, List, Optional

from python.framework.types.trading_env_types.order_types import OrderCapabilities, OrderFeature


def features_the_venue_lacks(
    required_features: Iterable[OrderFeature],
    capabilities: OrderCapabilities,
) -> List[OrderFeature]:
    """
    Which of a logic's declared features the venue's API does not hold.

    Args:
        required_features: What the logic declared
        capabilities: What the venue's adapter states

    Returns:
        The features the venue lacks, in the order they were declared
    """
    return [feature for feature in required_features
            if not capabilities.offers_order_feature(feature)]


def order_feature_refusal(
    logic_name: str,
    venue_label: str,
    venue_lacks: Collection[OrderFeature],
    pipeline_lacks: Collection[OrderFeature] = (),
    exception_possible: bool = False,
) -> Optional[str]:
    """
    The sentence a run is refused with.

    Args:
        logic_name: The decision logic, as the configuration names it
        venue_label: The venue, as its adapter labels it
        venue_lacks: Declared features the venue's API does not hold
        pipeline_lacks: Declared features this pipeline does not carry
        exception_possible: Whether this run could declare the feature as an exception: a
            backtest can, a session cannot

    Returns:
        The message, or None when nothing is short
    """
    if not venue_lacks and not pipeline_lacks:
        return None
    declared = ', '.join(feature.value for feature in [*venue_lacks, *pipeline_lacks])
    if venue_lacks:
        lacks = ', '.join(feature.value for feature in venue_lacks)
        what = f'{venue_label} holds no {lacks} for a position through its API'
    else:
        lacks = ', '.join(feature.value for feature in pipeline_lacks)
        what = f'this pipeline does not carry {lacks}'
    remedy = ('Implement the exits in the logic (OwnExitLevels, see the quickstart), or run on a '
              'venue that holds them')
    if exception_possible:
        remedy += (', or declare them under features_beyond_venue in the scenario set — recorded '
                   'with the run, and refused by a certificate')
    return f'❌ {logic_name} declares {declared} — {what}.\n   → {remedy}.'
