# User Decision Logic

Custom open-source decision logic implementations.

## Usage
1. Create your logic inheriting from `AbstractDecisionLogic`
2. Implement `compute()` and `get_required_workers()`
3. Reference in scenario config: `"decision_logic_type": "USER/my_custom_logic"`

## Example
See `framework/decision_logic/simple_consensus.py` for reference.